"""Director API — brief to finished video, stage by stage or on autopilot."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import director, jobs, projects
from ..deps import actor, guarded, require_role
from ..timeline import Timeline

router = APIRouter(dependencies=[guarded])
editor_only = require_role("editor")


class Brief(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    audience: str | None = None
    platform: str | None = None
    length_s: int = Field(30, ge=6, le=180)
    tone: str | None = None
    budget: str = Field("balanced", pattern="^(lean|balanced|premium)$")
    facts: str | None = None
    presenter_asset_id: str | None = None
    music_asset_id: str | None = None
    voice: str | None = None
    captions: bool = True


class NewDirector(BaseModel):
    brief: Brief
    name: str | None = None
    brand_kit_id: str | None = "champions-group"
    aspects: list[str] = ["16:9"]
    quality: str = Field("draft", pattern="^(draft|final)$")
    autopilot: bool = False


def _get(pid: str) -> dict:
    p = projects.get(pid)
    if not p or p["kind"] != "director":
        raise HTTPException(404, "director project not found")
    return p


def _job(kind: str, pid: str, body) -> dict:
    job = jobs.spawn(f"director.{kind}", body, payload={"stage": kind}, project_id=pid)
    return {"job_id": job["id"], "project_id": pid}


@router.get("/api/director")
async def list_director():
    return {"projects": projects.list_("director")}


def short_name(text: str, limit: int = 60) -> str:
    """First line of a brief, cut on a word boundary with an ellipsis."""
    line = " ".join((text or "").split()) or "Untitled video"
    if len(line) <= limit:
        return line
    cut = line[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:.-—")
    return f"{cut}…"


@router.post("/api/director", dependencies=[editor_only])
async def create(body: NewDirector, request: Request):
    data = {"brief": body.brief.model_dump(), "brand_kit_id": body.brand_kit_id, "aspects": body.aspects or ["16:9"],
            "quality": body.quality, "stage": "new", "plan": None, "scene_state": {},
            "auto_name": not body.name}  # replaced by the plan's title once the script is written
    p = projects.create(body.name or short_name(body.brief.prompt), "director", data, brand_kit_id=body.brand_kit_id,
                        actor=actor(request)["id"])
    data["id"] = p["id"]
    projects.save(p["id"], data)
    if body.autopilot:
        return _job("autopilot", p["id"], lambda h: director.autopilot(p["id"], h))
    return _job("plan", p["id"], lambda h: director.stage_plan(p["id"], h))


@router.get("/api/director/{pid}")
async def get(pid: str):
    p = _get(pid)
    est = director.estimate(p["data"]) if p["data"].get("plan") else None
    return {**p, "estimate": est}


class PlanUpdate(BaseModel):
    plan: dict
    quality: str | None = None
    aspects: list[str] | None = None


@router.put("/api/director/{pid}/plan", dependencies=[editor_only])
async def update_plan(pid: str, body: PlanUpdate):
    p = _get(pid)
    data = p["data"]
    errors = director.llm.validation_errors(body.plan, director.PLAN_SCHEMA)
    if errors:
        raise HTTPException(422, "; ".join(errors[:4]))
    old = {s.get("id"): s for s in (data.get("plan") or {}).get("scenes", [])}
    plan = director._clean_plan(body.plan, data["brief"])
    for s in plan["scenes"]:
        prev = old.get(s["id"])
        st = data.setdefault("scene_state", {}).setdefault(s["id"], {})
        if not prev or any(prev.get(k) != s.get(k) for k in ("visual", "visual_type", "camera")):
            st["dirty_visual"] = True
        if not prev or prev.get("voiceover") != s.get("voiceover"):
            st["dirty_vo"] = True
    data["plan"] = plan
    if body.quality:
        data["quality"] = body.quality
    if body.aspects:
        data["aspects"] = body.aspects
    p = projects.save(pid, data)
    return {**p, "estimate": director.estimate(data)}


class StageRequest(BaseModel):
    scene_ids: list[str] | None = None
    force: bool = False
    quality: str | None = None
    preset: str = "h264"
    aspects: list[str] | None = None


@router.post("/api/director/{pid}/plan", dependencies=[editor_only])
async def replan(pid: str):
    _get(pid)
    return _job("plan", pid, lambda h: director.stage_plan(pid, h))


@router.post("/api/director/{pid}/storyboard", dependencies=[editor_only])
async def storyboard(pid: str, body: StageRequest):
    if not _get(pid)["data"].get("plan"):
        raise HTTPException(400, "plan first")
    return _job("storyboard", pid, lambda h: director.stage_storyboard(pid, h, body.scene_ids, body.force))


@router.post("/api/director/{pid}/produce", dependencies=[editor_only])
async def produce(pid: str, body: StageRequest):
    if not _get(pid)["data"].get("plan"):
        raise HTTPException(400, "plan first")
    return _job("produce", pid, lambda h: director.stage_produce(pid, h, body.quality))


@router.post("/api/director/{pid}/render", dependencies=[editor_only])
async def render(pid: str, body: StageRequest):
    if not _get(pid)["data"].get("timelines"):
        raise HTTPException(400, "produce first")
    return _job("render", pid, lambda h: director.stage_render(pid, h, body.preset, body.aspects))


@router.post("/api/director/{pid}/autopilot", dependencies=[editor_only])
async def autopilot(pid: str):
    _get(pid)
    return _job("autopilot", pid, lambda h: director.autopilot(pid, h))


class ChatRequest(BaseModel):
    message: str = Field(min_length=2, max_length=2000)
    apply: bool = True        # re-produce affected scenes and re-render after the edit


@router.post("/api/director/{pid}/chat", dependencies=[editor_only])
async def chat(pid: str, body: ChatRequest):
    p = _get(pid)
    if not p["data"].get("plan"):
        raise HTTPException(400, "plan first")
    result = await director.chat_edit(pid, body.message)
    job = None
    if body.apply and p["data"].get("stage") in ("produced", "rendered"):
        async def rerun(h):
            await director.stage_produce(pid, h)
            if p["data"].get("stage") == "rendered":
                return await director.stage_render(pid, h)
            return {}
        job = _job("rerun", pid, rerun)
    return {**result, "job": job}


class OpenInEditor(BaseModel):
    aspect: str = "16:9"


@router.post("/api/director/{pid}/open-in-editor", dependencies=[editor_only])
async def open_in_editor(pid: str, body: OpenInEditor, request: Request):
    data = _get(pid)["data"]
    tl = (data.get("timelines") or {}).get(body.aspect)
    if not tl:
        raise HTTPException(400, "produce this aspect first")
    Timeline(**tl)
    name = f"{(data.get('plan') or {}).get('title', 'Director')} ({body.aspect})"
    return projects.create(name, "edit", tl, brand_kit_id=data.get("brand_kit_id"), actor=actor(request)["id"])
