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

    draw = ImageDraw.Draw(img)
    fill = parse_color(style.color)
    stroke_w = int(round(style.stroke_width * scale))
    stroke_fill = parse_color(style.stroke_color, (0, 0, 0, 255)) if style.stroke_color else None
    hl_fill = parse_color(style.highlight_color) if style.highlight_color else None
    hl_bg = parse_color(style.highlight_bg) if style.highlight_bg else None

    # word positions for highlight/background boxes
    shown = 0
    word_index = 0
    placements: list[tuple[float, float, str, int]] = []  # x, y, word, index
    line_boxes: list[tuple[float, float, float, float]] = []
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
            cursor += _text_width(font, word + " ", spacing)
            word_index += 1

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
