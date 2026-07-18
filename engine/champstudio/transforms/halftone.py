"""Halftone — classic print-style dot screen."""

from __future__ import annotations

from PIL import Image, ImageDraw

from ._util import load_rgb, png_bytes


def run(data: bytes, params: dict) -> list[tuple[bytes, str]]:
    dot = int(params.get("dot", 8))
    dot = max(4, min(dot, 24))

    gray = load_rgb(data).convert("L")
    w, h = gray.size
    out = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(out)
    px = gray.load()

    for y in range(0, h, dot):
        for x in range(0, w, dot):
            # average the cell
            total = n = 0
            for yy in range(y, min(y + dot, h), 2):
                for xx in range(x, min(x + dot, w), 2):
                    total += px[xx, yy]
                    n += 1
            darkness = 1 - (total / n) / 255 if n else 0
            r = darkness * dot * 0.65
            if r >= 0.8:
                cx, cy = x + dot / 2, y + dot / 2
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=0)

    return [(png_bytes(out.convert("RGB")), "image/png")]
