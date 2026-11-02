"""AI editing tools for the editor (Premiere's Text-Based Editing, Auto Captions,
Remix/Auto Ducking, Auto Reframe, Scene Edit Detection, Enhance Speech — plus
LLM rough cuts, B-roll and chat commands)."""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from pathlib import Path

import cv2
import numpy as np

from . import brands, library, llm, producers, projects, transcribe
from .providers.base import ProviderError
from .render import media
from .timeline import Clip, Keyframe, Timeline, Track, canvas_for
from .timeline_ops import (next_free_video_track, remove_source_ranges, source_words_to_timeline, split_clip,
                           summarize, track)

log = logging.getLogger("champstudio.ai_edit")

FILLERS = {"um", "uh", "uhm", "umm", "erm", "er", "ah", "hmm", "mm", "mhm"}
MAYBE_FILLERS = {"like", "so", "basically", "actually", "literally", "right", "okay", "you", "know", "i", "mean"}


def _clip(tl: Timeline, clip_id: str) -> Clip:
    c = tl.clip(clip_id)
    if c is None:
        raise KeyError(f"clip {clip_id} not found")
    return c


def _path(c: Clip) -> str:
    if not c.asset_id or not library.asset_path(c.asset_id):
        raise ValueError("clip has no media asset")
    return str(library.asset_path(c.asset_id))


# ── text-based editing ───────────────────────────────────────────────


def remove_ranges(tl: Timeline, clip_id: str, ranges: list[list[float]]) -> dict:
    new, fmap = remove_source_ranges(tl, clip_id, [(float(a), float(b)) for a, b in ranges])
    removed = sum(e - s for s, e in fmap.removed)
    return {"timeline": new.model_dump(by_alias=True),
            "summary": f"Removed {len(fmap.removed)} range(s), {removed / tl.fps:.1f} s"}


async def silence_cut(tl: Timeline, clip_id: str, *, fillers: bool = True, min_silence_s: float = 0.45,
                      padding_s: float = 0.12, noise_db: float = -35) -> dict:
    c = _clip(tl, clip_id)
    path = _path(c)
    gaps = await asyncio.to_thread(media.silences, path, noise_db=noise_db, min_s=min_silence_s)
    ranges = [(s + padding_s, e - padding_s) for s, e in gaps if e - s > 2 * padding_s + 0.05]
    n_sil = len(ranges)
    n_fill = 0
    if fillers:
        try:
            t = await transcribe.transcribe_asset(c.asset_id)
            words = t["words"]
            idx = [i for i, w in enumerate(words) if _norm(w["word"]) in FILLERS]
            ambiguous = [i for i, w in enumerate(words) if _norm(w["word"]) in MAYBE_FILLERS]
            if ambiguous:
                idx += await _llm_fillers(words, ambiguous)
            for i in sorted(set(idx)):
                w = words[i]
                ranges.append((max(0.0, w["start"] - 0.03), w["end"] + 0.03))
            n_fill = len(set(idx))
        except ProviderError as e:
            log.info("filler detection skipped: %s", e)
    new, fmap = remove_source_ranges(tl, clip_id, ranges)
    removed = sum(e - s for s, e in fmap.removed) / tl.fps
    return {"timeline": new.model_dump(by_alias=True),
            "summary": f"Removed {n_sil} pauses and {n_fill} filler words ({removed:.1f} s shorter)"}


def _norm(w: str) -> str:
    return re.sub(r"[^a-z']", "", w.lower())


async def _llm_fillers(words: list[dict], candidates: list[int]) -> list[int]:
    lines = []
    for i in candidates[:300]:
        ctx = " ".join(w["word"] for w in words[max(0, i - 6): i + 7])
        lines.append(f"{i}: '{words[i]['word']}' in: …{ctx}…")
    schema = {"type": "object", "required": ["filler_indices"],
              "properties": {"filler_indices": {"type": "array", "items": {"type": "integer"}}}}
    out = await llm.structured(
        "bulk", "You find verbal filler words in transcripts. A word is filler only when removing it does not "
        "change the meaning (e.g. 'like' as hesitation, 'you know', 'I mean', 'so' at sentence start as a tic). "
        "Return the indices that are fillers.", "\n".join(lines), schema, name="fillers", temperature=0)
    allowed = set(candidates)
    picked = [i for i in out["filler_indices"] if i in allowed]
    # "you know" / "I mean" come in pairs: keep both words of a pair together
    extra = []
    for i in picked:
        if _norm(words[i]["word"]) == "you" and i + 1 < len(words) and _norm(words[i + 1]["word"]) == "know":
            extra.append(i + 1)
        if _norm(words[i]["word"]) == "i" and i + 1 < len(words) and _norm(words[i + 1]["word"]) == "mean":
            extra.append(i + 1)
    return picked + extra


# ── captions ─────────────────────────────────────────────────────────


async def captions(tl: Timeline, clip_ids: list[str] | None = None, style: dict | None = None,
                   brand_kit_id: str | None = None) -> dict:
    tl = tl.model_copy(deep=True)
    if clip_ids:
        clips = [_clip(tl, i) for i in clip_ids]
    else:
        clips = [c for t in tl.audio_tracks() if not t.muted for c in t.clips
                 if c.asset_id and c.role in (None, "dialogue", "vo")]
    words: list[dict] = []
    for c in clips:
        if not c.asset_id:
            continue
        t = await transcribe.transcribe_asset(c.asset_id)
        words += source_words_to_timeline(tl, c, t["words"])
    words.sort(key=lambda w: w["s"])
    if not words:
        return {"timeline": tl.model_dump(by_alias=True), "summary": "No speech found"}
    kit = brands.get(brand_kit_id)
    vertical = tl.height > tl.width
    tid = next_free_video_track(tl, 0, tl.duration, "Captions")
    track(tl, tid).clips.append(Clip(
        id="captions-auto", type="captions", name="Captions", start=0, duration=max(tl.duration, words[-1]["e"]),
        box=[0.06, 0.66, 0.88, 0.14] if vertical else [0.1, 0.8, 0.8, 0.13],
        style={**kit["styles"]["captions"], **(style or {})},
        captions={"words": words, "mode": "karaoke", "max_chars": 28 if vertical else 36}))
    return {"timeline": tl.model_dump(by_alias=True), "summary": f"Captioned {len(words)} words"}


# ── ducking ──────────────────────────────────────────────────────────


def duck(tl: Timeline, duck_db: float = 10.0, ramp_frames: int = 8) -> dict:
    tl = tl.model_copy(deep=True)
    speech = sorted((c.start, c.end) for t in tl.audio_tracks() for c in t.clips if c.role in ("dialogue", "vo"))
    if not speech:  # no roles set: treat non-music audio linked to video as dialogue
        speech = sorted((c.start, c.end) for t in tl.audio_tracks() for c in t.clips
                        if c.role != "music" and c.linked)
    merged: list[list[int]] = []
    for s, e in speech:
        if merged and s <= merged[-1][1] + 2 * ramp_frames:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    g = 10 ** (-abs(duck_db) / 20)
    n = 0
    for t in tl.audio_tracks():
        for c in t.clips:
            if c.role != "music":
                continue
            base = c.volume
            kfs: list[Keyframe] = [Keyframe(f=0, v=1.0, ease="linear")]
            for s, e in merged:
                a, b = s - c.start, e - c.start
                if b <= 0 or a >= c.duration:
                    continue
                kfs += [Keyframe(f=max(0, a - ramp_frames), v=1.0, ease="linear"),
                        Keyframe(f=max(0, a), v=g, ease="linear"),
                        Keyframe(f=min(c.duration, b), v=g, ease="linear"),
                        Keyframe(f=min(c.duration, b + ramp_frames), v=1.0, ease="linear")]
            kfs.sort(key=lambda k: k.f)
            dedup: list[Keyframe] = []
            for k in kfs:
                if dedup and dedup[-1].f == k.f:
                    dedup[-1] = k
                else:
                    dedup.append(k)
            c.keyframes["volume"] = dedup
            c.duck_db = None
            c.volume = base
            n += 1
    return {"timeline": tl.model_dump(by_alias=True),
            "summary": f"Ducked {n} music clip(s) under {len(merged)} speech section(s) by {abs(duck_db):.0f} dB"}


# ── auto reframe ─────────────────────────────────────────────────────


def _face_detector():
    try:
        path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        det = cv2.CascadeClassifier(path)
        return None if det.empty() else det
    except Exception:  # noqa: BLE001
        return None


def subject_track(path: str, *, start_s: float, dur_s: float, every_s: float = 0.33) -> list[tuple[float, float]]:
    """(t_rel_seconds, center_x 0..1) of the main subject: faces first, else motion/saliency."""
    info = media.probe(path)
    sw, sh = info.get("width") or 16, info.get("height") or 9
    h = 270
    w = int(round(sw * h / sh / 2) * 2)
    det = _face_detector()
    reader = media.FrameReader(path, start_s=start_s, count=max(1, int(dur_s / every_s)), fps=1 / every_s,
                               w=w, h=h, fit="fill")
    out, prev = [], None
    try:
        for i in range(max(1, int(dur_s / every_s))):
            frame = reader.next()
            gray = cv2.cvtColor(frame, cv2.COLOR_RGBA2GRAY)
            cx = None
            if det is not None:
                faces = det.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5, minSize=(20, 20))
                if len(faces):
                    x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
                    cx = (x + fw / 2) / w
            if cx is None:
                edges = cv2.Canny(gray, 60, 160).astype(np.float32)
                if prev is not None:
                    edges += cv2.absdiff(gray, prev).astype(np.float32) * 2
                col = edges.sum(axis=0)
                if col.sum() > 0:
                    cx = float((col * np.arange(w)).sum() / col.sum() / w)
            prev = gray
            out.append((i * every_s, cx if cx is not None else 0.5))
    finally:
        reader.close()
    return out


def smooth_path(points: list[tuple[float, float]], *, alpha: float = 0.35, deadzone: float = 0.04) -> list[tuple[float, float]]:
    out, cur = [], None
    for t, x in points:
        if cur is None:
            cur = x
        elif abs(x - cur) > deadzone:
            cur = cur + alpha * (x - cur)
        out.append((t, round(cur, 4)))
    return out


def reframe(tl: Timeline, clip_id: str, aspect: str = "9:16") -> dict:
    tl = tl.model_copy(deep=True)
    c = _clip(tl, clip_id)
    if c.type not in ("video", "image"):
        raise ValueError("reframe works on video or image clips")
    path = _path(c)
    fps = tl.fps
    if c.type == "video":
        pts = smooth_path(subject_track(path, start_s=c.in_ / fps, dur_s=c.duration * c.speed / fps))
    else:
        pts = [(0.0, 0.5)]
    kfs = []
    for t_rel, x in pts:
        f = int(round(t_rel * fps / c.speed))
        if f < c.duration and (not kfs or abs(kfs[-1].v - x) > 0.002):
            kfs.append(Keyframe(f=f, v=x, ease="ease"))
    c.fit = "reframe"
    c.keyframes["crop_x"] = kfs or [Keyframe(f=0, v=0.5)]
    return {"timeline": tl.model_dump(by_alias=True),
            "summary": f"Reframed for {aspect} with {len(kfs)} keyframes"}


def duplicate_as(tl: Timeline, aspect: str) -> Timeline:
    """Same sequence on a new canvas; every full-frame video/image clip is reframed."""
    new = tl.model_copy(deep=True)
    new.width, new.height = canvas_for(aspect)
    for t in new.video_tracks():
        for c in t.clips:
            if c.type in ("video", "image") and c.box == [0.0, 0.0, 1.0, 1.0] and c.asset_id:
                try:
                    new = Timeline(**reframe(new, c.id, aspect)["timeline"])
                except Exception as e:  # noqa: BLE001 — keep centre crop
                    log.info("reframe %s skipped: %s", c.id, e)
    return new


# ── scene detection / speech enhancement ─────────────────────────────


def scene_detect(asset_id: str, threshold: float = 0.3) -> dict:
    p = library.asset_path(asset_id)
    if not p:
        raise KeyError(asset_id)
    cuts = media.scene_cuts(str(p), threshold=threshold)
    return {"cuts": [round(c, 3) for c in cuts], "summary": f"Found {len(cuts)} shot changes"}


def split_at_cuts(tl: Timeline, clip_id: str, cuts_s: list[float]) -> Timeline:
    c = _clip(tl, clip_id)
    fps = tl.fps
    for cut in sorted(cuts_s, reverse=True):
        f = c.start + int(round((cut * fps - c.in_) / c.speed))
        tl = split_clip(tl, clip_id, f)
    return tl


def enhance_speech(asset_id: str) -> dict:
    src = library.asset_path(asset_id)
    if not src:
        raise KeyError(asset_id)
    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / "enhanced.wav")
        media._run([media.FFMPEG, "-v", "error", "-nostdin", "-y", "-i", str(src), "-vn",
                    "-af", "highpass=f=80,lowpass=f=12000,afftdn=nf=-25:tn=1,"
                           "acompressor=threshold=-20dB:ratio=3:attack=5:release=120,loudnorm=I=-16:TP=-1.5:LRA=9",
                    "-ar", "48000", "-ac", "2", out], timeout=3600)
        asset = library.save_asset_file(out, "audio/wav", tool="enhance-speech", parent_id=asset_id,
                                        prompt=(library.get_asset(asset_id) or {}).get("prompt", ""))
    return {"asset": asset, "summary": "Speech enhanced (denoise, EQ, compression, loudness)"}


# ── B-roll ───────────────────────────────────────────────────────────

BROLL_SCHEMA = {
    "type": "object", "required": ["beats"],
    "properties": {"beats": {"type": "array", "maxItems": 12, "items": {
        "type": "object", "required": ["start_s", "end_s", "prompt"],
        "properties": {"start_s": {"type": "number"}, "end_s": {"type": "number"},
                       "prompt": {"type": "string", "maxLength": 400}}}}},
}


async def broll(tl: Timeline, brief: str | None = None, max_beats: int = 6, brand_kit_id: str | None = None) -> dict:
    tl = tl.model_copy(deep=True)
    fps = tl.fps
    words: list[dict] = []
    for t in tl.audio_tracks():
        for c in t.clips:
            if c.asset_id and c.role in (None, "dialogue", "vo"):
                try:
                    tr = await transcribe.transcribe_asset(c.asset_id)
                except ProviderError as e:
                    raise ProviderError(f"transcription needed for B-roll: {e}") from e
                words += source_words_to_timeline(tl, c, tr["words"])
    if not words:
        return {"timeline": tl.model_dump(by_alias=True), "summary": "No speech to illustrate"}
    words.sort(key=lambda w: w["s"])
    transcript = " ".join(f"[{w['s'] / fps:.1f}] {w['w']}" if i % 8 == 0 else w["w"] for i, w in enumerate(words))
    out = await llm.structured(
        "planner",
        f"You are a B2B video editor choosing B-roll cutaways. Pick at most {max_beats} moments (2–5 s each, not "
        "overlapping, not in the first 2 s) where a visual would reinforce what is said. For each give the start/end "
        "in seconds (timeline time, use the [time] marks) and a photographic image prompt — no text, no logos, "
        "no real people's likeness.",
        f"Brief: {brief or 'n/a'}\nTranscript with time marks:\n{transcript}", BROLL_SCHEMA, name="broll")
    kit = brands.get(brand_kit_id)
    style = f"clean modern commercial photography, accents {kit['colors']['primary']}, no text, no logos"
    placed = 0
    total = tl.duration
    for beat in out["beats"][:max_beats]:
        s = max(0, int(round(beat["start_s"] * fps)))
        e = min(total, int(round(beat["end_s"] * fps)))
        if e - s < fps:
            continue
        aspect = "9:16" if tl.height > tl.width else "16:9"
        still = await producers.still(beat["prompt"], aspect=aspect, style=style)
        tid = next_free_video_track(tl, s, e, "B-roll")
        track(tl, tid).clips.append(Clip(
            id=f"broll-{placed}-{s}", type="image", asset_id=still["id"], start=s, duration=e - s, name="B-roll",
            transition_in={"type": "crossfade", "duration": 8}, anim_out={"type": "fade", "duration": 8},
            keyframes={"scale": [Keyframe(f=0, v=1.0, ease="linear"), Keyframe(f=e - s, v=1.12)]}))
        placed += 1
    return {"timeline": tl.model_dump(by_alias=True), "summary": f"Added {placed} B-roll cutaway(s)"}


# ── chat command ─────────────────────────────────────────────────────

COMMAND_SCHEMA = {
    "type": "object", "required": ["ops", "summary"],
    "properties": {
        "summary": {"type": "string", "maxLength": 200},
        "ops": {"type": "array", "maxItems": 30, "items": {
            "type": "object", "required": ["op"],
            "properties": {
                "op": {"type": "string", "enum": ["move", "trim", "delete", "split", "set", "add_text",
                                                  "add_marker", "lower_third"]},
                "clip_id": {"type": "string"},
                "start": {"type": "integer"}, "duration": {"type": "integer"}, "in": {"type": "integer"},
                "frame": {"type": "integer"}, "ripple": {"type": "boolean"},
                "field": {"type": "string", "enum": ["name", "text", "volume", "speed", "fit", "box", "transform",
                                                    "style", "anim_in", "anim_out", "transition_in", "color",
                                                    "enabled", "fade_in", "fade_out", "role"]},
                "value": {"type": ["string", "number", "boolean", "object", "array", "null"]},
                "text": {"type": "string", "maxLength": 200}, "label": {"type": "string", "maxLength": 80},
                "name": {"type": "string", "maxLength": 60}, "role": {"type": "string", "maxLength": 80},
            }}},
    },
}

SYSTEM_COMMAND = """You operate a video editor's timeline. Turn the user's instruction into ops.
Frames are integers at the timeline fps. Ops:
- move {clip_id, start}; trim {clip_id, start?, duration?, in?}; delete {clip_id, ripple}; split {clip_id, frame}
- set {clip_id, field, value} (box = [x,y,w,h] in 0..1; transform = {x,y,scale,rotation,opacity};
  anim_in/anim_out/transition_in = {type, duration}; style = text style object)
- add_text {text, start, duration}; add_marker {frame, label}; lower_third {name, role, start, duration?}
Only reference clip ids that exist. Keep changes minimal. summary: one sentence."""


async def command(tl: Timeline, message: str, brand_kit_id: str | None = None) -> dict:
    import json

    plan = await llm.structured("planner", SYSTEM_COMMAND,
                                f"Timeline:\n{json.dumps(summarize(tl))}\n\nInstruction: {message}",
                                COMMAND_SCHEMA, name="timeline_ops")
    new = apply_ops(tl, plan["ops"], brand_kit_id=brand_kit_id)
    return {"timeline": new.model_dump(by_alias=True), "summary": plan["summary"], "ops": plan["ops"]}


def apply_ops(tl: Timeline, ops: list[dict], brand_kit_id: str | None = None) -> Timeline:
    tl = tl.model_copy(deep=True)
    kit = brands.get(brand_kit_id)
    for op in ops:
        kind = op["op"]
        c = tl.clip(op["clip_id"]) if op.get("clip_id") else None
        if kind in ("move", "trim", "delete", "split", "set") and c is None:
            raise ValueError(f"{kind}: unknown clip {op.get('clip_id')}")
        if kind == "move":
            c.start = max(0, int(op["start"]))
        elif kind == "trim":
            if "start" in op:
                delta = int(op["start"]) - c.start
                c.start += delta
                c.in_ = max(0, c.in_ + int(round(delta * c.speed)))
                c.duration = max(1, c.duration - delta)
            if "duration" in op:
                c.duration = max(1, int(op["duration"]))
            if "in" in op:
                c.in_ = max(0, int(op["in"]))
        elif kind == "delete":
            for t in tl.tracks:
                if c in t.clips:
                    t.clips.remove(c)
                    if op.get("ripple"):
                        for other_t in tl.tracks:
                            for o in other_t.clips:
                                if o.start >= c.end:
                                    o.start -= c.duration
                    break
        elif kind == "split":
            tl = split_clip(tl, c.id, int(op["frame"]))
        elif kind == "set":
            field = op["field"]
            value = op.get("value")
            data = c.model_dump(by_alias=True)
            data[field] = value
            updated = Clip(**data)
            for t in tl.tracks:
                for i, x in enumerate(t.clips):
                    if x.id == c.id:
                        t.clips[i] = updated
        elif kind == "add_text":
            start, dur = int(op.get("start", 0)), int(op.get("duration", 3 * tl.fps))
            tid = next_free_video_track(tl, start, start + dur, "Titles")
            track(tl, tid).clips.append(Clip(id=f"txt-{start}-{len(track(tl, tid).clips)}", type="text",
                                             text=op.get("text", ""), start=start, duration=dur,
                                             box=[0.08, 0.38, 0.84, 0.24], style=kit["styles"]["title"],
                                             anim_in={"type": "slide-up", "duration": 10},
                                             anim_out={"type": "fade", "duration": 8}))
        elif kind == "add_marker":
            from .timeline import Marker

            tl.markers.append(Marker(f=int(op.get("frame", 0)), label=op.get("label", "")))
        elif kind == "lower_third":
            from . import templates

            t = templates.get("lower-third")
            aspect = "9:16" if tl.height > tl.width else "16:9"
            values = templates.with_defaults(t, {"name": op.get("name", ""), "role": op.get("role", "")})
            g = templates.compile_timeline(t, {"values": values, "vo": {}, "music_id": None}, kit, aspect)
            start = int(op.get("start", 0))
            dur = int(op.get("duration") or g.duration)
            new_clips = [gc for gt in g.video_tracks() for gc in gt.clips]
            # keep clear of on-screen text and captions already in that time range
            band = (min(c.box[1] for c in new_clips), max(c.box[1] + c.box[3] for c in new_clips))
            busy = [c for t in tl.video_tracks() for c in t.clips
                    if c.type in ("text", "captions") and c.start < start + dur and c.end > start
                    and c.box[1] < band[1] and c.box[1] + c.box[3] > band[0]]
            shift = (0.06 - band[0]) if busy else 0.0
            tid = next_free_video_track(tl, start, start + dur, "Graphics")
            for gc in new_clips:
                gc.start += start
                gc.duration = min(gc.duration, dur)
                gc.id = f"lt-{start}-{gc.id}"
                if shift:
                    gc.box = [gc.box[0], round(gc.box[1] + shift, 4), gc.box[2], gc.box[3]]
                track(tl, tid).clips.append(gc)
    for t in tl.tracks:
        t.clips.sort(key=lambda x: x.start)
    return Timeline(**tl.model_dump(by_alias=True))


# ── rough cut ────────────────────────────────────────────────────────

ROUGH_SCHEMA = {
    "type": "object", "required": ["title", "segments"],
    "properties": {"title": {"type": "string", "maxLength": 80},
                   "segments": {"type": "array", "minItems": 1, "maxItems": 80, "items": {"type": "string"}}},
}


def sentence_segments(asset_id: str, words: list[dict], gap_s: float = 0.6, max_s: float = 14) -> list[dict]:
    segs, cur = [], []
    for w in words:
        if cur and (w["start"] - cur[-1]["end"] > gap_s or cur[-1]["word"].endswith((".", "?", "!"))
                    or w["end"] - cur[0]["start"] > max_s):
            segs.append(cur)
            cur = []
        cur.append(w)
    if cur:
        segs.append(cur)
    return [{"id": f"{asset_id}:{i}", "asset_id": asset_id, "start": s[0]["start"], "end": s[-1]["end"],
             "text": " ".join(w["word"] for w in s)} for i, s in enumerate(segs)]


async def rough_cut(asset_ids: list[str], brief: str, *, aspect: str = "16:9", target_s: float = 60,
                    owner: str = "", captions_on: bool = True) -> dict:
    segments: list[dict] = []
    for aid in asset_ids:
        t = await transcribe.transcribe_asset(aid)
        segments += sentence_segments(aid, t["words"])
    if not segments:
        raise ValueError("no speech found in the selected media")
    listing = "\n".join(f"{s['id']} ({s['end'] - s['start']:.1f}s): {s['text']}" for s in segments)
    out = await llm.structured(
        "planner",
        "You are an editor making a rough cut from interview/recording transcripts. Choose and order segment ids "
        "to tell a tight story for the brief, close to the target length. Prefer complete thoughts, a strong "
        "opening line and a clear ending. Use each id at most once; only use ids from the list.",
        f"Brief: {brief}\nTarget length: {target_s:.0f} s\nSegments:\n{listing}", ROUGH_SCHEMA, name="rough_cut")
    by_id = {s["id"]: s for s in segments}
    w, h = canvas_for(aspect)
    tl = Timeline(fps=30, width=w, height=h, tracks=[Track(id="v1", kind="video", name="V1"),
                                                     Track(id="a1", kind="audio", name="A1")])
    fps = tl.fps
    pos = 0
    used = set()
    for sid in out["segments"]:
        s = by_id.get(sid)
        if not s or sid in used:
            continue
        used.add(sid)
        info = media.probe(str(library.asset_path(s["asset_id"])))
        a = max(0.0, s["start"] - 0.12)
        b = min(info["duration"], s["end"] + 0.18)
        dur = max(1, int(round((b - a) * fps)))
        vid, aud = f"v-{len(used)}", f"a-{len(used)}"
        if info.get("has_video"):
            tl.tracks[0].clips.append(Clip(id=vid, type="video", asset_id=s["asset_id"], start=pos, duration=dur,
                                           linked=aud, name=s["text"][:40], **{"in": int(round(a * fps))}))
        tl.tracks[1].clips.append(Clip(id=aud, type="audio", asset_id=s["asset_id"], start=pos, duration=dur,
                                       linked=vid if info.get("has_video") else None, role="dialogue",
                                       fade_in=1, fade_out=1, name=s["text"][:40], **{"in": int(round(a * fps))}))
        pos += dur
    # footage shot for another ratio: follow the subject instead of centre-cropping
    for c in list(tl.tracks[0].clips):
        info = media.probe(str(library.asset_path(c.asset_id)))
        src_ratio = (info.get("width") or 16) / max(1, info.get("height") or 9)
        if abs(src_ratio - w / h) > 0.05:
            try:
                tl = Timeline(**(await asyncio.to_thread(reframe, tl, c.id, aspect))["timeline"])
            except Exception as e:  # noqa: BLE001 — centre crop is an acceptable fallback
                log.info("rough-cut reframe %s skipped: %s", c.id, e)
    if captions_on:
        tl = Timeline(**(await captions(tl))["timeline"])
    p = projects.create(out["title"] or "Rough cut", "edit", tl.model_dump(by_alias=True), actor=owner)
    return {"project_id": p["id"], "summary": f"Rough cut of {len(used)} segments, {pos / fps:.0f} s"}
