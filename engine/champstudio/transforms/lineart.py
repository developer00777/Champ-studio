"""Line art — clean edge drawing from any image."""

from __future__ import annotations

import cv2
import numpy as np

from ._util import load_gray, np_gray_to_png

_THRESHOLDS = {"low": (100, 220), "medium": (60, 160), "high": (30, 100)}


def run(data: bytes, params: dict) -> list[tuple[bytes, str]]:
    lo, hi = _THRESHOLDS.get(params.get("detail", "medium"), _THRESHOLDS["medium"])
    gray = load_gray(data)
    blur = cv2.bilateralFilter(gray, 7, 50, 50)
    edges = cv2.Canny(blur, lo, hi)
    edges = cv2.dilate(edges, np.ones((2, 2), np.uint8))
    # Default: dark lines on paper (drawing style); invert for white-on-ink.
    out = edges if params.get("invert") else 255 - edges
    return [(np_gray_to_png(out), "image/png")]
