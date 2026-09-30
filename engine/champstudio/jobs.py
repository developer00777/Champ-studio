"""In-process video job registry.

The engine owns provider polling so every client (web, Mac app) just polls
`GET /api/videos/jobs/{id}` and stays identical. Completed videos are
downloaded straight into the library.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from . import library
from .providers.base import ProviderError

log = logging.getLogger("champstudio.jobs")

POLL_INTERVAL_S = 5
POLL_TIMEOUT_S = 1200  # 20 min

_jobs: dict[str, dict] = {}


def redact(value):
    """Copy of `value` with inline data URLs replaced, for storage in asset params.

    Base64 frames and audio are megabytes each; the library only needs to know
    that something inline was sent, never the bytes.
    """
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str) and value.startswith("data:"):
        return "<inline data>"
    return value


def get_job(job_id: str) -> dict | None:
    return _jobs.get(job_id)


def list_jobs(limit: int = 50) -> list[dict]:
    return sorted(_jobs.values(), key=lambda j: j["created_at"], reverse=True)[:limit]


async def start_video_job(
    client,
    *,
    prompt: str,
    model: str,
    parent_id: str | None = None,
    submit_kwargs: dict,
    kind: str = "video",
    extra_params: dict | None = None,
) -> dict:
    """Submit to the provider and spawn a background poller. Returns the job record.

    `kind` tags the job for clients ("video" from Animate, "lipsync" from Lip Sync);
    `extra_params` is recorded on the resulting asset next to the redacted submit kwargs.
    """
    provider_job_id = await client.submit_video(prompt=prompt, model=model, **submit_kwargs)
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "kind": kind,
        "status": "pending",
        "provider": client.provider,
        "provider_job_id": provider_job_id,
        "model": model,
        "prompt": prompt,
        "parent_id": parent_id,
        "asset": None,
        "error": None,
        "created_at": time.time(),
    }
    _jobs[job_id] = job
    params = {**redact(submit_kwargs), **(extra_params or {}), "kind": kind}
    asyncio.create_task(_poll_loop(client, job, params))
    return job


async def _poll_loop(client, job: dict, params: dict) -> None:
    start = time.time()
    try:
        while time.time() - start < POLL_TIMEOUT_S:
            await asyncio.sleep(POLL_INTERVAL_S)
            state = await client.poll_video(job["provider_job_id"])
            status = state.get("status", "pending")
            if status == "completed":
                data = await client.download(state["url"])
                asset = library.save_asset(
                    data,
                    "video/mp4",
                    prompt=job["prompt"],
                    model=job["model"],
                    provider=job["provider"],
                    params=params,
                    parent_id=job.get("parent_id"),
                    cost=state.get("cost"),
                )
                job.update(status="completed", asset=asset)
                return
            if status == "failed":
                job.update(status="failed", error=state.get("error", "generation failed"))
                return
            job["status"] = status
        job.update(status="failed", error=f"timed out after {POLL_TIMEOUT_S}s")
    except ProviderError as e:
        job.update(status="failed", error=str(e))
    except Exception as e:  # noqa: BLE001 — job must always resolve
        log.exception("video job %s crashed", job["id"])
        job.update(status="failed", error=f"internal error: {e}")
