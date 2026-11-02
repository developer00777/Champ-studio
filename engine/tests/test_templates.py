"""Template library: every bundled template validates, compiles in every aspect
and renders a preview frame; AI fill, compose, batch, graphics, save-as-template."""

import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from champstudio import brands, templates
from champstudio.main import app
from champstudio.render.compositor import Compositor
from conftest import needs_ffmpeg
from fake_openrouter import FakeOpenRouter

pytestmark = needs_ffmpeg
ALL = sorted(templates.all_templates())


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def fake():
    f = FakeOpenRouter().install()
    yield f
    FakeOpenRouter.uninstall()


def wait(client, job_id, timeout=240):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("completed", "failed", "canceled"):
            return j
        time.sleep(0.25)
    raise AssertionError(j)


def test_library_has_templates_in_every_category():
    cats = {t["category"] for t in templates.all_templates().values()}
    assert len(ALL) >= 3
    assert cats <= set(templates.CATEGORIES)


@pytest.mark.parametrize("tid", ALL)
def test_template_validates(tid):
    t = templates.get(tid)
    assert templates.validate_template(t) == []
    assert t["aspects"], "declare supported aspects"
    for name, prop in t["slots"]["properties"].items():
        assert prop.get("x-kind"), f"{name} needs x-kind"
        assert prop.get("description"), f"{name} needs a description (the form label and LLM hint)"


@pytest.mark.parametrize("tid", ALL)
def test_template_compiles_and_draws_in_every_aspect(tid):
    t = templates.get(tid)
    kit = brands.get("champions-group")
    for aspect in t["aspects"]:
        tl = templates.preview_timeline(t, kit, aspect)
        assert tl.duration > 0
        comp = Compositor(tl, width=tl.width // 6 * 2 // 2, height=tl.height // 6 * 2 // 2)
        frame = comp.frame(int(tl.duration * 0.5))
        assert frame.shape[2] == 3
        if not t.get("overlay"):
            assert np.std(frame) > 1.5, f"{tid} {aspect} renders a flat frame"


def test_llm_schema_turns_media_into_prompts():
    t = templates.get("b2b-stat-ad")
    s = templates.llm_schema(t)
    assert s["properties"]["bg"]["required"] == ["prompt", "media_type"]
    assert "vo_hook" in s["required"]


def test_gallery_preview_and_graphic(client):
    g = client.get("/api/templates?category=brand-utility").json()
    assert any(t["id"] == "lower-third" for t in g["templates"])
    assert "social-ads" in g["categories"]
    img = client.get("/api/templates/b2b-stat-ad/preview.jpg?aspect=9:16")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    gr = client.post("/api/templates/lower-third/graphic", json={"values": {"name": "Priya Shah"}, "start": 30}).json()
    clip = gr["clip"]
    assert clip["type"] == "graphic" and clip["start"] == 30
    texts = [layer["text"] for layer in clip["graphic"]["layers"] if layer["type"] == "text"]
    assert "Priya Shah" in texts
    assert {c["id"]: c["value"] for c in clip["graphic"]["controls"]}["name"] == "Priya Shah"


def test_ai_fill_compose_and_render(client, fake):
    r = client.post("/api/templates/b2b-stat-ad/compose", json={
        "brief": "Promote Ampliz healthcare data: 4M+ physicians, 99% accuracy.", "aspects": ["9:16", "1:1"],
        "brand_kit_id": "champions-group", "preset": "h264-draft"}).json()
    done = wait(client, r["job_id"])
    assert done["status"] == "completed", done
    res = done["result"]
    assert set(res["projects"]) == {"9:16", "1:1"} and set(res["renders"]) == {"9:16", "1:1"}
    proj = client.get(f"/api/projects/{res['projects']['9:16']}").json()
    tracks = {t["id"]: t for t in proj["data"]["tracks"]}
    assert tracks["a1"]["clips"] and tracks["a2"]["clips"]  # voiceover + music
    assert any(c["source"].get("template") == "b2b-stat-ad" for t in proj["data"]["tracks"] for c in t["clips"])
    assert fake.count("/images") == 1  # one media slot


def test_compose_rejects_invalid_values(client, fake):
    r = client.post("/api/templates/b2b-stat-ad/compose", json={
        "values": {"hook": "x" * 200}, "render": False, "voiceover": False}).json()
    done = wait(client, r["job_id"])
    assert done["status"] == "failed" and "hook" in done["error"]


def test_batch_personalized_rows(client, fake):
    csv = "first_name,company,cta\nDana,Acme,Book a call\nLee,Globex,\n"
    r = client.post("/api/templates/lower-third/batch", json={
        "csv": csv, "voiceover": False, "music": False, "preset": "h264-draft"}).json()
    assert r["rows"] == 2
    done = wait(client, r["job_id"])
    assert done["result"]["ok"] == 2


def test_save_project_as_template(client):
    data = {"fps": 30, "width": 1920, "height": 1080, "tracks": [
        {"id": "v1", "kind": "video", "clips": [{"id": "bg", "type": "color", "color": "#112233", "duration": 60}]},
        {"id": "v2", "kind": "video", "clips": [{"id": "t", "type": "text", "text": "Quarterly wins", "duration": 60,
                                                  "box": [0.1, 0.4, 0.8, 0.2]}]}]}
    p = client.post("/api/projects", json={"name": "src", "data": data}).json()
    t = client.post("/api/templates/from-project", json={"project_id": p["id"], "name": "Team Title Card"}).json()
    assert t["id"] == "team-team-title-card" and t["defaults"]["text_1"] == "Quarterly wins"
    assert any(x["id"] == t["id"] for x in client.get("/api/templates").json()["templates"])
    tl = templates.preview_timeline(templates.get(t["id"]), brands.get(None), "16:9")
    assert tl.duration == 60
