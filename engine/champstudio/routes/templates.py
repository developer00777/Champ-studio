"""Template library API: gallery, previews, AI fill, compose, batch, save-as-template."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .. import brands, db, jobs, library, projects, templates
from ..config import data_dir
from ..deps import actor, guarded, require_role
from ..render import export
from ..timeline import ASPECTS as CANVAS_ASPECTS, Timeline

router = APIRouter(dependencies=[guarded])
editor_only = require_role("editor")


def _template(tid: str) -> dict:
    try:
        return templates.get(tid)
    except KeyError as e:
        raise HTTPException(404, "template not found") from e


@router.get("/api/templates")
async def gallery(category: str | None = None, q: str | None = None, aspect: str | None = None):
    items = [templates.summary(t) for t in templates.all_templates().values()]
    if category:
        items = [t for t in items if t["category"] == category]
    if aspect:
        items = [t for t in items if not t["aspects"] or aspect in t["aspects"]]
    if q:
        ql = q.lower()
        items = [t for t in items if ql in json.dumps(t).lower()]
    items.sort(key=lambda t: (list(templates.CATEGORIES).index(t["category"]) if t["category"] in templates.CATEGORIES
                              else 99, t["name"]))
    return {"templates": items, "categories": templates.CATEGORIES}


@router.get("/api/templates/{tid}")
async def template_detail(tid: str):
    t = _template(tid)
    return {**t, "llm_schema": templates.llm_schema(t)}


def _preview_bytes(t: dict, aspect: str, brand_id: str | None, height: int) -> bytes:
    kit = brands.get(brand_id)
    key = hashlib.sha1(json.dumps([t, kit, aspect, height], sort_keys=True, default=str).encode()).hexdigest()[:16]
    d = data_dir() / "template_previews"
    d.mkdir(exist_ok=True)
    p = d / f"{t['id']}-{key}.jpg"
    if p.exists():
        return p.read_bytes()
    tl = templates.preview_timeline(t, kit, aspect)
    first = tl.tracks[0].clips[0] if tl.tracks and tl.tracks[0].clips else None
    f = int((first.end if first else tl.duration) * 0.7) if tl.duration else 0
    w = int(round(tl.width * height / tl.height / 2) * 2)
    img = export.still(tl, f, width=w, height=height)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    p.write_bytes(buf.getvalue())
    return buf.getvalue()


@router.get("/api/templates/{tid}/preview.jpg")
async def preview(tid: str, aspect: str = "16:9", brand: str | None = None, height: int = 270):
    t = _template(tid)
    data = await run_in_threadpool(_preview_bytes, t, aspect, brand, max(120, min(height, 720)))
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=300"})


class FillRequest(BaseModel):
    brief: str = Field(min_length=3, max_length=4000)
    brand_kit_id: str | None = None


@router.post("/api/templates/{tid}/fill", dependencies=[editor_only])
async def fill(tid: str, body: FillRequest):
    t = _template(tid)
    values = await templates.ai_fill(t, body.brief, brands.get(body.brand_kit_id))
    return {"values": values}


class ComposeRequest(BaseModel):
    values: dict = {}
    brief: str | None = None                 # fill with AI first when given
    brand_kit_id: str | None = None
    aspects: list[str] = ["16:9"]
    voiceover: bool = True
    allow_video: bool = False
    music: bool = True
    render: bool = True
    preset: str = "h264"
    name: str | None = None


async def compose_job(h: jobs.JobHandle, t: dict, body: ComposeRequest, owner: str) -> dict:
    kit = brands.get(body.brand_kit_id)
    values = dict(body.values)
    if body.brief:
        h.stage("filling slots with AI", 0.05)
        values = {**await templates.ai_fill(t, body.brief, kit), **values}
    errors = templates.validate_values(t, templates.with_defaults(t, values))
    if errors:
        raise templates.TemplateError("; ".join(errors[:5]))
    h.stage("generating media and voiceover", 0.15)
    prepared = await templates.prepare(t, values, kit, aspect=body.aspects[0], voiceover=body.voiceover,
                                       allow_video=body.allow_video, music=body.music)
    out: dict = {"values": prepared["values"], "projects": {}, "renders": {}}
    for i, aspect in enumerate(body.aspects):
        tl = templates.compile_timeline(t, prepared, kit, aspect)
        name = f"{body.name or t['name']} ({aspect})"
        p = projects.create(name, "edit", tl.model_dump(by_alias=True), brand_kit_id=body.brand_kit_id, actor=owner)
        out["projects"][aspect] = p["id"]
        if body.render:
            h.stage(f"rendering {aspect}", 0.4 + 0.6 * i / len(body.aspects))
            spec = export.PRESETS[body.preset]
            with tempfile.TemporaryDirectory() as tmp:
                path = str(Path(tmp) / f"out{spec['ext']}")
                await asyncio.to_thread(export.render, tl, path, preset=body.preset)
                asset = library.save_asset_file(path, spec["mime"], prompt=name, tool="template",
                                                params={"template": t["id"], "aspect": aspect, "project_id": p["id"]})
            out["renders"][aspect] = asset["id"]
    db.audit("template.compose", t["id"], {"aspects": body.aspects}, actor=owner)
    return out


@router.post("/api/templates/{tid}/compose", dependencies=[editor_only])
async def compose(tid: str, body: ComposeRequest, request: Request):
    t = _template(tid)
    bad = [a for a in body.aspects if a not in templates.ASPECTS]
    if bad or not body.aspects:
        raise HTTPException(400, f"aspects must be from {templates.ASPECTS}")
    if body.preset not in export.PRESETS:
        raise HTTPException(400, "unknown preset")
    owner = actor(request)["id"]
    job = jobs.spawn("template.compose", lambda h: compose_job(h, t, body, owner),
                     payload={"template": tid, "aspects": body.aspects})
    return {"job_id": job["id"]}


class BatchRequest(BaseModel):
    rows: list[dict] | None = None
    csv: str | None = None
    brand_kit_id: str | None = None
    aspect: str = "16:9"
    preset: str = "h264"
    voiceover: bool = True
    music: bool = True
    max_rows: int = Field(200, le=1000)


def _merge(text: str, row: dict) -> str:
    return re.sub(r"\{([a-zA-Z0-9_ ]+)\}", lambda m: str(row.get(m.group(1).strip(), m.group(0))), text)


@router.post("/api/templates/{tid}/batch", dependencies=[editor_only])
async def batch(tid: str, body: BatchRequest, request: Request):
    """Personalized videos at scale: one render per row. Row keys are slot names;
    any other columns (first_name, company…) merge into text as {first_name}."""
    t = _template(tid)
    rows = list(body.rows or [])
    if body.csv:
        rows += list(csv.DictReader(io.StringIO(body.csv)))
    if not rows:
        raise HTTPException(400, "no rows")
    rows = rows[: body.max_rows]
    owner = actor(request)["id"]
    slot_names = set(t["slots"]["properties"])
    base = templates.with_defaults(t, {})

    async def run(h: jobs.JobHandle) -> dict:
        kit = brands.get(body.brand_kit_id)
        results = []
        shared_music = None
        for i, row in enumerate(rows):
            h.stage(f"row {i + 1}/{len(rows)}", i / len(rows))
            values = {k: (_merge(v, row) if isinstance(v, str) else v) for k, v in base.items()}
            values.update({k: v for k, v in row.items() if k in slot_names and v not in (None, "")})
            try:
                prepared = await templates.prepare(t, values, kit, aspect=body.aspect, voiceover=body.voiceover,
                                                   music=body.music and shared_music is None)
                if prepared.get("music_id"):
                    shared_music = prepared["music_id"]
                prepared["music_id"] = prepared.get("music_id") or shared_music
                tl = templates.compile_timeline(t, prepared, kit, body.aspect)
                spec = export.PRESETS[body.preset]
                with tempfile.TemporaryDirectory() as tmp:
                    path = str(Path(tmp) / f"out{spec['ext']}")
                    await asyncio.to_thread(export.render, tl, path, preset=body.preset)
                    label = row.get("name") or row.get("company") or row.get("first_name") or f"row {i + 1}"
                    asset = library.save_asset_file(path, spec["mime"], prompt=f"{t['name']} — {label}",
                                                    tool="template-batch", params={"template": tid, "row": row})
                results.append({"row": i, "asset_id": asset["id"], "label": label})
            except Exception as e:  # noqa: BLE001 — one bad row must not stop the batch
                results.append({"row": i, "error": str(e)})
        db.audit("template.batch", tid, {"rows": len(rows)}, actor=owner)
        return {"results": results, "ok": sum(1 for r in results if "asset_id" in r)}

    job = jobs.spawn("template.batch", run, payload={"template": tid, "rows": len(rows)})
    return {"job_id": job["id"], "rows": len(rows)}


class GraphicRequest(BaseModel):
    values: dict = {}
    brand_kit_id: str | None = None
    aspect: str = "16:9"
    start: int = 0


@router.post("/api/templates/{tid}/graphic")
async def graphic(tid: str, body: GraphicRequest):
    """Compile a template into editor clips without generating anything
    (Essential Graphics / MOGRT-style insertion of titles and overlays)."""
    t = _template(tid)
    kit = brands.get(body.brand_kit_id)
    values = templates.with_defaults(t, body.values)
    tl = templates.compile_timeline(t, {"values": values, "vo": {}, "music_id": None}, kit, body.aspect)
    tracks = []
    for tr in tl.video_tracks():
        clips = []
        for c in tr.clips:
            c.start += body.start
            clips.append(c.model_dump(by_alias=True))
        tracks.append({"id": tr.id, "clips": clips})
    return {"tracks": tracks, "duration": tl.duration}


class SaveTemplate(BaseModel):
    template: dict


@router.post("/api/templates", dependencies=[editor_only])
async def save_template(body: SaveTemplate, request: Request):
    try:
        t = templates.save_user_template(body.template)
    except templates.TemplateError as e:
        raise HTTPException(422, str(e)) from e
    db.audit("template.save", t["id"], {}, actor=actor(request)["id"])
    return t


class FromProject(BaseModel):
    project_id: str
    name: str
    category: str = "brand-utility"
    description: str = ""


@router.post("/api/templates/from-project", dependencies=[editor_only])
async def from_project(body: FromProject, request: Request):
    p = projects.get(body.project_id)
    if not p:
        raise HTTPException(404, "project not found")
    t = template_from_timeline(Timeline(**p["data"]), body.name, body.category, body.description)
    try:
        saved = templates.save_user_template(t)
    except templates.TemplateError as e:
        raise HTTPException(422, str(e)) from e
    db.audit("template.from_project", saved["id"], {"project": body.project_id}, actor=actor(request)["id"])
    return saved


@router.delete("/api/templates/{tid}", dependencies=[require_role("admin")])
async def delete_template(tid: str):
    if not templates.delete_user_template(tid):
        raise HTTPException(404, "only team templates can be deleted")
    return {"deleted": tid}


def template_from_timeline(tl: Timeline, name: str, category: str, description: str = "") -> dict:
    """Every text clip becomes a text slot, every image/video clip a media slot;
    timing and layout are kept as one scene."""
    fps = tl.fps
    props: dict = {}
    defaults: dict = {}
    layers = []
    n_text = n_media = 0
    for track in tl.video_tracks():
        for c in sorted(track.clips, key=lambda c: c.start):
            layer: dict = {"box": {"*": [round(v, 4) for v in c.box]}, "start": round(c.start / fps, 3),
                           "end": round(c.end / fps, 3), "name": c.name or c.type}
            for k in ("anim_in", "anim_out"):
                a = getattr(c, k)
                if a:
                    layer[k] = {"type": a.type, "sec": round(a.duration / fps, 3)}
            if c.type == "text":
                n_text += 1
                slot = f"text_{n_text}"
                props[slot] = {"type": "string", "maxLength": max(20, len(c.text) * 2), "x-kind": "text",
                               "description": c.name or f"Text {n_text}"}
                defaults[slot] = c.text
                layer.update(type="text", text="{{" + slot + "}}", style=c.style.model_dump(exclude_none=True))
            elif c.type in ("image", "video") and c.asset_id:
                n_media += 1
                slot = f"media_{n_media}"
                props[slot] = {"x-kind": "media", "description": c.name or f"Media {n_media}"}
                defaults[slot] = {"asset_id": c.asset_id}
                layer.update(type="media", src={"$slot": slot}, fit=c.fit, motion="none")
            elif c.type == "shape":
                layer.update(type="shape", shape=c.shape.model_dump())
            elif c.type == "color":
                layer.update(type="color", color=c.color)
            else:
                continue
            layers.append(layer)
    tid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "team-template"
    aspect = next((a for a, (w, h) in CANVAS_ASPECTS.items() if abs(w / h - tl.width / tl.height) < 0.01), "16:9")
    return {"cst": "1.0", "id": f"team-{tid}", "name": name, "category": category,
            "description": description or "Saved from a project", "tags": ["team"], "aspects": [aspect],
            "slots": {"type": "object", "required": [], "properties": props}, "defaults": defaults,
            "background": tl.background,
            "scenes": [{"id": "main", "duration": {"mode": "fixed", "sec": round(tl.duration / fps, 3)},
                        "layers": layers}],
            "captions": {"enabled": False}}
