"""OpenRouter provider — unified image API, async video API, speech API.

Image API   : POST https://openrouter.ai/api/v1/images        (sync, base64 out)
Video API   : POST https://openrouter.ai/api/v1/videos        (job id, poll)
Speech API  : POST https://openrouter.ai/api/v1/audio/speech  (sync, raw audio bytes)
Chat        : POST https://openrouter.ai/api/v1/chat/completions (LLM; json_schema output;
              music via modalities ["text","audio"] + stream)
STT         : POST https://openrouter.ai/api/v1/audio/transcriptions
              {model, input_audio: {data, format}, response_format: "verbose_json",
               timestamp_granularities: ["word","segment"]} -> {text, words[{word,start,end}], ...}
Model lists : GET  /api/v1/images/models, /api/v1/videos/models,
              GET  /api/v1/models?output_modalities=speech

Payload shapes follow the API reference as of 2026-09-30:
  frame_images     [{"type": "image_url", "image_url": {"url"}, "frame_type": "first_frame"|"last_frame"}]
  input_references [{"type": "image_url", ...} | {"type": "audio_url", "audio_url": {"url"}}]
"""

from __future__ import annotations

import base64
import json

import httpx

from ..config import get_api_key
from .base import GeneratedMedia, ProviderError

BASE = "https://openrouter.ai/api/v1"
APP_HEADERS = {
    "HTTP-Referer": "https://champstudio.app",
    "X-Title": "Champ Studio",
}


def image_part(url: str) -> dict:
    return {"type": "image_url", "image_url": {"url": url}}


def audio_part(url: str) -> dict:
    return {"type": "audio_url", "audio_url": {"url": url}}


class OpenRouterClient:
    provider = "openrouter"

    def __init__(
        self,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key or get_api_key("openrouter")
        if not self.api_key:
            raise ProviderError("OpenRouter API key not set — add it in Settings")
        self._transport = transport  # tests inject httpx.MockTransport; None = real network

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", **APP_HEADERS}

    def _http(self, timeout: float, *, follow_redirects: bool = False) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=timeout, transport=self._transport, follow_redirects=follow_redirects
        )

    # ── model discovery ──────────────────────────────────────────────

    async def list_image_models(self) -> list[dict]:
        return await self._list_models("images")

    async def list_video_models(self) -> list[dict]:
        return await self._list_models("videos")

    async def _list_models(self, kind: str) -> list[dict]:
        async with self._http(15.0) as client:
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

    async def list_speech_models(self) -> list[dict]:
        """Text-to-speech models from the general catalog; `supported_voices` becomes `voices`."""
        async with self._http(15.0) as client:
            r = await client.get(
                f"{BASE}/models", params={"output_modalities": "speech"}, headers=self._headers
            )
        if r.status_code >= 400:
            raise ProviderError(f"OpenRouter speech model list failed ({r.status_code})")
        return [
            {
                "id": m["id"],
                "name": m.get("name") or m["id"],
                "provider": "openrouter",
                "vendor": m["id"].split("/")[0],
                "voices": list(m.get("supported_voices") or []),
                "meta": {"pricing": m.get("pricing")} if m.get("pricing") is not None else {},
            }
            for m in r.json().get("data", [])
            if m.get("id")
        ]

    # ── speech (sync bytes) ──────────────────────────────────────────

    async def synthesize_speech(
        self,
        *,
        text: str,
        model: str,
        voice: str | None = None,
        speed: float | None = None,
    ) -> GeneratedMedia:
        body: dict = {"model": model, "input": text, "response_format": "mp3"}
        if voice:
            body["voice"] = voice
        if speed is not None and speed != 1.0:
            body["speed"] = speed
        async with self._http(300.0) as client:
            r = await client.post(f"{BASE}/audio/speech", headers=self._headers, json=body)
            if r.status_code == 400 and "pcm" in r.text.lower():
                # some models (Gemini TTS) only stream raw PCM: ask for that and wrap it as WAV
                body["response_format"] = "pcm"
                r = await client.post(f"{BASE}/audio/speech", headers=self._headers, json=body)
        if r.status_code >= 400:
            raise ProviderError(_err("speech synthesis", r))
        if not r.content:
            raise ProviderError("OpenRouter returned empty audio")
        ctype = r.headers.get("content-type") or "audio/mpeg"
        mime = ctype.split(";")[0].strip() or "audio/mpeg"
        data = r.content
        if body["response_format"] == "pcm" or "pcm" in mime or mime in ("audio/l16", "application/octet-stream"):
            data, mime = pcm_to_wav(data, ctype), "audio/wav"
        return GeneratedMedia(
            kind="audio",
            data=data,
            mime=mime,
            model=model,
            provider=self.provider,
            prompt=text,
            meta={"generation_id": r.headers.get("x-generation-id")},
        )

    # ── chat (LLM) ───────────────────────────────────────────────────

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict],
        response_format: dict | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float = 180.0,
        reasoning: dict | None = None,
    ) -> dict:
        """One chat completion. Returns {"content", "usage", "model", "finish_reason"}."""
        body: dict = {"model": model, "messages": messages, "usage": {"include": True}}
        if response_format:
            body["response_format"] = response_format
        if temperature is not None:
            body["temperature"] = temperature
        if reasoning:  # e.g. {"effort": "low"}; ignored by models that don't reason
            body["reasoning"] = reasoning
        if max_tokens:
            body["max_tokens"] = max_tokens
        async with self._http(timeout) as client:
            r = await client.post(f"{BASE}/chat/completions", headers=self._headers, json=body)
        if r.status_code >= 400:
            raise ProviderError(_err("chat", r))
        data = r.json()
        if data.get("error"):
            err = data["error"]
            raise ProviderError(f"OpenRouter chat failed: {err.get('message') if isinstance(err, dict) else err}")
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        if isinstance(content, list):  # some providers return content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        return {
            "content": content,
            "usage": data.get("usage") or {},
            "model": data.get("model") or model,
            "finish_reason": choice.get("finish_reason"),
        }

    # ── transcription (STT) ──────────────────────────────────────────

    async def transcribe(
        self,
        *,
        audio: bytes,
        audio_format: str,
        model: str,
        language: str | None = None,
        keyterms: list[str] | None = None,
    ) -> dict:
        """Word-timestamped transcript: {"text", "words": [{word,start,end}], "duration", "cost"}."""
        body: dict = {
            "model": model,
            "input_audio": {"data": base64.b64encode(audio).decode(), "format": audio_format},
            "response_format": "verbose_json",
            "timestamp_granularities": ["word", "segment"],
        }
        if language:
            body["language"] = language
        if keyterms:
            body["keyterms"] = keyterms
        async with self._http(120.0) as client:
            r = await client.post(f"{BASE}/audio/transcriptions", headers=self._headers, json=body)
        if r.status_code >= 400:
            raise ProviderError(_err("transcription", r))
        data = r.json()
        words = [
            {"word": w.get("word", "").strip(), "start": float(w.get("start", 0)), "end": float(w.get("end", 0))}
            for w in (data.get("words") or [])
            if (w.get("type") in (None, "word")) and w.get("word", "").strip()
        ]
        return {
            "text": data.get("text", ""),
            "words": words,
            "segments": data.get("segments") or [],
            "duration": data.get("duration"),
            "language": data.get("language"),
            "cost": (data.get("usage") or {}).get("cost"),
        }

    # ── music (chat with audio output, streamed) ─────────────────────

    async def generate_music(self, *, prompt: str, model: str, audio_format: str = "mp3") -> GeneratedMedia:
        body = {
            "model": model,
            "modalities": ["text", "audio"],
            "audio": {"format": audio_format},
            "stream": True,
            "messages": [{"role": "user", "content": prompt}],
            "usage": {"include": True},
        }
        chunks: list[str] = []
        cost = None
        async with self._http(300.0) as client:
            async with client.stream("POST", f"{BASE}/chat/completions", headers=self._headers, json=body) as r:
                if r.status_code >= 400:
                    await r.aread()
                    raise ProviderError(_err("music generation", r))
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        evt = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if evt.get("usage"):
                        cost = evt["usage"].get("cost", cost)
                    for ch in evt.get("choices") or []:
                        audio = (ch.get("delta") or {}).get("audio") or {}
                        if audio.get("data"):
                            chunks.append(audio["data"])
        if not chunks:
            raise ProviderError("OpenRouter returned no music audio")
        # chunks are either independently padded base64 or slices of one stream
        if all(len(c) % 4 == 0 for c in chunks):
            data = b"".join(base64.b64decode(c) for c in chunks)
        else:
            data = base64.b64decode("".join(chunks))
        mime = {"mp3": "audio/mpeg", "wav": "audio/wav"}.get(audio_format, "audio/mpeg")
        return GeneratedMedia(kind="audio", data=data, mime=mime, model=model,
                              provider=self.provider, prompt=prompt, cost=cost)

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
            body["input_references"] = [image_part(u) for u in input_references]

        async with self._http(300.0) as client:
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
        first_frame: str | None = None,
        last_frame: str | None = None,
        input_references: list[str] | None = None,   # image URLs / data URLs
        audio_references: list[str] | None = None,   # audio URLs / data URLs
        provider_options: dict | None = None,        # {"heygen": {...}} passthrough
    ) -> str:
        body: dict = {"model": model}
        if prompt:  # optional for image-driven models such as heygen/avatar-iv
            body["prompt"] = prompt
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
        frames = []
        if first_frame:
            frames.append({**image_part(first_frame), "frame_type": "first_frame"})
        if last_frame:
            frames.append({**image_part(last_frame), "frame_type": "last_frame"})
        if frames:
            body["frame_images"] = frames
        refs = [image_part(u) for u in (input_references or [])]
        refs += [audio_part(u) for u in (audio_references or [])]
        if refs:
            body["input_references"] = refs
        if provider_options:
            body["provider"] = {"options": provider_options}

        async with self._http(60.0) as client:
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
        async with self._http(30.0) as client:
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
        async with self._http(600.0, follow_redirects=True) as client:
            r = await client.get(url, headers=self._headers)
            r.raise_for_status()
            return r.content


def pcm_to_wav(pcm: bytes, content_type: str = "") -> bytes:
    """Wrap raw 16-bit little-endian PCM in a WAV header. Rate/channels come from the
    content type when given (e.g. audio/pcm;rate=24000;channels=1), else Gemini's 24 kHz mono."""
    import io
    import re
    import wave

    if pcm[:4] == b"RIFF":
        return pcm
    rate = int((re.search(r"rate=(\d+)", content_type) or [None, 24000])[1])
    channels = int((re.search(r"channels=(\d+)", content_type) or [None, 1])[1])
    if len(pcm) % 2:
        pcm = pcm[:-1]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _err(what: str, r: httpx.Response) -> str:
    detail = ""
    try:
        detail = (r.json().get("error") or {}).get("message", "")
    except Exception:
        detail = r.text[:200]
    return f"OpenRouter {what} failed ({r.status_code}): {detail}"
