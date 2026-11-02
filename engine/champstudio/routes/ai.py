"""AI editing API. Every endpoint returns {job_id}; the finished job's result
carries {timeline, summary} (or the documented payload) for the editor to
apply as one undoable step."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from .. import ai_edit, jobs, library
from ..deps import actor, guarded, require_role
from ..timeline import Timeline

router = APIRouter(dependencies=[guarded, require_role("editor")])


def _tl(data: dict) -> Timeline:
    try:
        return Timeline(**data)
    except ValidationError as e:
        raise HTTPException(422, f"invalid timeline: {e.errors()[:3]}") from e


def _spawn(kind: str, body) -> dict:
    job = jobs.spawn(f"ai.{kind}", body, payload={"tool": kind})
    return {"job_id": job["id"]}


def _needs_clip(tl: Timeline, clip_id: str) -> None:
    if tl.clip(clip_id) is None:
        raise HTTPException(404, f"clip {clip_id} not found")


class RemoveRanges(BaseModel):
    timeline: dict
    clip_id: str
    ranges: list[list[float]] = Field(min_length=1)


@router.post("/api/ai/remove-ranges")
async def remove_ranges(body: RemoveRanges):
    tl = _tl(body.timeline)
    _needs_clip(tl, body.clip_id)
    return _spawn("remove-ranges", lambda h: asyncio.to_thread(ai_edit.remove_ranges, tl, body.clip_id, body.ranges))


class SilenceCut(BaseModel):
    timeline: dict
    clip_id: str
    fillers: bool = True
    min_silence_s: float = Field(0.45, ge=0.15, le=5)
    padding_s: float = Field(0.12, ge=0, le=1)
    noise_db: float = -35


@router.post("/api/ai/silence-cut")
async def silence_cut(body: SilenceCut):
    tl = _tl(body.timeline)
    _needs_clip(tl, body.clip_id)
    return _spawn("silence-cut", lambda h: ai_edit.silence_cut(
        tl, body.clip_id, fillers=body.fillers, min_silence_s=body.min_silence_s, padding_s=body.padding_s,
        noise_db=body.noise_db))


class Captions(BaseModel):
    timeline: dict
    clip_ids: list[str] | None = None
    style: dict | None = None
    brand_kit_id: str | None = None


@router.post("/api/ai/captions")
async def captions(body: Captions):
    tl = _tl(body.timeline)
    return _spawn("captions", lambda h: ai_edit.captions(tl, body.clip_ids, body.style, body.brand_kit_id))


class Duck(BaseModel):
    timeline: dict
    duck_db: float = Field(10, ge=1, le=40)


@router.post("/api/ai/duck")
async def duck(body: Duck):
    tl = _tl(body.timeline)
    return _spawn("duck", lambda h: asyncio.to_thread(ai_edit.duck, tl, body.duck_db))


class Reframe(BaseModel):
    timeline: dict
    clip_id: str
    aspect: str = "9:16"


@router.post("/api/ai/reframe")
async def reframe(body: Reframe):
    tl = _tl(body.timeline)
    _needs_clip(tl, body.clip_id)
    return _spawn("reframe", lambda h: asyncio.to_thread(ai_edit.reframe, tl, body.clip_id, body.aspect))


class Duplicate(BaseModel):
    timeline: dict
    aspect: str = "9:16"


@router.post("/api/ai/duplicate-as")
async def duplicate_as(body: Duplicate):
    tl = _tl(body.timeline)

    async def run(h):
        new = await asyncio.to_thread(ai_edit.duplicate_as, tl, body.aspect)
        return {"timeline": new.model_dump(by_alias=True), "summary": f"Sequence reframed for {body.aspect}"}

    return _spawn("duplicate-as", run)


class Broll(BaseModel):
    timeline: dict
    brief: str | None = None
    max_beats: int = Field(6, ge=1, le=12)
    brand_kit_id: str | None = None


@router.post("/api/ai/broll")
async def broll(body: Broll):
    tl = _tl(body.timeline)
    return _spawn("broll", lambda h: ai_edit.broll(tl, body.brief, body.max_beats, body.brand_kit_id))


class Command(BaseModel):
    timeline: dict
    message: str = Field(min_length=2, max_length=2000)
    brand_kit_id: str | None = None


@router.post("/api/ai/command")
async def command(body: Command):
    tl = _tl(body.timeline)
    return _spawn("command", lambda h: ai_edit.command(tl, body.message, body.brand_kit_id))


class AssetBody(BaseModel):
    asset_id: str
    threshold: float = 0.3


@router.post("/api/ai/scene-detect")
async def scene_detect(body: AssetBody):
    if not library.get_asset(body.asset_id):
        raise HTTPException(404, "asset not found")
    return _spawn("scene-detect", lambda h: asyncio.to_thread(ai_edit.scene_detect, body.asset_id, body.threshold))


@router.post("/api/ai/enhance-speech")
async def enhance_speech(body: AssetBody):
    if not library.get_asset(body.asset_id):
        raise HTTPException(404, "asset not found")
    return _spawn("enhance-speech", lambda h: asyncio.to_thread(ai_edit.enhance_speech, body.asset_id))


class RoughCut(BaseModel):
    asset_ids: list[str] = Field(min_length=1, max_length=20)
    brief: str = Field(min_length=3, max_length=3000)
    aspect: str = "16:9"
    target_s: float = Field(60, ge=5, le=1800)
    captions: bool = True


@router.post("/api/ai/rough-cut")
async def rough_cut(body: RoughCut, request: Request):
    for a in body.asset_ids:
        if not library.get_asset(a):
            raise HTTPException(404, f"asset {a} not found")
    owner = actor(request)["id"]
    return _spawn("rough-cut", lambda h: ai_edit.rough_cut(body.asset_ids, body.brief, aspect=body.aspect,
                                                           target_s=body.target_s, owner=owner,
                                                           captions_on=body.captions))
