"""Projects: editor timelines and Director plans, with autosave, numbered
versions and per-project edit locks (Team Projects-style)."""

from __future__ import annotations

from . import db

LOCK_TTL_S = 15 * 60  # an editor's lock lapses after 15 min without autosave/heartbeat


class Conflict(RuntimeError):
    pass


class Locked(RuntimeError):
    pass


def _row(r, *, with_data: bool = True) -> dict:
    out = {k: r[k] for k in r.keys() if k != "data"}
    if with_data:
        out["data"] = db.loads(r["data"], {})
    lock_live = r["locked_by"] and r["locked_at"] and db.now() - r["locked_at"] < LOCK_TTL_S
    out["locked_by"] = r["locked_by"] if lock_live else None
    return out


def create(name: str, kind: str, data: dict, *, brand_kit_id: str | None = None, actor: str = "") -> dict:
    pid = db.new_id()
    ts = db.now()
    with db.conn() as c:
        c.execute(
            """INSERT INTO projects (id, name, kind, data, version, brand_kit_id, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (pid, name, kind, db.dumps(data), 1, brand_kit_id, ts, ts),
        )
    db.audit("project.create", pid, {"name": name, "kind": kind}, actor=actor)
    return get(pid)


def get(pid: str) -> dict | None:
    with db.conn() as c:
        r = c.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    return _row(r) if r else None


def list_(kind: str | None = None, limit: int = 200) -> list[dict]:
    q, args = "SELECT * FROM projects", []
    if kind:
        q += " WHERE kind = ?"
        args.append(kind)
    q += " ORDER BY updated_at DESC LIMIT ?"
    args.append(limit)
    with db.conn() as c:
        return [_row(r, with_data=False) for r in c.execute(q, args).fetchall()]


def save(pid: str, data: dict, *, base_version: int | None = None, actor: str = "",
         name: str | None = None) -> dict:
    """Autosave. `base_version` enables optimistic concurrency: a save against a
    stale version raises Conflict instead of silently overwriting a teammate."""
    with db.conn() as c:
        r = c.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
        if not r:
            raise KeyError(pid)
        cur = _row(r, with_data=False)
        if cur["locked_by"] and actor and cur["locked_by"] != actor:
            raise Locked(f"project is being edited by {cur['locked_by']}")
        if base_version is not None and base_version != r["version"]:
            raise Conflict(f"project changed since version {base_version} (now {r['version']})")
        sets = "data = ?, version = version + 1, updated_at = ?"
        args = [db.dumps(data), db.now()]
        if name:
            sets += ", name = ?"
            args.append(name)
        if actor and cur["locked_by"] == actor:
            sets += ", locked_at = ?"
            args.append(db.now())
        c.execute(f"UPDATE projects SET {sets} WHERE id = ?", (*args, pid))
    return get(pid)


def rename(pid: str, name: str, actor: str = "") -> dict | None:
    with db.conn() as c:
        c.execute("UPDATE projects SET name = ?, updated_at = ? WHERE id = ?", (name, db.now(), pid))
    db.audit("project.rename", pid, {"name": name}, actor=actor)
    return get(pid)


def delete(pid: str, actor: str = "") -> bool:
    with db.conn() as c:
        n = c.execute("DELETE FROM projects WHERE id = ?", (pid,)).rowcount
        c.execute("DELETE FROM project_versions WHERE project_id = ?", (pid,))
        c.execute("DELETE FROM comments WHERE project_id = ?", (pid,))
    if n:
        db.audit("project.delete", pid, {}, actor=actor)
    return bool(n)


# ── versions ─────────────────────────────────────────────────────────


def snapshot(pid: str, label: str = "", actor: str = "") -> dict:
    p = get(pid)
    if not p:
        raise KeyError(pid)
    vid = db.new_id()
    with db.conn() as c:
        n = c.execute("SELECT COALESCE(MAX(version), 0) AS v FROM project_versions WHERE project_id = ?",
                      (pid,)).fetchone()["v"] + 1
        c.execute(
            """INSERT INTO project_versions (id, project_id, version, label, data, author, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (vid, pid, n, label, db.dumps(p["data"]), actor, db.now()),
        )
    db.audit("project.version", pid, {"version": n, "label": label}, actor=actor)
    return {"id": vid, "project_id": pid, "version": n, "label": label, "author": actor}


def versions(pid: str) -> list[dict]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT id, project_id, version, label, author, created_at FROM project_versions "
            "WHERE project_id = ? ORDER BY version DESC", (pid,)).fetchall()
    return [dict(r) for r in rows]


def version_data(pid: str, version: int) -> dict | None:
    with db.conn() as c:
        r = c.execute("SELECT data FROM project_versions WHERE project_id = ? AND version = ?",
                      (pid, version)).fetchone()
    return db.loads(r["data"], {}) if r else None


def restore(pid: str, version: int, actor: str = "") -> dict:
    data = version_data(pid, version)
    if data is None:
        raise KeyError(f"version {version}")
    snapshot(pid, f"before restoring v{version}", actor)
    out = save(pid, data, actor=actor)
    db.audit("project.restore", pid, {"version": version}, actor=actor)
    return out


# ── locks ────────────────────────────────────────────────────────────


def lock(pid: str, actor: str, *, force: bool = False) -> dict:
    with db.conn() as c:
        r = c.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
        if not r:
            raise KeyError(pid)
        holder = _row(r, with_data=False)["locked_by"]
        if holder and holder != actor and not force:
            raise Locked(f"project is being edited by {holder}")
        c.execute("UPDATE projects SET locked_by = ?, locked_at = ? WHERE id = ?", (actor, db.now(), pid))
    db.audit("project.lock", pid, {"force": force}, actor=actor)
    return get(pid)


def unlock(pid: str, actor: str, *, force: bool = False) -> dict:
    with db.conn() as c:
        r = c.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
        if not r:
            raise KeyError(pid)
        if r["locked_by"] and r["locked_by"] != actor and not force:
            raise Locked("only the lock holder (or an admin) can unlock")
        c.execute("UPDATE projects SET locked_by = NULL, locked_at = NULL WHERE id = ?", (pid,))
    db.audit("project.unlock", pid, {"force": force}, actor=actor)
    return get(pid)
