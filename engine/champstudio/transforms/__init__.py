"""Derivative Lab — local transforms that turn any image into designer assets.

Each tool takes (png_or_any_image_bytes, params) and returns a list of
(bytes, mime) outputs. Registered in TOOLS for dynamic dispatch + discovery.
"""

from __future__ import annotations

from collections.abc import Callable

from . import background, halftone, lineart, palette, posterize, skeleton, upscale, vectorize

TOOLS: dict[str, dict] = {
    "vectorize": {
        "run": vectorize.run,
        "label": "Vectorize (SVG)",
        "description": "Trace the image into a true scalable vector (vtracer).",
        "params": {
            "mode": {"type": "select", "options": ["color", "binary"], "default": "color"},
            "detail": {"type": "select", "options": ["low", "medium", "high"], "default": "medium"},
        },
    },
    "skeleton": {
        "run": skeleton.run,
        "label": "Skeleton",
        "description": "Morphological skeleton — the 1px structural spine of the shapes, as PNG + SVG.",
        "params": {
            "invert": {"type": "bool", "default": False},
        },
    },
    "lineart": {
        "run": lineart.run,
        "label": "Line Art",
        "description": "Clean edge drawing (pencil-sketch style) from any image.",
        "params": {
            "detail": {"type": "select", "options": ["low", "medium", "high"], "default": "medium"},
            "invert": {"type": "bool", "default": False},
        },
    },
    "remove-bg": {
        "run": background.run,
        "label": "Remove Background",
        "description": "AI cutout with transparent background (rembg/U²-Net, fully local).",
        "params": {},
    },
    "palette": {
        "run": palette.run,
        "label": "Palette",
        "description": "Extract the dominant color palette as swatch PNG + CSS variables + JSON.",
        "params": {
            "colors": {"type": "int", "min": 2, "max": 12, "default": 6},
        },
    },
    "halftone": {
        "run": halftone.run,
        "label": "Halftone",
        "description": "Classic print-style halftone dots.",
        "params": {
            "dot": {"type": "int", "min": 4, "max": 24, "default": 8},
        },
    },
    "posterize": {
        "run": posterize.run,
        "label": "Posterize",
        "description": "Flat poster color reduction — screen-print look.",
        "params": {
            "levels": {"type": "int", "min": 2, "max": 8, "default": 4},
        },
    },
    "upscale": {
        "run": upscale.run,
        "label": "Upscale",
        "description": "Resize up with high-quality resampling (2x/4x).",
        "params": {
            "factor": {"type": "select", "options": ["2", "4"], "default": "2"},
        },
    },
}


def catalog() -> list[dict]:
    return [
        {"id": name, "label": t["label"], "description": t["description"], "params": t["params"]}
        for name, t in TOOLS.items()
    ]


def get_tool(name: str) -> Callable | None:
    entry = TOOLS.get(name)
    return entry["run"] if entry else None
