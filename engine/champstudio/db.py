"""Engine database — jobs, projects, versions, cost ledger, brand kits, audit.

Shares `library.db` with the asset library. SQL stays portable (no SQLite-only
types or upserts beyond ON CONFLICT) so a Postgres profile is a driver swap.
Schema changes are append-only entries in `_MIGRATIONS`.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

from .config import data_dir

_lock = threading.RLock()

_MIGRATIONS: list[str] = [
    # 1 — jobs survive restarts; every long-running thing (video gen, render, director) is a job
    """
    CREATE TABLE IF NOT EXISTS jobs (
        id          TEXT PRIMARY KEY,
        kind        TEXT NOT NULL,
        status      TEXT NOT NULL,          -- pending | running | waiting | completed | failed | canceled
        stage       TEXT DEFAULT '',
        progress    REAL DEFAULT 0,
        payload     TEXT DEFAULT '{}',
        result      TEXT DEFAULT NULL,
        error       TEXT DEFAULT NULL,
        project_id  TEXT DEFAULT NULL,
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
    """,
    # 2 — projects (editor timelines, director plans) with numbered versions
    """
    CREATE TABLE IF NOT EXISTS projects (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        kind        TEXT NOT NULL,          -- edit | director
        data        TEXT NOT NULL,
        version     INTEGER NOT NULL DEFAULT 1,
        brand_kit_id TEXT DEFAULT NULL,
        locked_by   TEXT DEFAULT NULL,
        locked_at   REAL DEFAULT NULL,
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS project_versions (
        id          TEXT PRIMARY KEY,
        project_id  TEXT NOT NULL,
        version     INTEGER NOT NULL,
        label       TEXT DEFAULT '',
        data        TEXT NOT NULL,
        author      TEXT DEFAULT '',
        created_at  REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_versions_project ON project_versions(project_id, version DESC);
    """,
    # 3 — cost ledger: every paid call, estimated before and actual after
    """
    CREATE TABLE IF NOT EXISTS ledger (
        id          TEXT PRIMARY KEY,
        ts          REAL NOT NULL,
        role        TEXT DEFAULT '',
        model       TEXT DEFAULT '',
        kind        TEXT DEFAULT '',        -- llm | image | video | speech | transcribe | music
        cost        REAL DEFAULT 0,
        estimated   INTEGER DEFAULT 0,
        project_id  TEXT DEFAULT NULL,
        job_id      TEXT DEFAULT NULL,
        meta        TEXT DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_ledger_ts ON ledger(ts DESC);
    """,
    # 4 — brand kits and user-saved templates
    """
    CREATE TABLE IF NOT EXISTS brand_kits (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        data        TEXT NOT NULL,
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS user_templates (
        id          TEXT PRIMARY KEY,
        data        TEXT NOT NULL,
        created_at  REAL NOT NULL
    );
    """,
    # 5 — audit log, review comments
    """
    CREATE TABLE IF NOT EXISTS audit (
        id          TEXT PRIMARY KEY,
        ts          REAL NOT NULL,
        actor       TEXT DEFAULT '',
        action      TEXT NOT NULL,
        target      TEXT DEFAULT '',
        detail      TEXT DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts DESC);
    CREATE TABLE IF NOT EXISTS comments (
        id          TEXT PRIMARY KEY,
        project_id  TEXT NOT NULL,
        version     INTEGER DEFAULT NULL,
        render_asset_id TEXT DEFAULT NULL,
        in_frame    INTEGER DEFAULT NULL,
        out_frame   INTEGER DEFAULT NULL,
        author      TEXT DEFAULT '',
        body        TEXT NOT NULL,
        drawing     TEXT DEFAULT NULL,
        parent_id   TEXT DEFAULT NULL,
        resolved    INTEGER DEFAULT 0,
        created_at  REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_comments_project ON comments(project_id, created_at);
    """,
    # 6 — users + review links (enterprise profile; local mode runs as one implicit admin)
    """
    CREATE TABLE IF NOT EXISTS users (
        id          TEXT PRIMARY KEY,
        email       TEXT UNIQUE NOT NULL,
        name        TEXT DEFAULT '',
        role        TEXT NOT NULL DEFAULT 'editor',   -- viewer | reviewer | editor | admin
        created_at  REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS review_links (
        token       TEXT PRIMARY KEY,
        project_id  TEXT NOT NULL,
        render_asset_id TEXT NOT NULL,
        status      TEXT NOT NULL DEFAULT 'needs_review', -- needs_review | changes_requested | approved
        created_by  TEXT DEFAULT '',
        created_at  REAL NOT NULL
    );
    """,
]

_migrated: set[str] = set()


def _path() -> str:
    return str(data_dir() / "library.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (v INTEGER NOT NULL)")
    row = conn.execute("SELECT v FROM schema_version").fetchone()
    current = row["v"] if row else 0
    if not row:
        conn.execute("INSERT INTO schema_version (v) VALUES (0)")
    for i, sql in enumerate(_MIGRATIONS[current:], start=current + 1):
        conn.executescript(sql)
        conn.execute("UPDATE schema_version SET v = ?", (i,))
    conn.commit()


@contextmanager
def conn():
    """Locked connection with migrations applied once per database file."""
    with _lock:
        c = _connect()
        try:
            if _path() not in _migrated:
                _migrate(c)
                _migrated.add(_path())
            yield c
            c.commit()
        finally:
            c.close()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def dumps(v) -> str:
    # lone surrogates (bad client encodings) would make every later read of the row fail: replace them
    text = json.dumps(v, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8", "replace").decode("utf-8")


def loads(s, default=None):
    if s is None:
        return default
    try:
        return json.loads(s)
    except (TypeError, json.JSONDecodeError):
        return default


def now() -> float:
    return time.time()


# ── audit ────────────────────────────────────────────────────────────


def audit(action: str, target: str = "", detail: dict | None = None, actor: str = "") -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO audit (id, ts, actor, action, target, detail) VALUES (?,?,?,?,?,?)",
            (new_id(), now(), actor, action, target, dumps(detail or {})),
        )


def audit_log(limit: int = 200, target: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM audit", []
    if target:
        q += " WHERE target = ?"
        args.append(target)
    q += " ORDER BY ts DESC LIMIT ?"
    args.append(limit)
    with conn() as c:
        rows = c.execute(q, args).fetchall()
    return [{**dict(r), "detail": loads(r["detail"], {})} for r in rows]
