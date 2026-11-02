"""Word-timestamped transcription through OpenRouter, in segments.

The upstream STT call times out around 60 s of audio (Phase 0 finding), so
audio is cut into ≤50 s pieces, preferably at silences, transcribed one by
one and stitched with offsets. Results are cached per asset in
derived/<id>/transcript.json. Word times are *source* seconds.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from . import costs, ingest, library, roles
from .providers.base import ProviderError
from .providers.openrouter import OpenRouterClient
from .render import media

SEGMENT_S = 50.0

_client_factory = OpenRouterClient


def set_client_factory(factory) -> None:
    global _client_factory
    _client_factory = factory


def plan_segments(duration: float, silences: list[tuple[float, float]], seg: float = SEGMENT_S) -> list[tuple[float, float]]:
    """Split [0, duration] into pieces ≤ seg, cutting in the middle of a silence when one is near."""
    out = []
    start = 0.0
    while duration - start > seg:
        target = start + seg
        best = None
        for s, e in silences:
            mid = (s + e) / 2
            if start + seg * 0.5 < mid <= target:
                best = mid
        cut = best or target
        out.append((start, cut))
        start = cut
    out.append((start, duration))
    return [(round(a, 3), round(b, 3)) for a, b in out if b - a > 0.05]


async def transcribe_file(path: str, *, language: str | None = None, keyterms: list[str] | None = None,
                          project_id: str | None = None) -> dict:
    info = media.probe(path)
    if not info.get("has_audio"):
        return {"text": "", "words": [], "duration": info.get("duration", 0)}
    duration = info["duration"]
    gaps = media.silences(path, noise_db=-35, min_s=0.3) if duration > SEGMENT_S else []
    client = _client_factory()
    words: list[dict] = []
    texts: list[str] = []
    total_cost = 0.0
    with tempfile.TemporaryDirectory(prefix="champ-stt-") as tmp:
        for i, (a, b) in enumerate(plan_segments(duration, gaps)):
            piece = str(Path(tmp) / f"seg{i}.mp3")
            media.extract_audio(path, piece, start_s=a, dur_s=b - a)
            last: Exception | None = None
            for model in roles.candidates("transcribe"):
                try:
                    res = await client.transcribe(audio=Path(piece).read_bytes(), audio_format="mp3",
                                                  model=model, language=language, keyterms=keyterms)
                    break
                except ProviderError as e:
                    last = e
            else:
                raise ProviderError(f"transcription failed: {last}")
            texts.append(res["text"].strip())
            for w in res["words"]:
                words.append({"word": w["word"], "start": round(w["start"] + a, 3), "end": round(w["end"] + a, 3)})
            cost = res.get("cost")
            if cost is None:
                cost = costs.estimate_transcribe(b - a)
            total_cost += float(cost)
    costs.record(kind="transcribe", model=roles.model_for("transcribe"), cost=total_cost,
                 role="transcribe", project_id=project_id, meta={"seconds": duration})
    return {"text": " ".join(t for t in texts if t), "words": words, "duration": duration,
            "language": language}


async def transcribe_asset(asset_id: str, *, language: str | None = None, force: bool = False,
                           keyterms: list[str] | None = None, project_id: str | None = None) -> dict:
    cache = ingest.derived_dir(asset_id) / "transcript.json"
    if cache.exists() and not force:
        return json.loads(cache.read_text())
    path = library.asset_path(asset_id)
    if not path:
        raise KeyError(asset_id)
    result = await transcribe_file(str(path), language=language, keyterms=keyterms, project_id=project_id)
    result["asset_id"] = asset_id
    cache.write_text(json.dumps(result))
    return result
