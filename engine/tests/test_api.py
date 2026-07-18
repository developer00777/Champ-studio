"""API tests against the FastAPI app with a temp data dir (no provider keys needed)."""

import io
import os
import tempfile

import pytest
from PIL import Image

_tmp = tempfile.mkdtemp(prefix="champstudio-test-")
os.environ["CHAMP_DATA_DIR"] = _tmp

from fastapi.testclient import TestClient  # noqa: E402

from champstudio.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def uploaded(client) -> str:
    img = Image.new("RGB", (64, 64), (255, 107, 53))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    r = client.post(
        "/api/assets/upload",
        files={"file": ("test.png", buf.getvalue(), "image/png")},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert set(body["keys"]) == {"openrouter", "muapi"}


def test_settings_roundtrip_masks_keys(client):
    r = client.put("/api/settings", json={"keys": {"openrouter": "sk-or-v1-secret123456"}})
    assert r.status_code == 200
    masked = r.json()["keys"]["openrouter"]
    assert "secret" not in masked and masked.startswith("sk-o")
    # clear it again so other tests see no key
    client.put("/api/settings", json={"keys": {"openrouter": ""}})
    assert client.get("/api/settings").json()["keys"]["openrouter"] is None


def test_transform_catalog(client):
    tools = {t["id"] for t in client.get("/api/transform").json()["tools"]}
    assert {"vectorize", "skeleton", "lineart", "remove-bg", "palette"} <= tools


def test_upload_and_library(client, uploaded):
    r = client.get("/api/assets")
    assert any(a["id"] == uploaded for a in r.json()["assets"])
    f = client.get(f"/api/assets/{uploaded}/file")
    assert f.status_code == 200 and f.headers["content-type"] == "image/png"


def test_transform_creates_lineage(client, uploaded):
    r = client.post("/api/transform/palette", json={"asset_id": uploaded, "params": {"colors": 3}})
    assert r.status_code == 200, r.text
    derived = r.json()["assets"]
    assert len(derived) == 3  # png + css + json
    detail = client.get(f"/api/assets/{derived[0]['id']}").json()
    assert [a["id"] for a in detail["lineage"]][0] == uploaded


def test_generation_without_keys_fails_cleanly(client):
    r = client.post("/api/images", json={"prompt": "a red panda"})
    assert r.status_code == 502
    assert "API key" in r.json()["detail"]


def test_unknown_tool_404(client, uploaded):
    assert client.post("/api/transform/nope", json={"asset_id": uploaded}).status_code == 404


def test_delete_asset(client):
    img = Image.new("RGB", (8, 8))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    r = client.post("/api/assets/upload", files={"file": ("x.png", buf.getvalue(), "image/png")})
    aid = r.json()["id"]
    assert client.delete(f"/api/assets/{aid}").status_code == 200
    assert client.get(f"/api/assets/{aid}").status_code == 404
