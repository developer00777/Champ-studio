"""Review & approval (Frame.io-style): frame-accurate comments on projects and
renders, version-aware; shareable review links that work without an account
(the token is the capability) with approve / request-changes."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import db, library, projects
from ..deps import actor, guarded, require_role

router = APIRouter()
STATUSES = ("needs_review", "changes_requested", "approved")


def _comment_row(r) -> dict:
    d = dict(r)
    d["drawing"] = db.loads(d.get("drawing"), None)
    d["resolved"] = bool(d["resolved"])
    return d


def _comments(project_id: str, render_asset_id: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM comments WHERE project_id = ?", [project_id]
    if render_asset_id:
        q += " AND (render_asset_id = ? OR render_asset_id IS NULL)"
        args.append(render_asset_id)
    q += " ORDER BY COALESCE(in_frame, 0), created_at"
    with db.conn() as c:
        return [_comment_row(r) for r in c.execute(q, args).fetchall()]


class NewComment(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    in_frame: int | None = None
    out_frame: int | None = None
    version: int | None = None
    render_asset_id: str | None = None
    drawing: dict | list | None = None
    parent_id: str | None = None
    author: str | None = None


def _add_comment(project_id: str, body: NewComment, author: str) -> dict:
    cid = db.new_id()
    with db.conn() as c:
        c.execute(
            """INSERT INTO comments (id, project_id, version, render_asset_id, in_frame, out_frame, author, body,
               drawing, parent_id, resolved, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,0,?)""",
            (cid, project_id, body.version, body.render_asset_id, body.in_frame, body.out_frame, author, body.body,
             db.dumps(body.drawing) if body.drawing is not None else None, body.parent_id, db.now()))
        r = c.execute("SELECT * FROM comments WHERE id = ?", (cid,)).fetchone()
    db.audit("comment.add", project_id, {"comment": cid}, actor=author)
    return _comment_row(r)


# ── authenticated (team) ─────────────────────────────────────────────


@router.get("/api/projects/{pid}/comments", dependencies=[guarded])
async def list_comments(pid: str, render_asset_id: str | None = None):
    if not projects.get(pid):
        raise HTTPException(404, "project not found")
    return {"comments": _comments(pid, render_asset_id)}


@router.post("/api/projects/{pid}/comments", dependencies=[require_role("reviewer")])
async def add_comment(pid: str, body: NewComment, request: Request):
    if not projects.get(pid):
        raise HTTPException(404, "project not found")
    user = actor(request)
    return _add_comment(pid, body, user.get("email") or user["id"])


class CommentUpdate(BaseModel):
    resolved: bool | None = None
    body: str | None = Field(None, max_length=4000)


@router.put("/api/comments/{cid}", dependencies=[require_role("reviewer")])
async def update_comment(cid: str, body: CommentUpdate, request: Request):
    sets, args = [], []
    if body.resolved is not None:
        sets.append("resolved = ?")
        args.append(int(body.resolved))
    if body.body is not None:
        sets.append("body = ?")
        args.append(body.body)
    if not sets:
        raise HTTPException(400, "nothing to update")
    with db.conn() as c:
        n = c.execute(f"UPDATE comments SET {', '.join(sets)} WHERE id = ?", (*args, cid)).rowcount
        r = c.execute("SELECT * FROM comments WHERE id = ?", (cid,)).fetchone()
    if not n:
        raise HTTPException(404, "comment not found")
    db.audit("comment.update", r["project_id"], {"comment": cid, **body.model_dump(exclude_none=True)},
             actor=actor(request)["id"])
    return _comment_row(r)


@router.delete("/api/comments/{cid}", dependencies=[require_role("editor")])
async def delete_comment(cid: str):
    with db.conn() as c:
        if not c.execute("DELETE FROM comments WHERE id = ?", (cid,)).rowcount:
            raise HTTPException(404, "comment not found")
    return {"deleted": cid}


class NewLink(BaseModel):
    render_asset_id: str


@router.post("/api/projects/{pid}/review-links", dependencies=[require_role("editor")])
async def create_link(pid: str, body: NewLink, request: Request):
    if not projects.get(pid):
        raise HTTPException(404, "project not found")
    a = library.get_asset(body.render_asset_id)
    if not a or a["kind"] != "video":
        raise HTTPException(400, "pick a rendered video")
    token = secrets.token_urlsafe(18)
    with db.conn() as c:
        c.execute("INSERT INTO review_links (token, project_id, render_asset_id, status, created_by, created_at) "
                  "VALUES (?,?,?,?,?,?)", (token, pid, body.render_asset_id, "needs_review", actor(request)["id"],
                                           db.now()))
    db.audit("review.link", pid, {"render": body.render_asset_id}, actor=actor(request)["id"])
    return {"token": token, "url": f"/review.html?t={token}"}


@router.get("/api/projects/{pid}/review-links", dependencies=[guarded])
async def list_links(pid: str):
    with db.conn() as c:
        rows = c.execute("SELECT * FROM review_links WHERE project_id = ? ORDER BY created_at DESC", (pid,)).fetchall()
    return {"links": [{**dict(r), "url": f"/review.html?t={r['token']}"} for r in rows]}


# ── public review (token = capability) ───────────────────────────────


def _link(token: str) -> dict:
    with db.conn() as c:
        r = c.execute("SELECT * FROM review_links WHERE token = ?", (token,)).fetchone()
    if not r:
        raise HTTPException(404, "review link not found or revoked")
    return dict(r)


@router.get("/api/review/{token}")
async def review(token: str):
    link = _link(token)
    p = projects.get(link["project_id"]) or {}
    fps = (p.get("data") or {}).get("fps", 30)
    return {"project": {"id": link["project_id"], "name": p.get("name", "")}, "status": link["status"],
            "video_url": f"/api/review/{token}/video", "fps": fps,
            "comments": _comments(link["project_id"], link["render_asset_id"])}


@router.get("/api/review/{token}/video")
async def review_video(token: str):
    link = _link(token)
    path = library.asset_path(link["render_asset_id"])
    if not path:
        raise HTTPException(404, "render missing")
    return FileResponse(path, media_type="video/mp4")


@router.post("/api/review/{token}/comments")
async def review_comment(token: str, body: NewComment):
    link = _link(token)
    author = (body.author or "Guest reviewer").strip()[:80]
    body.render_asset_id = link["render_asset_id"]
    return _add_comment(link["project_id"], body, f"{author} (link)")


class StatusBody(BaseModel):
    status: str
    author: str | None = None


@router.post("/api/review/{token}/status")
async def review_status(token: str, body: StatusBody):
    if body.status not in STATUSES:
        raise HTTPException(400, f"status must be one of {STATUSES}")
    link = _link(token)
    with db.conn() as c:
        c.execute("UPDATE review_links SET status = ? WHERE token = ?", (body.status, token))
    db.audit("review.status", link["project_id"], {"status": body.status, "render": link["render_asset_id"]},
             actor=f"{(body.author or 'guest')[:80]} (link)")
    return {"status": body.status}
