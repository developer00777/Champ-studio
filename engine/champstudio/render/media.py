"""ffmpeg/ffprobe helpers: probing, frame decoding, proxies, thumbnails,
waveform peaks, audio extraction, silence and scene detection.

Every function shells out to the `ffmpeg`/`ffprobe` binaries on PATH (bundled
in the Docker image; `CHAMP_FFMPEG`/`CHAMP_FFPROBE` override).
"""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np

FFMPEG = os.environ.get("CHAMP_FFMPEG") or "ffmpeg"
FFPROBE = os.environ.get("CHAMP_FFPROBE") or "ffprobe"


class MediaError(RuntimeError):
    pass


def available() -> bool:
    return bool(shutil.which(FFMPEG) and shutil.which(FFPROBE))


def _run(args: list[str], *, timeout: float = 600, input: bytes | None = None) -> subprocess.CompletedProcess:
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout, input=input)
    except FileNotFoundError as e:
        raise MediaError(f"{args[0]} not found — install ffmpeg") from e
    except subprocess.TimeoutExpired as e:
        raise MediaError(f"{args[0]} timed out") from e
    if p.returncode != 0:
        tail = p.stderr.decode(errors="replace").strip().splitlines()[-6:]
        raise MediaError(f"{Path(args[0]).name} failed: " + " | ".join(tail))
    return p


def _rate(s: str | None) -> float:
    if not s or s in ("0/0", "N/A"):
        return 0.0
    if "/" in s:
        a, b = s.split("/")
        return float(a) / float(b) if float(b) else 0.0
    return float(s)


@functools.lru_cache(maxsize=512)
def _probe_cached(path: str, mtime: float) -> dict:
    p = _run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path], timeout=60)
    data = json.loads(p.stdout or b"{}")
    fmt = data.get("format", {})
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"
              and s.get("disposition", {}).get("attached_pic") != 1), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    duration = float(fmt.get("duration") or (v or {}).get("duration") or (a or {}).get("duration") or 0)
    info = {
        "duration": duration,
        "has_video": v is not None,
        "has_audio": a is not None,
        "format": fmt.get("format_name", ""),
    }
    if v:
        r_rate, avg_rate = _rate(v.get("r_frame_rate")), _rate(v.get("avg_frame_rate"))
        rotation = 0
        for sd in v.get("side_data_list") or []:
            if "rotation" in sd:
                rotation = int(sd["rotation"])
        rotation = int((v.get("tags") or {}).get("rotate", rotation))
        w, h = int(v.get("width", 0)), int(v.get("height", 0))
        if abs(rotation) in (90, 270):
            w, h = h, w
        info.update(
            width=w,
            height=h,
            fps=avg_rate or r_rate,
            vfr=bool(r_rate and avg_rate and abs(r_rate - avg_rate) > 0.01 * r_rate),
            vcodec=v.get("codec_name"),
            pix_fmt=v.get("pix_fmt"),
            has_alpha=(v.get("pix_fmt") or "").startswith(("yuva", "rgba", "argb", "bgra", "gbrap")),
            frames=int(v.get("nb_frames") or 0),
        )
        if v.get("codec_name") in ("png", "mjpeg", "webp", "bmp", "gif") and duration == 0:
            info["still"] = True
    if a:
        info.update(sample_rate=int(a.get("sample_rate") or 0), channels=int(a.get("channels") or 0),
                    acodec=a.get("codec_name"))
    return info


def probe(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise MediaError(f"missing media file {p.name}")
    return _probe_cached(str(p), p.stat().st_mtime)


# ── frame decoding ───────────────────────────────────────────────────


def fit_filter(fit: str, w: int, h: int) -> str:
    """ffmpeg filter that maps any source onto exactly w×h RGBA."""
    if fit == "contain":
        return (f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=bicubic,format=rgba,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x00000000")
    if fit == "fill":
        return f"scale={w}:{h}:flags=bicubic,format=rgba"
    if fit == "reframe":  # height-match; caller crops horizontally per frame
        return f"scale=-2:{h}:flags=bicubic,format=rgba"
    return (f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=bicubic,"
            f"crop={w}:{h},format=rgba")


class FrameReader:
    """Sequential RGBA frames of a video segment at a fixed output rate.

    Reads `count` frames starting at `start_s` (source seconds), playing at
    `speed`. Past the end of the source the last frame repeats (freeze).
    """

    def __init__(self, path: str, *, start_s: float, count: int, fps: float, w: int, h: int,
                 fit: str = "cover", speed: float = 1.0, reverse: bool = False) -> None:
        self.w, self.h = w, h
        self.count = count
        self.read = 0
        self.last: np.ndarray | None = None
        self.out_w = w
        if fit == "reframe":
            info = probe(path)
            sw, sh = info.get("width") or w, info.get("height") or h
            self.out_w = max(w, int(round(sw * h / max(1, sh) / 2) * 2))
        vf = [fit_filter(fit, w, h)]
        if speed != 1.0:
            vf.insert(0, f"setpts=PTS/{speed}")
        if reverse:
            vf.insert(0, "reverse")
        vf.append(f"fps={fps}")
        src_dur = count / fps * speed + 0.5
        args = [FFMPEG, "-v", "error", "-nostdin"]
        if start_s > 0:
            args += ["-ss", f"{start_s:.4f}"]
        args += ["-t", f"{src_dur:.4f}", "-i", path, "-an", "-vf", ",".join(vf),
                 "-f", "rawvideo", "-pix_fmt", "rgba", "-"]
        self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.frame_bytes = self.out_w * h * 4

    def next(self) -> np.ndarray:
        if self.proc.stdout is not None and self.read < self.count + 2:
            buf = self.proc.stdout.read(self.frame_bytes)
            if buf and len(buf) == self.frame_bytes:
                self.last = np.frombuffer(buf, np.uint8).reshape(self.h, self.out_w, 4)
        self.read += 1
        if self.last is None:
            self.last = np.zeros((self.h, self.out_w, 4), np.uint8)
        return self.last

    def close(self) -> None:
        try:
            if self.proc.stdout:
                self.proc.stdout.close()
            self.proc.kill()
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass


def grab_frame(path: str, t: float, w: int, h: int, fit: str = "cover") -> np.ndarray:
    """One accurately-seeked RGBA frame (editor scrubbing, golden tests)."""
    info = probe(path)
    out_w = w
    if fit == "reframe":
        sw, sh = info.get("width") or w, info.get("height") or h
        out_w = max(w, int(round(sw * h / max(1, sh) / 2) * 2))
    t = max(0.0, min(t, max(0.0, info["duration"] - 0.02))) if info["duration"] else 0.0
    args = [FFMPEG, "-v", "error", "-nostdin"]
    if t > 0:
        args += ["-ss", f"{t:.4f}"]
    args += ["-i", path, "-frames:v", "1", "-an", "-vf", fit_filter(fit, w, h),
             "-f", "rawvideo", "-pix_fmt", "rgba", "-"]
    p = _run(args, timeout=60)
    need = out_w * h * 4
    if len(p.stdout) < need:
        return np.zeros((h, out_w, 4), np.uint8)
    return np.frombuffer(p.stdout[:need], np.uint8).reshape(h, out_w, 4)


# ── derived media for the editor ─────────────────────────────────────


def make_proxy(src: str, dst: str, *, height: int = 540, fps: float | None = None) -> None:
    """Low-res H.264 proxy with a short GOP so browser seeking is snappy.
    Variable frame rate sources are conformed to constant frame rate."""
    info = probe(src)
    rate = fps or (round(info.get("fps") or 30, 3))
    args = [FFMPEG, "-v", "error", "-nostdin", "-y", "-i", src,
            "-vf", f"scale=-2:{height}:flags=bicubic,fps={rate}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-g", str(max(1, int(rate // 2))),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    if info.get("has_audio"):
        args += ["-c:a", "aac", "-b:a", "96k", "-ac", "2"]
    else:
        args += ["-an"]
    _run(args + [dst], timeout=3600)


def conform_cfr(src: str, dst: str, fps: float) -> None:
    """Mezzanine copy at constant frame rate (fixes VFR phone footage A/V drift)."""
    _run([FFMPEG, "-v", "error", "-nostdin", "-y", "-i", src, "-vf", f"fps={fps}",
          "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", "192k", dst], timeout=7200)


def thumbnail_strip(src: str, dst: str, *, every_s: float = 2.0, height: int = 90, max_tiles: int = 60) -> dict:
    """Horizontal sprite of evenly spaced thumbnails. Returns {tiles, tile_w, tile_h, every_s}."""
    info = probe(src)
    dur = max(info["duration"], 0.1)
    every = max(every_s, dur / max_tiles)
    tiles = max(1, min(max_tiles, int(dur / every) + 1))
    aspect = (info.get("width") or 16) / max(1, info.get("height") or 9)
    if aspect < 1:  # portrait: size by width so cards don't upscale a 50 px sliver
        height = int(round(120 / aspect / 2) * 2)
    tile_w = int(round(height * aspect / 2) * 2)
    _run([FFMPEG, "-v", "error", "-nostdin", "-y", "-i", src,
          "-vf", f"fps=1/{every:.4f},scale={tile_w}:{height},tile={tiles}x1",
          "-frames:v", "1", "-q:v", "5", dst], timeout=600)
    return {"tiles": tiles, "tile_w": tile_w, "tile_h": height, "every_s": every, "v": 2}


def extract_audio(src: str, dst: str, *, rate: int = 16000, mono: bool = True,
                  start_s: float = 0, dur_s: float | None = None, fmt: str | None = None) -> None:
    args = [FFMPEG, "-v", "error", "-nostdin", "-y"]
    if start_s:
        args += ["-ss", f"{start_s:.3f}"]
    if dur_s:
        args += ["-t", f"{dur_s:.3f}"]
    args += ["-i", src, "-vn", "-ar", str(rate)]
    if mono:
        args += ["-ac", "1"]
    if fmt == "mp3" or dst.endswith(".mp3"):
        args += ["-c:a", "libmp3lame", "-b:a", "64k"]
    _run(args + [dst], timeout=1800)


def pcm(src: str, *, rate: int = 8000, start_s: float = 0, dur_s: float | None = None) -> np.ndarray:
    """Mono float32 samples in -1..1."""
    args = [FFMPEG, "-v", "error", "-nostdin"]
    if start_s:
        args += ["-ss", f"{start_s:.3f}"]
    if dur_s:
        args += ["-t", f"{dur_s:.3f}"]
    args += ["-i", src, "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
    p = _run(args, timeout=1800)
    return np.frombuffer(p.stdout, np.int16).astype(np.float32) / 32768.0


def waveform_peaks(src: str, *, per_second: int = 50) -> dict:
    """Peak envelope for timeline waveforms: {per_second, peaks[0..1]}."""
    rate = 8000
    samples = pcm(src, rate=rate)
    if samples.size == 0:
        return {"per_second": per_second, "peaks": []}
    hop = max(1, rate // per_second)
    n = samples.size // hop
    peaks = np.abs(samples[: n * hop]).reshape(n, hop).max(axis=1) if n else np.array([])
    return {"per_second": per_second, "peaks": [round(float(p), 3) for p in peaks]}


def silences(src: str, *, noise_db: float = -35, min_s: float = 0.4) -> list[tuple[float, float]]:
    """(start, end) seconds of silence via ffmpeg silencedetect."""
    p = subprocess.run([FFMPEG, "-v", "info", "-nostdin", "-i", src, "-vn",
                        "-af", f"silencedetect=noise={noise_db}dB:d={min_s}", "-f", "null", "-"],
                       capture_output=True, timeout=1800)
    log = p.stderr.decode(errors="replace")
    starts = [float(m) for m in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(m) for m in re.findall(r"silence_end: (-?[\d.]+)", log)]
    out = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else probe(src)["duration"]
        out.append((max(0.0, s), e))
    return out


def scene_cuts(src: str, *, threshold: float = 0.3) -> list[float]:
    """Timestamps (s) where the picture changes shot (Scene Edit Detection)."""
    p = subprocess.run([FFMPEG, "-v", "info", "-nostdin", "-i", src, "-an",
                        "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
                       capture_output=True, timeout=3600)
    return [float(t) for t in re.findall(r"pts_time:([\d.]+)", p.stderr.decode(errors="replace"))]


def loudness(src: str) -> dict:
    """Integrated loudness (LUFS) and true peak of a file."""
    p = subprocess.run([FFMPEG, "-v", "info", "-nostdin", "-i", src, "-vn",
                        "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                       capture_output=True, timeout=1800)
    log = p.stderr.decode(errors="replace")
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", log, re.S)
    if not m:
        return {}
    data = json.loads(m.group(0))
    return {"lufs": float(data.get("input_i", 0)), "true_peak": float(data.get("input_tp", 0))}
