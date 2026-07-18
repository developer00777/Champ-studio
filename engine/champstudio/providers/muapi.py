"""MuAPI provider (legacy/secondary BYOK engine, inherited from ChampVideo).

Auth: x-api-key header. Pattern: POST /api/v1/{model} -> request_id,
poll GET /api/v1/predictions/{id}/result until completed/failed.
"""

from __future__ import annotations

import asyncio

import httpx

from ..config import get_api_key
from .base import GeneratedMedia, ProviderError

BASE = "https://api.muapi.ai"
POLL_INTERVAL_S = 4
POLL_TIMEOUT_S = 900

# Curated lists carried over from ChampVideo (MuAPI has no public list endpoint).
IMAGE_MODELS = [
    {"id": "flux-dev-image", "name": "FLUX Dev"},
    {"id": "flux-schnell-image", "name": "FLUX Schnell"},
    {"id": "nano-banana-pro", "name": "Nano Banana Pro"},
    {"id": "ideogram-v3", "name": "Ideogram v3"},
    {"id": "reve-image", "name": "Reve Image"},
    {"id": "wan2.5-image", "name": "WAN 2.5 Image"},
]
VIDEO_MODELS = [
    {"id": "kling-v2-video", "name": "Kling v2", "max_duration": 10},
    {"id": "kling-v1.6-video", "name": "Kling v1.6", "max_duration": 10},
    {"id": "wan2.5-video", "name": "WAN 2.5", "max_duration": 8},
    {"id": "minimax-video-01", "name": "MiniMax Video-01", "max_duration": 6},
    {"id": "ltx-video", "name": "LTX Video", "max_duration": 5},
    {"id": "vidu-q1-video", "name": "Vidu Q1", "max_duration": 8},
]


class MuAPIClient:
    provider = "muapi"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or get_api_key("muapi")
        if not self.api_key:
            raise ProviderError("MuAPI key not set — add it in Settings")

    @property
    def _headers(self) -> dict:
        return {"Content-Type": "application/json", "x-api-key": self.api_key}

    async def list_image_models(self) -> list[dict]:
        return [{**m, "provider": "muapi", "vendor": "muapi"} for m in IMAGE_MODELS]

    async def list_video_models(self) -> list[dict]:
        return [{**m, "provider": "muapi", "vendor": "muapi"} for m in VIDEO_MODELS]

    async def _submit(self, endpoint: str, payload: dict) -> str | dict:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(f"{BASE}/api/v1/{endpoint}", headers=self._headers, json=payload)
        if r.status_code >= 400:
            raise ProviderError(f"MuAPI submit failed ({r.status_code}): {r.text[:200]}")
        data = r.json()
        return data.get("request_id") or data.get("id") or data

    async def _poll_once(self, request_id: str) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                f"{BASE}/api/v1/predictions/{request_id}/result", headers=self._headers
            )
        if r.status_code >= 500:
            return {"status": "pending"}
        if r.status_code >= 400:
            raise ProviderError(f"MuAPI poll failed ({r.status_code}): {r.text[:200]}")
        return r.json()

    @staticmethod
    def _extract_url(data: dict) -> str | None:
        return (
            (data.get("outputs") or [None])[0]
            or data.get("url")
            or (data.get("output") or {}).get("url")
        )

    async def generate_images(
        self, *, prompt: str, model: str, aspect_ratio: str | None = None, **_ignored
    ) -> list[GeneratedMedia]:
        payload: dict = {"prompt": prompt}
        if aspect_ratio:
            payload["aspect_ratio"] = aspect_ratio
        result = await self._submit(model, payload)
        if not isinstance(result, dict):
            result = await self._wait(result)
        url = self._extract_url(result)
        if not url:
            raise ProviderError("MuAPI returned no output URL")
        data = await self.download(url)
        mime = "image/jpeg" if url.lower().split("?")[0].endswith((".jpg", ".jpeg")) else "image/png"
        return [
            GeneratedMedia(
                kind="image", data=data, mime=mime, model=model,
                provider=self.provider, prompt=prompt,
            )
        ]

    async def submit_video(
        self,
        *,
        prompt: str,
        model: str,
        duration: int | None = None,
        aspect_ratio: str | None = None,
        image_url: str | None = None,
        **_ignored,
    ) -> str:
        payload: dict = {"prompt": prompt}
        if aspect_ratio:
            payload["aspect_ratio"] = aspect_ratio
        if duration:
            payload["duration"] = duration
        if image_url:
            payload["image_url"] = image_url
        result = await self._submit(model, payload)
        if isinstance(result, dict):  # synchronous result — stash URL as pseudo-job
            url = self._extract_url(result)
            if not url:
                raise ProviderError("MuAPI returned no output URL")
            return f"sync:{url}"
        return result

    async def poll_video(self, job_id: str) -> dict:
        if job_id.startswith("sync:"):
            return {"status": "completed", "url": job_id[5:]}
        data = await self._poll_once(job_id)
        status = (data.get("status") or "").lower()
        if status in ("completed", "succeeded", "success"):
            url = self._extract_url(data)
            if not url:
                return {"status": "failed", "error": "MuAPI completed without output URL"}
            return {"status": "completed", "url": url}
        if status in ("failed", "error"):
            return {"status": "failed", "error": str(data.get("error") or "generation failed")}
        return {"status": "in_progress" if status in ("processing", "running") else "pending"}

    async def _wait(self, request_id: str) -> dict:
        elapsed = 0
        while elapsed < POLL_TIMEOUT_S:
            await asyncio.sleep(POLL_INTERVAL_S)
            elapsed += POLL_INTERVAL_S
            data = await self._poll_once(request_id)
            status = (data.get("status") or "").lower()
            if status in ("completed", "succeeded", "success"):
                return data
            if status in ("failed", "error"):
                raise ProviderError(f"MuAPI generation failed: {data.get('error', 'unknown')}")
        raise ProviderError(f"MuAPI generation timed out after {POLL_TIMEOUT_S}s")

    async def download(self, url: str) -> bytes:
        async with httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.content
