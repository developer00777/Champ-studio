"""Editor ingest: probe, proxy, thumbnail strip and waveform peaks per asset.

Derived files live in <data_dir>/derived/<asset_id>/ (not in the library
grid) and are served by `GET /api/assets/{id}/derived/{name}`:
    probe.json  proxy.mp4  thumbs.jpg  thumbs.json  peaks.json  transcript.json
"""

from __future__ import annotations

import json
from pathlib import Path

from . import library
from .config import data_dir
from .render import media

DERIVED_NAMES = {"probe.json", "proxy.mp4", "thumbs.jpg", "thumbs.json", "peaks.json", "transcript.json",
                 "mezzanine.mp4"}


def derived_dir(asset_id: str) -> Path:
    d = data_dir() / "derived" / asset_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def derived_path(asset_id: str, name: str) -> Path | None:
    if name not in DERIVED_NAMES:
        return None
    p = data_dir() / "derived" / asset_id / name
    return p if p.exists() else None


def status(asset_id: str) -> dict:
    d = data_dir() / "derived" / asset_id
    out: dict = {"asset_id": asset_id, "ready": False}
    if (d / "probe.json").exists():
        out["probe"] = json.loads((d / "probe.json").read_text())
    for name in ("proxy.mp4", "thumbs.jpg", "peaks.json", "transcript.json"):
        if (d / name).exists():
            out[name.split(".")[0]] = f"/api/assets/{asset_id}/derived/{name}"
    if (d / "thumbs.json").exists():
        out["thumbs_meta"] = json.loads((d / "thumbs.json").read_text())
    kind = (library.get_asset(asset_id) or {}).get("kind")
    need = {"video": ["probe", "proxy", "thumbs", "peaks"], "audio": ["probe", "peaks"], "image": ["probe"]}
    out["ready"] = all(k in out for k in need.get(kind, ["probe"]))
    return out


def ingest(asset_id: str, progress=lambda p, s: None) -> dict:
    asset = library.get_asset(asset_id)
    src = library.asset_path(asset_id)
    if not asset or not src:
        raise KeyError(asset_id)
    d = derived_dir(asset_id)
    progress(0.05, "probing")
    if asset["kind"] == "image" or asset["kind"] == "vector":
        info = {"still": True, "duration": 0}
        try:
            from PIL import Image

            with Image.open(src) as im:
                info.update(width=im.width, height=im.height)
        except Exception:  # noqa: BLE001
            pass
        (d / "probe.json").write_text(json.dumps(info))
        return status(asset_id)
    info = media.probe(src)
    (d / "probe.json").write_text(json.dumps(info))
    if info.get("has_video"):
        if info.get("vfr") and not (d / "mezzanine.mp4").exists():
            progress(0.1, "conforming variable frame rate")
            media.conform_cfr(str(src), str(d / "mezzanine.mp4"), round(info.get("fps") or 30))
        if not (d / "proxy.mp4").exists():
            progress(0.2, "building proxy")
            media.make_proxy(str(src), str(d / "proxy.mp4"))
        if not (d / "thumbs.jpg").exists():
            progress(0.7, "thumbnails")
            meta = media.thumbnail_strip(str(src), str(d / "thumbs.jpg"))
            (d / "thumbs.json").write_text(json.dumps(meta))
    if info.get("has_audio") and not (d / "peaks.json").exists():
        progress(0.85, "waveform")
        (d / "peaks.json").write_text(json.dumps(media.waveform_peaks(str(src))))
    progress(1.0, "done")
    return status(asset_id)
