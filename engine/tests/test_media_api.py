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
