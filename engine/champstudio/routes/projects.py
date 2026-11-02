"""Projects API for the editor: CRUD, autosave with conflict detection,
versions, locks, preview frames, renders, ingest, transcripts, interchange."""

from __future__ import annotations

import asyncio
import io
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

from .. import db, ingest, jobs, library, otio_io, projects, transcribe
from ..deps import actor, guarded, require_role
from ..render import export
from ..render.compositor import RenderError
from ..render.media import MediaError
from ..timeline import Timeline, canvas_for

router = APIRouter(dependencies=[guarded])
editor_only = require_role("editor")


def _timeline(data: dict) -> Timeline:
    try:
        return Timeline(**data)
    except ValidationError as e:
        raise HTTPException(422, f"invalid timeline: {e.errors()[:3]}") from e


def _project(pid: str) -> dict:
    p = projects.get(pid)
    if not p:
        raise HTTPException(404, "project not found")
    return p


# ── CRUD ─────────────────────────────────────────────────────────────


class NewProject(BaseModel):
    name: str = "Untitled sequence"
    aspect: str = "16:9"
    fps: int = 30
    data: dict | None = None
    brand_kit_id: str | None = None


def empty_timeline(aspect: str = "16:9", fps: int = 30) -> dict:
    w, h = canvas_for(aspect)
    return Timeline(fps=fps, width=w, height=h, tracks=[
        {"id": "v1", "kind": "video", "name": "V1"}, {"id": "v2", "kind": "video", "name": "V2"},
        {"id": "v3", "kind": "video", "name": "V3"},
        {"id": "a1", "kind": "audio", "name": "A1"}, {"id": "a2", "kind": "audio", "name": "A2"},
        {"id": "a3", "kind": "audio", "name": "A3"},
    ]).model_dump(by_alias=True)


@router.get("/api/projects")
async def list_projects(kind: str | None = None):
    return {"projects": projects.list_(kind)}


@router.post("/api/projects", dependencies=[editor_only])
async def create_project(body: NewProject, request: Request):
    data = body.data or empty_timeline(body.aspect, body.fps)
    _timeline(data)
    return projects.create(body.name, "edit", data, brand_kit_id=body.brand_kit_id, actor=actor(request)["id"])


@router.get("/api/projects/{pid}")
async def get_project(pid: str):
    return _project(pid)


class SaveProject(BaseModel):
    data: dict
    base_version: int | None = None
    name: str | None = None


@router.put("/api/projects/{pid}", dependencies=[editor_only])
async def save_project(pid: str, body: SaveProject, request: Request):
    _project(pid)
    _timeline(body.data)
    try:
        return projects.save(pid, body.data, base_version=body.base_version,
                             actor=actor(request)["id"], name=body.name)
    except projects.Conflict as e:
        raise HTTPException(409, str(e)) from e
    except projects.Locked as e:
        raise HTTPException(423, str(e)) from e


@router.delete("/api/projects/{pid}", dependencies=[editor_only])
async def delete_project(pid: str, request: Request):
    if not projects.delete(pid, actor=actor(request)["id"]):
        raise HTTPException(404, "project not found")
    return {"deleted": pid}


# ── versions + locks ─────────────────────────────────────────────────


class VersionBody(BaseModel):
    label: str = ""


@router.get("/api/projects/{pid}/versions")
async def list_versions(pid: str):
    _project(pid)
    return {"versions": projects.versions(pid)}


@router.post("/api/projects/{pid}/versions", dependencies=[editor_only])
async def make_version(pid: str, body: VersionBody, request: Request):
    _project(pid)
    return projects.snapshot(pid, body.label, actor=actor(request)["id"])


@router.get("/api/projects/{pid}/versions/{version}")
async def get_version(pid: str, version: int):
    data = projects.version_data(pid, version)
    if data is None:
        raise HTTPException(404, "version not found")
    return {"version": version, "data": data}


@router.post("/api/projects/{pid}/versions/{version}/restore", dependencies=[editor_only])
async def restore_version(pid: str, version: int, request: Request):
    try:
        return projects.restore(pid, version, actor=actor(request)["id"])
    except KeyError as e:
        raise HTTPException(404, "version not found") from e


@router.post("/api/projects/{pid}/lock", dependencies=[editor_only])
async def lock(pid: str, request: Request, force: bool = False):
    user = actor(request)
    if force and user.get("role") != "admin":
        raise HTTPException(403, "only admins can take over a lock")
    try:
        return projects.lock(pid, user["id"], force=force)
    except projects.Locked as e:
        raise HTTPException(423, str(e)) from e
    except KeyError as e:
        raise HTTPException(404, "project not found") from e


@router.post("/api/projects/{pid}/unlock", dependencies=[editor_only])
async def unlock(pid: str, request: Request, force: bool = False):
    user = actor(request)
    try:
        return projects.unlock(pid, user["id"], force=force and user.get("role") == "admin")
    except projects.Locked as e:
        raise HTTPException(423, str(e)) from e
    except KeyError as e:
        raise HTTPException(404, "project not found") from e


# ── preview frames ───────────────────────────────────────────────────


class FrameRequest(BaseModel):
    timeline: dict
    f: int = 0
    height: int = 360


def _jpeg(tl: Timeline, f: int, height: int) -> bytes:
    h = max(90, min(height, tl.height))
    w = int(round(tl.width * h / tl.height / 2) * 2)
    img = export.still(tl, f, width=w, height=h)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82)
    return buf.getvalue()


@router.post("/api/render/frame")
async def render_frame(body: FrameRequest):
    tl = _timeline(body.timeline)
    try:
        data = await run_in_threadpool(_jpeg, tl, body.f, body.height)
    except (RenderError, MediaError) as e:
        raise HTTPException(422, str(e)) from e
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/api/projects/{pid}/frame")
async def project_frame(pid: str, f: int = 0, height: int = 360):
    tl = _timeline(_project(pid)["data"])
    try:
        data = await run_in_threadpool(_jpeg, tl, f, height)
    except (RenderError, MediaError) as e:
        raise HTTPException(422, str(e)) from e
    return Response(data, media_type="image/jpeg")


# ── renders ──────────────────────────────────────────────────────────


class RenderRequest(BaseModel):
    preset: str = "h264"
    timeline: dict | None = None      # render unsaved state; defaults to the saved project
    name: str | None = None


def start_render(tl: Timeline, *, preset: str, project_id: str | None, name: str, parent_id: str | None = None,
                 extra_params: dict | None = None) -> dict:
    spec = export.PRESETS.get(preset)
    if not spec:
        raise HTTPException(400, f"unknown preset '{preset}'")
    if tl.duration <= 0:
        raise HTTPException(400, "timeline is empty")

    async def body(h: jobs.JobHandle) -> dict:
        canceled = {"v": False}

        def progress(p: float, stage: str) -> None:
            job = jobs.get_job(h.id)
            if job and job["status"] == "canceled":
                canceled["v"] = True
            h.progress(p)

        with tempfile.TemporaryDirectory(prefix="champ-out-") as tmp:
            out = str(Path(tmp) / f"render{spec['ext']}")
            h.stage("rendering", 0.01)
            info = await asyncio.to_thread(export.render, tl, out, preset=preset, progress=progress,
                                           cancel=lambda: canceled["v"])
            asset = library.save_asset_file(
                out, spec["mime"], prompt=name, tool="render",
                params={"preset": preset, "project_id": project_id, **info, **(extra_params or {})},
                parent_id=parent_id)
        db.audit("render", project_id or "", {"preset": preset, "asset": asset["id"]})
        return {"asset": asset, **info}

    return jobs.spawn("render", body, payload={"preset": preset, "name": name}, project_id=project_id)


@router.get("/api/render/presets")
async def presets():
    return {"presets": {k: {"label": v["label"], "ext": v["ext"]} for k, v in export.PRESETS.items()}}


@router.post("/api/projects/{pid}/render", dependencies=[editor_only])
async def render_project(pid: str, body: RenderRequest):
    p = _project(pid)
    tl = _timeline(body.timeline or p["data"])
    job = start_render(tl, preset=body.preset, project_id=pid, name=body.name or p["name"])
    return {"job_id": job["id"], "status": job["status"]}


@router.post("/api/render", dependencies=[editor_only])
async def render_timeline(body: RenderRequest):
    if not body.timeline:
        raise HTTPException(400, "timeline required")
    job = start_render(_timeline(body.timeline), preset=body.preset, project_id=None, name=body.name or "Render")
    return {"job_id": job["id"], "status": job["status"]}


# ── captions export ──────────────────────────────────────────────────


@router.get("/api/projects/{pid}/captions.{fmt}")
async def captions(pid: str, fmt: str):
    tl = _timeline(_project(pid)["data"])
    if fmt == "srt":
        return Response(export.to_srt(tl), media_type="application/x-subrip",
                        headers={"Content-Disposition": f'attachment; filename="{pid}.srt"'})
    if fmt == "vtt":
        return Response(export.to_vtt(tl), media_type="text/vtt")
    raise HTTPException(404, "use .srt or .vtt")


# ── interchange (OTIO / Premiere XML / EDL) ──────────────────────────


@router.get("/api/interchange/formats")
async def formats():
    return {"formats": otio_io.available_formats()}


@router.get("/api/projects/{pid}/interchange")
async def export_interchange(pid: str, format: str = "fcp_xml", package: bool = True):
    p = _project(pid)
    if format not in otio_io.available_formats():
        raise HTTPException(400, f"format '{format}' not available")
    tl = _timeline(p["data"])
    name = p["name"]
    if package:
        data = await run_in_threadpool(otio_io.export_package, tl, format, name=name)
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{pid}-{format}.zip"'})
    text = otio_io.export_string(tl, format, name=name)
    spec = otio_io.FORMATS[format]
    return Response(text, media_type=spec["mime"],
                    headers={"Content-Disposition": f'attachment; filename="{pid}{spec["ext"]}"'})


@router.post("/api/projects/import", dependencies=[editor_only])
async def import_interchange(request: Request, file: UploadFile = File(...), format: str = "auto"):
    raw = (await file.read()).decode("utf-8", errors="replace")
    name = file.filename or "Imported sequence"
    fmt = format
    if fmt == "auto":
        lower = name.lower()
        fmt = "otio" if lower.endswith(".otio") else "edl" if lower.endswith(".edl") else "fcp_xml"
    try:
        tl = otio_io.import_string(raw, fmt)
    except Exception as e:  # noqa: BLE001 — adapter errors are user-facing
        raise HTTPException(422, f"could not read {fmt}: {e}") from e
    return projects.create(Path(name).stem, "edit", tl.model_dump(by_alias=True), actor=actor(request)["id"])


# ── ingest + transcripts ─────────────────────────────────────────────


@router.post("/api/assets/{asset_id}/ingest")
async def ingest_asset(asset_id: str):
    if not library.get_asset(asset_id):
        raise HTTPException(404, "asset not found")
    st = ingest.status(asset_id)
    if st["ready"]:
        return {"status": st, "job_id": None}

    async def body(h: jobs.JobHandle) -> dict:
        return await asyncio.to_thread(ingest.ingest, asset_id, lambda p, s: h.stage(s, p))

    job = jobs.spawn("ingest", body, payload={"asset_id": asset_id})
    return {"status": st, "job_id": job["id"]}


@router.get("/api/assets/{asset_id}/media")
async def media_status(asset_id: str):
    if not library.get_asset(asset_id):
        raise HTTPException(404, "asset not found")
    return ingest.status(asset_id)


class TranscribeRequest(BaseModel):
    language: str | None = None
    force: bool = False
    keyterms: list[str] | None = None


@router.post("/api/assets/{asset_id}/transcribe")
async def transcribe_asset(asset_id: str, body: TranscribeRequest):
    if not library.get_asset(asset_id):
        raise HTTPException(404, "asset not found")

    async def run(h: jobs.JobHandle) -> dict:
        h.stage("transcribing", 0.1)
        t = await transcribe.transcribe_asset(asset_id, language=body.language, force=body.force,
                                              keyterms=body.keyterms)
        return {"words": len(t["words"]), "text": t["text"][:500]}

    job = jobs.spawn("transcribe", run, payload={"asset_id": asset_id})
    return {"job_id": job["id"]}


@router.get("/api/assets/{asset_id}/transcript")
async def get_transcript(asset_id: str):
    p = ingest.derived_path(asset_id, "transcript.json")
    if not p:
        raise HTTPException(404, "no transcript yet")
    return Response(p.read_text(), media_type="application/json")
