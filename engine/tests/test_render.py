"""Golden-frame tests for the compositor, audio mixdown and export."""

import io
import wave

import numpy as np
import pytest
from PIL import Image

from champstudio.render import audio, export, media
from champstudio.render.compositor import Compositor
from champstudio.timeline import Timeline, eval_keyframes, Keyframe
from conftest import add_to_library, needs_ffmpeg

pytestmark = needs_ffmpeg


def tl(**kw) -> Timeline:
    base = {"fps": 30, "width": 320, "height": 180, "background": "#000000", "tracks": []}
    base.update(kw)
    return Timeline(**base)


def px(frame, x, y):
    return tuple(int(v) for v in frame[y, x][:3])


def close(a, b, tol=40):
    return all(abs(i - j) <= tol for i, j in zip(a, b))


def test_keyframe_easing_linear_and_hold():
    kfs = [Keyframe(f=0, v=0, ease="linear"), Keyframe(f=10, v=10)]
    assert eval_keyframes(kfs, 5, 0) == pytest.approx(5)
    assert eval_keyframes([Keyframe(f=0, v=1, ease="hold"), Keyframe(f=10, v=9)], 9, 0) == 1
    assert eval_keyframes(kfs, -3, 0) == 0 and eval_keyframes(kfs, 99, 0) == 10


def test_color_and_shape_layers_composite_in_track_order():
    t = tl(tracks=[
        {"id": "v1", "kind": "video", "clips": [{"id": "bg", "type": "color", "color": "#00FF00", "duration": 30}]},
        {"id": "v2", "kind": "video", "clips": [{"id": "box", "type": "shape", "duration": 30,
                                                  "box": [0.25, 0.25, 0.5, 0.5], "shape": {"fill": "#FF0000"}}]},
    ])
    f = Compositor(t).frame(0)
    assert close(px(f, 5, 5), (0, 255, 0))
    assert close(px(f, 160, 90), (255, 0, 0))


def test_opacity_keyframes_and_fade_in():
    t = tl(tracks=[{"id": "v1", "kind": "video", "clips": [
        {"id": "w", "type": "color", "color": "#FFFFFF", "duration": 30,
         "keyframes": {"opacity": [{"f": 0, "v": 0, "ease": "linear"}, {"f": 20, "v": 1}]}}]}])
    c = Compositor(t)
    assert px(c.frame(0), 10, 10)[0] < 20
    assert 100 < px(c.frame(10), 10, 10)[0] < 160
    assert px(c.frame(25), 10, 10)[0] > 245


def test_text_renders_pixels_inside_its_box():
    t = tl(tracks=[{"id": "v1", "kind": "video", "clips": [
        {"id": "t", "type": "text", "text": "HELLO WORLD", "duration": 30, "box": [0.1, 0.3, 0.8, 0.4],
         "style": {"size": 120, "color": "#FFFFFF", "weight": 800}}]}])
    f = Compositor(t).frame(0)
    inside = f[54:126, 32:288]
    outside = f[0:40, :]
    assert inside.max() > 200 and outside.max() < 10


def test_video_clip_trim_and_speed(color_video):
    vid = add_to_library(color_video, "video/mp4")
    # in = 30 frames → starts on the blue half
    t = tl(tracks=[{"id": "v1", "kind": "video", "clips": [
        {"id": "c", "type": "video", "asset_id": vid, "duration": 15, "in": 30}]}])
    f = Compositor(t).frame(2)
    assert close(px(f, 160, 90), (0, 0, 255), 60)
    # double speed from 0: frame 20 → source 40 → blue
    t2 = tl(tracks=[{"id": "v1", "kind": "video", "clips": [
        {"id": "c", "type": "video", "asset_id": vid, "duration": 30, "speed": 2.0}]}])
    assert close(px(Compositor(t2).frame(5), 160, 90), (255, 0, 0), 60)
    assert close(px(Compositor(t2).frame(20), 160, 90), (0, 0, 255), 60)


def test_sequential_matches_random_access(color_video):
    vid = add_to_library(color_video, "video/mp4")
    t = tl(tracks=[{"id": "v1", "kind": "video", "clips": [
        {"id": "c", "type": "video", "asset_id": vid, "duration": 60}]}])
    seq = Compositor(t, sequential=True)
    frames = [seq.frame(i) for i in range(45)]
    seq.close()
    ra = Compositor(t)
    for i in (0, 20, 40):
        assert np.abs(frames[i].astype(int) - ra.frame(i).astype(int)).mean() < 12, i


def test_crossfade_transition_mixes_both_clips():
    t = tl(tracks=[{"id": "v1", "kind": "video", "clips": [
        {"id": "a", "type": "color", "color": "#FF0000", "start": 0, "duration": 30},
        {"id": "b", "type": "color", "color": "#0000FF", "start": 30, "duration": 30,
         "transition_in": {"type": "crossfade", "duration": 10}}]}])
    c = Compositor(t)
    mid = px(c.frame(35), 10, 10)
    assert 80 < mid[0] < 180 and 80 < mid[2] < 180
    assert close(px(c.frame(45), 10, 10), (0, 0, 255))


def test_wipe_and_dip_transitions():
    clips = lambda kind: tl(tracks=[{"id": "v1", "kind": "video", "clips": [
        {"id": "a", "type": "color", "color": "#FF0000", "duration": 30},
        {"id": "b", "type": "color", "color": "#0000FF", "start": 30, "duration": 30,
         "transition_in": {"type": kind, "duration": 10}}]}])
    w = Compositor(clips("wipe-left")).frame(35)
    assert close(px(w, 10, 90), (0, 0, 255)) and close(px(w, 310, 90), (255, 0, 0))
    d = Compositor(clips("dip-black")).frame(34)
    assert max(px(d, 10, 10)) < 60


def test_adjustment_layer_desaturates_below():
    t = tl(tracks=[
        {"id": "v1", "kind": "video", "clips": [{"id": "a", "type": "color", "color": "#FF0000", "duration": 10}]},
        {"id": "v2", "kind": "video", "clips": [{"id": "adj", "type": "adjustment", "duration": 10,
                                                  "adjust": {"saturation": 0}}]}])
    r, g, b = px(Compositor(t).frame(0), 10, 10)
    assert abs(r - g) < 6 and abs(g - b) < 6


def test_captions_highlight_active_word():
    words = [{"w": "Grow", "s": 0, "e": 10}, {"w": "your", "s": 10, "e": 20}, {"w": "pipeline", "s": 20, "e": 40}]
    t = tl(tracks=[{"id": "v1", "kind": "video", "clips": [
        {"id": "cap", "type": "captions", "duration": 40, "box": [0.05, 0.6, 0.9, 0.3],
         "captions": {"words": words, "mode": "karaoke"},
         "style": {"size": 60, "color": "#FFFFFF", "highlight_color": "#FFD400"}}]}])
    c = Compositor(t)
    f_late = c.frame(25)
    yellow = ((f_late[..., 0] > 200) & (f_late[..., 1] > 180) & (f_late[..., 2] < 80)).sum()
    assert yellow > 20
    assert export.to_srt(t).startswith("1\n00:00:00,000 --> 00:00:01,333\nGrow your pipeline")


def test_reframe_crops_wide_source_for_vertical(color_video):
    vid = add_to_library(color_video, "video/mp4")
    t = Timeline(fps=30, width=180, height=320, tracks=[{"id": "v1", "kind": "video", "clips": [
        {"id": "c", "type": "video", "asset_id": vid, "duration": 10, "fit": "reframe",
         "keyframes": {"crop_x": [{"f": 0, "v": 0.2}]}}]}])
    f = Compositor(t).frame(0)
    assert f.shape == (320, 180, 3) and close(px(f, 90, 160), (255, 0, 0), 60)


def _wav_rms(path: str, start: float, end: float) -> float:
    with wave.open(path) as w:
        rate, ch = w.getframerate(), w.getnchannels()
        w.setpos(int(start * rate))
        data = np.frombuffer(w.readframes(int((end - start) * rate)), np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(data ** 2))) if data.size else 0.0


def test_audio_mixdown_places_clips_and_ducks(tmp_path, tone):
    music = add_to_library(tone, "audio/wav")
    vo = add_to_library(tone, "audio/wav")
    t = tl(loudness=None, tracks=[
        {"id": "a1", "kind": "audio", "clips": [
            {"id": "m", "type": "audio", "asset_id": music, "duration": 90, "role": "music", "duck_db": 18}]},
        {"id": "a2", "kind": "audio", "clips": [
            {"id": "v", "type": "audio", "asset_id": vo, "start": 45, "duration": 15, "role": "vo", "volume": 0.0001}]},
    ])
    out = str(tmp_path / "mix.wav")
    audio.mixdown(t, out, loudness=None)
    assert media.probe(out)["duration"] == pytest.approx(3.0, abs=0.06)
    before = _wav_rms(out, 0.2, 1.0)
    ducked = _wav_rms(out, 1.6, 1.9)
    assert before > 1000 and ducked < before / 4


def test_export_h264_and_review(tmp_path, color_video, tone):
    vid = add_to_library(color_video, "video/mp4")
    snd = add_to_library(tone, "audio/wav")
    t = tl(tracks=[
        {"id": "v1", "kind": "video", "clips": [{"id": "c", "type": "video", "asset_id": vid, "duration": 45}]},
        {"id": "v2", "kind": "video", "clips": [{"id": "t", "type": "text", "text": "Title", "duration": 45,
                                                  "box": [0, 0.7, 1, 0.3], "anim_in": {"type": "slide-up", "duration": 8}}]},
        {"id": "a1", "kind": "audio", "clips": [{"id": "s", "type": "audio", "asset_id": snd, "duration": 45,
                                                  "fade_out": 10}]},
    ])
    seen = []
    out = str(tmp_path / "out.mp4")
    res = export.render(t, out, preset="h264", progress=lambda p, s: seen.append(p))
    info = media.probe(out)
    assert info["has_video"] and info["has_audio"] and info["width"] == 320
    assert info["duration"] == pytest.approx(1.5, abs=0.1)
    assert res["frames"] == 45 and seen[-1] == 1.0
    rev = str(tmp_path / "review.mp4")
    export.render(t, rev, preset="review")
    assert media.probe(rev)["height"] == 180  # never upscales


def test_still_returns_image():
    t = tl(tracks=[{"id": "v1", "kind": "video", "clips": [{"id": "a", "type": "color", "color": "#123456"}]}])
    img = export.still(t, 0)
    assert isinstance(img, Image.Image) and img.size == (320, 180)
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    assert buf.tell() > 100


def test_media_helpers(tmp_path, color_video, speechlike):
    info = media.probe(color_video)
    assert info["fps"] == pytest.approx(30) and info["has_audio"]
    proxy = str(tmp_path / "proxy.mp4")
    media.make_proxy(color_video, proxy, height=90)
    assert media.probe(proxy)["height"] == 90
    strip = media.thumbnail_strip(color_video, str(tmp_path / "s.jpg"), every_s=0.5, height=40)
    assert strip["tiles"] >= 3
    peaks = media.waveform_peaks(color_video)
    assert len(peaks["peaks"]) == pytest.approx(100, abs=3)
    gaps = media.silences(speechlike, min_s=0.4)
    assert len(gaps) == 1 and gaps[0][0] == pytest.approx(1.0, abs=0.1)
    cuts = media.scene_cuts(color_video)
    assert any(abs(c - 1.0) < 0.1 for c in cuts)
