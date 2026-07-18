"""Morphological skeleton — the 1px structural spine of the image's shapes.

Outputs a PNG (white skeleton on ink) and an SVG (vtracer binary trace of the
slightly-dilated skeleton so strokes survive vectorization).
"""

from __future__ import annotations

import cv2
import numpy as np
from skimage.morphology import skeletonize

from . import vectorize
from ._util import load_gray, np_gray_to_png


def run(data: bytes, params: dict) -> list[tuple[bytes, str]]:
    gray = load_gray(data)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, binary = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Foreground = the darker shapes by default; invert flips that.
    fg = binary < 128
    if params.get("invert"):
        fg = ~fg
    # Guard: if "foreground" swallowed most of the frame, flip to the minority side.
    if fg.mean() > 0.5:
        fg = ~fg

    skel = skeletonize(fg)

    # PNG: white spine on Champ ink
    png = np.zeros_like(gray)
    png[skel] = 255
    png_out = np_gray_to_png(png)

    # SVG: dilate to 3px so the trace produces clean strokes, then binary-vectorize
    thick = cv2.dilate(skel.astype(np.uint8) * 255, np.ones((3, 3), np.uint8))
    svg_src = np_gray_to_png(255 - thick)  # vtracer binary mode traces dark-on-light
    svg_out = vectorize.run(svg_src, {"mode": "binary", "detail": "high"})[0]

    return [(png_out, "image/png"), svg_out]
