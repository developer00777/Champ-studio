"""Asset library — SQLite metadata + files on disk under <data_dir>/assets.

Every generation and transform lands here. `parent_id` builds lineage chains
(image -> video -> vector -> ...), so any derived asset is traceable to its source.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import data_dir
from .providers.base import suffix_for

_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,            -- image | video | audio | vector | data
    mime        TEXT NOT NULL,
    filename    TEXT NOT NULL,
    prompt      TEXT DEFAULT '',
    model       TEXT DEFAULT '',
    provider    TEXT DEFAULT '',
    tool        TEXT DEFAULT '',          -- transform tool name, '' for generations
    params      TEXT DEFAULT '{}',
    parent_id   TEXT DEFAULT NULL,
    cost        REAL DEFAULT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assets_created ON assets(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_assets_parent ON assets(parent_id);
"""


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(data_dir() / "library.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def kind_for_mime(mime: str) -> str:
    if mime == "image/svg+xml":
        return "vector"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    return "data"


def save_asset(
    data: bytes,
    mime: str,
    *,
    prompt: str = "",
    model: str = "",
    provider: str = "",
    tool: str = "",
    params: dict | None = None,
    parent_id: str | None = None,
    cost: float | None = None,
) -> dict:
    asset_id = uuid.uuid4().hex[:12]
    filename = f"{asset_id}{suffix_for(mime)}"
    (data_dir() / "assets" / filename).write_bytes(data)
    row = {
        "id": asset_id,
        "kind": kind_for_mime(mime),
        "mime": mime,
        "filename": filename,
        "prompt": prompt,
        "model": model,
        "provider": provider,
        "tool": tool,
        "params": json.dumps(params or {}),
        "parent_id": parent_id,
        "cost": cost,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with _lock, _db() as conn:
        conn.execute(
            """INSERT INTO assets (id, kind, mime, filename, prompt, model, provider,
               tool, params, parent_id, cost, created_at)
               VALUES (:id, :kind, :mime, :filename, :prompt, :model, :provider,
               :tool, :params, :parent_id, :cost, :created_at)""",
            row,
        )
    return _public(row)


def list_assets(kind: str | None = None, limit: int = 200, offset: int = 0) -> list[dict]:
    q = "SELECT * FROM assets"
    args: list = []
    if kind:
        q += " WHERE kind = ?"
        args.append(kind)
    q += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    with _lock, _db() as conn:
        rows = conn.execute(q, args).fetchall()
    return [_public(dict(r)) for r in rows]


def get_asset(asset_id: str) -> dict | None:
    with _lock, _db() as conn:
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    return _public(dict(row)) if row else None


def asset_path(asset_id: str) -> Path | None:
    a = get_asset(asset_id)
    if not a:
        return None
    p = data_dir() / "assets" / a["filename"]
    return p if p.exists() else None


def delete_asset(asset_id: str) -> bool:
    a = get_asset(asset_id)
    if not a:
        return False
    with _lock, _db() as conn:
        conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
    try:
        (data_dir() / "assets" / a["filename"]).unlink(missing_ok=True)
    except OSError:
        pass
    return True


def lineage(asset_id: str) -> list[dict]:
    """Chain from root ancestor down to this asset."""
    chain: list[dict] = []
    current = get_asset(asset_id)
    seen: set[str] = set()
    while current and current["id"] not in seen:
        chain.append(current)
        seen.add(current["id"])
        current = get_asset(current["parent_id"]) if current["parent_id"] else None
    return list(reversed(chain))


def _public(row: dict) -> dict:
    out = dict(row)
    try:
        out["params"] = json.loads(out.get("params") or "{}")
    except json.JSONDecodeError:
        out["params"] = {}
    out["url"] = f"/api/assets/{out['id']}/file"
    return out
