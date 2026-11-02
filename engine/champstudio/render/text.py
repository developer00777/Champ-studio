"""Text rasterization with Pillow — titles, lower thirds, captions.

ffmpeg builds without `drawtext` exist (Phase 0 finding), so every glyph is
drawn here into a transparent RGBA image the compositor overlays. Fonts are
bundled OFL files (engine/champstudio/assets/fonts) plus brand fonts uploaded
to <data_dir>/fonts, so output is identical on Mac, Docker and Windows.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..config import data_dir
from ..timeline import TextStyle

BUNDLED = Path(__file__).resolve().parents[1] / "assets" / "fonts"

# family -> list of (weight, file, variable?)
_STATIC_WEIGHTS = {"Regular": 400, "Medium": 500, "SemiBold": 600, "Bold": 700, "ExtraBold": 800, "Black": 900}


def _font_dirs() -> list[Path]:
    dirs = [BUNDLED]
    user = data_dir() / "fonts"
    if user.is_dir():
        dirs.append(user)
    return dirs


@functools.lru_cache(maxsize=4)
def _registry(_key: tuple) -> dict[str, list[tuple[int, Path, bool]]]:
    reg: dict[str, list[tuple[int, Path, bool]]] = {}
    for d in _font_dirs():
        for p in sorted(d.glob("*.[ot]tf")):
            stem = p.stem
            variable = "[" in stem or stem in ("Inter", "Montserrat", "SpaceGrotesk", "PlayfairDisplay", "DMSans")
            if "-" in stem:
                fam, style = stem.split("-", 1)
                weight = _STATIC_WEIGHTS.get(style, 400)
            else:
                fam, weight = re.sub(r"\[.*\]", "", stem), 400
            reg.setdefault(_norm(fam), []).append((weight, p, variable))
    return reg


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def registry() -> dict[str, list[tuple[int, Path, bool]]]:
    key = tuple(sorted(str(p) for d in _font_dirs() for p in d.glob("*.[ot]tf")))
    return _registry(key)


def families() -> list[str]:
    names = []
    for d in _font_dirs():
        for p in d.glob("*.[ot]tf"):
            fam = p.stem.split("-", 1)[0]
            fam = re.sub(r"\[.*\]", "", fam)
            pretty = re.sub(r"(?<=[a-zA-Z])(?=[A-Z][a-z])", " ", fam)
            if pretty not in names:
                names.append(pretty)
    return sorted(names)


@functools.lru_cache(maxsize=256)
def load_font(family: str, weight: int, size: int) -> ImageFont.FreeTypeFont:
    reg = registry()
    options = reg.get(_norm(family)) or reg.get("inter") or next(iter(reg.values()))
    w, path, variable = min(options, key=lambda o: abs(o[0] - weight))
    font = ImageFont.truetype(str(path), max(1, size))
    if variable:
        try:
            axes = font.get_variation_axes()
            values = []
            for ax in axes:
                name = ax.get("name", b"")
                name = name.decode() if isinstance(name, bytes) else str(name)
                if name.lower().startswith("weight") or ax.get("tag") == b"wght":
                    values.append(max(ax["minimum"], min(ax["maximum"], weight)))
                else:
                    values.append(ax.get("default", ax["minimum"]))
            font.set_variation_by_axes(values)
        except (OSError, AttributeError, KeyError):
            pass  # not actually variable / FreeType without variation support
    return font


def parse_color(c: str | None, default=(255, 255, 255, 255)) -> tuple[int, int, int, int]:
    if not c:
        return default
    c = c.strip().lstrip("#")
    if len(c) in (3, 4):
        c = "".join(ch * 2 for ch in c)
    try:
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        a = int(c[6:8], 16) if len(c) >= 8 else 255
        return (r, g, b, a)
    except (ValueError, IndexError):
        return default


# ── layout ───────────────────────────────────────────────────────────


def _text_width(font: ImageFont.FreeTypeFont, text: str, spacing: float) -> float:
    if not text:
        return 0.0
    if spacing:  # tracked text is drawn glyph by glyph (no kerning): measure it the same way
        return sum(font.getlength(ch) for ch in text) + spacing * max(0, len(text) - 1)
    return font.getlength(text)


def wrap(text: str, font: ImageFont.FreeTypeFont, max_w: float, spacing: float = 0.0) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        words = para.split(" ")
        line = ""
        for word in words:
            trial = f"{line} {word}".strip()
            if _text_width(font, trial, spacing) <= max_w or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def _fit(text: str, style: TextStyle, w: int, h: int, scale: float):
    """Pick the largest size (<= style.size) where wrapped text fits the box."""
    max_size = max(6, int(style.size * scale))
    min_size = max(6, int((style.min_size or style.size * 0.35) * scale))
    size = max_size
    while True:
        pad = style.bg_padding * size if style.bg_color else 0  # padding scales with the size tried
        font = load_font(style.font, style.weight, size)
        spacing = style.letter_spacing * size
        lines = wrap(text, font, max(1, w - 2 * pad), spacing)
        lh = size * style.line_height
        total_h = lh * len(lines)
        widest = max((_text_width(font, ln, spacing) for ln in lines), default=0)
        fits = total_h <= h - 2 * pad and widest <= w - 2 * pad + 1
        if fits or style.fit == "none" or size <= min_size:
            return font, lines, size, spacing
        size = max(min_size, int(size * 0.92))


_layouts: dict[tuple, dict] = {}


def layout(text: str, style: TextStyle, w: int, h: int, scale: float = 1.0) -> dict:
    """Where every word and line of `text` lands inside a w×h box (shared by the
    rasterizer, per-unit text animators and responsive pins)."""
    key = (text, style.model_dump_json(), w, h, round(scale, 6))
    hit = _layouts.get(key)
    if hit is None:
        if len(_layouts) > 512:
            _layouts.clear()
        hit = _layouts[key] = _layout(text, style, w, h, scale)
    return hit


def _layout(text: str, style: TextStyle, w: int, h: int, scale: float) -> dict:
    if style.uppercase:
        text = text.upper()
    font, lines, size, spacing = _fit(text, style, w, h, scale)
    lh = size * style.line_height
    ascent, descent = font.getmetrics()
    block_h = lh * (len(lines) - 1) + ascent + descent
    if style.valign == "top":
        y0 = 0.0
    elif style.valign == "bottom":
        y0 = h - block_h
    else:
        y0 = (h - block_h) / 2
    word_index = 0
    placements: list[tuple[float, float, str, int]] = []  # x, y, word, index
    line_boxes: list[tuple[float, float, float, float]] = []
    line_of: list[int] = []
    for i, line in enumerate(lines):
        lw = _text_width(font, line, spacing)
        if style.align == "left":
            x = 0.0 if not style.bg_color else style.bg_padding * size
        elif style.align == "right":
            x = w - lw - (style.bg_padding * size if style.bg_color else 0)
        else:
            x = (w - lw) / 2
        y = y0 + i * lh
        line_boxes.append((x, y, x + lw, y + ascent + descent))
        cursor = x
        for word in line.split(" "):
            placements.append((cursor, y, word, word_index))
            line_of.append(i)
            cursor += _text_width(font, word + " ", spacing)
            word_index += 1
    return {"font": font, "lines": lines, "size": size, "spacing": spacing, "ascent": ascent,
            "descent": descent, "placements": placements, "line_boxes": line_boxes, "line_of": line_of}


def content_bbox(text: str, style: TextStyle, w: int, h: int, scale: float = 1.0) -> tuple[float, float, float, float] | None:
    """Ink-ish bounds of laid-out text (including its background pill) in box px."""
    if not text.strip():
        return None
    lay = layout(text, style, w, h, scale)
    boxes = [b for b in lay["line_boxes"] if b[2] - b[0] >= 1]
    if not boxes:
        return None
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    if style.bg_color:
        pad = style.bg_padding * lay["size"]
        x0, x1, y0, y1 = x0 - pad, x1 + pad, y0 - pad * 0.6, y1 + pad * 0.6
    return x0, y0, x1, y1


def unit_cells(text: str, style: TextStyle, w: int, h: int, scale: float, by: str) -> list[tuple[int, int, int, int]]:
    """Partition the w×h text image into one cell per character, word or line
    (cells tile the image exactly, so slicing them never drops or doubles ink)."""
    if not text.strip():
        return []
    lay = layout(text, style, w, h, scale)
    font, spacing = lay["font"], lay["spacing"]
    ext = lay["ascent"] + lay["descent"]
    rows: list[list[tuple[float, float]]] = [[] for _ in lay["line_boxes"]]
    for (x, _y, word, _idx), li in zip(lay["placements"], lay["line_of"]):
        if by == "line":
            continue
        if by == "word":
            if word:
                rows[li].append((x, x + _text_width(font, word, spacing)))
            continue
        cx = x
        for ch in word:
            cw = font.getlength(ch)
            if ch.strip():
                rows[li].append((cx, cx + cw))
            cx += cw + spacing
    if by == "line":
        rows = [[(b[0], b[2])] if b[2] - b[0] >= 1 else [] for b in lay["line_boxes"]]
    tops = [b[1] for b in lay["line_boxes"]]
    cells: list[tuple[int, int, int, int]] = []
    for li, units in enumerate(rows):
        ya = 0 if li == 0 else int(round((tops[li - 1] + ext + tops[li]) / 2))
        yb = h if li == len(rows) - 1 else int(round((tops[li] + ext + tops[li + 1]) / 2))
        units = sorted(units)
        for ui, (a, b) in enumerate(units):
            xa = 0 if ui == 0 else int(round((units[ui - 1][1] + a) / 2))
            xb = w if ui == len(units) - 1 else int(round((b + units[ui + 1][0]) / 2))
            if by == "line":
                xa, xb = 0, w
            if xb > xa and yb > ya:
                cells.append((xa, ya, xb, yb))
    return cells


def render_text(
    text: str,
    style: TextStyle,
    w: int,
    h: int,
    *,
    scale: float = 1.0,
    visible_chars: int | None = None,
    highlight_word: int | None = None,
) -> Image.Image:
    """Rasterize `text` into a w×h transparent image.

    `scale` maps style px (authored at 1080p height) to output px.
    `visible_chars` drives typewriter reveals; `highlight_word` (index into the
    whitespace-split words) colours one word for karaoke captions.
    """
    img = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    if not text.strip():
        return img
    lay = layout(text, style, w, h, scale)
    font, size, spacing = lay["font"], lay["size"], lay["spacing"]
    ascent, descent = lay["ascent"], lay["descent"]
    placements, line_boxes = lay["placements"], lay["line_boxes"]

    draw = ImageDraw.Draw(img)
    fill = parse_color(style.color)
    stroke_w = int(round(style.stroke_width * scale))
    stroke_fill = parse_color(style.stroke_color, (0, 0, 0, 255)) if style.stroke_color else None
    hl_fill = parse_color(style.highlight_color) if style.highlight_color else None
    hl_bg = parse_color(style.highlight_bg) if style.highlight_bg else None
    shown = 0

    if style.bg_color:
        pad = style.bg_padding * size
        radius = style.bg_radius * size
        bg = parse_color(style.bg_color)
        for (x1, y1, x2, y2) in line_boxes:
            if x2 - x1 < 1:
                continue
            draw.rounded_rectangle((x1 - pad, y1 - pad * 0.6, x2 + pad, y2 + pad * 0.6), radius=radius, fill=bg)

    if style.shadow:
        shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow)
        off = max(1, int(size * 0.06))
        _draw_words(sd, placements, font, spacing, parse_color(style.shadow_color, (0, 0, 0, 170)),
                    visible_chars, None, None, off)
        shadow = shadow.filter(ImageFilter.GaussianBlur(max(1, size * 0.08)))
        img = Image.alpha_composite(shadow, img)
        draw = ImageDraw.Draw(img)

    if hl_bg is not None and highlight_word is not None:
        for (x, y, word, idx) in placements:
            if idx == highlight_word:
                ww = _text_width(font, word, spacing)
                p = size * 0.12
                draw.rounded_rectangle((x - p, y - p * 0.5, x + ww + p, y + ascent + descent + p * 0.5),
                                       radius=size * 0.18, fill=hl_bg)

    _draw_words(draw, placements, font, spacing, fill, visible_chars, highlight_word, hl_fill, 0,
                stroke_w, stroke_fill)
    del shown
    return img


def _draw_words(draw, placements, font, spacing, fill, visible_chars, highlight_word, hl_fill, off,
                stroke_w=0, stroke_fill=None):
    budget = visible_chars if visible_chars is not None else 10**9
    for (x, y, word, idx) in placements:
        if budget <= 0:
            break
        text = word[: max(0, budget)]
        budget -= len(word) + 1
        color = hl_fill if (hl_fill is not None and idx == highlight_word) else fill
        if spacing:
            cx = x
            for ch in text:
                draw.text((cx + off, y + off), ch, font=font, fill=color,
                          stroke_width=stroke_w, stroke_fill=stroke_fill)
                cx += font.getlength(ch) + spacing
        else:
            draw.text((x + off, y + off), text, font=font, fill=color,
                      stroke_width=stroke_w, stroke_fill=stroke_fill)


# ── captions ─────────────────────────────────────────────────────────


def caption_lines(words: list[dict], max_chars: int) -> list[list[int]]:
    """Group word indices into display lines of at most `max_chars`,
    breaking after sentence punctuation or long pauses."""
    groups: list[list[int]] = []
    cur: list[int] = []
    length = 0
    for i, w in enumerate(words):
        token = w["w"]
        gap = (w["s"] - words[i - 1]["e"]) if i else 0
        if cur and (length + 1 + len(token) > max_chars or gap > 12):
            groups.append(cur)
            cur, length = [], 0
        cur.append(i)
        length += len(token) + (1 if length else 0)
        if token.endswith((".", "!", "?")) and length > max_chars * 0.5:
            groups.append(cur)
            cur, length = [], 0
    if cur:
        groups.append(cur)
    return groups
