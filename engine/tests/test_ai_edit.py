"""AI editing tools + review/approval, against a fake OpenRouter."""

import time

import pytest
from fastapi.testclient import TestClient

from champstudio import ai_edit
from champstudio.main import app
from champstudio.timeline import Timeline
from champstudio.timeline_ops import FrameMap, remove_source_ranges
from conftest import add_to_library, needs_ffmpeg
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


def run(client, path, body, timeout=120):
    r = client.post(path, json=body)
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("completed", "failed"):
            assert j["status"] == "completed", j["error"]
            return j["result"]
        time.sleep(0.2)
    raise AssertionError("job stuck")


def interview(vid: str) -> dict:
    return {"fps": 30, "width": 320, "height": 180, "tracks": [
        {"id": "v1", "kind": "video", "clips": [
            {"id": "v", "type": "video", "asset_id": vid, "start": 0, "duration": 90, "linked": "a"},
            {"id": "after", "type": "color", "color": "#fff", "start": 90, "duration": 30}]},
        {"id": "v2", "kind": "video", "clips": [
            {"id": "cap", "type": "captions", "start": 0, "duration": 120, "captions": {"words": [
                {"w": "one", "s": 0, "e": 20}, {"w": "two", "s": 30, "e": 50}, {"w": "three", "s": 70, "e": 85},
                {"w": "four", "s": 95, "e": 110}]}}]},
        {"id": "a1", "kind": "audio", "clips": [
            {"id": "a", "type": "audio", "asset_id": vid, "start": 0, "duration": 90, "linked": "v", "role": "dialogue"}]},
        {"id": "a2", "kind": "audio", "clips": [
            {"id": "m", "type": "audio", "asset_id": vid, "start": 0, "duration": 120, "role": "music"}]}],
        "markers": [{"f": 100, "label": "end card"}]}


def test_frame_map():
    m = FrameMap()
    m.add(10, 20)
    m.add(40, 50)
    assert m(5) == 5 and m(15) is None and m(25) == 15 and m(55) == 35 and m.clamp(45) == 30


def test_remove_ranges_ripples_linked_clips_and_retimes_captions(color_video):
    vid = add_to_library(color_video, "video/mp4")
    tl = Timeline(**interview(vid))
    new, fmap = remove_source_ranges(tl, "v", [(1.0, 2.0)])  # frames 30..60 removed
    v1 = [c for c in new.tracks[0].clips]
    assert [(c.id, c.start, c.duration, c.in_) for c in v1] == [("v", 0, 30, 0), ("v-k1", 30, 30, 60), ("after", 60, 30, 0)]
    a1 = new.tracks[2].clips
    assert [(c.id, c.start, c.in_, c.linked) for c in a1] == [("a", 0, 0, "v"), ("a-k1", 30, 60, "v-k1")]
    assert a1[0].fade_out == 1 and a1[1].fade_in == 1
    words = new.tracks[1].clips[0].captions.words
    assert [(w.w, w.s) for w in words] == [("one", 0), ("three", 40), ("four", 65)]  # "two" was cut
    assert new.markers[0].f == 70
    assert new.duration == 90


def test_silence_cut_on_real_audio(client, speechlike):
    aud = add_to_library(speechlike, "audio/wav")
    tl = {"fps": 30, "width": 320, "height": 180, "tracks": [{"id": "a1", "kind": "audio", "clips": [
        {"id": "a", "type": "audio", "asset_id": aud, "duration": 90}]}]}
    res = run(client, "/api/ai/silence-cut", {"timeline": tl, "clip_id": "a", "fillers": False, "padding_s": 0.1})
    clips = res["timeline"]["tracks"][0]["clips"]
    assert len(clips) == 2
    total = sum(c["duration"] for c in clips)
    assert 60 <= total <= 72  # ~0.8 s of the 1 s silence removed
    assert "pauses" in res["summary"]


def test_fillers_use_transcript(client, fake, speechlike):
    aud = add_to_library(speechlike, "audio/wav")
    fake.words = [{"word": "Hello", "start": 0.0, "end": 0.4}, {"word": "um", "start": 0.45, "end": 0.7},
                  {"word": "team", "start": 2.1, "end": 2.6}]
    tl = {"fps": 30, "width": 320, "height": 180, "tracks": [{"id": "a1", "kind": "audio", "clips": [
        {"id": "a", "type": "audio", "asset_id": aud, "duration": 90}]}]}
    res = run(client, "/api/ai/silence-cut", {"timeline": tl, "clip_id": "a", "fillers": True})
    assert "1 filler" in res["summary"]


def test_captions_duck_and_command(client, fake, color_video):
    vid = add_to_library(color_video, "video/mp4")
    fake.words = [{"word": "Grow", "start": 0.2, "end": 0.5}, {"word": "faster.", "start": 0.6, "end": 1.0}]
    tl = interview(vid)
    res = run(client, "/api/ai/captions", {"timeline": tl, "clip_ids": ["a"]})
    caps = [c for t in res["timeline"]["tracks"] for c in t["clips"] if c["id"] == "captions-auto"]
    assert [w["w"] for w in caps[0]["captions"]["words"]] == ["Grow", "faster."]
    assert caps[0]["captions"]["words"][0]["s"] == 6

    ducked = run(client, "/api/ai/duck", {"timeline": tl, "duck_db": 12})
    music = next(c for t in ducked["timeline"]["tracks"] for c in t["clips"] if c["id"] == "m")
    vals = [k["v"] for k in music["keyframes"]["volume"]]
    assert min(vals) == pytest.approx(10 ** (-12 / 20), rel=1e-3) and vals[-1] == 1.0

    fake.canned["timeline_ops"] = {"summary": "Added a title and lower third.", "ops": [
        {"op": "add_text", "text": "Q3 results", "start": 0, "duration": 45},
        {"op": "lower_third", "name": "Priya Shah", "role": "CMO", "start": 15},
        {"op": "set", "clip_id": "after", "field": "color", "value": "#FF6B35"},
        {"op": "add_marker", "frame": 12, "label": "logo"}]}
    cmd = run(client, "/api/ai/command", {"timeline": tl, "message": "add a title and a lower third for Priya"})
    out = Timeline(**cmd["timeline"])
    texts = [c.text for t in out.tracks for c in t.clips if c.type == "text"]
    assert "Q3 results" in texts and "Priya Shah" in texts
    assert out.clip("after").color == "#FF6B35" and out.markers[-1].label == "logo"


def test_command_rejects_unknown_clip(fake):
    with pytest.raises(ValueError):
        ai_edit.apply_ops(Timeline(fps=30), [{"op": "move", "clip_id": "nope", "start": 3}])


def test_reframe_scene_detect_enhance(client, color_video):
    vid = add_to_library(color_video, "video/mp4")
    tl = interview(vid)
    res = run(client, "/api/ai/reframe", {"timeline": tl, "clip_id": "v", "aspect": "9:16"})
    v = Timeline(**res["timeline"]).clip("v")
    assert v.fit == "reframe" and v.keyframes["crop_x"]
    dup = run(client, "/api/ai/duplicate-as", {"timeline": tl, "aspect": "9:16"})
    assert dup["timeline"]["width"] == 1080 and dup["timeline"]["height"] == 1920
    cuts = run(client, "/api/ai/scene-detect", {"asset_id": vid})["cuts"]
    assert any(abs(c - 1.0) < 0.1 for c in cuts)
    split = ai_edit.split_at_cuts(Timeline(**tl), "v", cuts)
    assert len(split.tracks[0].clips) == 3
    enh = run(client, "/api/ai/enhance-speech", {"asset_id": vid})
    assert enh["asset"]["kind"] == "audio" and enh["asset"]["parent_id"] == vid


def test_rough_cut_and_broll(client, fake, color_video):
    vid = add_to_library(color_video, "video/mp4")
    fake.words = [{"word": "We", "start": 0.0, "end": 0.2}, {"word": "grew.", "start": 0.25, "end": 0.6},
                  {"word": "Data", "start": 1.4, "end": 1.6}, {"word": "wins.", "start": 1.65, "end": 1.9}]
    fake.canned["rough_cut"] = {"title": "Growth story", "segments": [f"{vid}:1", f"{vid}:0", "bogus"]}
    res = run(client, "/api/ai/rough-cut", {"asset_ids": [vid], "brief": "short story", "target_s": 10})
    p = client.get(f"/api/projects/{res['project_id']}").json()
    v1 = next(t for t in p["data"]["tracks"] if t["id"] == "v1")["clips"]
    assert [c["in"] for c in v1][0] > 30  # segment 1 (starts ~1.4 s) comes first
    assert any(t["clips"] and t["clips"][0]["type"] == "captions" for t in p["data"]["tracks"])
    fake.canned["broll"] = {"beats": [{"start_s": 0.0, "end_s": 1.5, "prompt": "warehouse at dawn"},
                                      {"start_s": 0.5, "end_s": 0.6, "prompt": "too short"}]}
    br = run(client, "/api/ai/broll", {"timeline": p["data"]})
    assert "1 B-roll" in br["summary"]


def test_review_flow(client, color_video):
    vid = add_to_library(color_video, "video/mp4")
    p = client.post("/api/projects", json={"name": "Review me"}).json()
    c = client.post(f"/api/projects/{p['id']}/comments", json={"body": "Logo too small", "in_frame": 12,
                                                               "out_frame": 30}).json()
    assert c["author"] == "local"
    link = client.post(f"/api/projects/{p['id']}/review-links", json={"render_asset_id": vid}).json()
    tok = link["token"]
    pub = client.get(f"/api/review/{tok}").json()
    assert pub["status"] == "needs_review" and pub["comments"][0]["body"] == "Logo too small"
    assert client.get(f"/api/review/{tok}/video").status_code == 200
    g = client.post(f"/api/review/{tok}/comments", json={"body": "Approved once logo fixed", "in_frame": 40,
                                                         "author": "Client"}).json()
    assert g["author"] == "Client (link)"
    assert client.post(f"/api/review/{tok}/status", json={"status": "approved"}).json()["status"] == "approved"
    upd = client.put(f"/api/comments/{c['id']}", json={"resolved": True}).json()
    assert upd["resolved"] is True
    assert client.get("/api/review/not-a-token").status_code == 404
    audit = client.get("/api/audit").json()["entries"]
    assert any(e["action"] == "review.status" for e in audit)
