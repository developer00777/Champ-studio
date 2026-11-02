"""Audio mixdown for a Timeline with one ffmpeg filter graph.

Per audio clip: trim from its source, speed (atempo), reverse, clip/track
gain, keyframed volume, fades, place at its timeline start (adelay), then
ducking under dialogue/voiceover for clips with `duck_db`. Everything is
summed (amix normalize=0), padded/trimmed to the timeline length and
optionally loudness-normalized (EBU R128, default -14 LUFS for social).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..timeline import Clip, Timeline
from . import media
from .compositor import resolve_source

RAMP_S = 0.25


def _atempo_chain(speed: float) -> list[str]:
    out = []
    s = speed
    while s > 2.0:
        out.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        out.append("atempo=0.5")
        s /= 0.5
    if abs(s - 1.0) > 1e-4:
        out.append(f"atempo={s:.5f}")
    return out


def _volume_expr(points: list[tuple[float, float]]) -> str:
    """Piecewise-linear gain over clip-local seconds."""
    pts = sorted(points)
    if len(pts) == 1:
        return f"{pts[0][1]:.4f}"
    expr = f"{pts[-1][1]:.4f}"
    for (t0, v0), (t1, v1) in reversed(list(zip(pts, pts[1:]))):
        span = max(1e-3, t1 - t0)
        seg = f"({v0:.4f}+({v1 - v0:.4f})*(t-{t0:.4f})/{span:.4f})"
        expr = f"if(lt(t,{t1:.4f}),{seg},{expr})"
    return f"if(lt(t,{pts[0][0]:.4f}),{pts[0][1]:.4f},{expr})"


def _duck_expr(ranges: list[tuple[float, float]], gain: float) -> str:
    """Gain envelope (timeline seconds) that dips to `gain` inside speech ranges with ramps."""
    if not ranges:
        return "1"
    r = RAMP_S
    terms = [f"max(0,min(1,min((t-{s - r:.3f})/{r},({e + r:.3f}-t)/{r})))" for s, e in ranges]
    membership = f"min(1,{'+'.join(terms)})"
    return f"1-{membership}*{1 - gain:.4f}"


def flatten_audio(tl: Timeline) -> list[tuple[Clip, float]]:
    """(clip, track_gain) for every audible clip, including nested sequences."""
    tracks = tl.audio_tracks()
    soloed = any(t.solo for t in tracks)
    out: list[tuple[Clip, float]] = []
    for t in tracks:
        if t.muted or (soloed and not t.solo):
            continue
        for c in t.clips:
            if c.enabled and c.type == "audio":
                out.append((c, t.volume))
    # nested sequences contribute their own audio, shifted into place
    for t in tl.video_tracks():
        for c in t.clips:
            if c.enabled and c.nest:
                inner = Timeline(**c.nest)
                for ic, g in flatten_audio(inner):
                    shifted = ic.model_copy(deep=True)
                    shifted.start = ic.start + c.start - c.in_
                    if shifted.end <= c.start or shifted.start >= c.end:
                        continue
                    if shifted.start < c.start:
                        cut = c.start - shifted.start
                        shifted.in_ += int(cut * shifted.speed)
                        shifted.start, shifted.duration = c.start, shifted.duration - cut
                    if shifted.end > c.end:
                        shifted.duration = c.end - shifted.start
                    out.append((shifted, g))
    return out


def speech_ranges(tl: Timeline, clips: list[tuple[Clip, float]]) -> list[tuple[float, float]]:
    fps = tl.fps
    rng = sorted((c.start / fps, c.end / fps) for c, _ in clips if c.role in ("dialogue", "vo"))
    merged: list[list[float]] = []
    for s, e in rng:
        if merged and s <= merged[-1][1] + 2 * RAMP_S:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def mixdown(tl: Timeline, out_wav: str, *, loudness: float | None = None, rate: int = 48000) -> dict:
    """Write the timeline's mix to `out_wav`. Returns {inputs, duration}."""
    fps = tl.fps
    total = max(1, tl.duration) / fps
    clips = flatten_audio(tl)
    speech = speech_ranges(tl, clips)
    args = [media.FFMPEG, "-v", "error", "-nostdin", "-y"]
    chains: list[str] = []
    labels: list[str] = []
    idx = 0
    for clip, track_gain in clips:
        path = resolve_source(clip)
        if not path:
            continue
        info = media.probe(path)
        if not info.get("has_audio"):
            continue
        dur = clip.duration / fps
        src_in = clip.in_ / fps
        args += ["-ss", f"{src_in:.4f}", "-t", f"{dur * clip.speed + 0.05:.4f}", "-i", path]
        f = [f"[{idx}:a]aformat=sample_rates={rate}:channel_layouts=stereo"]
        if clip.reverse:
            f.append("areverse")
        f += _atempo_chain(clip.speed)
        f.append(f"atrim=0:{dur:.4f}")
        f.append("asetpts=PTS-STARTPTS")
        gain = clip.volume * track_gain
        kfs = clip.keyframes.get("volume")
        if kfs:
            pts = [(k.f / fps, k.v * gain) for k in kfs]
            f.append(f"volume='{_volume_expr(pts)}':eval=frame")
        elif abs(gain - 1) > 1e-4:
            f.append(f"volume={gain:.4f}")
        if clip.fade_in:
            f.append(f"afade=t=in:st=0:d={clip.fade_in / fps:.4f}")
        if clip.fade_out:
            f.append(f"afade=t=out:st={max(0, dur - clip.fade_out / fps):.4f}:d={clip.fade_out / fps:.4f}")
        delay_ms = int(round(clip.start / fps * 1000))
        if delay_ms:
            f.append(f"adelay={delay_ms}|{delay_ms}")
        if clip.duck_db and speech and clip.role not in ("dialogue", "vo"):
            g = 10 ** (-abs(clip.duck_db) / 20)
            f.append(f"volume='{_duck_expr(speech, g)}':eval=frame")
        label = f"a{idx}"
        chains.append(",".join(f) + f"[{label}]")
        labels.append(f"[{label}]")
        idx += 1

    if not labels:
        args += ["-f", "lavfi", "-t", f"{total:.4f}", "-i", f"anullsrc=r={rate}:cl=stereo"]
        args += ["-c:a", "pcm_s16le", out_wav]
        media._run(args, timeout=600)
        return {"inputs": 0, "duration": total}

    tail = f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:dropout_transition=0"
    tail += f",apad=whole_dur={total:.4f},atrim=0:{total:.4f}"
    if loudness is not None:
        tail += f",loudnorm=I={loudness}:TP=-1.5:LRA=11"
    tail += f",aresample={rate}[mix]"
    graph = ";".join(chains + [tail])
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(graph)
        script = fh.name
    try:
        args += ["-filter_complex_script", script, "-map", "[mix]", "-c:a", "pcm_s16le",
                 "-ar", str(rate), out_wav]
        media._run(args, timeout=3600)
    finally:
        Path(script).unlink(missing_ok=True)
    return {"inputs": len(labels), "duration": total}
