"""Frame compositor — evaluates a Timeline at any frame.

One evaluator serves the export render (sequential frames, streaming
decoders), editor preview frames and golden-frame tests (random access), so
what the editor shows is what the export writes.

Per frame, for each visible video track bottom→top: find the active clip (and
the outgoing clip during a transition), produce its RGBA content (video frame,
image, text, shape, colour, captions), apply colour adjustments, animations,
keyframed transform, then alpha-composite onto the canvas.
"""

from __future__ import annotations

import functools
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .. import library
from ..config import data_dir
from ..timeline import Clip, Timeline, Track, prop
from . import media, text as textmod

BUNDLED_ASSETS = Path(__file__).resolve().parents[1] / "assets"


class RenderError(RuntimeError):
    pass


# ── source resolution ────────────────────────────────────────────────


def resolve_source(clip: Clip) -> str | None:
    if clip.asset_id:
        p = library.asset_path(clip.asset_id)
        if not p:
            raise RenderError(f"clip '{clip.name or clip.id}': asset {clip.asset_id} is missing")
        return str(p)
    if clip.src:
        p = Path(clip.src).resolve()
        allowed = [data_dir().resolve(), BUNDLED_ASSETS.resolve()]
        if not any(str(p).startswith(str(a)) for a in allowed):
            raise RenderError(f"clip '{clip.id}': source outside the data directory")
        if not p.exists():
            raise RenderError(f"clip '{clip.id}': source file missing")
        return str(p)
    return None


# ── pixel helpers ────────────────────────────────────────────────────


def hex_rgba(c: str | None, default=(0, 0, 0, 255)) -> tuple[int, int, int, int]:
    return textmod.parse_color(c, default)


def over(dst: np.ndarray, src: np.ndarray, x: int, y: int, opacity: float = 1.0) -> None:
    """Alpha-composite RGBA `src` onto `dst` (RGB or RGBA) at integer (x, y), in place."""
    H, W = dst.shape[:2]
    h, w = src.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x0 >= x1 or y0 >= y1 or opacity <= 0:
        return
    s = src[y0 - y : y1 - y, x0 - x : x1 - x]
    d = dst[y0:y1, x0:x1]
    if dst.shape[2] == 3:  # RGB destination (rare): blend via a temporary RGBA view
        tmp = np.dstack([d, np.full(d.shape[:2], 255, np.uint8)])
        over(tmp, s, 0, 0, opacity)
        d[:] = tmp[..., :3]
        return
    a = cv2.extractChannel(s, 3)
    if opacity >= 0.999 and cv2.minMaxLoc(a)[0] >= 255:  # opaque: plain copy
        d[:] = s
        return
    if opacity < 0.999:
        a = cv2.multiply(a, np.full_like(a, 255), scale=opacity / 255.0)
    inv = cv2.bitwise_not(a)
    a4 = cv2.merge([a, a, a, a])
    blended = cv2.add(cv2.multiply(s, a4, scale=1 / 255.0), cv2.multiply(d, cv2.merge([inv, inv, inv, inv]),
                                                                          scale=1 / 255.0))
    # exact alpha: a + da * (1 - a)
    cv2.insertChannel(cv2.add(a, cv2.multiply(cv2.extractChannel(d, 3), inv, scale=1 / 255.0)), blended, 3)
    d[:] = blended


def opaque_bbox(layer: np.ndarray) -> tuple[int, int, int, int] | None:
    """(x0, y0, x1, y1) of non-transparent pixels, or None if fully transparent."""
    nz = cv2.findNonZero((layer[..., 3] > 0).astype(np.uint8))
    if nz is None:
        return None
    x, y, w, h = cv2.boundingRect(nz)
    return x, y, x + w, y + h


def place(dst: np.ndarray, layer: np.ndarray, cx: float, cy: float, scale: float, rotation: float,
          opacity: float) -> None:
    """Composite `layer` centred at (cx, cy) with scale/rotation (degrees, clockwise)."""
    h, w = layer.shape[:2]
    if opacity <= 0.001 or scale <= 0.001:
        return
    if abs(scale - 1) < 1e-4 and abs(rotation) < 1e-3:
        over(dst, layer, int(round(cx - w / 2)), int(round(cy - h / 2)), opacity)
        return
    rad = math.radians(rotation)
    cos, sin = math.cos(rad) * scale, math.sin(rad) * scale
    # forward map: p' = R*S*(p - c_layer) + c_canvas
    corners = np.array([[0, 0], [w, 0], [0, h], [w, h]], np.float32) - [w / 2, h / 2]
    xs = corners[:, 0] * cos - corners[:, 1] * sin + cx
    ys = corners[:, 0] * sin + corners[:, 1] * cos + cy
    H, W = dst.shape[:2]
    bx0, by0 = max(0, int(math.floor(xs.min()))), max(0, int(math.floor(ys.min())))
    bx1, by1 = min(W, int(math.ceil(xs.max()))), min(H, int(math.ceil(ys.max())))
    if bx0 >= bx1 or by0 >= by1:
        return
    m = np.array([[cos, -sin, cx - bx0 - (w / 2) * cos + (h / 2) * sin],
                  [sin, cos, cy - by0 - (w / 2) * sin - (h / 2) * cos]], np.float32)
    warped = cv2.warpAffine(layer, m, (bx1 - bx0, by1 - by0), flags=cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    over(dst, warped, bx0, by0, opacity)


@functools.lru_cache(maxsize=8)
def _load_lut(path: str) -> np.ndarray:
    """.cube 3D LUT → 64³ lookup table (uint8 RGB) indexed by value >> 2."""
    size, rows = 0, []
    for line in Path(path).read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.upper().startswith("LUT_3D_SIZE"):
            size = int(line.split()[-1])
            continue
        parts = line.split()
        if len(parts) == 3:
            try:
                rows.append([float(v) for v in parts])
            except ValueError:
                continue
    if not size or len(rows) < size**3:
        raise RenderError("invalid .cube LUT")
    cube = np.array(rows[: size**3], np.float32).reshape(size, size, size, 3)  # [b][g][r]
    grid = np.linspace(0, size - 1, 64, dtype=np.float32)
    i0 = np.floor(grid).astype(int)
    i1 = np.minimum(i0 + 1, size - 1)
    f = grid - i0
    r0, g0, b0 = np.meshgrid(i0, i0, i0, indexing="ij")
    r1, g1, b1 = np.meshgrid(i1, i1, i1, indexing="ij")
    fr, fg, fb = np.meshgrid(f, f, f, indexing="ij")
    fr, fg, fb = fr[..., None], fg[..., None], fb[..., None]

    def c(b, g, r):
        return cube[b, g, r]

    out = (
        c(b0, g0, r0) * (1 - fr) * (1 - fg) * (1 - fb)
        + c(b0, g0, r1) * fr * (1 - fg) * (1 - fb)
        + c(b0, g1, r0) * (1 - fr) * fg * (1 - fb)
        + c(b0, g1, r1) * fr * fg * (1 - fb)
        + c(b1, g0, r0) * (1 - fr) * (1 - fg) * fb
        + c(b1, g0, r1) * fr * (1 - fg) * fb
        + c(b1, g1, r0) * (1 - fr) * fg * fb
        + c(b1, g1, r1) * fr * fg * fb
    )  # indexed [r][g][b]
    return np.clip(out * 255 + 0.5, 0, 255).astype(np.uint8)


def apply_adjust(rgba: np.ndarray, adj, f_rel: float, clip: Clip) -> np.ndarray:
    if adj is None:
        return rgba
    exposure = prop(clip, "exposure", f_rel, adj.exposure)
    contrast = prop(clip, "contrast", f_rel, adj.contrast)
    saturation = prop(clip, "saturation", f_rel, adj.saturation)
    brightness = prop(clip, "brightness", f_rel, adj.brightness)
    temperature, tint = adj.temperature, adj.tint
    out = rgba
    neutral = (exposure == 0 and contrast == 0 and saturation == 1 and brightness == 0
               and temperature == 0 and tint == 0)
    if not neutral:
        rgb = rgba[..., :3].astype(np.float32) / 255.0
        if exposure:
            rgb *= 2.0 ** exposure
        if brightness:
            rgb += brightness * 0.3
        if contrast:
            rgb = (rgb - 0.5) * (1 + contrast) + 0.5
        if saturation != 1:
            luma = rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32)
            rgb = luma[..., None] + (rgb - luma[..., None]) * saturation
        if temperature:
            rgb[..., 0] += temperature * 0.08
            rgb[..., 2] -= temperature * 0.08
        if tint:
            rgb[..., 1] -= tint * 0.08
        out = rgba.copy()
        out[..., :3] = np.clip(rgb * 255 + 0.5, 0, 255).astype(np.uint8)
    if adj.lut_asset_id:
        p = library.asset_path(adj.lut_asset_id)
        if p:
            table = _load_lut(str(p))
            q = out[..., :3] >> 2
            graded = table[q[..., 0], q[..., 1], q[..., 2]]
            k = max(0.0, min(1.0, adj.lut_strength))
            if out is rgba:
                out = rgba.copy()
            out[..., :3] = (graded.astype(np.float32) * k + out[..., :3].astype(np.float32) * (1 - k)).astype(np.uint8)
    return out


# ── compositor ───────────────────────────────────────────────────────


class Compositor:
    """Evaluate `timeline` at output size (defaults to the timeline's own size)."""

    def __init__(self, timeline: Timeline, *, width: int | None = None, height: int | None = None,
                 sequential: bool = False) -> None:
        self.tl = timeline
        self.W = int(width or timeline.width)
        self.H = int(height or timeline.height)
        self.W -= self.W % 2
        self.H -= self.H % 2
        self.text_scale = self.H / 1080.0
        self.sequential = sequential
        self.fps = timeline.fps
        self._readers: dict[str, media.FrameReader] = {}
        self._images: dict[tuple, np.ndarray] = {}
        self._text: dict[tuple, np.ndarray] = {}
        self._sources: dict[str, str | None] = {}
        self._nested: dict[str, Compositor] = {}
        self._caption_lines: dict[str, list[list[int]]] = {}
        self._bbox: dict[int, tuple] = {}
        self._bg_frame: np.ndarray | None = None
        self.bg = hex_rgba(timeline.background)

    # ── public ──

    def frame(self, f: int) -> np.ndarray:
        """RGB uint8 (H, W, 3) frame."""
        if self._bg_frame is None:
            self._bg_frame = np.empty((self.H, self.W, 4), np.uint8)
            self._bg_frame[:] = (*self.bg[:3], 255)
        canvas = self._bg_frame.copy()
        self.draw(canvas, f)
        if self.sequential:
            self._gc_readers(f)
        return cv2.cvtColor(canvas, cv2.COLOR_RGBA2RGB)

    def frame_rgba(self, f: int) -> np.ndarray:
        canvas = np.zeros((self.H, self.W, 4), np.uint8)
        self.draw(canvas, f)
        return canvas

    def draw(self, canvas: np.ndarray, f: int) -> None:
        tracks = [t for t in self.tl.video_tracks() if not t.hidden]
        for track in tracks:
            self._draw_track(canvas, track, f)

    def close(self) -> None:
        for r in self._readers.values():
            r.close()
        self._readers.clear()
        for n in self._nested.values():
            n.close()

    # ── tracks / transitions ──

    def _draw_track(self, canvas: np.ndarray, track: Track, f: int) -> None:
        clips = sorted((c for c in track.clips if c.enabled), key=lambda c: c.start)
        active = [c for c in clips if c.start <= f < c.end]
        if not active:
            return
        clip = active[-1]
        tr = clip.transition_in
        if tr and tr.duration > 0 and f < clip.start + tr.duration:
            prev = next((c for c in reversed(clips) if c is not clip and c.end <= clip.start + 1
                         and c.end >= clip.start - 1), None)
            if prev is None and len(active) > 1:
                prev = active[-2]
            t = (f - clip.start + 0.5) / tr.duration
            self._draw_transition(canvas, prev, clip, f, tr.type, max(0.0, min(1.0, t)))
            return
        for c in active:  # overlapping clips on one track: later start draws on top
            if c.type == "adjustment":
                self._apply_adjustment_layer(canvas, c, f)
            else:
                self._draw_clip(canvas, c, f)

    def _draw_transition(self, canvas, a: Clip | None, b: Clip, f: int, kind: str, t: float) -> None:
        A = np.zeros((self.H, self.W, 4), np.uint8)
        B = np.zeros((self.H, self.W, 4), np.uint8)
        if a is not None:
            self._draw_clip(A, a, min(f, a.end - 1))
        if kind.startswith("slide"):
            dx = {"slide-left": -1, "slide-right": 1}.get(kind, 0)
            dy = -1 if kind == "slide-up" else 0
            e = t * t * (3 - 2 * t)
            self._draw_clip(B, b, f)
            out = np.zeros_like(A)
            over(out, A, int(round(dx * e * self.W)), int(round(dy * e * self.H)))
            over(out, B, int(round(-dx * (1 - e) * self.W)), int(round(-dy * (1 - e) * self.H)))
            over(canvas, out, 0, 0)
            return
        self._draw_clip(B, b, f)
        if kind in ("dip-black", "dip-white"):
            col = np.array([0, 0, 0] if kind == "dip-black" else [255, 255, 255], np.float32)
            src, k = (A, 1 - 2 * t) if t < 0.5 else (B, 2 * t - 1)
            mix = np.empty_like(A)
            mix[..., :3] = (src[..., :3].astype(np.float32) * k + col * (1 - k)).astype(np.uint8)
            mix[..., 3] = 255
            over(canvas, mix, 0, 0)
            return
        if kind in ("wipe-left", "wipe-right", "wipe"):
            cut = int(round(t * self.W))
            out = A.copy()
            if kind == "wipe-right":
                out[:, self.W - cut :] = B[:, self.W - cut :]
            else:
                out[:, :cut] = B[:, :cut]
            over(canvas, out, 0, 0)
            return
        if kind == "zoom":
            over(canvas, A, 0, 0, 1 - t)
            s = 1.25 - 0.25 * t
            place(canvas, B, self.W / 2, self.H / 2, s, 0, t)
            return
        # crossfade (default): blend premultiplied
        fa = A.astype(np.float32)
        fb = B.astype(np.float32)
        out = np.empty_like(A)
        alpha = fa[..., 3:4] * (1 - t) + fb[..., 3:4] * t
        pa, pb = fa[..., :3] * fa[..., 3:4], fb[..., :3] * fb[..., 3:4]
        rgb = (pa * (1 - t) + pb * t) / np.maximum(alpha, 1e-3)
        out[..., :3] = np.clip(rgb + 0.5, 0, 255).astype(np.uint8)
        out[..., 3:4] = np.clip(alpha + 0.5, 0, 255).astype(np.uint8)
        over(canvas, out, 0, 0)

    # ── clips ──

    def _box_px(self, clip: Clip) -> tuple[int, int, int, int]:
        x, y, w, h = (clip.box + [0, 0, 1, 1])[:4]
        bw = max(2, int(round(w * self.W)))
        bh = max(2, int(round(h * self.H)))
        return int(round(x * self.W)), int(round(y * self.H)), bw, bh

    def _draw_clip(self, canvas: np.ndarray, clip: Clip, f: int) -> None:
        f_rel = f - clip.start
        bx, by, bw, bh = self._box_px(clip)
        opacity = prop(clip, "opacity", f_rel, clip.transform.opacity)
        scale = prop(clip, "scale", f_rel, clip.transform.scale)
        rotation = prop(clip, "rotation", f_rel, clip.transform.rotation)
        ox = prop(clip, "x", f_rel, clip.transform.x)
        oy = prop(clip, "y", f_rel, clip.transform.y)
        visible_chars = None
        wipe = 1.0

        for anim, phase in ((clip.anim_in, "in"), (clip.anim_out, "out")):
            if not anim or anim.duration <= 0:
                continue
            if phase == "in":
                if f_rel >= anim.duration:
                    continue
                p = (f_rel + 1) / anim.duration
            else:
                start = clip.duration - anim.duration
                if f_rel < start:
                    continue
                p = 1 - (f_rel - start + 1) / anim.duration
            p = max(0.0, min(1.0, p))
            e = 1 - (1 - p) ** 3  # ease-out
            kind = anim.type
            if kind == "fade":
                opacity *= p
            elif kind in ("slide-up", "slide-down", "slide-left", "slide-right"):
                d = 0.06 * (1 - e)
                if kind == "slide-up":
                    oy += d
                elif kind == "slide-down":
                    oy -= d
                elif kind == "slide-left":
                    ox += d
                else:
                    ox -= d
                opacity *= min(1.0, p * 1.6)
            elif kind == "pop":
                over_shoot = 1 + 0.12 * math.sin(math.pi * e)
                scale *= (0.6 + 0.4 * e) * over_shoot if p < 1 else 1
                opacity *= min(1.0, p * 2)
            elif kind == "zoom-in":
                scale *= 0.85 + 0.15 * e
                opacity *= p
            elif kind == "zoom-out":
                scale *= 1.15 - 0.15 * e
                opacity *= p
            elif kind == "wipe":
                wipe = min(wipe, e)
            elif kind == "typewriter" and clip.type in ("text",):
                n = len(clip.text)
                visible_chars = int(math.ceil(n * p)) if phase == "in" else visible_chars
                if phase == "out":
                    opacity *= p

        if opacity <= 0.001:
            return
        layer = self._content(clip, f, f_rel, bw, bh, visible_chars)
        if layer is None:
            return
        if clip.adjust is not None:
            layer = apply_adjust(layer, clip.adjust, f_rel, clip)
        if wipe < 1.0:
            layer = layer.copy()
            cut = int(round(layer.shape[1] * wipe))
            layer[:, cut:, 3] = 0
        cx = bx + bw / 2 + ox * self.W
        cy = by + bh / 2 + oy * self.H
        if clip.type in ("text", "captions", "shape") and wipe >= 1.0:
            # most of a text/shape box is transparent: composite only the inked part
            key = id(layer)
            bbox = self._bbox.get(key)
            if bbox is None or bbox[0] is not layer:
                bbox = (layer, opaque_bbox(layer))
                if len(self._bbox) > 512:
                    self._bbox.clear()
                self._bbox[key] = bbox
            box = bbox[1]
            if box is None:
                return
            x0, y0, x1, y1 = box
            h0, w0 = layer.shape[:2]
            if (x1 - x0) * (y1 - y0) < w0 * h0 * 0.9:
                dx, dy = (x0 + x1) / 2 - w0 / 2, (y0 + y1) / 2 - h0 / 2
                rad = math.radians(rotation)
                cx += (dx * math.cos(rad) - dy * math.sin(rad)) * scale
                cy += (dx * math.sin(rad) + dy * math.cos(rad)) * scale
                layer = layer[y0:y1, x0:x1]
        place(canvas, layer, cx, cy, scale, rotation, max(0.0, min(1.0, opacity)))

    def _apply_adjustment_layer(self, canvas: np.ndarray, clip: Clip, f: int) -> None:
        if clip.adjust is None:
            return
        bx, by, bw, bh = self._box_px(clip)
        x1, y1 = min(self.W, bx + bw), min(self.H, by + bh)
        region = canvas[by:y1, bx:x1]
        rgba = np.concatenate([region[..., :3], np.full(region.shape[:2] + (1,), 255, np.uint8)], axis=2)
        adjusted = apply_adjust(rgba, clip.adjust, f - clip.start, clip)
        k = max(0.0, min(1.0, prop(clip, "opacity", f - clip.start, clip.transform.opacity)))
        region[..., :3] = (adjusted[..., :3].astype(np.float32) * k + region[..., :3].astype(np.float32) * (1 - k)).astype(np.uint8)

    def _content(self, clip: Clip, f: int, f_rel: int, bw: int, bh: int, visible_chars) -> np.ndarray | None:
        t = clip.type
        if t == "color":
            r, g, b, a = hex_rgba(clip.color)
            key = ("color", clip.color, bw, bh)
            if key not in self._images:
                arr = np.empty((bh, bw, 4), np.uint8)
                arr[:] = (r, g, b, a)
                self._images[key] = arr
            return self._images[key]
        if t == "shape":
            return self._shape(clip, bw, bh)
        if t == "text":
            key = ("text", clip.text, clip.style.model_dump_json(), bw, bh, visible_chars)
            if key not in self._text:
                if len(self._text) > 256:
                    self._text.clear()
                img = textmod.render_text(clip.text, clip.style, bw, bh, scale=self.text_scale,
                                          visible_chars=visible_chars)
                self._text[key] = np.asarray(img)
            return self._text[key]
        if t == "captions":
            return self._captions(clip, f, bw, bh)
        if t == "image":
            return self._image(clip, bw, bh, f_rel)
        if t == "video":
            if clip.nest:
                return self._nested_frame(clip, f_rel, bw, bh)
            return self._video(clip, f, f_rel, bw, bh)
        return None

    def _shape(self, clip: Clip, bw: int, bh: int) -> np.ndarray:
        key = ("shape", clip.shape.model_dump_json(), bw, bh)
        if key in self._images:
            return self._images[key]
        sh = clip.shape
        img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        if sh.gradient and len(sh.gradient) >= 2:
            top, bot = hex_rgba(sh.gradient[0]), hex_rgba(sh.gradient[1])
            ramp = np.linspace(0, 1, bh, dtype=np.float32)[:, None]
            grad = np.empty((bh, bw, 4), np.uint8)
            for i in range(4):
                grad[..., i] = (top[i] * (1 - ramp) + bot[i] * ramp).astype(np.uint8)
            fill_img = Image.fromarray(grad, "RGBA")
            mask = Image.new("L", (bw, bh), 0)
            md = ImageDraw.Draw(mask)
            r = sh.radius * min(bw, bh)
            if sh.kind == "ellipse":
                md.ellipse((0, 0, bw - 1, bh - 1), fill=255)
            else:
                md.rounded_rectangle((0, 0, bw - 1, bh - 1), radius=r, fill=255)
            img.paste(fill_img, (0, 0), mask)
        else:
            d = ImageDraw.Draw(img)
            fill = hex_rgba(sh.fill) if sh.fill else None
            stroke = hex_rgba(sh.stroke) if sh.stroke else None
            sw = int(round(sh.stroke_width * self.text_scale))
            if sh.kind == "ellipse":
                d.ellipse((0, 0, bw - 1, bh - 1), fill=fill, outline=stroke, width=sw)
            elif sh.kind == "line":
                d.line((0, bh // 2, bw, bh // 2), fill=stroke or fill, width=max(1, sw or bh))
            else:
                d.rounded_rectangle((0, 0, bw - 1, bh - 1), radius=sh.radius * min(bw, bh),
                                    fill=fill, outline=stroke, width=sw)
        arr = np.asarray(img)
        self._images[key] = arr
        return arr

    def _source(self, clip: Clip) -> str | None:
        k = clip.asset_id or clip.src or ""
        if k not in self._sources:
            self._sources[k] = resolve_source(clip)
        return self._sources[k]

    def _image(self, clip: Clip, bw: int, bh: int, f_rel: int) -> np.ndarray | None:
        path = self._source(clip)
        if not path:
            return None
        crop_x = clip.keyframes.get("crop_x")
        key = ("image", path, clip.fit, bw, bh)
        if key not in self._images:
            if path.lower().endswith(".svg"):
                raise RenderError("SVG images must be rasterized before use on the timeline")
            img = Image.open(path).convert("RGBA")
            self._images[key] = _fit_image(img, clip.fit, bw, bh)
        arr = self._images[key]
        if clip.fit == "reframe":
            return _crop_reframe(arr, bw, prop(clip, "crop_x", f_rel, 0.5) if crop_x else 0.5)
        return arr

    def _video(self, clip: Clip, f: int, f_rel: int, bw: int, bh: int) -> np.ndarray | None:
        path = self._source(clip)
        if not path:
            return None
        info = media.probe(path)
        if info.get("still") or not info.get("has_video"):
            return self._image(clip, bw, bh, f_rel)
        src_start = clip.in_ / self.fps
        if self.sequential and not clip.freeze:
            reader = self._readers.get(clip.id)
            if reader is None or reader.w != bw or reader.h != bh:
                if reader:
                    reader.close()
                start = src_start + (f_rel * clip.speed / self.fps)
                if clip.reverse:
                    start = src_start
                reader = media.FrameReader(path, start_s=start, count=clip.end - f, fps=self.fps,
                                           w=bw, h=bh, fit=clip.fit, speed=clip.speed, reverse=clip.reverse)
                reader.started_at = f  # type: ignore[attr-defined]
                self._readers[clip.id] = reader
            frame = reader.next()
        else:
            t = src_start if clip.freeze else src_start + f_rel * clip.speed / self.fps
            frame = media.grab_frame(path, t, bw, bh, clip.fit)
        if clip.fit == "reframe":
            return _crop_reframe(frame, bw, prop(clip, "crop_x", f_rel, 0.5))
        return frame

    def _nested_frame(self, clip: Clip, f_rel: int, bw: int, bh: int) -> np.ndarray:
        comp = self._nested.get(clip.id)
        if comp is None:
            comp = Compositor(Timeline(**clip.nest), width=bw, height=bh, sequential=False)
            self._nested[clip.id] = comp
        return comp.frame_rgba(clip.in_ + int(f_rel * clip.speed))

    def _captions(self, clip: Clip, f: int, bw: int, bh: int) -> np.ndarray | None:
        cap = clip.captions
        if not cap or not cap.words:
            return None
        words = [w.model_dump() for w in cap.words]
        if clip.id not in self._caption_lines:
            self._caption_lines[clip.id] = textmod.caption_lines(words, cap.max_chars)
        lines = self._caption_lines[clip.id]
        current = None
        for li, idxs in enumerate(lines):
            s = words[idxs[0]]["s"]
            e = words[idxs[-1]]["e"]
            nxt = words[lines[li + 1][0]]["s"] if li + 1 < len(lines) else e + self.fps // 2
            if s <= f < max(e, min(nxt, e + self.fps)):
                current = li
                break
        if current is None:
            return None
        idxs = lines[current]
        hl = None
        for j, wi in enumerate(idxs):
            if words[wi]["s"] <= f:
                hl = j
        if cap.mode == "word":
            text = words[idxs[hl or 0]]["w"]
            hl_idx = None
        else:
            text = " ".join(words[i]["w"] for i in idxs)
            hl_idx = hl if cap.mode == "karaoke" else None
        key = ("cap", clip.id, current, hl_idx, text, bw, bh)
        if key not in self._text:
            if len(self._text) > 256:
                self._text.clear()
            img = textmod.render_text(text, clip.style, bw, bh, scale=self.text_scale, highlight_word=hl_idx)
            self._text[key] = np.asarray(img)
        return self._text[key]

    def _gc_readers(self, f: int) -> None:
        for cid in list(self._readers):
            c = self.tl.clip(cid)
            if c is None or f >= c.end + 2 * self.fps:
                self._readers.pop(cid).close()


def _fit_image(img: Image.Image, fit: str, bw: int, bh: int) -> np.ndarray:
    iw, ih = img.size
    if fit == "fill":
        return np.asarray(img.resize((bw, bh), Image.LANCZOS))
    if fit == "reframe":
        nw = max(bw, int(round(iw * bh / ih)))
        return np.asarray(img.resize((nw, bh), Image.LANCZOS))
    if fit == "contain":
        s = min(bw / iw, bh / ih)
    else:
        s = max(bw / iw, bh / ih)
    nw, nh = max(1, int(round(iw * s))), max(1, int(round(ih * s)))
    resized = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    canvas.paste(resized, ((bw - nw) // 2, (bh - nh) // 2))
    return np.asarray(canvas)


def _crop_reframe(frame: np.ndarray, bw: int, center_x: float) -> np.ndarray:
    fw = frame.shape[1]
    if fw <= bw:
        return frame
    cx = center_x * fw
    x0 = int(round(min(max(0, cx - bw / 2), fw - bw)))
    return frame[:, x0 : x0 + bw]
