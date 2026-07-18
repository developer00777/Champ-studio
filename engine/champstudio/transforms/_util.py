"""Shared helpers for transforms."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

MAX_SIDE = 4096


def load_rgb(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img).convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    return img


def load_gray(data: bytes) -> np.ndarray:
    return np.array(load_rgb(data).convert("L"))


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def np_gray_to_png(arr: np.ndarray) -> bytes:
    return png_bytes(Image.fromarray(arr.astype(np.uint8), mode="L"))
