"""Media producers shared by Director and the template engine.

Each call picks the model from its role (with fallback), checks the budget
first, records actual cost in the ledger, and lands its output in the asset
library with lineage (`parent_id`).
"""

from __future__ import annotations

import base64
import logging
import re
import tempfile
from pathlib import Path

from . import catalog, costs, jobs, library, roles
from .providers.base import ProviderError
from .providers.openrouter import OpenRouterClient
from .render import media

log = logging.getLogger("champstudio.producers")

_client_factory = OpenRouterClient


def set_client_factory(factory) -> None:
    global _client_factory
    _client_factory = factory


def client() -> OpenRouterClient:
    return _client_factory()


def data_url(asset_id: str) -> str:
    a = library.get_asset(asset_id)
    p = library.asset_path(asset_id)
    if not a or not p:
        raise ProviderError(f"asset {asset_id} missing")
    return f"data:{a['mime']};base64,{base64.b64encode(p.read_bytes()).decode()}"


MUSIC_PROMPTS = {
    "corporate-upbeat": "Upbeat modern corporate background music, light percussion, plucked synths, optimistic, "
                        "no vocals, steady 110 bpm, suitable under a voiceover",
    "calm-tech": "Calm minimal technology ambient bed, soft pads and gentle arpeggio, no vocals, unobtrusive",
    "inspiring": "Inspiring cinematic build, piano and strings, warm, no vocals, rising energy",
    "energetic": "Energetic punchy electronic pop instrumental, driving beat, no vocals, social media ad",
    "chill": "Chill lo-fi instrumental, warm keys, soft beat, no vocals",
    "dramatic": "Dramatic cinematic trailer bed, deep pulses and hits, no vocals",
    "none": "",
}


async def still(prompt: str, *, aspect: str = "16:9", project_id: str | None = None,
                reference_ids: list[str] | None = None, style: str = "") -> dict:
    full = f"{prompt.strip()}. {style}".strip(". ") if style else prompt
    costs.check_budget(costs.IMAGE_PRICE_GUESS, project_id=project_id)
    refs = [data_url(r) for r in (reference_ids or [])]
    last: Exception | None = None
    for model in roles.candidates("image"):
        try:
            out = await client().generate_images(prompt=full, model=model, n=1, aspect_ratio=aspect,
                                                 input_references=refs or None)
            m = out[0]
            costs.record(kind="image", model=model, role="image", project_id=project_id,
                         cost=m.cost if m.cost is not None else costs.IMAGE_PRICE_GUESS, estimated=m.cost is None)
            return library.save_asset(m.data, m.mime, prompt=full, model=model, provider=m.provider,
                                      params={"aspect_ratio": aspect, "project_id": project_id},
                                      parent_id=(reference_ids or [None])[0], cost=m.cost)
        except ProviderError as e:
            log.warning("still via %s failed: %s", model, e)
            last = e
    raise ProviderError(f"image generation failed: {last}")


async def clip(prompt: str, *, seconds: float, aspect: str, quality: str = "draft",
               still_id: str | None = None, project_id: str | None = None, audio: bool = False,
               timeout_s: float = 1500) -> dict:
    """Generate a video clip (image-to-video when `still_id`), wait, return the asset."""
    role = "video_final" if quality == "final" else "video_draft"
    r = roles.role(role)
    last: Exception | None = None
    for model in roles.candidates(role):
        dur = catalog.snap_duration(model, seconds)
        res = catalog.best_resolution(model, (r.get("params") or {}).get("resolution"))
        gen_audio = audio if catalog.spec(model).get("audio") is not False else None
        estimate = costs.estimate_video(model, dur, res, bool(gen_audio))
        costs.check_budget(estimate, project_id=project_id)
        kwargs: dict = {"duration": dur, "aspect_ratio": catalog.best_aspect(model, aspect)}
        if res:
            kwargs["resolution"] = res
        if gen_audio is not None:
            kwargs["generate_audio"] = bool(gen_audio)
        if still_id:
            kwargs["first_frame"] = data_url(still_id)
        try:
            job = await jobs.start_video_job(client(), prompt=prompt, model=model, parent_id=still_id,
                                             submit_kwargs=kwargs, kind="video", project_id=project_id,
                                             extra_params={"quality": quality})
            done = await jobs.wait_for(job["id"], timeout_s=timeout_s, interval=2)
            if done["status"] == "completed" and done.get("asset"):
                return done["asset"]
            last = ProviderError(done.get("error") or "video generation failed")
        except ProviderError as e:
            last = e
        log.warning("clip via %s failed: %s", model, last)
    raise ProviderError(f"video generation failed: {last}")


async def avatar(portrait_id: str, audio_id: str, *, aspect: str = "16:9", project_id: str | None = None,
                 timeout_s: float = 1500) -> dict:
    model = roles.model_for("avatar")
    seconds = media.probe(str(library.asset_path(audio_id)))["duration"]
    costs.check_budget(costs.estimate_video(model, seconds, "720p"), project_id=project_id)
    job = await jobs.start_video_job(
        client(), prompt="", model=model, parent_id=portrait_id, kind="lipsync", project_id=project_id,
        submit_kwargs={"input_references": [data_url(portrait_id)], "audio_references": [data_url(audio_id)],
                       "aspect_ratio": aspect if aspect in ("16:9", "9:16", "1:1") else "16:9",
                       "resolution": "720p"},
        extra_params={"audio_asset_id": audio_id})
    done = await jobs.wait_for(job["id"], timeout_s=timeout_s, interval=2)
    if done["status"] != "completed":
        raise ProviderError(done.get("error") or "avatar generation failed")
    return done["asset"]


async def voiceover(text: str, *, voice: str | None = None, model: str | None = None,
                    project_id: str | None = None, speed: float | None = None) -> tuple[dict, float]:
    r = roles.role("speech")
    model = model or r["model"]
    voice = voice or (r.get("params") or {}).get("voice")
    costs.check_budget(costs.estimate_speech(len(text)), project_id=project_id)
    m = await client().synthesize_speech(text=text, model=model, voice=voice, speed=speed)
    costs.record(kind="speech", model=model, role="speech", project_id=project_id,
                 cost=m.cost if m.cost is not None else costs.estimate_speech(len(text)), estimated=m.cost is None)
    asset = library.save_asset(m.data, m.mime, prompt=text, model=model, provider=m.provider,
                               params={"voice": voice, "project_id": project_id})
    return asset, media.probe(str(library.asset_path(asset["id"])))["duration"]


async def music(mood: str, *, project_id: str | None = None, extra: str = "") -> dict | None:
    prompt = MUSIC_PROMPTS.get(mood, mood)
    if not prompt:
        return None
    model = roles.model_for("music")
    costs.check_budget(costs.MUSIC_PER_CLIP, project_id=project_id)
    m = await client().generate_music(prompt=f"{prompt}. {extra}".strip(". "), model=model)
    costs.record(kind="music", model=model, role="music", project_id=project_id,
                 cost=m.cost if m.cost is not None else costs.MUSIC_PER_CLIP, estimated=m.cost is None)
    return library.save_asset(m.data, m.mime, prompt=prompt, model=model, provider=m.provider,
                              params={"mood": mood, "project_id": project_id})


def even_word_timings(text: str, start: float, end: float) -> list[dict]:
    """Fallback timings: spread words over [start, end] weighted by length."""
    words = [w for w in re.split(r"\s+", text.strip()) if w]
    if not words:
        return []
    weights = [len(w) + 2 for w in words]
    total = sum(weights)
    span = max(0.1, end - start)
    out, t = [], start
    for w, k in zip(words, weights):
        d = span * k / total
        out.append({"word": w, "start": round(t, 3), "end": round(t + d, 3)})
        t += d
    return out


async def word_timings(audio_id: str, text: str, *, project_id: str | None = None) -> list[dict]:
    """Word-level timings for a voiceover we generated: transcription aligned to
    our script; falls back to even spacing when transcription is unavailable."""
    path = str(library.asset_path(audio_id))
    duration = media.probe(path)["duration"]
    try:
        from . import transcribe

        t = await transcribe.transcribe_file(path, project_id=project_id)
        words = t["words"]
    except Exception as e:  # noqa: BLE001 — captions still work with even timing
        log.info("transcription unavailable (%s); using even word timing", e)
        words = []
    script = [w for w in re.split(r"\s+", text.strip()) if w]
    if words and abs(len(words) - len(script)) <= max(2, len(script) // 5):
        # keep the script's spelling/punctuation, the transcript's timing
        if len(words) == len(script):
            return [{"word": s, "start": w["start"], "end": w["end"]} for s, w in zip(script, words)]
        return words
    lead = 0.05
    return even_word_timings(text, lead, max(lead + 0.2, duration - 0.1))


def silent_wav(seconds: float) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "silence.wav"
        media._run([media.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-t", f"{seconds:.3f}",
                    "-i", "anullsrc=r=48000:cl=stereo", str(p)])
        return library.save_asset(p.read_bytes(), "audio/wav", tool="silence")
