"""Core API: jobs, cost ledger, model roles, fonts, users and audit."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from pydantic import BaseModel

from .. import auth, costs, db, jobs, roles
from ..config import data_dir
from ..deps import actor, guarded, require_role
from ..render import text as textmod

router = APIRouter(dependencies=[guarded])


# ── jobs ─────────────────────────────────────────────────────────────


@router.get("/api/jobs")
async def list_jobs(kind: str | None = None, project_id: str | None = None, limit: int = 50):
    return {"jobs": jobs.list_jobs(limit=limit, kind=kind, project_id=project_id)}


@router.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


@router.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request):
    job = jobs.cancel_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    db.audit("job.cancel", job_id, {}, actor=actor(request)["id"])
    return job


# ── costs / budgets ──────────────────────────────────────────────────


@router.get("/api/costs")
async def cost_summary(days: int = 30):
    return costs.summary(days)


class EstimateRequest(BaseModel):
    kind: str                       # video | speech | transcribe | llm | image | music
    model: str | None = None
    seconds: float = 0
    resolution: str | None = None
    audio: bool = False
    chars: int = 0
    count: int = 1


@router.post("/api/costs/estimate")
async def estimate(body: EstimateRequest):
    if body.kind == "video":
        model = body.model or roles.model_for("video_draft")
        usd = costs.estimate_video(model, body.seconds, body.resolution, body.audio) * body.count
    elif body.kind == "speech":
        usd = costs.estimate_speech(body.chars)
    elif body.kind == "transcribe":
        usd = costs.estimate_transcribe(body.seconds)
    elif body.kind == "image":
        usd = costs.IMAGE_PRICE_GUESS * body.count
    elif body.kind == "music":
        usd = costs.MUSIC_PER_CLIP * body.count
    else:
        usd = costs.estimate_llm(body.model or roles.model_for("planner"), 3000, 1500) * body.count
    return {"usd": round(usd, 4)}


# ── model roles ──────────────────────────────────────────────────────


@router.get("/api/roles")
async def get_roles():
    return {"roles": roles.roles(), "defaults": roles.DEFAULT_ROLES}


# ── fonts ────────────────────────────────────────────────────────────


@router.get("/api/fonts")
async def fonts():
    files = []
    for d in textmod._font_dirs():
        for p in sorted(d.glob("*.[ot]tf")):
            fam = p.stem.split("-", 1)[0]
            fam = fam.split("[", 1)[0]
            pretty = re.sub(r"(?<=[a-zA-Z])(?=[A-Z][a-z])", " ", fam)  # DMSans → DM Sans, SpaceGrotesk → Space Grotesk
            style = p.stem.split("-", 1)[1] if "-" in p.stem else ""
            files.append({"family": pretty, "file": p.name, "url": f"/api/fonts/file/{p.name}",
                          "weight": textmod._STATIC_WEIGHTS.get(style, 400), "variable": "-" not in p.stem})
    return {"families": textmod.families(), "files": files}


@router.post("/api/fonts", dependencies=[require_role("editor")])
async def upload_font(file: UploadFile = File(...)):
    name = (file.filename or "").replace("\\", "/").split("/")[-1]
    if not name.lower().endswith((".ttf", ".otf")):
        raise HTTPException(400, "upload a .ttf or .otf font file")
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "font too large")
    d = data_dir() / "fonts"
    d.mkdir(exist_ok=True)
    safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_.[],")
    (d / safe).write_bytes(data)
    textmod.load_font.cache_clear()
    return {"families": textmod.families()}


# ── users + audit (admin) ────────────────────────────────────────────


@router.get("/api/me")
async def me(request: Request):
    return actor(request)


@router.get("/api/users", dependencies=[require_role("admin")])
async def users():
    return {"users": auth.list_users()}


class RoleUpdate(BaseModel):
    role: str


@router.put("/api/users/{user_id}/role", dependencies=[require_role("admin")])
async def set_user_role(user_id: str, body: RoleUpdate, request: Request):
    try:
        u = auth.set_role(user_id, body.role)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not u:
        raise HTTPException(404, "user not found")
    db.audit("user.role", user_id, {"role": body.role}, actor=actor(request)["id"])
    return u


@router.get("/api/audit", dependencies=[require_role("admin")])
async def audit(limit: int = 200, target: str | None = None):
    return {"entries": db.audit_log(limit=limit, target=target)}
