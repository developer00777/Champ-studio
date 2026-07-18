"""Upscale — high-quality Lanczos resampling (2x/4x), capped at 8K on a side."""

from __future__ import annotations

from PIL import Image

from ._util import load_rgb, png_bytes

MAX_OUT = 8192


def run(data: bytes, params: dict) -> list[tuple[bytes, str]]:
    factor = int(params.get("factor", 2))
    factor = 4 if factor >= 4 else 2
    img = load_rgb(data)
    w, h = img.size
    scale = min(factor, MAX_OUT / max(w, h))
    if scale > 1:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return [(png_bytes(img), "image/png")]
