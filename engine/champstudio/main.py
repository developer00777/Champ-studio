"""Champ Studio Engine — FastAPI app.

Serves the REST API for every client (web UI, macOS app, Docker) and the
bundled web UI at /.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import config, jobs, library, transforms
from .providers.base import ProviderError
from .providers.muapi import MuAPIClient
from .providers.openrouter import OpenRouterClient

log = logging.getLogger("champstudio")

WEB_DIR = Path(os.environ.get("CHAMP_WEB_DIR", Path(__file__).resolve().parents[2] / "web"))

app = FastAPI(title="Champ Studio Engine", version=config.ENGINE_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── optional shared-deployment token gate ────────────────────────────


async def require_access(request: Request) -> None:
    token = config.access_token()
    if not token:
        return
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if supplied != token:
        raise HTTPException(401, "Missing or invalid access token")


guarded = Depends(require_access)


@app.exception_handler(ProviderError)
async def provider_error_handler(_req, exc: ProviderError):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


def _client(provider: str | None):
    """Provider selection: explicit > openrouter if keyed > muapi if keyed."""
    if provider == "openrouter":
        return OpenRouterClient()
    if provider == "muapi":
        return MuAPIClient()
    if config.get_api_key("openrouter"):
        return OpenRouterClient()
    if config.get_api_key("muapi"):
        return MuAPIClient()
    raise ProviderError("No provider API key configured — add one in Settings")


# ── health + settings ────────────────────────────────────────────────


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "name": "Champ Studio Engine",
        "version": config.ENGINE_VERSION,
        "keys": {
            "openrouter": bool(config.get_api_key("openrouter")),
            "muapi": bool(config.get_api_key("muapi")),
        },
    }


class SettingsUpdate(BaseModel):
    keys: dict[str, str] | None = None
    defaults: dict | None = None


@app.get("/api/settings", dependencies=[guarded])
async def read_settings():
    return config.get_settings()


@app.put("/api/settings", dependencies=[guarded])
async def write_settings(body: SettingsUpdate):
    config.update_settings(keys=body.keys, defaults=body.defaults)
    return config.get_settings()


# ── models ───────────────────────────────────────────────────────────


@app.get("/api/models/images", dependencies=[guarded])
async def image_models():
    return await _merged_models("image")


@app.get("/api/models/videos", dependencies=[guarded])
async def video_models():
    return await _merged_models("video")


async def _merged_models(kind: str) -> list[dict]:
    out: list[dict] = []
    for provider, cls in (("openrouter", OpenRouterClient), ("muapi", MuAPIClient)):
        if not config.get_api_key(provider):
            continue
        try:
            client = cls()
            models = await (
                client.list_image_models() if kind == "image" else client.list_video_models()
            )
            out.extend(models)
        except ProviderError as e:
            log.warning("model list (%s): %s", provider, e)
    return out


# ── images ───────────────────────────────────────────────────────────


class ImageRequest(BaseModel):
    prompt: str = Field(min_length=1)
    model: str = "google/gemini-2.5-flash-image"
    provider: str | None = None
    n: int = Field(default=1, ge=1, le=10)
    aspect_ratio: str | None = None
    resolution: str | None = None
    output_format: str | None = None       # png | jpeg | webp | svg
    background: str | None = None          # auto | transparent | opaque
    seed: int | None = None
    input_asset_ids: list[str] = []


@app.post("/api/images", dependencies=[guarded])
async def generate_images(body: ImageRequest):
    client = _client(body.provider)

    input_references = []
    for asset_id in body.input_asset_ids:
        path = library.asset_path(asset_id)
        if not path:
            raise HTTPException(404, f"input asset {asset_id} not found")
        asset = library.get_asset(asset_id)
        b64 = base64.b64encode(path.read_bytes()).decode()
        input_references.append(f"data:{asset['mime']};base64,{b64}")

    media = await client.generate_images(
        prompt=body.prompt,
        model=body.model,
        n=body.n,
        aspect_ratio=body.aspect_ratio,
        resolution=body.resolution,
        output_format=body.output_format,
        background=body.background,
        seed=body.seed,
        input_references=input_references or None,
    )
    parent = body.input_asset_ids[0] if body.input_asset_ids else None
    assets = [
        library.save_asset(
            m.data,
            m.mime,
            prompt=body.prompt,
            model=body.model,
            provider=m.provider,
            params=body.model_dump(exclude={"prompt", "model"}, exclude_none=True),
            parent_id=parent,
            cost=m.cost,
        )
        for m in media
        if m.data
    ]
    return {"assets": assets}


# ── videos (async jobs) ──────────────────────────────────────────────


class VideoRequest(BaseModel):
    prompt: str = Field(min_length=1)
    model: str = "google/veo-3.1"
    provider: str | None = None
    duration: int | None = None
    aspect_ratio: str | None = None
    resolution: str | None = None
    generate_audio: bool | None = None
    seed: int | None = None
    image_asset_id: str | None = None      # image-to-video: use as first frame


@app.post("/api/videos", dependencies=[guarded])
async def generate_video(body: VideoRequest):
    client = _client(body.provider)

    submit_kwargs: dict = {
        k: v
        for k, v in dict(
            duration=body.duration,
            aspect_ratio=body.aspect_ratio,
            resolution=body.resolution,
            generate_audio=body.generate_audio,
            seed=body.seed,
        ).items()
        if v is not None
    }

    if body.image_asset_id:
        path = library.asset_path(body.image_asset_id)
        if not path:
            raise HTTPException(404, f"asset {body.image_asset_id} not found")
        asset = library.get_asset(body.image_asset_id)
        data_url = f"data:{asset['mime']};base64,{base64.b64encode(path.read_bytes()).decode()}"
        if client.provider == "openrouter":
            submit_kwargs["frame_images"] = [{"url": data_url, "frame_type": "first_frame"}]
        else:
            submit_kwargs["image_url"] = data_url

    job = await jobs.start_video_job(
        client,
        prompt=body.prompt,
        model=body.model,
        parent_id=body.image_asset_id,
        submit_kwargs=submit_kwargs,
    )
    return {"job_id": job["id"], "status": job["status"]}


@app.get("/api/videos/jobs", dependencies=[guarded])
async def video_jobs():
    return {"jobs": jobs.list_jobs()}


@app.get("/api/videos/jobs/{job_id}", dependencies=[guarded])
async def video_job(job_id: str):
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


# ── transforms (Derivative Lab) ──────────────────────────────────────


@app.get("/api/transform", dependencies=[guarded])
async def transform_catalog():
    return {"tools": transforms.catalog()}


class TransformRequest(BaseModel):
    asset_id: str
    params: dict = {}


@app.post("/api/transform/{tool}", dependencies=[guarded])
async def run_transform(tool: str, body: TransformRequest):
    fn = transforms.get_tool(tool)
    if not fn:
        raise HTTPException(404, f"unknown tool '{tool}'")
    path = library.asset_path(body.asset_id)
    if not path:
        raise HTTPException(404, f"asset {body.asset_id} not found")
    source = library.get_asset(body.asset_id)
    if source["kind"] != "image":
        raise HTTPException(
            400,
            "transforms take raster images — export/rasterize vectors before re-processing",
        )

    try:
        outputs = await run_in_threadpool(fn, path.read_bytes(), body.params)
    except Exception as e:  # noqa: BLE001 — surface tool failures cleanly
        log.exception("transform %s failed", tool)
        raise HTTPException(500, f"{tool} failed: {e}") from e

    assets = [
        library.save_asset(
            data,
            mime,
            prompt=source.get("prompt", ""),
            tool=tool,
            params=body.params,
            parent_id=body.asset_id,
        )
        for data, mime in outputs
    ]
    return {"assets": assets}


# ── library ──────────────────────────────────────────────────────────


@app.get("/api/assets", dependencies=[guarded])
async def assets(kind: str | None = None, limit: int = 200, offset: int = 0):
    return {"assets": library.list_assets(kind=kind, limit=limit, offset=offset)}


@app.post("/api/assets/upload", dependencies=[guarded])
async def upload_asset(file: UploadFile = File(...)):
    mime = file.content_type or "image/png"
    if not (mime.startswith("image/") or mime.startswith("video/")):
        raise HTTPException(400, "only image or video uploads")
    data = await file.read()
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(413, "file too large (100 MB max)")
    return library.save_asset(data, mime, tool="upload", params={"filename": file.filename})


@app.get("/api/assets/{asset_id}", dependencies=[guarded])
async def asset_detail(asset_id: str):
    a = library.get_asset(asset_id)
    if not a:
        raise HTTPException(404, "asset not found")
    return {**a, "lineage": library.lineage(asset_id)}


@app.get("/api/assets/{asset_id}/file")
async def asset_file(asset_id: str):
    # Unguarded: media needs to load in <img>/<video> tags without headers.
    path = library.asset_path(asset_id)
    if not path:
        raise HTTPException(404, "asset not found")
    a = library.get_asset(asset_id)
    return FileResponse(path, media_type=a["mime"], filename=a["filename"])


@app.delete("/api/assets/{asset_id}", dependencies=[guarded])
async def remove_asset(asset_id: str):
    if not library.delete_asset(asset_id):
        raise HTTPException(404, "asset not found")
    return {"deleted": asset_id}


# ── web UI ───────────────────────────────────────────────────────────

if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("CHAMP_PORT", config.DEFAULT_PORT))
    host = os.environ.get("CHAMP_HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
