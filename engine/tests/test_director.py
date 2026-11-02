"""Director end to end against a fake OpenRouter: brief → plan → storyboard →
produce → timelines → render, chat edits that re-run only affected scenes,
budgets, and hand-off to the editor."""

import time

import pytest
from fastapi.testclient import TestClient

from champstudio import config, director, projects
from champstudio.main import app
from champstudio.render import media
from conftest import needs_ffmpeg
from fake_openrouter import FakeOpenRouter

pytestmark = needs_ffmpeg


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def fake():
    f = FakeOpenRouter().install()
    yield f
    FakeOpenRouter.uninstall()


def wait(client, job_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("completed", "failed", "canceled"):
            return j
        time.sleep(0.2)
    raise AssertionError(f"job stuck {j}")


def brief(**kw):
    return {"brief": {"prompt": "30s LinkedIn ad for verified B2B contact data", "length_s": 10, **kw},
            "aspects": ["16:9", "9:16"], "brand_kit_id": "champions-group"}


def test_full_pipeline_stage_by_stage(client, fake):
    r = client.post("/api/director", json=brief()).json()
    pid = r["project_id"]
    assert wait(client, r["job_id"])["status"] == "completed"
    p = client.get(f"/api/director/{pid}").json()
    plan = p["data"]["plan"]
    assert [s["visual_type"] for s in plan["scenes"]] == ["ai_video", "text_card", "ai_image"]
    assert all(s["id"] for s in plan["scenes"]) and p["estimate"]["usd"] > 0
    # duration is stretched to fit the voiceover word count
    assert plan["scenes"][1]["duration_s"] >= 8 / 2.5

    sb = client.post(f"/api/director/{pid}/storyboard", json={}).json()
    assert wait(client, sb["job_id"])["status"] == "completed"
    assert fake.count("/images") == 2  # only ai_video + ai_image scenes get stills

    prod = client.post(f"/api/director/{pid}/produce", json={}).json()
    done = wait(client, prod["job_id"])
    assert done["status"] == "completed", done
    data = client.get(f"/api/director/{pid}").json()["data"]
    assert set(data["timelines"]) == {"16:9", "9:16"}
    tl = data["timelines"]["9:16"]
    assert tl["width"] == 1080 and tl["height"] == 1920
    kinds = {t["id"]: [c["type"] for c in t["clips"]] for t in tl["tracks"]}
    assert kinds["v1"] == ["video", "shape", "image"]
    assert kinds["a1"] == ["audio"] * 3 and kinds["a2"] and kinds["v4"] == ["captions"]
    assert fake.count("/audio/speech") == 3 and fake.count("/videos") >= 1

    ren = client.post(f"/api/director/{pid}/render", json={"preset": "h264-draft"}).json()
    assert wait(client, ren["job_id"], 240)["status"] == "completed"
    data = client.get(f"/api/director/{pid}").json()["data"]
    for aspect, asset_id in data["renders"].items():
        info = media.probe(str(director.library.asset_path(asset_id)))
        assert info["has_video"] and info["has_audio"]
    assert data["stage"] == "rendered"

    # chat edit: only the hook's voiceover is re-recorded
    speech_before = fake.count("/audio/speech")
    images_before = fake.count("/images")
    chat = client.post(f"/api/director/{pid}/chat", json={"message": "make the hook punchier"}).json()
    assert chat["dirty_vo"] and not chat["dirty_visual"]
    assert wait(client, chat["job"]["job_id"], 240)["status"] == "completed"
    assert fake.count("/audio/speech") == speech_before + 1
    assert fake.count("/images") == images_before
    plan = client.get(f"/api/director/{pid}").json()["data"]["plan"]
    assert plan["scenes"][0]["voiceover"] == "Tired of bounced emails?"

    ed = client.post(f"/api/director/{pid}/open-in-editor", json={"aspect": "16:9"}).json()
    assert ed["kind"] == "edit" and ed["data"]["meta"]["source"] == "director"


def test_autopilot_and_failed_clip_degrades_to_still(client, fake):
    fake.fail_video = True
    r = client.post("/api/director", json={**brief(), "aspects": ["1:1"], "autopilot": True}).json()
    done = wait(client, r["job_id"], 240)
    assert done["status"] == "completed", done
    data = client.get(f"/api/director/{r['project_id']}").json()["data"]
    assert data["warnings"] and "s1" in data["warnings"][0]
    v1 = next(t for t in data["timelines"]["1:1"]["tracks"] if t["id"] == "v1")
    assert v1["clips"][0]["type"] == "image"  # fell back to the storyboard still with motion
    assert data["renders"]["1:1"]


def test_budget_cap_blocks_paid_stage(client, fake):
    r = client.post("/api/director", json=brief()).json()
    wait(client, r["job_id"])
    from champstudio import costs

    config.update_settings(defaults={"budget": {"monthly_usd": costs.spent() + 0.0001}})
    try:
        sb = client.post(f"/api/director/{r['project_id']}/storyboard", json={}).json()
        done = wait(client, sb["job_id"])
        assert done["status"] == "failed" and "budget" in done["error"].lower()
    finally:
        config.update_settings(defaults={"budget": {}})


def test_patch_validation_rejects_bad_paths():
    plan = director._clean_plan(FakeOpenRouter and __import__("fake_openrouter").PLAN, {"budget": "balanced"})
    with pytest.raises(ValueError):
        director.apply_patch(plan, [{"op": "replace", "path": "/brief/prompt", "value": "x"}])
    new, vis, vo, structure = director.apply_patch(plan, [
        {"op": "add", "path": "/scenes/-", "value": {**plan["scenes"][0], "id": None}},
        {"op": "replace", "path": "/scenes/1/visual", "value": "a lighthouse"}])
    assert structure and len(new["scenes"]) == 4 and len(vis) == 2 and len(vo) == 1


def test_lean_budget_never_pays_for_video(client, fake):
    r = client.post("/api/director", json=brief(budget="lean")).json()
    wait(client, r["job_id"])
    plan = client.get(f"/api/director/{r['project_id']}").json()["data"]["plan"]
    assert all(s["visual_type"] != "ai_video" for s in plan["scenes"])
    assert projects.get(r["project_id"])["kind"] == "director"
