"""Export a Timeline to a media file: audio mixdown + composited frames piped
into ffmpeg's encoder. Also SRT/VTT export from caption clips.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ..timeline import Timeline, TextStyle, timecode
from . import audio, media, text as textmod
from .compositor import Compositor, place

PRESETS: dict[str, dict] = {
    "h264": {"label": "H.264 MP4 (web/social)", "ext": ".mp4", "mime": "video/mp4",
             "v": ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p"],
             "a": ["-c:a", "aac", "-b:a", "192k"], "extra": ["-movflags", "+faststart"]},
    "h264-draft": {"label": "Draft MP4 (half size, fast)", "ext": ".mp4", "mime": "video/mp4", "scale": 0.5,
                   "v": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "27", "-pix_fmt", "yuv420p"],
                   "a": ["-c:a", "aac", "-b:a", "128k"], "extra": ["-movflags", "+faststart"]},
    "review": {"label": "Review copy (720p + timecode)", "ext": ".mp4", "mime": "video/mp4", "height": 720,
               "timecode": True,
               "v": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p"],
               "a": ["-c:a", "aac", "-b:a", "128k"], "extra": ["-movflags", "+faststart"]},
    "prores": {"label": "ProRes 422 HQ (MOV)", "ext": ".mov", "mime": "video/quicktime",
               "v": ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"],
               "a": ["-c:a", "pcm_s16le"], "extra": []},
    "dnxhr": {"label": "DNxHR HQ (MOV)", "ext": ".mov", "mime": "video/quicktime",
              "v": ["-c:v", "dnxhd", "-profile:v", "dnxhr_hq", "-pix_fmt", "yuv422p"],
              "a": ["-c:a", "pcm_s16le"], "extra": []},
    "webm": {"label": "WebM VP9", "ext": ".webm", "mime": "video/webm",
             "v": ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "32", "-row-mt", "1", "-deadline", "realtime",
                   "-cpu-used", "8", "-pix_fmt", "yuv420p"],
             "a": ["-c:a", "libopus", "-b:a", "128k"], "extra": []},
    "audio-wav": {"label": "Audio only (WAV)", "ext": ".wav", "mime": "audio/wav", "audio_only": True},
    "audio-mp3": {"label": "Audio only (MP3)", "ext": ".mp3", "mime": "audio/mpeg", "audio_only": True},
}

ProgressFn = Callable[[float, str], None]

# Renders are CPU-heavy: cap how many run at once (CHAMP_RENDER_WORKERS, default 2);
# the rest wait in line and report "queued".
_slots = threading.BoundedSemaphore(max(1, int(os.environ.get("CHAMP_RENDER_WORKERS", "2"))))


def _noop(_p: float, _s: str) -> None:
    pass


def output_size(tl: Timeline, preset: dict) -> tuple[int, int]:
    w, h = tl.width, tl.height
    if preset.get("scale"):
        w, h = int(w * preset["scale"]), int(h * preset["scale"])
    if preset.get("height") and h > preset["height"]:
        k = preset["height"] / h
        w, h = int(w * k), preset["height"]
    return w - w % 2, h - h % 2


def render(tl: Timeline, out_path: str, *, preset: str = "h264", progress: ProgressFn = _noop,
           cancel: Callable[[], bool] = lambda: False) -> dict:
    """Render `tl` to `out_path`. Returns {duration_s, frames, width, height, seconds_taken}."""
    spec = PRESETS.get(preset)
    if not spec:
        raise ValueError(f"unknown preset '{preset}'")
    if tl.duration <= 0:
        raise ValueError("timeline is empty")
    progress(0.0, "queued")
    with _slots:
        return _render(tl, out_path, spec, progress, cancel)


def _render(tl: Timeline, out_path: str, spec: dict, progress: ProgressFn, cancel: Callable[[], bool]) -> dict:
    total = tl.duration
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="champ-render-") as tmp:
        wav = str(Path(tmp) / "mix.wav")
        progress(0.02, "mixing audio")
        audio.mixdown(tl, wav, loudness=tl.loudness)
        if spec.get("audio_only"):
            codec = ["-c:a", "libmp3lame", "-b:a", "192k"] if out_path.endswith(".mp3") else ["-c:a", "pcm_s16le"]
            media._run([media.FFMPEG, "-v", "error", "-nostdin", "-y", "-i", wav, *codec, out_path])
            progress(1.0, "done")
            return {"duration_s": total / tl.fps, "frames": 0, "width": 0, "height": 0,
                    "seconds_taken": round(time.time() - t0, 2)}

        W, H = output_size(tl, spec)
        progress(0.08, "rendering frames")
        cmd = [media.FFMPEG, "-v", "error", "-nostdin", "-y",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(tl.fps), "-i", "-",
               "-i", wav, "-map", "0:v", "-map", "1:a", *spec["v"], *spec["a"], *spec.get("extra", []),
               "-r", str(tl.fps), "-shortest", out_path]
        enc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        comp = Compositor(tl, width=W, height=H, sequential=True)
        tc_style = TextStyle(font="Inter", weight=600, size=30, color="#FFFFFF", bg_color="#000000B0",
                             align="left", valign="top", fit="none")
        try:
            for f in range(total):
                if cancel():
                    raise InterruptedError("render canceled")
                frame = comp.frame(f)
                if spec.get("timecode"):
                    tc = textmod.render_text(timecode(f, tl.fps), tc_style, int(W * 0.3), int(H * 0.08),
                                             scale=H / 1080)
                    import numpy as np  # local: only review renders need it

                    place(frame, np.asarray(tc), W * 0.03 + tc.width / 2, H * 0.03 + tc.height / 2, 1, 0, 1)
                enc.stdin.write(frame.tobytes())  # type: ignore[union-attr]
                if f % 5 == 0:
                    progress(0.08 + 0.9 * (f + 1) / total, "rendering frames")
            enc.stdin.close()  # type: ignore[union-attr]
            err = enc.stderr.read().decode(errors="replace") if enc.stderr else ""
            if enc.wait() != 0:
                raise media.MediaError(f"encoder failed: {err.strip()[-400:]}")
        except BaseException:
            enc.kill()
            raise
        finally:
            comp.close()
    progress(1.0, "done")
    return {"duration_s": total / tl.fps, "frames": total, "width": W, "height": H,
            "seconds_taken": round(time.time() - t0, 2)}


def still(tl: Timeline, f: int, *, width: int | None = None, height: int | None = None):
    """One frame as a PIL image (editor program monitor / golden tests)."""
    from PIL import Image

    comp = Compositor(tl, width=width, height=height, sequential=False)
    try:
        return Image.fromarray(comp.frame(max(0, min(f, max(0, tl.duration - 1)))))
    finally:
        comp.close()


# ── captions export ──────────────────────────────────────────────────


def caption_cues(tl: Timeline) -> list[tuple[float, float, str]]:
    cues = []
    fps = tl.fps
    for t in tl.video_tracks():
        for c in t.clips:
            if c.type != "captions" or not c.captions or not c.enabled:
                continue
            words = [w.model_dump() for w in c.captions.words]
            for idxs in textmod.caption_lines(words, c.captions.max_chars):
                s = words[idxs[0]]["s"] / fps
                e = words[idxs[-1]]["e"] / fps
                cues.append((s, e, " ".join(words[i]["w"] for i in idxs)))
    return sorted(cues)


def _ts(sec: float, sep: str) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(tl: Timeline) -> str:
    return "\n".join(f"{i}\n{_ts(s, ',')} --> {_ts(e, ',')}\n{txt}\n"
                     for i, (s, e, txt) in enumerate(caption_cues(tl), 1))


def to_vtt(tl: Timeline) -> str:
    body = "\n".join(f"{_ts(s, '.')} --> {_ts(e, '.')}\n{txt}\n" for s, e, txt in caption_cues(tl))
    return "WEBVTT\n\n" + body
