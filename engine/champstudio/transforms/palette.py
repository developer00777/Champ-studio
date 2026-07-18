"""Dominant color palette — swatch PNG + CSS custom properties + JSON."""

from __future__ import annotations

import json

import cv2
import numpy as np
from PIL import Image, ImageDraw

from ._util import load_rgb, png_bytes


def run(data: bytes, params: dict) -> list[tuple[bytes, str]]:
    k = int(params.get("colors", 6))
    k = max(2, min(k, 12))

    img = load_rgb(data)
    small = img.copy()
    small.thumbnail((256, 256), Image.LANCZOS)
    pixels = np.asarray(small).reshape(-1, 3).astype(np.float32)

    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1.0)
    _, labels, centers = cv2.kmeans(
        pixels, k, None, criteria, 5, cv2.KMEANS_PP_CENTERS
    )
    counts = np.bincount(labels.flatten(), minlength=k)
    order = np.argsort(-counts)
    colors = [
        {
            "hex": "#{:02X}{:02X}{:02X}".format(*centers[i].clip(0, 255).astype(int)),
            "rgb": [int(c) for c in centers[i].clip(0, 255)],
            "share": round(float(counts[i]) / len(labels), 4),
        }
        for i in order
    ]

    # Swatch PNG
    sw, sh = 160, 200
    strip = Image.new("RGB", (sw * k, sh), "white")
    draw = ImageDraw.Draw(strip)
    for idx, c in enumerate(colors):
        draw.rectangle([idx * sw, 0, (idx + 1) * sw, sh - 40], fill=tuple(c["rgb"]))
        draw.text((idx * sw + 12, sh - 30), c["hex"], fill="black")

    css = ":root {\n" + "\n".join(
        f"  --palette-{i + 1}: {c['hex']};" for i, c in enumerate(colors)
    ) + "\n}\n"
    payload = json.dumps({"colors": colors}, indent=2).encode()

    return [
        (png_bytes(strip), "image/png"),
        (css.encode(), "text/css"),
        (payload, "application/json"),
    ]
