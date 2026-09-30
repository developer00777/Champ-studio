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
