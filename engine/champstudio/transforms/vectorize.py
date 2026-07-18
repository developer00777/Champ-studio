"""Vectorize a raster image into SVG using vtracer."""

from __future__ import annotations

import os
import tempfile

import vtracer

from ._util import load_rgb, png_bytes

_DETAIL = {
    # filter_speckle, color_precision, layer_difference, path_precision
    "low": dict(filter_speckle=8, color_precision=4, layer_difference=32, path_precision=3),
    "medium": dict(filter_speckle=4, color_precision=6, layer_difference=16, path_precision=5),
    "high": dict(filter_speckle=2, color_precision=8, layer_difference=8, path_precision=8),
}


def run(data: bytes, params: dict) -> list[tuple[bytes, str]]:
    mode = params.get("mode", "color")
    detail = _DETAIL.get(params.get("detail", "medium"), _DETAIL["medium"])

    # vtracer's Python binding is file-path based; round-trip through temp files.
    normalized = png_bytes(load_rgb(data))
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "in.png")
        dst = os.path.join(td, "out.svg")
        with open(src, "wb") as f:
            f.write(normalized)
        vtracer.convert_image_to_svg_py(
            src,
            dst,
            colormode="color" if mode == "color" else "binary",
            hierarchical="stacked",
            mode="spline",
            corner_threshold=60,
            length_threshold=4.0,
            max_iterations=10,
            splice_threshold=45,
            **detail,
        )
        with open(dst, "rb") as f:
            svg = f.read()
    return [(svg, "image/svg+xml")]
