"""Champ Studio Engine — FastAPI app.

Serves the REST API for every client (web UI, macOS app, Docker) and the
bundled web UI at /.
"""

from __future__ import annotations

import base64
import logging
import os
import shutil
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from . import auth, config, costs, ingest, jobs, library, roles, transforms
from .deps import guarded
from .providers.base import ProviderError
from .providers.muapi import MuAPIClient
from .providers.openrouter import OpenRouterClient

log = logging.getLogger("champstudio")

def _default_web_dir() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller bundle
        return Path(getattr(sys, "_MEIPASS", ".")) / "web"
    return Path(__file__).resolve().parents[2] / "web"


WEB_DIR = Path(os.environ.get("CHAMP_WEB_DIR") or _default_web_dir())

@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        n = await jobs.resume_after_restart(_client)
        if n:
            log.info("resumed %d provider job(s) after restart", n)
    except Exception:  # noqa: BLE001 — never block startup on job recovery
        log.exception("job recovery failed")
    yield


app = FastAPI(title="Champ Studio Engine", version=config.ENGINE_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ProviderError)
async def provider_error_handler(_req, exc: ProviderError):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(costs.BudgetExceeded)
async def budget_error_handler(_req, exc: costs.BudgetExceeded):
    return JSONResponse(status_code=402, content={"detail": str(exc)})


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
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "sso": auth.oidc_enabled(),
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


@app.get("/api/models/speech", dependencies=[guarded])
async def speech_models():
    if not config.get_api_key("openrouter"):
        return []
    try:
        return await OpenRouterClient().list_speech_models()
    except ProviderError as e:
        log.warning("model list (speech): %s", e)
        return []


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
    model: str = Field(default_factory=lambda: roles.model_for("image"))
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
    total = sum(m.cost or 0 for m in media[:1])  # OpenRouter reports one cost for the request
    if total:
        costs.record(kind="image", model=body.model, cost=total, role="image")
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


# ── audio (text-to-speech) ───────────────────────────────────────────

DEFAULT_SPEECH_MODEL = "google/gemini-3.8-flash-tts"


class AudioRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    model: str = DEFAULT_SPEECH_MODEL
    voice: str | None = None
    speed: float | None = Field(default=None, ge=0.25, le=4.0)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text must not be blank")
        return v.strip()


@app.post("/api/audio", dependencies=[guarded])
async def generate_audio(body: AudioRequest):
    client = OpenRouterClient()  # v1: speech is OpenRouter-only
    media = await client.synthesize_speech(
        text=body.text, model=body.model, voice=body.voice, speed=body.speed
    )
    costs.record(kind="speech", model=body.model, role="speech",
                 cost=media.cost if media.cost is not None else costs.estimate_speech(len(body.text)),
                 estimated=media.cost is None)
    params = body.model_dump(exclude={"text", "model"}, exclude_none=True)
    if media.meta.get("generation_id"):
        params["generation_id"] = media.meta["generation_id"]
    asset = library.save_asset(
        media.data,
        media.mime,
        prompt=body.text,
        model=body.model,
        provider=media.provider,
        params=params,
        cost=media.cost,
    )
    return {"assets": [asset]}


# ── videos (async jobs) ──────────────────────────────────────────────


def _asset_data_url(asset_id: str, expect_kind: str) -> tuple[dict, str]:
    """Load a library asset as a base64 data URL for provider payloads."""
    asset = library.get_asset(asset_id)
    path = library.asset_path(asset_id)
    if not asset or not path:
        raise HTTPException(404, f"asset {asset_id} not found")
    if asset["kind"] != expect_kind:
        raise HTTPException(400, f"asset {asset_id} is {asset['kind']}, expected {expect_kind}")
    b64 = base64.b64encode(path.read_bytes()).decode()
    return asset, f"data:{asset['mime']};base64,{b64}"


class VideoRequest(BaseModel):
    prompt: str = Field(min_length=1)
    model: str = Field(default_factory=lambda: roles.model_for("video_draft"))
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
    costs.check_budget(costs.estimate_video(body.model, body.duration or 8, body.resolution,
                                            bool(body.generate_audio)))

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
        _, data_url = _asset_data_url(body.image_asset_id, "image")
        if client.provider == "openrouter":
            submit_kwargs["first_frame"] = data_url
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


# ── lip sync (portrait + voice track → talking head) ─────────────────

DEFAULT_LIPSYNC_MODEL = "heygen/avatar-iv"


class LipSyncRequest(BaseModel):
    image_asset_id: str
    audio_asset_id: str
    model: str = DEFAULT_LIPSYNC_MODEL
    aspect_ratio: str | None = None
    resolution: str | None = None
    prompt: str | None = None   # optional motion/expression direction


@app.post("/api/lipsync", dependencies=[guarded])
async def lipsync(body: LipSyncRequest):
    client = OpenRouterClient()  # v1: lip sync is OpenRouter-only (heygen/avatar-iv)
    try:
        from .render import media as _media

        seconds = _media.probe(str(library.asset_path(body.audio_asset_id)))["duration"]
    except Exception:  # noqa: BLE001 — estimate only
        seconds = 30
    costs.check_budget(costs.estimate_video(body.model, seconds, body.resolution))
    _, image_url = _asset_data_url(body.image_asset_id, "image")
    _, audio_url = _asset_data_url(body.audio_asset_id, "audio")
    submit_kwargs: dict = {"input_references": [image_url], "audio_references": [audio_url]}
    if body.aspect_ratio:
        submit_kwargs["aspect_ratio"] = body.aspect_ratio
    if body.resolution:
        submit_kwargs["resolution"] = body.resolution
    job = await jobs.start_video_job(
        client,
        prompt=body.prompt or "",
        model=body.model,
        parent_id=body.image_asset_id,
        submit_kwargs=submit_kwargs,
        kind="lipsync",
        extra_params={"audio_asset_id": body.audio_asset_id},
    )
    return {"job_id": job["id"], "status": job["status"]}


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


MAX_UPLOAD = int(os.environ.get("CHAMP_MAX_UPLOAD_MB", "4096")) * 1024 * 1024
_EXT_MIME = {"mov": "video/quicktime", "mkv": "video/x-matroska", "webm": "video/webm", "mp4": "video/mp4",
             "mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4", "aac": "audio/mp4", "ogg": "audio/ogg",
             "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}


@app.post("/api/assets/upload", dependencies=[guarded])
async def upload_asset(file: UploadFile = File(...)):
    mime = file.content_type or "application/octet-stream"
    name = (file.filename or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if ext == "cube":
        mime = "application/octet-stream"  # colour LUT for the editor
    elif not mime.startswith(("image/", "video/", "audio/")) and ext in _EXT_MIME:
        mime = _EXT_MIME[ext]
    elif not mime.startswith(("image/", "video/", "audio/")):
        raise HTTPException(400, "only image, video, audio or .cube uploads")
    # stream to disk: editor footage can be gigabytes
    tmp = tempfile.NamedTemporaryFile(delete=False, dir=config.data_dir(), suffix=".upload")
    size = 0
    try:
        with tmp:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, f"file too large ({MAX_UPLOAD // 1024 // 1024} MB max)")
                tmp.write(chunk)
        return library.save_asset_file(tmp.name, mime, tool="upload", params={"filename": file.filename})
    finally:
        Path(tmp.name).unlink(missing_ok=True)


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


@app.get("/api/assets/{asset_id}/derived/{name}")
async def asset_derived(asset_id: str, name: str):
    # Unguarded like /file: proxies and thumbnails load in <video>/<img> tags.
    path = ingest.derived_path(asset_id, name)
    if not path:
        raise HTTPException(404, "not generated yet")
    media_type = {"mp4": "video/mp4", "jpg": "image/jpeg", "json": "application/json"}[name.rsplit(".", 1)[-1]]
    return FileResponse(path, media_type=media_type)


@app.get("/api/fonts/file/{name}")
async def font_file(name: str):
    # Unguarded: @font-face requests can't carry auth headers; fonts are OFL/brand files.
    from .render import text as _text

    for d in _text._font_dirs():
        p = d / name
        if p.parent == d and p.suffix.lower() in (".ttf", ".otf") and p.exists():
            return FileResponse(p, media_type="font/ttf" if p.suffix.lower() == ".ttf" else "font/otf")
    raise HTTPException(404, "font not found")


@app.delete("/api/assets/{asset_id}", dependencies=[guarded])
async def remove_asset(asset_id: str):
    if not library.delete_asset(asset_id):
        raise HTTPException(404, "asset not found")
    return {"deleted": asset_id}


# ── feature routers ──────────────────────────────────────────────────

from .routes import ai as _ai_routes  # noqa: E402
from .routes import brands as _brand_routes  # noqa: E402
from .routes import core as _core_routes  # noqa: E402
from .routes import director as _director_routes  # noqa: E402
from .routes import projects as _project_routes  # noqa: E402
from .routes import review as _review_routes  # noqa: E402
from .routes import templates as _template_routes  # noqa: E402

app.include_router(auth.router)
for _r in (_core_routes, _project_routes, _brand_routes, _director_routes, _template_routes, _ai_routes,
           _review_routes):
    app.include_router(_r.router)

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
