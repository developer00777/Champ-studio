"""Motion graphics: graphic clips (layers + controls), text animators, counters,
responsive pins, masks, new shapes, graphic templates and save-as-template."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from champstudio import brands, graphics, templates
from champstudio.main import app
from champstudio.render import text as textmod
from champstudio.render.compositor import Compositor
from champstudio.timeline import Clip, Counter, TextAnim, TextStyle, Timeline

W, H = 640, 360


def tl_with(*clips, bg="#000000"):
    return Timeline(width=W, height=H, background=bg, tracks=[{"id": "v1", "kind": "video", "clips": list(clips)}])


def graphic(layers, controls=(), duration=60):
    return {"id": "g", "type": "graphic", "start": 0, "duration": duration,
            "graphic": {"layers": list(layers), "controls": list(controls)}}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ── pure helpers ─────────────────────────────────────────────────────


def test_bindings_drive_text_colour_visibility_and_box():
    layer = {"id": "l", "type": "shape", "box": [0.1, 0.1, 0.5, 0.1], "shape": {"fill": "#FFFFFF"},
             "bind": {"shape.fill": "{{accent}}", "enabled": "{{show}}", "box.2": "{{pct}} * 0.008"}}
    out = graphics.apply_bindings(layer, {"accent": "#FF0000", "show": "", "pct": "50%"})
    assert out["shape"]["fill"] == "#FF0000" and out["enabled"] is False
    assert out["box"][2] == pytest.approx(0.4)
    text = graphics.apply_bindings({"text": "x", "bind": {"text": "{{a}} — {{b}}%"}}, {"a": "Up", "b": 72.0})
    assert text["text"] == "Up — 72%"
    # unset controls keep the authored value
    assert graphics.apply_bindings(layer, {})["shape"]["fill"] == "#FFFFFF"


def test_layer_timing_holds_to_end_when_the_graphic_is_trimmed():
    c = Clip(**graphic([{"id": "a", "type": "text", "text": "A", "start": 10, "duration": 20, "hold": True},
                        {"id": "b", "type": "text", "text": "B", "start": 5, "duration": 10, "hold": False}], duration=90))
    a, b = graphics.resolve_layers(c)
    assert (a.start, a.duration) == (10, 80) and (b.start, b.duration) == (5, 10)


def test_counter_keeps_format():
    co = Counter(start=0, duration=30)
    assert graphics.counter_text("$4,200,000 ARR", co, 0) == "$0 ARR"
    assert graphics.counter_text("$4,200,000 ARR", co, 30) == "$4,200,000 ARR"
    assert graphics.counter_text("3.5x", co, 30) == "3.5x"
    mid = graphics.counter_text("87%", co, 15)
    assert mid.endswith("%") and 0 < int(mid[:-1]) < 87


def test_unit_progress_staggers_and_mirrors_out():
    anim = TextAnim(by="char", duration=10, stagger=5, out=True)
    assert graphics.unit_progress(anim, 0, 3, 9, 100) == 1.0
    assert graphics.unit_progress(anim, 2, 3, 9, 100) == 0.0
    assert graphics.unit_progress(anim, 0, 3, 99, 100) < 1.0  # leaving at the end


def test_unit_cells_tile_the_text_box():
    style = TextStyle(size=48)
    for by in ("char", "word", "line"):
        cells = textmod.unit_cells("Hello big world", style, 400, 120, 1.0, by)
        assert cells, by
        area = sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in cells)
        assert area == 400 * 120 or by != "line"
    assert len(textmod.unit_cells("Hello big world", style, 400, 120, 1.0, "word")) == 3
    assert len(textmod.unit_cells("Hi there", style, 400, 120, 1.0, "char")) == 7


# ── rendering ────────────────────────────────────────────────────────


def test_graphic_renders_layers_with_controls():
    g = graphic([
        {"id": "bg", "type": "shape", "box": [0.1, 0.1, 0.3, 0.3], "shape": {"fill": "#FFFFFF"}, "bind": {"shape.fill": "{{c}}"}},
        {"id": "dot", "type": "shape", "box": [0.6, 0.6, 0.1, 0.1], "shape": {"kind": "ellipse", "fill": "#0000FF"}},
    ], controls=[{"id": "c", "kind": "color", "value": "#00FF00"}])
    fr = Compositor(tl_with(g)).frame(10)
    assert tuple(fr[int(0.25 * H), int(0.25 * W)]) == (0, 255, 0)
    assert tuple(fr[int(0.65 * H), int(0.65 * W)]) == (0, 0, 255)
    assert tuple(fr[5, 5]) == (0, 0, 0)  # transparent around the layers


def test_pin_wraps_text_and_follows_its_length():
    def bar_width(name):
        g = graphic([
            {"id": "bar", "type": "shape", "box": [0.05, 0.4, 0.9, 0.2], "shape": {"fill": "#FF0000"}, "pin": {"to": "t", "pad": [0, 0, 0, 0]}},
            {"id": "t", "type": "text", "text": name, "box": [0.05, 0.4, 0.9, 0.2], "style": {"size": 60, "align": "left", "color": "#FFFFFF"}},
        ])
        fr = Compositor(tl_with(g)).frame(5)
        row = fr[int(0.5 * H)]
        red = np.where((row[:, 0] > 200) & (row[:, 1] < 60))[0]
        return red.max() - red.min() if len(red) else 0

    short, long = bar_width("Al"), bar_width("Alexandra Morgan-Smith")
    assert 0 < short < long < 0.9 * W


def test_mask_below_clips_to_layers_beneath():
    g = graphic([
        {"id": "base", "type": "shape", "box": [0.0, 0.0, 0.5, 1.0], "shape": {"fill": "#FFFFFF"}},
        {"id": "m", "type": "shape", "box": [0.0, 0.0, 1.0, 1.0], "shape": {"fill": "#FF0000"}, "mask": "below"},
    ])
    fr = Compositor(tl_with(g)).frame(3)
    assert tuple(fr[H // 2, W // 4]) == (255, 0, 0)   # red only over the white half
    assert tuple(fr[H // 2, 3 * W // 4]) == (0, 0, 0)


def test_text_animator_reveals_over_time():
    t = {"id": "t", "type": "text", "text": "Kinetic type", "start": 0, "duration": 60, "box": [0, 0, 1, 1],
         "style": {"size": 120, "color": "#FFFFFF"}, "text_anim": {"by": "char", "type": "rise", "duration": 6, "stagger": 3}}
    comp = Compositor(tl_with(t))
    ink = [int((comp.frame(f)[..., 0] > 128).sum()) for f in (0, 8, 40)]
    assert ink[0] < ink[1] < ink[2]


def test_star_polygon_and_angled_gradient():
    star = {"id": "s", "type": "shape", "duration": 10, "box": [0.25, 0.0, 0.5, 1.0],
            "shape": {"kind": "star", "sides": 5, "fill": "#FFFFFF"}}
    fr = Compositor(tl_with(star)).frame(1)
    assert tuple(fr[H // 2, W // 2]) == (255, 255, 255) and tuple(fr[2, int(0.27 * W)]) == (0, 0, 0)
    grad = {"id": "g", "type": "shape", "duration": 10, "shape": {"kind": "rect", "gradient": ["#000000", "#FFFFFF"], "gradient_angle": 90}}
    fr = Compositor(tl_with(grad)).frame(1)
    assert fr[H // 2, 5, 0] < 30 and fr[H // 2, W - 5, 0] > 225  # left → right


# ── templates ────────────────────────────────────────────────────────

MG = sorted(t for t in templates.all_templates() if t.startswith("mg-"))


def test_motion_graphics_library_is_large_and_valid():
    assert len(MG) >= 30
    for tid in MG:
        t = templates.get(tid)
        assert t["graphic"] and t["category"] == "motion-graphics"
        assert not templates.validate_template({k: v for k, v in t.items() if k != "origin"}), tid


@pytest.mark.parametrize("tid", MG)
def test_motion_graphic_compiles_to_one_clip_and_renders(tid):
    t = templates.get(tid)
    kit = brands.get(None)
    for aspect in ("16:9", "9:16"):
        clip = templates.graphic_clip(t, {}, kit, aspect)
        assert clip["type"] == "graphic" and clip["graphic"]["layers"]
        tl = Timeline(width=320, height=180 if aspect == "16:9" else 568, tracks=[{"id": "v", "kind": "video", "clips": [clip]}])
        assert Compositor(tl).frame_rgba(int(clip["duration"] * 0.6))[..., 3].max() > 0, (tid, aspect)


def test_slot_controls_drive_the_compiled_graphic():
    t = templates.get("mg-stat-progress")
    clip = Clip(**templates.graphic_clip(t, {"percent": 25}, brands.get(None)))
    fill = next(layer for layer in graphics.resolve_layers(clip) if layer.id.endswith("fill"))
    assert fill.box[2] == pytest.approx(0.2)
    clip.graphic.controls[[c.id for c in clip.graphic.controls].index("percent")].value = 100
    fill = next(layer for layer in graphics.resolve_layers(clip) if layer.id.endswith("fill"))
    assert fill.box[2] == pytest.approx(0.8)


def test_template_if_layers_are_left_out():
    t = templates.get("outro-end-screen")
    clip = templates.graphic_clip(t, {}, brands.get(None))
    full = templates._compile_timeline(t, {"values": templates.with_defaults(t, {}), "vo": {}, "music_id": None},
                                       brands.get(None), "16:9")
    assert len(clip["graphic"]["layers"]) == sum(len(tr.clips) for tr in full.video_tracks())
    first_scene = [layer for layer in clip["graphic"]["layers"] if layer["id"].startswith("thanks-")]
    assert first_scene and not any(layer["hold"] for layer in first_scene)


def test_graphic_endpoint_hover_preview_and_save_as_template(client):
    g = client.get("/api/templates").json()["templates"]
    assert sum(1 for t in g if t["graphic"]) >= 30
    img = client.get("/api/templates/mg-lt-pill/preview.jpg?aspect=16:9&at=0.3")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    clip = client.post("/api/templates/mg-lt-pill/graphic", json={"values": {"name": "Priya Shah"}}).json()["clip"]
    for c in clip["graphic"]["controls"]:
        if c["id"] == "role":
            c["value"] = "Head of Growth"
    r = client.post("/api/templates/from-graphic", json={"clip": clip, "name": "Team pill lower third"})
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["id"] == "team-team-pill-lower-third" and saved["graphic"]
    assert saved["defaults"]["name"] == "Priya Shah" and saved["defaults"]["role"] == "Head of Growth"
    again = client.post(f"/api/templates/{saved['id']}/graphic", json={"values": {"name": "Sam Lee"}}).json()["clip"]
    texts = [layer["text"] for layer in again["graphic"]["layers"] if layer["type"] == "text"]
    assert "Sam Lee" in texts and "Head of Growth" in texts
    pins = [layer["pin"] for layer in again["graphic"]["layers"] if layer.get("pin")]
    assert pins and all(p["to"] in {layer["id"] for layer in again["graphic"]["layers"]} for p in pins)


def test_add_template_to_new_and_existing_sequence(client):
    r = client.post("/api/templates/mg-lt-pill/add-to-project", json={"values": {"name": "Dana Lee"}, "aspect": "9:16"})
    assert r.status_code == 200, r.text
    pid = r.json()["project_id"]
    p = client.get(f"/api/projects/{pid}").json()
    assert p["kind"] == "edit" and (p["data"]["width"], p["data"]["height"]) == (1080, 1920)
    clips = [c for t in p["data"]["tracks"] for c in t["clips"]]
    assert len(clips) == 1 and clips[0]["type"] == "graphic"
    # a second graphic lands on its own track above the first, with a unique id
    r2 = client.post("/api/templates/mg-callout-badge/add-to-project", json={"project_id": pid}).json()
    tracks = client.get(f"/api/projects/{pid}").json()["data"]["tracks"]
    video = [t for t in tracks if t["kind"] == "video"]
    where = {c["id"]: i for i, t in enumerate(video) for c in t["clips"]}
    assert where[r2["clip_id"]] > where[clips[0]["id"]]
    assert client.post("/api/templates/mg-lt-pill/add-to-project", json={"project_id": "nope"}).status_code == 404
