"""OpenRouter's public video-model catalog (no key needed), cached for a day.

Gives each model's supported durations, resolutions, aspect ratios, frame
inputs and per-second prices so Director/templates request clips a model can
actually make, and cost estimates track live prices.
"""

from __future__ import annotations

import json
import logging
import os
import time

import httpx

from . import costs
from .config import data_dir

log = logging.getLogger("champstudio.catalog")
URL = "https://openrouter.ai/api/v1/videos/models"
TTL_S = 24 * 3600

# Used when the catalog can't be fetched (offline laptop, tests).
FALLBACK: dict[str, dict] = {
    "google/veo-3.1-lite": {"durations": [4, 6], "resolutions": ["720p", "1080p"],
                            "aspects": ["16:9", "9:16"], "frames": ["first_frame", "last_frame"]},
    "google/veo-3.1-fast": {"durations": [4, 6, 8], "resolutions": ["720p", "1080p", "4K"],
                            "aspects": ["16:9", "9:16"], "frames": ["first_frame", "last_frame"]},
    "x-ai/grok-imagine-video-1.5-lite": {"durations": list(range(1, 16)), "resolutions": ["480p", "720p", "1080p"],
                                         "aspects": ["16:9", "9:16", "1:1"], "frames": ["first_frame"]},
    "kwaivgi/kling-v3.0-std": {"durations": list(range(3, 16)), "resolutions": ["720p"],
                               "aspects": ["16:9", "9:16", "1:1"], "frames": ["first_frame", "last_frame"]},
    "alibaba/wan-3.0": {"durations": list(range(2, 31)), "resolutions": ["480p", "720p", "1080p"],
                        "aspects": ["16:9", "4:3", "1:1", "3:4", "9:16"], "frames": ["first_frame", "last_frame"]},
}

_mem: dict | None = None


def _cache_path():
    return data_dir() / "video_catalog.json"


def load(force: bool = False) -> dict[str, dict]:
    global _mem
    if _mem is not None and not force and time.time() - _mem["ts"] < TTL_S:
        return _mem["models"]
    if os.environ.get("CHAMP_OFFLINE"):
        _mem = {"ts": time.time(), "models": FALLBACK}
        return FALLBACK
    p = _cache_path()
    if p.exists() and not force:
        try:
            cached = json.loads(p.read_text())
            if time.time() - cached["ts"] < TTL_S:
                _mem = cached
                return cached["models"]
        except (json.JSONDecodeError, KeyError, OSError):
            pass
    try:
        r = httpx.get(URL, timeout=10)
        r.raise_for_status()
        raw = r.json().get("data", [])
        models = {
            m["id"]: {
                "durations": m.get("supported_durations") or [],
                "resolutions": m.get("supported_resolutions") or [],
                "aspects": m.get("supported_aspect_ratios") or [],
                "frames": m.get("supported_frame_images") or [],
                "audio": m.get("generate_audio"),
                "pricing_skus": m.get("pricing_skus") or {},
                "name": m.get("name"),
            }
            for m in raw if m.get("id")
        }
        costs.update_video_prices_from_catalog([{"id": k, "pricing_skus": v["pricing_skus"]} for k, v in models.items()])
        _mem = {"ts": time.time(), "models": models}
        p.write_text(json.dumps(_mem))
        return models
    except Exception as e:  # noqa: BLE001 — offline is fine
        log.info("video catalog unavailable (%s); using fallback", e)
        _mem = {"ts": time.time(), "models": FALLBACK}
        return FALLBACK


def spec(model: str) -> dict:
    return load().get(model) or FALLBACK.get(model) or {"durations": [5], "resolutions": ["720p"],
                                                        "aspects": ["16:9", "9:16", "1:1"], "frames": ["first_frame"]}


def snap_duration(model: str, seconds: float) -> int:
    """Smallest supported duration ≥ seconds (or the longest available)."""
    durs = sorted(int(d) for d in (spec(model).get("durations") or [5]))
    for d in durs:
        if d >= seconds - 0.05:
            return d
    return durs[-1]


def best_aspect(model: str, aspect: str) -> str:
    aspects = spec(model).get("aspects") or ["16:9"]
    if aspect in aspects:
        return aspect
    target = _ratio(aspect)
    return min(aspects, key=lambda a: abs(_ratio(a) - target))


def best_resolution(model: str, wanted: str | None) -> str | None:
    res = spec(model).get("resolutions") or []
    if not res:
        return wanted
    if wanted in res:
        return wanted
    order = ["480p", "720p", "768p", "1080p", "2K", "4K"]
    if wanted in order:
        below = [r for r in res if r in order and order.index(r) <= order.index(wanted)]
        if below:
            return max(below, key=order.index)
    return res[0]


def _ratio(a: str) -> float:
    try:
        w, h = a.split(":")
        return float(w) / float(h)
    except ValueError:
        return 16 / 9
