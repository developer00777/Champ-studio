# Phase 1: Audio and Voice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A script typed into the web UI becomes a voiceover asset, and that voiceover plus a portrait becomes a lip-synced talking-head video, all through the existing OpenRouter key.

**Architecture:** Everything is additive on the existing engine. The library learns an `audio` kind; the OpenRouter provider gains speech synthesis, speech-model listing, and documented payload shapes for frame images and reference assets; two endpoints (`POST /api/audio`, `POST /api/lipsync`) reuse the existing asset library and video job registry; the web Create view gets Image / Voice / Lip Sync tabs. Lip sync uses OpenRouter's `heygen/avatar-iv` video model with a portrait image reference and an audio reference. No new provider files, no new keys, no Mac app changes (the Mac app becomes a shell over the web UI in Phase 4).

**Tech Stack:** Python 3.12, FastAPI, httpx (with `httpx.MockTransport` in tests), pytest with `asyncio_mode = "auto"`, SQLite, buildless ES-module web UI. Run tests from `engine/` with `.venv/bin/pytest`.

## Global Constraints

- OpenRouter only for v1 (decision 2026-10-01). No HeyGen, ElevenLabs or MuAPI code for audio or lip sync. MuAPI keeps its existing image/video role untouched.
- Keys stay in `settings.json` (0600) or env; raw keys never appear in responses or logs.
- Base64 data URLs must never be written into the library `params` column.
- Payload shapes follow the OpenRouter API reference as of 2026-09-30: `frame_images` items are `{"type": "image_url", "image_url": {"url"}, "frame_type"}`; `input_references` items are `{"type": "image_url", "image_url": {"url"}}` or `{"type": "audio_url", "audio_url": {"url"}}`; speech is `POST /api/v1/audio/speech` with `{"model", "input", "voice", "response_format": "mp3"}` returning raw `audio/mpeg` bytes.
- Default speech model `google/gemini-3.8-flash-tts`, default voice `Kore`. Default lip-sync model `heygen/avatar-iv`.
- No new Python dependencies.
- Commit after every task with a conventional message ending in `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Work on branch `feat/phase-1-audio-voice`, never directly on `main`.

## File map

| File | Responsibility after this plan |
|---|---|
| `engine/champstudio/providers/base.py` | Shared types; MIME suffix table gains audio types |
| `engine/champstudio/library.py` | `kind_for_mime` recognises audio |
| `engine/champstudio/providers/openrouter.py` | OpenRouter HTTP client: images, videos, speech; injectable transport; content-part helpers |
| `engine/champstudio/jobs.py` | In-process video job registry; jobs carry `kind`; saved params are redacted |
| `engine/champstudio/main.py` | FastAPI routes; new `/api/models/speech`, `/api/audio`, `/api/lipsync`; shared `_asset_data_url` |
| `engine/tests/test_openrouter.py` | Provider payload and response tests via `httpx.MockTransport` |
| `engine/tests/test_media_api.py` | API tests for audio assets, speech, lip sync, job params (providers mocked) |
| `web/index.html` | Create view split into three tab panes |
| `web/app.js` | Tabs, Voice pane, Lip Sync pane, audio-aware cards and picker |
| `web/styles.css` | Tab row, audio tile, lip-sync setup panel |
| `README.md`, `docs/consolidation/phase-0-findings.md` | Feature docs and live-test record |

---

### Task 0: Branch

**Files:** none

- [ ] **Step 1: Create the working branch from main**

```bash
cd "/Users/deep/Apps&Projects/Champ Studio"
git checkout -b feat/phase-1-audio-voice main
git status -sb
```

Expected: `## feat/phase-1-audio-voice`

- [ ] **Step 2: Confirm the test suite is green before touching anything**

Run: `cd engine && .venv/bin/pytest -q`
Expected: all tests pass (transform tests may take ~30 s on first run).

---

### Task 1: Audio assets in the library

**Files:**
- Modify: `engine/champstudio/providers/base.py:25-34`
- Modify: `engine/champstudio/library.py:48-55`
- Modify: `engine/champstudio/main.py:321-329`
- Create: `engine/tests/test_media_api.py`

**Interfaces:**
- Produces: assets with `kind == "audio"` for any `audio/*` mime; `suffix_for("audio/mpeg") == ".mp3"`; `POST /api/assets/upload` accepts audio.

- [ ] **Step 1: Write the failing test**

Create `engine/tests/test_media_api.py`:

```python
"""Phase 1 API tests: audio assets, speech synthesis, lip sync jobs.

Providers are monkeypatched — no network, no keys needed beyond a fake one
written through /api/settings and cleared again.
"""

import asyncio
import io
import os
import tempfile

import pytest
from PIL import Image

_tmp = tempfile.mkdtemp(prefix="champstudio-media-test-")
os.environ["CHAMP_DATA_DIR"] = _tmp

from fastapi.testclient import TestClient  # noqa: E402

from champstudio import jobs  # noqa: E402
from champstudio.main import app  # noqa: E402
from champstudio.providers.base import GeneratedMedia  # noqa: E402
from champstudio.providers.openrouter import OpenRouterClient  # noqa: E402

FAKE_MP3 = b"ID3" + b"\x00" * 64


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def with_key(client):
    client.put("/api/settings", json={"keys": {"openrouter": "sk-or-v1-test"}})
    yield
    client.put("/api/settings", json={"keys": {"openrouter": ""}})


@pytest.fixture(scope="module")
def portrait(client) -> str:
    img = Image.new("RGB", (64, 64), (255, 107, 53))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    r = client.post("/api/assets/upload", files={"file": ("face.png", buf.getvalue(), "image/png")})
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.fixture(scope="module")
def voice_track(client) -> str:
    r = client.post("/api/assets/upload", files={"file": ("vo.mp3", FAKE_MP3, "audio/mpeg")})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_audio_upload_lands_as_audio_kind(client, voice_track):
    asset = client.get(f"/api/assets/{voice_track}").json()
    assert asset["kind"] == "audio"
    assert asset["filename"].endswith(".mp3")
    f = client.get(f"/api/assets/{voice_track}/file")
    assert f.status_code == 200
    assert f.headers["content-type"].startswith("audio/mpeg")


def test_library_filters_by_audio_kind(client, voice_track):
    ids = {a["id"] for a in client.get("/api/assets?kind=audio").json()["assets"]}
    assert voice_track in ids
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd engine && .venv/bin/pytest tests/test_media_api.py -q`
Expected: FAIL in the `voice_track` fixture with `400 ... only image or video uploads`.

- [ ] **Step 3: Add audio suffixes**

In `engine/champstudio/providers/base.py`, replace the `MIME_SUFFIX` dict:

```python
MIME_SUFFIX = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
    "audio/ogg": ".ogg",
    "audio/webm": ".weba",
    "application/json": ".json",
    "text/css": ".css",
}
```

- [ ] **Step 4: Teach the library the audio kind**

In `engine/champstudio/library.py`, replace `kind_for_mime`:

```python
def kind_for_mime(mime: str) -> str:
    if mime == "image/svg+xml":
        return "vector"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    return "data"
```

Also update the schema comment on the `kind` column so it reads `-- image | video | audio | vector | data`.

- [ ] **Step 5: Accept audio uploads**

In `engine/champstudio/main.py`, `upload_asset`, replace the mime check:

```python
    mime = file.content_type or "image/png"
    if not mime.startswith(("image/", "video/", "audio/")):
        raise HTTPException(400, "only image, video or audio uploads")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_media_api.py tests/test_api.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add engine/champstudio/providers/base.py engine/champstudio/library.py engine/champstudio/main.py engine/tests/test_media_api.py
git commit -m "feat(library): audio asset kind + audio uploads

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: OpenRouter provider — injectable transport and documented payload shapes

**Files:**
- Modify: `engine/champstudio/providers/openrouter.py` (whole file)
- Modify: `engine/champstudio/main.py:231-240`
- Create: `engine/tests/test_openrouter.py`

**Interfaces:**
- Produces: `OpenRouterClient(api_key=None, transport: httpx.AsyncBaseTransport | None = None)`.
- Produces: `OpenRouterClient.submit_video(*, prompt, model, duration=None, aspect_ratio=None, resolution=None, generate_audio=None, seed=None, first_frame: str | None = None, last_frame: str | None = None, input_references: list[str] | None = None, audio_references: list[str] | None = None, provider_options: dict | None = None) -> str`.
- Produces: module functions `image_part(url) -> dict` and `audio_part(url) -> dict`.
- `generate_images` keeps its signature; `input_references` strings are wrapped into image parts.

- [ ] **Step 1: Write the failing tests**

Create `engine/tests/test_openrouter.py`:

```python
"""OpenRouter provider tests — request bodies and response handling via httpx.MockTransport."""

import json

import httpx
import pytest

from champstudio.providers.base import ProviderError
from champstudio.providers.openrouter import OpenRouterClient, audio_part, image_part


def make_client(handler):
    return OpenRouterClient(api_key="sk-or-test", transport=httpx.MockTransport(handler))


def test_content_part_helpers_match_api_reference():
    assert image_part("data:image/png;base64,AAA") == {
        "type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"},
    }
    assert audio_part("https://x/y.mp3") == {"type": "audio_url", "audio_url": {"url": "https://x/y.mp3"}}


async def test_submit_video_sends_documented_frame_and_reference_shapes():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(202, json={"id": "gen-vid-1"})

    job = await make_client(handler).submit_video(
        prompt="walks forward",
        model="kwaivgi/kling-v3.0-std",
        duration=5,
        first_frame="data:image/png;base64,AAA",
        last_frame="data:image/png;base64,BBB",
        input_references=["data:image/png;base64,CCC"],
        audio_references=["data:audio/mpeg;base64,DDD"],
        provider_options={"heygen": {"voice_id": "v1"}},
    )
    assert job == "gen-vid-1"
    assert seen["url"] == "https://openrouter.ai/api/v1/videos"
    assert seen["auth"] == "Bearer sk-or-test"
    body = seen["body"]
    assert body["model"] == "kwaivgi/kling-v3.0-std" and body["prompt"] == "walks forward"
    assert body["duration"] == 5
    assert body["frame_images"] == [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}, "frame_type": "first_frame"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,BBB"}, "frame_type": "last_frame"},
    ]
    assert body["input_references"] == [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,CCC"}},
        {"type": "audio_url", "audio_url": {"url": "data:audio/mpeg;base64,DDD"}},
    ]
    assert body["provider"] == {"options": {"heygen": {"voice_id": "v1"}}}


async def test_submit_video_omits_empty_prompt_and_unset_fields():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(202, json={"id": "gen-vid-2"})

    await make_client(handler).submit_video(prompt="", model="heygen/avatar-iv",
                                            input_references=["data:image/png;base64,AAA"])
    assert set(seen["body"]) == {"model", "input_references"}


async def test_generate_images_wraps_references_as_image_parts():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"b64_json": "aGVsbG8=", "media_type": "image/png"}],
                                         "usage": {"cost": 0.01}})

    media = await make_client(handler).generate_images(
        prompt="a fox", model="google/gemini-2.5-flash-image",
        input_references=["data:image/png;base64,AAA"],
    )
    assert seen["body"]["input_references"] == [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]
    assert media[0].data == b"hello" and media[0].cost == 0.01


async def test_video_error_is_a_provider_error_with_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "frame_images invalid"}})

    with pytest.raises(ProviderError, match="frame_images invalid"):
        await make_client(handler).submit_video(prompt="x", model="m")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd engine && .venv/bin/pytest tests/test_openrouter.py -q`
Expected: FAIL with `ImportError: cannot import name 'audio_part'`.

- [ ] **Step 3: Rewrite the provider**

Replace the whole of `engine/champstudio/providers/openrouter.py` with:

```python
"""OpenRouter provider — unified image API, async video API, speech API.

Image API   : POST https://openrouter.ai/api/v1/images        (sync, base64 out)
Video API   : POST https://openrouter.ai/api/v1/videos        (job id, poll)
Speech API  : POST https://openrouter.ai/api/v1/audio/speech  (sync, raw audio bytes)
Model lists : GET  /api/v1/images/models, /api/v1/videos/models,
              GET  /api/v1/models?output_modalities=speech

Payload shapes follow the API reference as of 2026-09-30:
  frame_images     [{"type": "image_url", "image_url": {"url"}, "frame_type": "first_frame"|"last_frame"}]
  input_references [{"type": "image_url", ...} | {"type": "audio_url", "audio_url": {"url"}}]
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


def _err(what: str, r: httpx.Response) -> str:
    detail = ""
    try:
        detail = (r.json().get("error") or {}).get("message", "")
    except Exception:
        detail = r.text[:200]
    return f"OpenRouter {what} failed ({r.status_code}): {detail}"
```

- [ ] **Step 4: Update the video endpoint to the new keyword**

In `engine/champstudio/main.py`, `generate_video`, replace the `if body.image_asset_id:` block:

```python
    if body.image_asset_id:
        path = library.asset_path(body.image_asset_id)
        if not path:
            raise HTTPException(404, f"asset {body.image_asset_id} not found")
        asset = library.get_asset(body.image_asset_id)
        data_url = f"data:{asset['mime']};base64,{base64.b64encode(path.read_bytes()).decode()}"
        if client.provider == "openrouter":
            submit_kwargs["first_frame"] = data_url
        else:
            submit_kwargs["image_url"] = data_url
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_openrouter.py tests/test_api.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add engine/champstudio/providers/openrouter.py engine/champstudio/main.py engine/tests/test_openrouter.py
git commit -m "fix(openrouter): documented frame_images/input_references shapes, audio references, injectable transport

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: OpenRouter provider — speech synthesis and speech model listing

**Files:**
- Modify: `engine/champstudio/providers/openrouter.py` (add two methods after `_list_models`)
- Modify: `engine/tests/test_openrouter.py` (append tests)

**Interfaces:**
- Produces: `OpenRouterClient.list_speech_models() -> list[dict]` with items `{"id", "name", "provider": "openrouter", "vendor", "voices": list[str], "meta": {"pricing": ...}}`.
- Produces: `OpenRouterClient.synthesize_speech(*, text: str, model: str, voice: str | None = None, speed: float | None = None) -> GeneratedMedia` with `kind="audio"`, `mime="audio/mpeg"`, `data=bytes`, `meta={"generation_id": str | None}`.

- [ ] **Step 1: Write the failing tests**

Append to `engine/tests/test_openrouter.py`:

```python
async def test_list_speech_models_exposes_voices():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/models"
        assert request.url.params["output_modalities"] == "speech"
        return httpx.Response(200, json={"data": [
            {"id": "google/gemini-3.8-flash-tts", "name": "Gemini 3.8 Flash TTS",
             "supported_voices": ["Kore", "Puck"], "pricing": {"prompt": "0.0000005"}},
            {"id": "fish-audio/s2-pro", "name": "Fish S2 Pro"},
        ]})

    models = await make_client(handler).list_speech_models()
    assert models[0] == {
        "id": "google/gemini-3.8-flash-tts", "name": "Gemini 3.8 Flash TTS",
        "provider": "openrouter", "vendor": "google", "voices": ["Kore", "Puck"],
        "meta": {"pricing": {"prompt": "0.0000005"}},
    }
    assert models[1]["voices"] == [] and models[1]["vendor"] == "fish-audio"


async def test_synthesize_speech_posts_mp3_request_and_returns_audio_media():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"ID3fake", headers={
            "content-type": "audio/mpeg", "x-generation-id": "gen-tts-1"})

    media = await make_client(handler).synthesize_speech(
        text="Hello team", model="google/gemini-3.8-flash-tts", voice="Kore", speed=1.2)
    assert seen["url"] == "https://openrouter.ai/api/v1/audio/speech"
    assert seen["body"] == {"model": "google/gemini-3.8-flash-tts", "input": "Hello team",
                            "voice": "Kore", "response_format": "mp3", "speed": 1.2}
    assert media.kind == "audio" and media.mime == "audio/mpeg" and media.data == b"ID3fake"
    assert media.model == "google/gemini-3.8-flash-tts" and media.prompt == "Hello team"
    assert media.meta["generation_id"] == "gen-tts-1"


async def test_synthesize_speech_omits_voice_and_default_speed():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"ID3", headers={"content-type": "audio/mpeg"})

    await make_client(handler).synthesize_speech(text="x", model="m", speed=1.0)
    assert seen["body"] == {"model": "m", "input": "x", "response_format": "mp3"}


async def test_synthesize_speech_errors_surface_provider_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "voice is required"}})

    with pytest.raises(ProviderError, match="voice is required"):
        await make_client(handler).synthesize_speech(text="x", model="m")


async def test_synthesize_speech_rejects_empty_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"", headers={"content-type": "audio/mpeg"})

    with pytest.raises(ProviderError, match="empty audio"):
        await make_client(handler).synthesize_speech(text="x", model="m")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd engine && .venv/bin/pytest tests/test_openrouter.py -q`
Expected: 5 failures with `AttributeError: 'OpenRouterClient' object has no attribute 'list_speech_models'` / `synthesize_speech`.

- [ ] **Step 3: Add the speech methods**

In `engine/champstudio/providers/openrouter.py`, insert after `_list_models` (before the `# ── images (sync)` comment):

```python
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
        if r.status_code >= 400:
            raise ProviderError(_err("speech synthesis", r))
        if not r.content:
            raise ProviderError("OpenRouter returned empty audio")
        mime = (r.headers.get("content-type") or "audio/mpeg").split(";")[0].strip() or "audio/mpeg"
        return GeneratedMedia(
            kind="audio",
            data=r.content,
            mime=mime,
            model=model,
            provider=self.provider,
            prompt=text,
            meta={"generation_id": r.headers.get("x-generation-id")},
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_openrouter.py -q`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add engine/champstudio/providers/openrouter.py engine/tests/test_openrouter.py
git commit -m "feat(openrouter): speech synthesis via /audio/speech + speech model listing

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `/api/models/speech` and `/api/audio`

**Files:**
- Modify: `engine/champstudio/main.py` (after the `video_models` route and after the images section)
- Modify: `engine/tests/test_media_api.py` (append)

**Interfaces:**
- Consumes: `OpenRouterClient.list_speech_models`, `OpenRouterClient.synthesize_speech` (Task 3), `library.save_asset`.
- Produces: `GET /api/models/speech -> list[dict]` (empty list when no OpenRouter key); `POST /api/audio {text, model?, voice?, speed?} -> {"assets": [asset]}` where the asset has `kind == "audio"`.

- [ ] **Step 1: Write the failing tests**

Append to `engine/tests/test_media_api.py`:

```python
def test_speech_models_empty_without_key(client):
    assert client.get("/api/models/speech").json() == []


def test_audio_without_key_fails_cleanly(client):
    r = client.post("/api/audio", json={"text": "hello"})
    assert r.status_code == 502
    assert "API key" in r.json()["detail"]


def test_audio_rejects_blank_text(client, with_key):
    assert client.post("/api/audio", json={"text": "   "}).status_code == 422


def test_audio_creates_audio_asset(client, with_key, monkeypatch):
    seen = {}

    async def fake_synth(self, *, text, model, voice=None, speed=None):
        seen.update(text=text, model=model, voice=voice, speed=speed)
        return GeneratedMedia(kind="audio", data=FAKE_MP3, mime="audio/mpeg", model=model,
                              provider="openrouter", prompt=text, meta={"generation_id": "g1"})

    monkeypatch.setattr(OpenRouterClient, "synthesize_speech", fake_synth)
    r = client.post("/api/audio", json={"text": "One engine, one library.", "voice": "Puck", "speed": 1.1})
    assert r.status_code == 200, r.text
    asset = r.json()["assets"][0]
    assert asset["kind"] == "audio" and asset["mime"] == "audio/mpeg"
    assert asset["model"] == "google/gemini-3.8-flash-tts" and asset["provider"] == "openrouter"
    assert asset["prompt"] == "One engine, one library."
    assert asset["params"] == {"voice": "Puck", "speed": 1.1, "generation_id": "g1"}
    assert seen == {"text": "One engine, one library.", "model": "google/gemini-3.8-flash-tts",
                    "voice": "Puck", "speed": 1.1}
    assert client.get(asset["url"]).content == FAKE_MP3
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd engine && .venv/bin/pytest tests/test_media_api.py -q`
Expected: `test_speech_models_empty_without_key` and the `/api/audio` tests FAIL with 404 / 405.

- [ ] **Step 3: Add the routes**

In `engine/champstudio/main.py`, add after `video_models`:

```python
@app.get("/api/models/speech", dependencies=[guarded])
async def speech_models():
    if not config.get_api_key("openrouter"):
        return []
    try:
        return await OpenRouterClient().list_speech_models()
    except ProviderError as e:
        log.warning("model list (speech): %s", e)
        return []
```

Add a new section after the images section (before `# ── videos (async jobs)`):

```python
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
```

And extend the pydantic import at the top of `main.py`:

```python
from pydantic import BaseModel, Field, field_validator
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_media_api.py tests/test_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/champstudio/main.py engine/tests/test_media_api.py
git commit -m "feat(api): POST /api/audio text-to-speech + GET /api/models/speech

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Job registry — job kinds and redacted params

**Files:**
- Modify: `engine/champstudio/jobs.py`
- Modify: `engine/tests/test_media_api.py` (append)

**Interfaces:**
- Produces: `jobs.start_video_job(client, *, prompt, model, parent_id=None, submit_kwargs, kind: str = "video", extra_params: dict | None = None) -> dict`; job records carry `"kind"`; the saved asset's `params` equal `{**redact(submit_kwargs), **extra_params, "kind": kind}`.
- Produces: `jobs.redact(value)` which replaces any string starting with `data:` by `"<inline data>"`, recursively through dicts and lists.

- [ ] **Step 1: Write the failing test**

Append to `engine/tests/test_media_api.py`:

```python
class _FakeVideoClient:
    provider = "openrouter"

    def __init__(self):
        self.submitted = None

    async def submit_video(self, **kwargs):
        self.submitted = kwargs
        return "provider-job-1"

    async def poll_video(self, job_id):
        return {"status": "completed", "url": "https://example.test/out.mp4", "cost": 0.25}

    async def download(self, url):
        return b"\x00\x00\x00\x18ftypmp42"


async def _wait_for(job_id, timeout_s=2.0):
    for _ in range(int(timeout_s / 0.02)):
        job = jobs.get_job(job_id)
        if job["status"] in ("completed", "failed"):
            return job
        await asyncio.sleep(0.02)
    raise AssertionError(f"job {job_id} still {jobs.get_job(job_id)['status']}")


def test_redact_strips_data_urls():
    assert jobs.redact({"first_frame": "data:image/png;base64,AAA", "duration": 5,
                        "input_references": ["data:audio/mpeg;base64,BBB", "https://x/y.png"]}) == {
        "first_frame": "<inline data>", "duration": 5,
        "input_references": ["<inline data>", "https://x/y.png"],
    }


async def test_job_records_kind_and_saves_redacted_params(monkeypatch):
    monkeypatch.setattr(jobs, "POLL_INTERVAL_S", 0)
    fake = _FakeVideoClient()
    job = await jobs.start_video_job(
        fake, prompt="", model="heygen/avatar-iv", parent_id=None,
        submit_kwargs={"input_references": ["data:image/png;base64,AAA"],
                       "audio_references": ["data:audio/mpeg;base64,BBB"], "resolution": "720p"},
        kind="lipsync", extra_params={"audio_asset_id": "abc123"},
    )
    assert job["kind"] == "lipsync"
    assert fake.submitted["input_references"] == ["data:image/png;base64,AAA"]  # provider got real data
    done = await _wait_for(job["id"])
    assert done["status"] == "completed", done["error"]
    params = done["asset"]["params"]
    assert params == {"input_references": ["<inline data>"], "audio_references": ["<inline data>"],
                      "resolution": "720p", "audio_asset_id": "abc123", "kind": "lipsync"}
    assert done["asset"]["cost"] == 0.25 and done["asset"]["kind"] == "video"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd engine && .venv/bin/pytest tests/test_media_api.py -q -k "redact or job_records"`
Expected: FAIL with `AttributeError: module 'champstudio.jobs' has no attribute 'redact'`.

- [ ] **Step 3: Implement kinds and redaction**

In `engine/champstudio/jobs.py`, replace `start_video_job` and add `redact`:

```python
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
```

And in `_poll_loop`, rename the third parameter and use it for the saved params:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_media_api.py tests/test_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/champstudio/jobs.py engine/tests/test_media_api.py
git commit -m "feat(jobs): job kinds + redact inline data URLs from stored params

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `POST /api/lipsync`

**Files:**
- Modify: `engine/champstudio/main.py` (videos section)
- Modify: `engine/tests/test_media_api.py` (append)

**Interfaces:**
- Consumes: `jobs.start_video_job(..., kind, extra_params)` (Task 5); `OpenRouterClient.submit_video(input_references, audio_references, ...)` (Task 2).
- Produces: `POST /api/lipsync {image_asset_id, audio_asset_id, model?="heygen/avatar-iv", aspect_ratio?, resolution?, prompt?} -> {"job_id", "status"}`; the job appears in `GET /api/videos/jobs` with `kind == "lipsync"`; the finished video asset's `parent_id` is the portrait.
- Produces: helper `_asset_data_url(asset_id: str, expect_kind: str) -> tuple[dict, str]` raising 404 when missing and 400 when the kind is wrong; `/api/videos` now uses it too.

- [ ] **Step 1: Write the failing tests**

Append to `engine/tests/test_media_api.py`:

```python
def test_lipsync_without_key_fails_cleanly(client, portrait, voice_track):
    r = client.post("/api/lipsync", json={"image_asset_id": portrait, "audio_asset_id": voice_track})
    assert r.status_code == 502 and "API key" in r.json()["detail"]


def test_lipsync_validates_assets(client, with_key, portrait, voice_track):
    r = client.post("/api/lipsync", json={"image_asset_id": "nope", "audio_asset_id": voice_track})
    assert r.status_code == 404
    r = client.post("/api/lipsync", json={"image_asset_id": voice_track, "audio_asset_id": portrait})
    assert r.status_code == 400
    assert "expected image" in r.json()["detail"]


def test_lipsync_submits_portrait_and_audio_references(client, with_key, portrait, voice_track, monkeypatch):
    seen = {}

    async def fake_submit(self, **kwargs):
        seen.update(kwargs)
        return "gen-vid-lip"

    async def fake_poll(self, job_id):
        return {"status": "in_progress"}

    monkeypatch.setattr(OpenRouterClient, "submit_video", fake_submit)
    monkeypatch.setattr(OpenRouterClient, "poll_video", fake_poll)
    r = client.post("/api/lipsync", json={
        "image_asset_id": portrait, "audio_asset_id": voice_track,
        "aspect_ratio": "9:16", "resolution": "720p",
    })
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    assert seen["model"] == "heygen/avatar-iv" and seen["prompt"] == ""
    assert seen["aspect_ratio"] == "9:16" and seen["resolution"] == "720p"
    assert seen["input_references"][0].startswith("data:image/png;base64,")
    assert seen["audio_references"][0].startswith("data:audio/mpeg;base64,")
    job = client.get(f"/api/videos/jobs/{job_id}").json()
    assert job["kind"] == "lipsync" and job["parent_id"] == portrait
    assert any(j["id"] == job_id for j in client.get("/api/videos/jobs").json()["jobs"])


def test_image_to_video_uses_first_frame_keyword(client, with_key, portrait, monkeypatch):
    seen = {}

    async def fake_submit(self, **kwargs):
        seen.update(kwargs)
        return "gen-vid-i2v"

    async def fake_poll(self, job_id):
        return {"status": "in_progress"}

    monkeypatch.setattr(OpenRouterClient, "submit_video", fake_submit)
    monkeypatch.setattr(OpenRouterClient, "poll_video", fake_poll)
    r = client.post("/api/videos", json={"prompt": "slow zoom", "image_asset_id": portrait})
    assert r.status_code == 200, r.text
    assert seen["first_frame"].startswith("data:image/png;base64,")
    assert "frame_images" not in seen and "image_url" not in seen
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd engine && .venv/bin/pytest tests/test_media_api.py -q -k "lipsync or first_frame"`
Expected: lipsync tests FAIL with 404 (route missing); `first_frame` test PASSES already (Task 2) — that is fine, it guards the refactor below.

- [ ] **Step 3: Add the helper, refactor `/api/videos`, add `/api/lipsync`**

In `engine/champstudio/main.py`, videos section, add the helper before `class VideoRequest`:

```python
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
```

Replace the `if body.image_asset_id:` block in `generate_video` with:

```python
    if body.image_asset_id:
        _, data_url = _asset_data_url(body.image_asset_id, "image")
        if client.provider == "openrouter":
            submit_kwargs["first_frame"] = data_url
        else:
            submit_kwargs["image_url"] = data_url
```

Add after the `video_job` route (end of the videos section):

```python
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
```

- [ ] **Step 4: Run the full suite**

Run: `cd engine && .venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/champstudio/main.py engine/tests/test_media_api.py
git commit -m "feat(api): POST /api/lipsync — portrait + voice track through heygen/avatar-iv

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Web UI — Create tabs and the Voice pane

**Files:**
- Modify: `web/index.html:46-80` (Create section)
- Modify: `web/app.js` (`modelCombo`, new `createTabs`, new `voice`, `mediaFor`, `assetCard`, library filters)
- Modify: `web/styles.css` (append)

**Interfaces:**
- Consumes: `GET /api/models/speech`, `POST /api/audio`.
- Produces: `createTabs.activate(name)` and an `onTab` registry (`onTab.lipsync` is set in Task 8); `modelCombo` gains `onChange(model)` and a `model` getter; `mediaFor` renders `<audio>` for `kind === "audio"`.

- [ ] **Step 1: Restructure the Create section markup**

In `web/index.html`, replace the whole `<section class="view" id="view-create">…</section>` with:

```html
    <!-- ══ CREATE ══ -->
    <section class="view" id="view-create">
      <header class="view-head">
        <h1>Create</h1>
        <p>Images, voiceovers and talking heads. <kbd>⌘</kbd><kbd>↵</kbd> to run.</p>
      </header>
      <div class="tab-row" id="create-tabs" role="tablist">
        <button class="tab" role="tab" data-tab="image">Image</button>
        <button class="tab" role="tab" data-tab="audio">Voice</button>
        <button class="tab" role="tab" data-tab="lipsync">Lip Sync</button>
      </div>

      <div class="tab-pane" data-pane="image">
        <div class="prompt-dock glass">
          <textarea id="create-prompt" rows="1" placeholder="Describe the image you want — subject, style, lighting…"></textarea>
          <button class="btn ember big" id="create-go">Generate</button>
        </div>
        <div class="controls-row" id="create-controls">
          <div class="ctl" id="create-model-slot"><label>Model</label></div>
          <div class="ctl"><label>Aspect</label><div class="chip-row" id="create-aspect"></div></div>
          <div class="ctl"><label>Resolution</label>
            <select id="create-res" class="sel">
              <option value="">Auto</option><option value="512">512</option>
              <option value="1K">1K</option><option value="2K">2K</option><option value="4K">4K</option>
            </select></div>
          <div class="ctl"><label>Count</label>
            <select id="create-count" class="sel"><option>1</option><option>2</option><option>3</option><option>4</option></select></div>
          <div class="ctl"><label>Format</label>
            <select id="create-format" class="sel">
              <option value="">Default</option><option value="png">PNG</option><option value="jpeg">JPEG</option>
              <option value="webp">WebP</option><option value="svg">SVG</option>
            </select></div>
          <div class="ctl"><label>Transparent BG</label>
            <button class="switch" id="create-transparent" role="switch" aria-checked="false"><span></span></button></div>
          <div class="ctl"><label>Reference</label>
            <button class="btn ghost" id="create-ref-btn">+ Add image</button></div>
        </div>
        <div class="ref-strip" id="create-refs" hidden></div>
        <div class="results-grid" id="create-results"></div>
        <div class="empty-state" id="create-empty">
          <div class="empty-mark">✦</div>
          <h3>Ready when you are</h3>
          <p>Type a prompt above and hit <strong>Generate</strong> — results land here and in your Library.</p>
        </div>
      </div>

      <div class="tab-pane" data-pane="audio" hidden>
        <div class="prompt-dock glass">
          <textarea id="audio-text" rows="2" placeholder="Paste the script to voice — up to 4,000 characters…"></textarea>
          <button class="btn ember big" id="audio-go">Voice it</button>
        </div>
        <div class="controls-row">
          <div class="ctl" id="audio-model-slot"><label>Model</label></div>
          <div class="ctl"><label>Voice</label><select id="audio-voice" class="sel"></select></div>
          <div class="ctl"><label>Speed</label>
            <div class="num-wrap"><input type="number" id="audio-speed" class="sel num" min="0.5" max="2" step="0.1" value="1"><span>×</span></div></div>
        </div>
        <div class="results-grid" id="audio-results"></div>
        <div class="empty-state" id="audio-empty">
          <div class="empty-mark">♪</div>
          <h3>No voiceovers yet</h3>
          <p>Paste a script and hit <strong>Voice it</strong>. Every take is saved to your Library as audio.</p>
        </div>
      </div>

      <div class="tab-pane" data-pane="lipsync" hidden>
        <div class="empty-state">
          <div class="empty-mark">☺</div>
          <h3>Lip Sync arrives in the next task</h3>
        </div>
      </div>
    </section>
```

- [ ] **Step 2: Extend `modelCombo` with `onChange` and a `model` getter**

In `web/app.js`, change the signature line to:

```js
function modelCombo(slot, { endpoint, storeKey, fallback, onChange }) {
```

Replace the `select` function inside it with:

```js
  function select(m) {
    value = m.id; valueName = m.name || m.id;
    setPref(storeKey, value); setPref(storeKey + ".name", valueName);
    nameSpan.textContent = valueName;
    closePop();
    onChange?.(m);
  }
```

Replace the async loader IIFE at the end of `modelCombo` with:

```js
  (async () => {
    try {
      const list = await api(endpoint);
      models = Array.isArray(list) && list.length ? list : [fallback];
    } catch { models = [fallback]; }
    const current = models.find((m) => m.id === value);
    if (current) onChange?.(current); else select(models[0]);
  })();

  return {
    get value() { return value; },
    get model() { return models.find((m) => m.id === value) || null; },
  };
```

- [ ] **Step 3: Add the tab controller and Voice pane**

In `web/app.js`, directly above the `/* ── CREATE view ─── */` comment, insert:

```js
/* ── CREATE tabs ────────────────────────────────────────────────── */

const onTab = {}; // tab name -> fn, run when the tab becomes visible
const createTabs = (() => {
  const tabs = $$("#create-tabs .tab");
  const panes = $$("#view-create .tab-pane");
  function activate(name) {
    if (!tabs.some((t) => t.dataset.tab === name)) name = "image";
    tabs.forEach((t) => {
      const on = t.dataset.tab === name;
      t.classList.toggle("active", on);
      t.setAttribute("aria-selected", String(on));
    });
    panes.forEach((p) => { p.hidden = p.dataset.pane !== name; });
    setPref("create.tab", name);
    onTab[name]?.();
  }
  tabs.forEach((t) => t.addEventListener("click", () => activate(t.dataset.tab)));
  document.body.addEventListener("click", (e) => {
    const go = e.target.closest("[data-create-tab]");
    if (go) { show("create"); activate(go.dataset.createTab); }
  });
  activate(pref("create.tab", "image"));
  onEnter.create = () => onTab[pref("create.tab", "image")]?.();
  return { activate };
})();
```

Directly below the `create` IIFE (after `})();` that ends `const create = ...`), insert:

```js
/* ── VOICE pane (text-to-speech) ────────────────────────────────── */

const voice = (() => {
  const textEl = $("#audio-text"), goBtn = $("#audio-go");
  const results = $("#audio-results"), emptyEl = $("#audio-empty");
  const voiceSel = $("#audio-voice"), speedEl = $("#audio-speed");
  autoGrow(textEl);
  speedEl.value = pref("audio.speed", 1);
  speedEl.onchange = () => setPref("audio.speed", Number(speedEl.value));

  function paintVoices(m) {
    const voices = m?.voices || [];
    const remembered = pref(`audio.voice.${m?.id}`, voices[0] || "");
    voiceSel.replaceChildren(...(voices.length
      ? voices.map((v) => el("option", { value: v, selected: v === remembered }, v))
      : [el("option", { value: "" }, "Provider default")]));
    voiceSel.disabled = !voices.length;
  }
  const model = modelCombo($("#audio-model-slot"), {
    endpoint: "/api/models/speech", storeKey: "audio.model",
    fallback: {
      id: "google/gemini-3.8-flash-tts", name: "Gemini 3.8 Flash TTS", provider: "openrouter", vendor: "google",
      voices: ["Kore", "Puck", "Zephyr", "Charon", "Fenrir", "Leda"],
    },
    onChange: paintVoices,
  });
  voiceSel.onchange = () => setPref(`audio.voice.${model.value}`, voiceSel.value);

  async function synthesize() {
    const text = textEl.value.trim();
    if (!text) { toast("Paste a script first.", "error"); textEl.focus(); return; }
    emptyEl.hidden = true;
    const shimmer = el("div", { class: "shimmer-card" }, el("div", { class: "shimmer" }), el("div", { class: "shimmer bar" }));
    results.prepend(shimmer);
    goBtn.disabled = true; goBtn.classList.add("busy");
    try {
      const payload = { text, model: model.value };
      if (voiceSel.value) payload.voice = voiceSel.value;
      const speed = Number(speedEl.value);
      if (speed && speed !== 1) payload.speed = speed;
      const { assets } = await api("/api/audio", { method: "POST", body: payload });
      shimmer.remove();
      results.prepend(...assets.map((a) => assetCard(a)));
      toast("Voiceover ready", "ok");
    } catch (e) {
      shimmer.remove();
      if (!results.children.length) emptyEl.hidden = false;
      toast(e.message, "error", 7000);
    } finally {
      goBtn.disabled = false; goBtn.classList.remove("busy");
    }
  }
  goBtn.addEventListener("click", synthesize);
  textEl.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); synthesize(); }
  });
  return {};
})();
```

- [ ] **Step 4: Make cards, the detail modal and the library understand audio**

In `web/app.js`, replace `mediaFor` with:

```js
function mediaFor(asset, { controls = false } = {}) {
  if (asset.kind === "video") {
    return el("video", { class: "media", src: asset.url, controls, muted: !controls, playsinline: true, preload: "metadata" });
  }
  if (asset.kind === "audio") {
    return el("div", { class: "media audio-tile" },
      el("span", { class: "audio-glyph" }, "♪"),
      el("audio", { src: asset.url, controls: true, preload: "metadata", onclick: (e) => e.stopPropagation() }));
  }
  return el("img", { class: "media", src: asset.url, alt: asset.prompt || "asset", loading: "lazy" });
}
```

In `assetCard`, change the actions block so audio gets a Lip Sync button:

```js
  const actions = [];
  if (asset.kind === "image" || asset.kind === "vector") {
    actions.push(el("button", { class: "btn sm", onclick: () => sendToAnimate(asset) }, "Animate"));
    actions.push(el("button", { class: "btn sm", onclick: () => sendToLab(asset) }, "Lab"));
  }
  if (asset.kind === "audio" || asset.kind === "image") {
    actions.push(el("button", { class: "btn sm", onclick: () => sendToLipSync(asset) }, "Lip Sync"));
  }
```

In `openDetail`, add a Lip Sync action to the `actions` element, right after the `canDerive && … "Open in Lab"` line:

```js
    (asset.kind === "audio" || asset.kind === "image") && el("button", { class: "btn", onclick: () => { close(); sendToLipSync(asset); } }, "Lip Sync"),
```

In the `library` IIFE, add an Audio chip between Videos and Vectors:

```js
    { value: "video", label: "Videos" },
    { value: "audio", label: "Audio" },
    { value: "vector", label: "Vectors" },
```

In `pickFromLibrary`, replace the `usable.map(...)` expression so audio assets render as tiles:

```js
        ? usable.map((a) => el("button", { class: "pick", title: a.prompt || a.filename || a.id, onclick: () => done(a) },
            a.kind === "audio"
              ? el("div", { class: "pick-audio" }, el("span", {}, "♪"), el("small", {}, (a.prompt || a.filename || a.id).slice(0, 60)))
              : el("img", { src: a.url, alt: "", loading: "lazy" })))
```

Add a temporary stub directly above the `/* ── ANIMATE view ─── */` comment (Task 8 replaces it):

```js
function sendToLipSync(asset) { show("create"); createTabs.activate("lipsync"); toast(`Lip Sync pane lands in the next task (${asset.kind})`); }
```

- [ ] **Step 5: Append styles**

Append to `web/styles.css`:

```css
/* ── create tabs ────────────────────────────────────────────────── */
.tab-row { display: flex; gap: 4px; margin: -6px 0 20px; border-bottom: 1px solid var(--champ-slate); }
.tab {
  background: none; border: 0; border-bottom: 2px solid transparent; margin-bottom: -1px;
  color: var(--champ-mist); padding: 8px 14px; font: 600 13px var(--font-body); cursor: pointer;
}
.tab:hover { color: var(--champ-cloud); }
.tab.active { color: var(--ember-hover); border-bottom-color: var(--ember); }

/* ── audio assets ───────────────────────────────────────────────── */
.audio-tile { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; padding: 14px; }
.audio-tile .audio-glyph { font-size: 40px; color: var(--ember); line-height: 1; }
.audio-tile audio { width: 100%; height: 34px; }
.badge.audio { color: var(--amber); border-color: rgba(245, 158, 11, .4); }
.picker-grid .pick-audio {
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px;
  aspect-ratio: 1; padding: 8px; text-align: center; color: var(--champ-cloud); font-size: 30px;
}
.picker-grid .pick-audio small { font: 11px var(--font-mono); color: var(--champ-mist); overflow-wrap: anywhere; }
.detail-media .audio-tile { min-height: 200px; }
```

- [ ] **Step 6: Verify in the browser**

Start the engine: `cd "/Users/deep/Apps&Projects/Champ Studio" && scripts/run-local.sh` (or `engine/.venv/bin/python -m champstudio.main`). Open `http://127.0.0.1:8787/#/create`.

Check:
1. Three tabs render; Image tab shows the unchanged image form; switching tabs survives a reload (pref).
2. Voice tab: model combo shows "Gemini 3.8 Flash TTS" (fallback when no key), the Voice select lists Kore/Puck/…, Speed defaults to 1.
3. With no key, "Voice it" shows the toast `OpenRouter API key not set — add it in Settings`.
4. Make a test track and upload it (the Lab file input only accepts images): `ffmpeg -y -f lavfi -i "sine=frequency=440:duration=2" /tmp/tone.mp3 && curl -F "file=@/tmp/tone.mp3;type=audio/mpeg" http://127.0.0.1:8787/api/assets/upload` — expect JSON with `"kind":"audio"` — then open Library, click the Audio chip, confirm the tile shows ♪ with a working player and a Lip Sync button.
5. Browser console shows no errors.

- [ ] **Step 7: Commit**

```bash
git add web/index.html web/app.js web/styles.css
git commit -m "feat(web): Create tabs + Voice pane (text-to-speech) + audio-aware cards

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Web UI — Lip Sync pane

**Files:**
- Modify: `web/index.html` (replace the lipsync placeholder pane)
- Modify: `web/app.js` (hoist `jobCard`, add `lipsync`, replace `sendToLipSync` stub, filter Animate's job list)
- Modify: `web/styles.css` (append)

**Interfaces:**
- Consumes: `POST /api/lipsync`, `GET /api/videos/jobs` (jobs carry `kind`), `POST /api/assets/upload`, `createTabs`, `onTab`.
- Produces: `lipsync.setImage(asset)`, `lipsync.setAudio(asset)`, `lipsync.refreshJobs()`; `sendToLipSync(asset)` routes an image to the portrait slot and audio to the voice slot; a module-level `jobCard(job)`.

- [ ] **Step 1: Replace the placeholder pane**

In `web/index.html`, replace the `<div class="tab-pane" data-pane="lipsync" hidden>…</div>` block with:

```html
      <div class="tab-pane" data-pane="lipsync" hidden>
        <div class="lipsync-setup glass">
          <div class="ls-slot">
            <label>Portrait</label>
            <div class="ls-preview" id="ls-image-preview"></div>
            <div class="drop-actions">
              <button class="btn ghost sm" id="ls-image-pick">From library</button>
              <button class="btn ghost sm" id="ls-image-upload">Upload</button>
              <input type="file" id="ls-image-file" accept="image/*" hidden>
            </div>
          </div>
          <div class="ls-slot">
            <label>Voice track</label>
            <div class="ls-preview" id="ls-audio-preview"></div>
            <div class="drop-actions">
              <button class="btn ghost sm" id="ls-audio-pick">From library</button>
              <button class="btn ghost sm" id="ls-audio-upload">Upload</button>
              <button class="btn ghost sm" data-create-tab="audio">Make one in Voice</button>
              <input type="file" id="ls-audio-file" accept="audio/*" hidden>
            </div>
          </div>
        </div>
        <div class="controls-row">
          <div class="ctl"><label>Aspect</label><div class="chip-row" id="ls-aspect"></div></div>
          <div class="ctl"><label>Resolution</label>
            <select id="ls-res" class="sel"><option value="720p">720p</option><option value="1080p">1080p</option></select></div>
          <div class="ctl"><label>Model</label><span class="sel static">heygen/avatar-iv · $0.05/s</span></div>
          <div class="ctl"><label>&nbsp;</label><button class="btn ember big" id="ls-go" disabled>Sync lips</button></div>
        </div>
        <h2 class="section-title">Lip sync jobs</h2>
        <div class="jobs-list" id="ls-jobs"></div>
        <div class="empty-state" id="ls-empty">
          <div class="empty-mark">☺</div>
          <h3>No talking heads yet</h3>
          <p>Pick a portrait and a voice track, then hit <strong>Sync lips</strong>. Finished videos land in your Library.</p>
        </div>
      </div>
```

- [ ] **Step 2: Hoist `jobCard` out of the Animate closure**

In `web/app.js`, cut the `function jobCard(job) { … }` definition out of the `animate` IIFE and paste it, unchanged, directly above the `/* ── ANIMATE view ─── */` comment under a new heading:

```js
/* ── shared job card (Animate + Lip Sync) ───────────────────────── */

function jobCard(job) {
  const st = job.status || "pending";
  const media = st === "completed" && job.asset
    ? el("div", { class: "job-media" }, el("video", { src: job.asset.url, controls: true, playsinline: true, preload: "metadata" }))
    : el("div", { class: "job-media" }, el("div", { class: "job-wait" },
        st === "failed" ? el("span", { style: "font-size:22px" }, "✕") : el("div", { class: "pulse-ring" }),
        st === "failed" ? "render failed" : st === "pending" ? "queued…" : "rendering…"));
  return el("div", { class: "job-card", dataset: { job: job.id } },
    media,
    el("div", { class: "job-info" },
      el("div", { class: "job-top" },
        el("span", { class: `status-pill ${st}` }, st.replace("_", " ")),
        el("span", { class: "job-model" }, shortModel(job.model)),
        el("span", { class: "job-date" }, fmtDate(job.created_at))),
      el("p", { class: "prompt", title: job.prompt || "" }, job.prompt || (job.kind === "lipsync" ? "Lip sync" : "")),
      job.error ? el("div", { class: "job-error" }, job.error) : [],
      st === "completed" && job.asset ? el("div", { class: "job-actions" },
        el("button", { class: "btn sm", onclick: () => download(job.asset) }, "Download"),
        el("button", { class: "btn sm", onclick: () => openDetail(job.asset.id) }, "Details")) : []));
}
```

In the `animate` IIFE's `refreshJobs`, keep lip-sync jobs out of the Animate list:

```js
    jobs = (jobs || []).filter((j) => j.kind !== "lipsync");
```

(replacing the line `jobs = jobs || [];`).

- [ ] **Step 3: Add the Lip Sync pane controller and the real `sendToLipSync`**

Delete the stub `function sendToLipSync(asset) { … }` added in Task 7. Then insert, directly below the `voice` IIFE:

```js
/* ── LIP SYNC pane (portrait + voice track → talking head) ──────── */

const lipsync = (() => {
  const goBtn = $("#ls-go"), list = $("#ls-jobs"), emptyEl = $("#ls-empty");
  const imgPrev = $("#ls-image-preview"), audPrev = $("#ls-audio-preview");
  const aspect = chipGroup($("#ls-aspect"),
    ["9:16", "16:9", "1:1"].map((v) => ({ value: v, label: v })), "lipsync.aspect", "9:16");
  const resSel = $("#ls-res");
  resSel.value = pref("lipsync.res", "720p");
  resSel.onchange = () => setPref("lipsync.res", resSel.value);
  let image = null, audio = null; // Asset | null

  function paint() {
    imgPrev.replaceChildren(image
      ? el("img", { src: image.url, alt: "Portrait" })
      : el("span", { class: "empty-mark" }, "☺"));
    audPrev.replaceChildren(audio
      ? el("audio", { src: audio.url, controls: true, preload: "metadata" })
      : el("span", { class: "empty-mark" }, "♪"));
    goBtn.disabled = !(image && audio);
  }
  function setImage(a) { image = a; paint(); }
  function setAudio(a) { audio = a; paint(); }

  async function uploadTo(setter, file, kind) {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
      toast(`Uploading ${file.name}…`);
      const asset = await api("/api/assets/upload", { method: "POST", form });
      if (asset.kind !== kind) { toast(`That file is ${asset.kind}, not ${kind}.`, "error"); return; }
      setter(asset);
      toast("Uploaded", "ok");
    } catch (e) { toast(e.message, "error"); }
  }
  $("#ls-image-pick").addEventListener("click", async () => { const a = await pickFromLibrary({ kinds: ["image"] }); if (a) setImage(a); });
  $("#ls-audio-pick").addEventListener("click", async () => { const a = await pickFromLibrary({ kinds: ["audio"] }); if (a) setAudio(a); });
  $("#ls-image-upload").addEventListener("click", () => $("#ls-image-file").click());
  $("#ls-audio-upload").addEventListener("click", () => $("#ls-audio-file").click());
  $("#ls-image-file").addEventListener("change", (e) => { uploadTo(setImage, e.target.files[0], "image"); e.target.value = ""; });
  $("#ls-audio-file").addEventListener("change", (e) => { uploadTo(setAudio, e.target.files[0], "audio"); e.target.value = ""; });

  let pollTimer = null;
  async function refreshJobs() {
    let jobs;
    try { ({ jobs } = await api("/api/videos/jobs")); }
    catch { return; }
    jobs = (jobs || []).filter((j) => j.kind === "lipsync");
    emptyEl.hidden = jobs.length > 0;
    list.replaceChildren(...jobs.map(jobCard));
    const active = jobs.some((j) => j.status === "pending" || j.status === "in_progress");
    if (active && !pollTimer) pollTimer = setInterval(refreshJobs, 3000);
    if (!active && pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  async function submit() {
    if (!(image && audio)) { toast("Pick a portrait and a voice track first.", "error"); return; }
    goBtn.disabled = true; goBtn.classList.add("busy");
    try {
      await api("/api/lipsync", { method: "POST", body: {
        image_asset_id: image.id, audio_asset_id: audio.id,
        aspect_ratio: aspect.value, resolution: resSel.value,
      } });
      toast("Lip sync job queued", "ok");
      refreshJobs();
    } catch (e) {
      toast(e.message, "error", 7000);
    } finally {
      goBtn.classList.remove("busy"); paint();
    }
  }
  goBtn.addEventListener("click", submit);
  onTab.lipsync = refreshJobs;
  paint();
  return { setImage, setAudio, refreshJobs };
})();

function sendToLipSync(asset) {
  if (asset.kind === "audio") lipsync.setAudio(asset); else lipsync.setImage(asset);
  show("create");
  createTabs.activate("lipsync");
}
```

- [ ] **Step 4: Append styles**

Append to `web/styles.css`:

```css
/* ── lip sync setup ─────────────────────────────────────────────── */
.lipsync-setup {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 18px;
  padding: 18px; border-radius: var(--radius-lg); margin-bottom: 20px;
}
.ls-slot > label { display: block; font: 600 10.5px var(--font-body); letter-spacing: .09em; text-transform: uppercase; color: var(--champ-mist); margin-bottom: 8px; }
.ls-preview {
  height: 190px; border-radius: var(--radius); background: var(--champ-carbon); border: 1px dashed var(--champ-steel);
  display: flex; align-items: center; justify-content: center; margin-bottom: 10px; overflow: hidden; padding: 10px;
}
.ls-preview .empty-mark { margin: 0; }
.ls-preview img { max-width: 100%; max-height: 100%; object-fit: contain; border-radius: 8px; }
.ls-preview audio { width: 100%; }
.sel.static { display: inline-flex; align-items: center; font: 12px var(--font-mono); color: var(--champ-cloud); }
```

- [ ] **Step 5: Verify in the browser**

With the engine running (`scripts/run-local.sh`), open `http://127.0.0.1:8787/#/create`, Lip Sync tab:
1. Both slots show placeholders; "Sync lips" is disabled.
2. Upload a portrait via the Upload button (any PNG); the preview shows it.
3. "From library" on the voice slot lists only audio assets (the ♪ tiles from Task 7's curl upload); pick one; the player appears; "Sync lips" enables.
4. Click "Sync lips" with no key: toast `OpenRouter API key not set — add it in Settings`; the button re-enables.
5. From Library, an audio card's "Lip Sync" button lands in this tab with the track pre-filled; an image card's "Lip Sync" pre-fills the portrait.
6. Animate's job list still renders its own jobs; no console errors.

- [ ] **Step 6: Commit**

```bash
git add web/index.html web/app.js web/styles.css
git commit -m "feat(web): Lip Sync pane — portrait + voice track → heygen/avatar-iv job

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Docs, version, and the live smoke test

**Files:**
- Modify: `README.md`
- Modify: `engine/champstudio/config.py:16`
- Modify: `docs/consolidation/phase-0-findings.md` (Open items)

**Interfaces:** none.

- [ ] **Step 1: Bump the engine version**

In `engine/champstudio/config.py` set `ENGINE_VERSION = "1.1.0"`.

- [ ] **Step 2: Document the two features in the README**

Find the README section that lists what the web UI / engine can do (search for `Derivative Lab`) and add, in the same style as its neighbours:

```markdown
- **Voice** — paste a script, pick a model and voice, get an mp3 in the Library (`POST /api/audio`, OpenRouter `/audio/speech`).
- **Lip Sync** — a portrait plus a voice track becomes a talking-head video through `heygen/avatar-iv` on OpenRouter (`POST /api/lipsync`). No HeyGen account needed; billed per second on the OpenRouter key.
```

And in the API endpoint list (search for `/api/transform`), add:

```markdown
| `GET /api/models/speech` | Text-to-speech models with their voices |
| `POST /api/audio` | `{text, model?, voice?, speed?}` → audio asset |
| `POST /api/lipsync` | `{image_asset_id, audio_asset_id, aspect_ratio?, resolution?}` → video job |
```

If the README has no endpoint table, add the three lines as a bullet list under the feature entries instead.

- [ ] **Step 3: Run the whole suite and a fresh engine boot**

Run: `cd engine && .venv/bin/pytest -q`
Expected: PASS.

Run: `curl -s http://127.0.0.1:8787/api/health` (engine running via `scripts/run-local.sh`)
Expected: `"version":"1.1.0"`.

- [ ] **Step 4: Live smoke test (needs a real OpenRouter key — skip and leave the checklist unticked if none is available)**

Costs under $0.50 in total. Save the key through the UI Settings page (never on the command line), then:

```bash
# 1. voice — expect an audio asset and an audible mp3
curl -s -X POST http://127.0.0.1:8787/api/audio -H 'Content-Type: application/json' \
  -d '{"text":"One engine, one library, one set of keys.","voice":"Kore"}' | tee /tmp/audio.json
AUDIO_ID=$(python3 -c "import json;print(json.load(open('/tmp/audio.json'))['assets'][0]['id'])")
curl -s -o /tmp/vo.mp3 "http://127.0.0.1:8787/api/assets/$AUDIO_ID/file" && afplay /tmp/vo.mp3

# 2. image-to-video with the corrected frame_images shape — pick any image asset id from the Library
curl -s -X POST http://127.0.0.1:8787/api/videos -H 'Content-Type: application/json' \
  -d '{"prompt":"subtle head turn","model":"alibaba/wan-3.0","duration":2,"resolution":"480p","image_asset_id":"<IMAGE_ID>"}'

# 3. lip sync — a portrait image asset + the audio asset above
curl -s -X POST http://127.0.0.1:8787/api/lipsync -H 'Content-Type: application/json' \
  -d '{"image_asset_id":"<IMAGE_ID>","audio_asset_id":"'"$AUDIO_ID"'","aspect_ratio":"9:16","resolution":"720p"}'
curl -s http://127.0.0.1:8787/api/videos/jobs | python3 -m json.tool | head -40
```

Record the outcome of each call in `docs/consolidation/phase-0-findings.md` under **Open items**, replacing item 1 with one of:

- `1. Live test 2026-MM-DD: voice ✓, image-to-video ✓, lip sync ✓ (data URLs accepted).`
- or the exact error text returned, plus which payload field it names.

If lip sync rejects data URLs or the audio reference, the fix belongs in `engine/champstudio/main.py` `lipsync()` (for example, `prompt` carrying the script and `provider_options={"heygen": {"voice_id": …}}` instead of an audio reference) and in `test_media_api.py::test_lipsync_submits_portrait_and_audio_references`; record the finding first, then adjust.

- [ ] **Step 5: Commit**

```bash
git add README.md engine/champstudio/config.py docs/consolidation/phase-0-findings.md
git commit -m "docs: Voice + Lip Sync usage, engine 1.1.0, live test record

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Done when

- `cd engine && .venv/bin/pytest -q` passes, including `tests/test_openrouter.py` and `tests/test_media_api.py`.
- In the browser: Create → Voice turns a script into an audio card; Create → Lip Sync takes that card plus a portrait and queues a `heygen/avatar-iv` job that shows in its own job list.
- No base64 in `library.db` `params` (check: `sqlite3 ~/.champstudio/library.db "select count(*) from assets where params like '%data:%'"` returns 0).
- Branch `feat/phase-1-audio-voice` has one commit per task; nothing on `main`.
