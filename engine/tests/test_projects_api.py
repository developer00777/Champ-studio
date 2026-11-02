"""Editor/projects API: autosave + conflicts, versions, locks, frames,
render jobs, ingest, interchange round trips."""

import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from champstudio import otio_io
from champstudio.main import app
from champstudio.timeline import Timeline
from conftest import add_to_library, needs_ffmpeg

pytestmark = needs_ffmpeg


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def wait_job(client, job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("completed", "failed", "canceled"):
            return j
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} stuck: {j}")


def sample_timeline(vid: str) -> dict:
    return {"fps": 30, "width": 320, "height": 180, "tracks": [
        {"id": "v1", "kind": "video", "clips": [
            {"id": "c1", "type": "video", "asset_id": vid, "duration": 30, "name": "Red"},
            {"id": "c2", "type": "video", "asset_id": vid, "start": 30, "duration": 30, "in": 30, "name": "Blue",
             "transition_in": {"type": "crossfade", "duration": 6}}]},
        {"id": "v2", "kind": "video", "clips": [
            {"id": "t1", "type": "text", "text": "Lower third", "duration": 60, "box": [0.05, 0.75, 0.6, 0.2]},
            {"id": "cap", "type": "captions", "duration": 60, "box": [0, 0.5, 1, 0.2],
             "captions": {"words": [{"w": "Hello", "s": 0, "e": 15}, {"w": "team.", "s": 15, "e": 30}]}}]},
        {"id": "a1", "kind": "audio", "clips": [
            {"id": "s1", "type": "audio", "asset_id": vid, "duration": 60, "linked": "c1"}]}],
        "markers": [{"f": 12, "label": "logo in"}]}


def test_project_lifecycle_versions_and_conflicts(client, color_video):
    vid = add_to_library(color_video, "video/mp4")
    p = client.post("/api/projects", json={"name": "Spot", "aspect": "9:16"}).json()
    assert p["data"]["width"] == 1080 and p["data"]["height"] == 1920 and p["version"] == 1
    data = sample_timeline(vid)
    saved = client.put(f"/api/projects/{p['id']}", json={"data": data, "base_version": 1}).json()
    assert saved["version"] == 2
    stale = client.put(f"/api/projects/{p['id']}", json={"data": data, "base_version": 1})
    assert stale.status_code == 409
    v = client.post(f"/api/projects/{p['id']}/versions", json={"label": "rough cut"}).json()
    assert v["version"] == 1
    data2 = {**data, "markers": []}
    client.put(f"/api/projects/{p['id']}", json={"data": data2})
    restored = client.post(f"/api/projects/{p['id']}/versions/1/restore").json()
    assert restored["data"]["markers"][0]["label"] == "logo in"
    assert [x["label"] for x in client.get(f"/api/projects/{p['id']}/versions").json()["versions"]][:2] == \
        ["before restoring v1", "rough cut"]
    bad = client.put(f"/api/projects/{p['id']}", json={"data": {"tracks": [{"id": "x", "kind": "nope"}]}})
    assert bad.status_code == 422


def test_locks(client):
    p = client.post("/api/projects", json={"name": "Locked"}).json()
    locked = client.post(f"/api/projects/{p['id']}/lock").json()
    assert locked["locked_by"] == "local"
    unlocked = client.post(f"/api/projects/{p['id']}/unlock").json()
    assert unlocked["locked_by"] is None


def test_frame_preview_and_render_job(client, color_video):
    vid = add_to_library(color_video, "video/mp4")
    data = sample_timeline(vid)
    r = client.post("/api/render/frame", json={"timeline": data, "f": 40, "height": 180})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and len(r.content) > 500
    p = client.post("/api/projects", json={"name": "Render me", "data": data}).json()
    job = client.post(f"/api/projects/{p['id']}/render", json={"preset": "h264-draft"}).json()
    done = wait_job(client, job["job_id"])
    assert done["status"] == "completed", done
    asset = done["result"]["asset"]
    assert asset["kind"] == "video" and asset["params"]["frames"] == 60
    srt = client.get(f"/api/projects/{p['id']}/captions.srt").text
    assert "Hello team." in srt


def test_ingest_builds_proxy_thumbs_and_peaks(client, color_video):
    vid = add_to_library(color_video, "video/mp4")
    r = client.post(f"/api/assets/{vid}/ingest").json()
    assert r["job_id"]
    assert wait_job(client, r["job_id"])["status"] == "completed"
    st = client.get(f"/api/assets/{vid}/media").json()
    assert st["ready"] and st["thumbs_meta"]["tiles"] >= 1
    proxy = client.get(st["proxy"])
    assert proxy.status_code == 200 and proxy.headers["content-type"] == "video/mp4"
    assert client.post(f"/api/assets/{vid}/ingest").json()["job_id"] is None  # cached


def test_silent_video_becomes_ready(client, media_dir):
    """AI clips often have no audio track: they must still count as ready (no waveform needed),
    otherwise the editor re-queues ingest forever."""
    import subprocess

    out = media_dir / "silent.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=green:s=160x90:r=30:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True)
    vid = add_to_library(str(out), "video/mp4")
    r = client.post(f"/api/assets/{vid}/ingest").json()
    assert wait_job(client, r["job_id"])["status"] == "completed"
    st = client.get(f"/api/assets/{vid}/media").json()
    assert st["ready"] and "peaks" not in st and st["probe"]["has_audio"] is False
    assert client.post(f"/api/assets/{vid}/ingest").json()["job_id"] is None  # nothing left to build


def test_animate_job_list_only_holds_video_and_lipsync_jobs(client):
    from champstudio import jobs

    for kind in ("video", "lipsync", "ingest", "render", "director.plan"):
        jobs.create_job(kind, {"prompt": kind})
    kinds = {j["kind"] for j in client.get("/api/videos/jobs").json()["jobs"]}
    assert kinds <= {"video", "lipsync"} and {"video", "lipsync"} <= kinds


def test_otio_round_trip_is_lossless_for_our_fields(color_video):
    vid = add_to_library(color_video, "video/mp4")
    tl = Timeline(**sample_timeline(vid))
    text = otio_io.export_string(tl, "otio")
    back = otio_io.import_string(text, "otio")
    assert [t.id for t in back.tracks] == ["v1", "v2", "a1"]
    c2 = back.clip("c2")
    assert c2.start == 30 and c2.in_ == 30 and c2.transition_in.type == "crossfade"
    assert back.clip("t1").text == "Lower third" and back.markers[0].label == "logo in"


def test_premiere_xml_and_edl_export(client, color_video):
    if "fcp_xml" not in otio_io.available_formats():
        pytest.skip("OTIO plugins not installed")
    vid = add_to_library(color_video, "video/mp4")
    p = client.post("/api/projects", json={"name": "XML out", "data": sample_timeline(vid)}).json()
    r = client.get(f"/api/projects/{p['id']}/interchange?format=fcp_xml")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert any(n.endswith(".xml") for n in names) and any(n.startswith("media/") for n in names)
    xml = z.read(next(n for n in names if n.endswith(".xml"))).decode()
    assert "<xmeml" in xml and "media/" in xml
    edl = client.get(f"/api/projects/{p['id']}/interchange?format=edl&package=false").text
    assert "TITLE:" in edl
    up = client.post("/api/projects/import", files={"file": ("seq.xml", xml.encode(), "application/xml")})
    assert up.status_code == 200, up.text
    assert any(t["kind"] == "video" for t in up.json()["data"]["tracks"])


def test_costs_and_roles_endpoints(client):
    assert "planner" in client.get("/api/roles").json()["roles"]
    est = client.post("/api/costs/estimate", json={"kind": "video", "model": "google/veo-3.1-lite",
                                                   "seconds": 10, "resolution": "720p"}).json()
    assert est["usd"] == pytest.approx(0.3)
    assert "total" in client.get("/api/costs").json()
    assert "Inter" in client.get("/api/fonts").json()["families"]


def test_job_retention_prunes_history_only():
    from champstudio import db, jobs

    old = jobs.create_job("render", {"x": 1}, status="completed")
    keep = jobs.create_job("video", {"prompt": "recent"}, status="completed")
    running = jobs.create_job("render", {"x": 2}, status="in_progress")
    dup_a = jobs.create_job("ingest", {"asset_id": "same"}, status="completed")
    dup_b = jobs.create_job("ingest", {"asset_id": "same"}, status="completed")
    with db.conn() as c:
        c.execute("UPDATE jobs SET created_at = created_at - ? WHERE id IN (?, ?)", (100 * 86400, old["id"], running["id"]))
        c.execute("UPDATE jobs SET created_at = created_at - 5 WHERE id = ?", (dup_a["id"],))
    jobs.prune()
    alive = {j["id"] for j in jobs.list_jobs(limit=1000)}
    assert old["id"] not in alive and dup_a["id"] not in alive  # old history and superseded ingest
    assert {keep["id"], running["id"], dup_b["id"]} <= alive     # recent, unfinished, newest ingest
