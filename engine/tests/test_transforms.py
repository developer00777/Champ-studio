"""Transform suite tests — every tool runs on a synthetic image and returns valid output."""

import io

import pytest
from PIL import Image, ImageDraw

from champstudio import transforms


@pytest.fixture(scope="module")
def sample() -> bytes:
    """A 320x240 test card: shapes + gradient so every tool has something to chew on."""
    img = Image.new("RGB", (320, 240), (244, 244, 240))
    d = ImageDraw.Draw(img)
    d.ellipse([40, 40, 160, 160], fill=(255, 107, 53))          # champ ember circle
    d.rectangle([180, 60, 290, 200], fill=(11, 13, 18))          # ink block
    d.line([20, 220, 300, 30], fill=(59, 130, 246), width=6)     # azure stroke
    for x in range(320):
        d.point((x, 235), fill=(x % 256, 100, 150))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.parametrize("tool", [t for t in transforms.TOOLS if t != "remove-bg"])
def test_tool_runs(tool, sample):
    fn = transforms.get_tool(tool)
    outputs = fn(sample, {})
    assert outputs, f"{tool} returned nothing"
    for data, mime in outputs:
        assert isinstance(data, bytes) and len(data) > 50
        assert "/" in mime


def test_vectorize_produces_svg(sample):
    outputs = transforms.get_tool("vectorize")(sample, {"mode": "color"})
    data, mime = outputs[0]
    assert mime == "image/svg+xml"
    assert b"<svg" in data[:200]
    assert b"path" in data


def test_skeleton_outputs_png_and_svg(sample):
    outputs = transforms.get_tool("skeleton")(sample, {})
    mimes = [m for _, m in outputs]
    assert "image/png" in mimes and "image/svg+xml" in mimes


def test_palette_json_and_css(sample):
    outputs = transforms.get_tool("palette")(sample, {"colors": 4})
    mimes = [m for _, m in outputs]
    assert set(mimes) == {"image/png", "text/css", "application/json"}
    css = next(d for d, m in outputs if m == "text/css").decode()
    assert "--palette-1:" in css


def test_upscale_doubles_size(sample):
    out, _ = transforms.get_tool("upscale")(sample, {"factor": 2})[0]
    img = Image.open(io.BytesIO(out))
    assert img.size == (640, 480)


@pytest.mark.slow
def test_remove_bg(sample):
    """Downloads U^2-Net weights on first run — excluded from the default suite."""
    outputs = transforms.get_tool("remove-bg")(sample, {})
    img = Image.open(io.BytesIO(outputs[0][0]))
    assert img.mode == "RGBA"
