"""OpenRouter provider — unified image API + async video API.

Image API   : POST https://openrouter.ai/api/v1/images        (sync, base64 out)
Video API   : POST https://openrouter.ai/api/v1/videos        (job id, poll)
Model lists : GET  /api/v1/images/models, /api/v1/videos/models

Verified against OpenRouter docs on 2026-07-18.
"""

from __future__ import annotations

import base64

import httpx

from ..config import get_api_key
from .base import GeneratedMedia, ProviderError

BASE = "https://openrouter.ai/api/v1"
APP_HEADERS = {
    "HTTP-Referer": "https://champstudio.app",
    "X-Title": "Champ Studio",
}


class OpenRouterClient:
    provider = "openrouter"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or get_api_key("openrouter")
        if not self.api_key:
            raise ProviderError("OpenRouter API key not set — add it in Settings")

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", **APP_HEADERS}

    # ── model discovery ──────────────────────────────────────────────

    async def list_image_models(self) -> list[dict]:
        return await self._list_models("images")

    async def list_video_models(self) -> list[dict]:
        return await self._list_models("videos")

    async def _list_models(self, kind: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{BASE}/{kind}/models", headers=self._headers)
            if r.status_code >= 400:
                raise ProviderError(f"OpenRouter model list failed ({r.status_code})")
            data = r.json().get("data", [])
        return [
            {
                "id": m.get("id"),
                "name": m.get("name") or m.get("id"),
                "provider": "openrouter",
                "vendor": (m.get("id") or "").split("/")[0],
                "meta": {
                    k: m.get(k)
                    for k in ("pricing", "supported_parameters", "description")
                    if m.get(k) is not None
                },
            }
            for m in data
            if m.get("id")
        ]

    # ── images (sync) ────────────────────────────────────────────────

    async def generate_images(
        self,
        *,
        prompt: str,
        model: str,
        n: int = 1,
        aspect_ratio: str | None = None,
        resolution: str | None = None,
        output_format: str | None = None,
        background: str | None = None,
        seed: int | None = None,
        input_references: list[str] | None = None,
    ) -> list[GeneratedMedia]:
        body: dict = {"model": model, "prompt": prompt, "n": max(1, min(n, 10))}
        if aspect_ratio:
            body["aspect_ratio"] = aspect_ratio
        if resolution:
            body["resolution"] = resolution
        if output_format:
            body["output_format"] = output_format
        if background:
            body["background"] = background
        if seed is not None:
            body["seed"] = seed
        if input_references:
            body["input_references"] = input_references

        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(f"{BASE}/images", headers=self._headers, json=body)
        if r.status_code >= 400:
            raise ProviderError(_err("image generation", r))
        payload = r.json()
        cost = (payload.get("usage") or {}).get("cost")
        out: list[GeneratedMedia] = []
        for item in payload.get("data", []):
            b64 = item.get("b64_json")
            if not b64:
                continue
            out.append(
                GeneratedMedia(
                    kind="image",
                    data=base64.b64decode(b64),
                    mime=item.get("media_type") or "image/png",
                    model=model,
                    provider=self.provider,
                    prompt=prompt,
                    cost=cost,
                )
            )
        if not out:
            raise ProviderError("OpenRouter returned no images")
        return out

    # ── videos (async job) ───────────────────────────────────────────

    async def submit_video(
        self,
        *,
        prompt: str,
        model: str,
        duration: int | None = None,
        aspect_ratio: str | None = None,
        resolution: str | None = None,
        generate_audio: bool | None = None,
        seed: int | None = None,
        frame_images: list[dict] | None = None,       # [{"url": ..., "frame_type": "first_frame"}]
        input_references: list[str] | None = None,
    ) -> str:
        body: dict = {"model": model, "prompt": prompt}
        if duration:
            body["duration"] = duration
        if aspect_ratio:
            body["aspect_ratio"] = aspect_ratio
        if resolution:
            body["resolution"] = resolution
        if generate_audio is not None:
            body["generate_audio"] = generate_audio
        if seed is not None:
            body["seed"] = seed
        if frame_images:
            body["frame_images"] = frame_images
        if input_references:
            body["input_references"] = input_references

        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(f"{BASE}/videos", headers=self._headers, json=body)
        if r.status_code >= 400:
            raise ProviderError(_err("video submit", r))
        job_id = r.json().get("id")
        if not job_id:
            raise ProviderError("OpenRouter video submit returned no job id")
        return job_id

    async def poll_video(self, job_id: str) -> dict:
        """One poll. Returns {status, url?, error?, cost?} with status in
        pending|in_progress|completed|failed."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(f"{BASE}/videos/{job_id}", headers=self._headers)
        if r.status_code >= 400:
            raise ProviderError(_err("video poll", r))
        data = r.json()
        status = data.get("status", "pending")
        out: dict = {"status": status}
        if status == "completed":
            urls = data.get("unsigned_urls") or []
            out["url"] = urls[0] if urls else f"{BASE}/videos/{job_id}/content?index=0"
            out["cost"] = (data.get("usage") or {}).get("cost")
        elif status == "failed":
            err = data.get("error")
            out["error"] = err.get("message") if isinstance(err, dict) else str(err or "generation failed")
        return out

    async def download(self, url: str) -> bytes:
        async with httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client:
            r = await client.get(url, headers=self._headers)
            r.raise_for_status()
            return r.content


def _err(what: str, r: httpx.Response) -> str:
    detail = ""
    try:
        detail = (r.json().get("error") or {}).get("message", "")
    except Exception:
        detail = r.text[:200]
    return f"OpenRouter {what} failed ({r.status_code}): {detail}"
