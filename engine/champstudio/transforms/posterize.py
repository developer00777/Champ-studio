"""Posterize — flat screen-print color reduction."""

from __future__ import annotations

from PIL import Image

from ._util import load_rgb, png_bytes


def run(data: bytes, params: dict) -> list[tuple[bytes, str]]:
    levels = int(params.get("levels", 4))
    levels = max(2, min(levels, 8))
    img = load_rgb(data)
    quantized = img.quantize(colors=levels * 4, method=Image.MEDIANCUT).convert("RGB")
    return [(png_bytes(quantized), "image/png")]
