"""Persistent job registry.

Every long-running thing — provider video generation, renders, ingest, Director
runs — is a row in the `jobs` table, so clients poll `GET /api/jobs/{id}` (or the
legacy `/api/videos/jobs/{id}`) and jobs survive engine restarts:

- provider video jobs resume polling on startup (the provider keeps working);
- local work that was mid-flight is marked failed ("interrupted by restart").

Completed videos are downloaded straight into the library.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from . import db, library
from .providers.base import ProviderError

log = logging.getLogger("champstudio.jobs")

POLL_INTERVAL_S = 5
POLL_TIMEOUT_S = 1200  # 20 min

_tasks: dict[str, asyncio.Task] = {}  # strong refs: un-referenced tasks can be GC'd mid-run

TERMINAL = ("completed", "failed", "canceled")


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


# ── rows ─────────────────────────────────────────────────────────────


def _row_to_job(r) -> dict:
    payload = db.loads(r["payload"], {}) or {}
    result = db.loads(r["result"], None)
    job = {
        **payload,
        "id": r["id"],
        "kind": r["kind"],
        "status": r["status"],
        "stage": r["stage"] or "",
        "progress": r["progress"] or 0,
        "error": r["error"],
        "project_id": r["project_id"],
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
        "result": result,
    }
    # legacy clients read `asset` straight off the job
    job["asset"] = (result or {}).get("asset") if isinstance(result, dict) else None
    return job


def create_job(kind: str, payload: dict | None = None, *, project_id: str | None = None,
               status: str = "pending") -> dict:
    job_id = db.new_id()
    ts = db.now()
    with db.conn() as c:
        c.execute(
            """INSERT INTO jobs (id, kind, status, stage, progress, payload, project_id,
               created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (job_id, kind, status, "", 0, db.dumps(redact(payload or {})), project_id, ts, ts),
        )
    return get_job(job_id)


def update_job(job_id: str, **fields) -> dict | None:
    """Update status/stage/progress/error/result; payload keys merge via `payload=`."""
    sets, args = [], []
    for k in ("status", "stage", "progress", "error"):
        if k in fields:
            sets.append(f"{k} = ?")
            args.append(fields[k])
    if "result" in fields:
        sets.append("result = ?")
        args.append(db.dumps(fields["result"]))
    if "payload" in fields:
        current = get_job(job_id)
        merged = {**{k: current[k] for k in _payload_keys(current)}, **redact(fields["payload"])}
        sets.append("payload = ?")
        args.append(db.dumps(merged))
    if not sets:
        return get_job(job_id)
    sets.append("updated_at = ?")
    args.append(db.now())
    args.append(job_id)
    with db.conn() as c:
        c.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", args)
    return get_job(job_id)


_ROW_KEYS = {"id", "kind", "status", "stage", "progress", "error", "project_id",
             "created_at", "updated_at", "result", "asset"}


def _payload_keys(job: dict) -> list[str]:
    return [k for k in job if k not in _ROW_KEYS]


def get_job(job_id: str) -> dict | None:
    with db.conn() as c:
        r = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(r) if r else None


def list_jobs(limit: int = 50, kind: str | None = None, project_id: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM jobs", []
    where = []
    if kind:
        where.append("kind = ?")
        args.append(kind)
    if project_id:
        where.append("project_id = ?")
        args.append(project_id)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    with db.conn() as c:
        rows = c.execute(q, args).fetchall()
    return [_row_to_job(r) for r in rows]


def cancel_job(job_id: str) -> dict | None:
    task = _tasks.get(job_id)
    if task and not task.done():
        task.cancel()
    job = get_job(job_id)
    if job and job["status"] not in TERMINAL:
        job = update_job(job_id, status="canceled", error="canceled by user")
    return job


# ── generic local work ───────────────────────────────────────────────


class JobHandle:
    """Passed to job bodies so they can report stage/progress."""

    def __init__(self, job_id: str) -> None:
        self.id = job_id
        self._last = 0.0

    def stage(self, stage: str, progress: float | None = None) -> None:
        fields: dict = {"stage": stage, "status": "running"}
        if progress is not None:
            fields["progress"] = max(0.0, min(1.0, progress))
        update_job(self.id, **fields)

    def progress(self, value: float) -> None:
        now = time.monotonic()
        if now - self._last < 0.5 and value < 1:  # throttle DB writes
            return
        self._last = now
        update_job(self.id, progress=max(0.0, min(1.0, value)))

    def waiting(self, stage: str, detail: dict | None = None) -> None:
        """Pause for a human decision (approval gates)."""
        update_job(self.id, status="waiting", stage=stage,
                   **({"payload": {"waiting": detail}} if detail else {}))


def spawn(
    kind: str,
    body: Callable[[JobHandle], Awaitable[dict | None]],
    *,
    payload: dict | None = None,
    project_id: str | None = None,
) -> dict:
    """Create a job row and run `body(handle)` in the background. `body` returns the result."""
    job = create_job(kind, payload, project_id=project_id)
    handle = JobHandle(job["id"])

    async def runner() -> None:
        try:
            update_job(job["id"], status="running")
            result = await body(handle)
            update_job(job["id"], status="completed", progress=1.0, stage="done",
                       result=result or {})
        except asyncio.CancelledError:
            update_job(job["id"], status="canceled", error="canceled")
        except ProviderError as e:
            update_job(job["id"], status="failed", error=str(e))
        except Exception as e:  # noqa: BLE001 — job must always resolve
            log.exception("%s job %s crashed", kind, job["id"])
            update_job(job["id"], status="failed", error=f"{type(e).__name__}: {e}")
        finally:
            _tasks.pop(job["id"], None)

    _tasks[job["id"]] = asyncio.get_running_loop().create_task(runner())
    return job


async def wait_for(job_id: str, timeout_s: float = 3600, interval: float = 0.25) -> dict:
    """Await a job reaching a terminal (or waiting) state."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        job = get_job(job_id)
        if job and job["status"] in (*TERMINAL, "waiting"):
            return job
        await asyncio.sleep(interval)
    raise TimeoutError(f"job {job_id} still running after {timeout_s}s")


# ── provider video jobs ──────────────────────────────────────────────


async def start_video_job(
    client,
    *,
    prompt: str,
    model: str,
    parent_id: str | None = None,
    submit_kwargs: dict,
    kind: str = "video",
    extra_params: dict | None = None,
    project_id: str | None = None,
) -> dict:
    """Submit to the provider and spawn a background poller. Returns the job record.

    `kind` tags the job for clients ("video" from Animate, "lipsync" from Lip Sync);
    `extra_params` is recorded on the resulting asset next to the redacted submit kwargs.
    """
    provider_job_id = await client.submit_video(prompt=prompt, model=model, **submit_kwargs)
    params = {**redact(submit_kwargs), **(extra_params or {}), "kind": kind}
    job = create_job(
        kind,
        {
            "provider": client.provider,
            "provider_job_id": provider_job_id,
            "model": model,
            "prompt": prompt,
            "parent_id": parent_id,
            "asset_params": params,
        },
        project_id=project_id,
    )
    _tasks[job["id"]] = asyncio.get_running_loop().create_task(_poll_loop(client, job["id"]))
    return job


async def _poll_loop(client, job_id: str) -> None:
    job = get_job(job_id)
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
                    params=job.get("asset_params") or {},
                    parent_id=job.get("parent_id"),
                    cost=state.get("cost"),
                )
                _record_cost(job, state.get("cost"))
                update_job(job_id, status="completed", progress=1.0, result={"asset": asset})
                return
            if status == "failed":
                update_job(job_id, status="failed", error=state.get("error", "generation failed"))
                return
            update_job(job_id, status=status)
        update_job(job_id, status="failed", error=f"timed out after {POLL_TIMEOUT_S}s")
    except asyncio.CancelledError:
        raise
    except ProviderError as e:
        update_job(job_id, status="failed", error=str(e))
    except Exception as e:  # noqa: BLE001 — job must always resolve
        log.exception("video job %s crashed", job_id)
        update_job(job_id, status="failed", error=f"internal error: {e}")
    finally:
        _tasks.pop(job_id, None)


def _record_cost(job: dict, cost) -> None:
    if cost is None:
        return
    try:
        from . import costs

        costs.record(kind="video", model=job.get("model", ""), cost=float(cost),
                     job_id=job["id"], project_id=job.get("project_id"))
    except Exception:  # noqa: BLE001 — accounting must never break delivery
        log.exception("cost ledger write failed")


async def resume_after_restart(client_for: Callable[[str], object]) -> int:
    """Called at startup: resume provider polls, fail interrupted local work."""
    resumed = 0
    for job in list_jobs(limit=500):
        if job["status"] in TERMINAL or job["status"] == "waiting":
            continue
        if job.get("provider_job_id"):
            try:
                client = client_for(job["provider"])
            except ProviderError:
                update_job(job["id"], status="failed", error="provider key removed")
                continue
            _tasks[job["id"]] = asyncio.get_running_loop().create_task(_poll_loop(client, job["id"]))
            resumed += 1
        else:
            update_job(job["id"], status="failed", error="interrupted by engine restart")
    return resumed
