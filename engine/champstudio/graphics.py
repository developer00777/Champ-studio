"""Motion graphics: graphic clips (Essential Graphics / MOGRT equivalent).

A graphic clip holds a stack of layers (Clips of type text/shape/image/color),
timed relative to the graphic, plus exposed controls ("master properties").
Layers read controls through `bind` — {"text": "{{name}}", "style.color":
"{{accent}}", "enabled": "{{show_role}}"} — so a template's few fields drive
many layer properties, exactly like a .mogrt in Premiere.

Everything here is pure (no I/O) and mirrored in web/editor-src/src/graphics.ts
so the realtime preview and the export agree.
"""

from __future__ import annotations

import copy
import math
import re
import zlib

from .timeline import Clip, Counter, TextAnim, ease

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
# "{{percent}} * 0.006 + 0.1": a control scaled into a layer number (bar lengths, positions)
_ARITH = re.compile(r"^\s*\{\{\s*([a-zA-Z0-9_]+)\s*\}\}((?:\s*[*/+-]\s*-?\d+(?:\.\d+)?)+)\s*$")
_OP = re.compile(r"([*/+-])\s*(-?\d+(?:\.\d+)?)")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_NUMERIC = {"size", "weight", "line_height", "letter_spacing", "stroke_width", "radius", "x", "y", "scale",
            "rotation", "opacity", "bg_padding", "bg_radius", "gradient_angle", "sides", "volume"}


# ── controls / bindings ──────────────────────────────────────────────


def control_values(clip: Clip) -> dict:
    g = clip.graphic
    return {c.id: c.value for c in g.controls} if g else {}


def _truthy(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(v)


def _fmt(v) -> str:
    if v is None or isinstance(v, dict):
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _number(v) -> float | None:
    try:
        return float(str(v).replace(",", "").strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def _bound_value(template: str, values: dict, path: str):
    arith = _ARITH.match(template)
    if arith:
        v = _number(values.get(arith.group(1)))
        if v is None:
            return None
        for op, k in _OP.findall(arith.group(2)):  # left to right, no precedence
            k = float(k)
            v = v * k if op == "*" else v / k if op == "/" and k else v + k if op == "+" else v - k
        return v
    whole = _PLACEHOLDER.fullmatch(template.strip())
    if whole:
        v = values.get(whole.group(1))
        if isinstance(v, dict):  # media control value
            v = v.get("asset_id")
    else:
        v = _PLACEHOLDER.sub(lambda m: _fmt(values.get(m.group(1))), template)
    leaf = path.rsplit(".", 1)[-1]
    if leaf in ("enabled", "uppercase", "shadow", "hold"):
        return _truthy(v)
    if leaf in _NUMERIC or path.startswith("box."):
        return _number(v)
    if leaf in ("color", "fill", "stroke", "bg_color", "stroke_color") and (v is None or v == ""):
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool) and leaf != "asset_id":  # number control → text
        return str(int(v)) if float(v).is_integer() else str(v)
    return v


def apply_bindings(layer: dict, values: dict) -> dict:
    """Layer dict with every `bind` path replaced by its control-driven value."""
    out = copy.deepcopy(layer)
    for path, template in (layer.get("bind") or {}).items():
        v = _bound_value(str(template), values, path)
        if v is None:  # unset control: keep the authored value
            continue
        if path.startswith("box."):  # box.0..3 → x, y, w, h
            i = int(path.split(".")[1])
            box = list(out.get("box") or [0, 0, 1, 1])
            if i < len(box):
                box[i] = max(0.0, float(v)) if i >= 2 else float(v)
                out["box"] = box
            continue
        if path.startswith("gradient."):  # gradient.0 / gradient.1 → shape.gradient[i]
            i = int(path.split(".")[1])
            grad = list((out.get("shape") or {}).get("gradient") or [])
            if v and i < len(grad):
                grad[i] = v
                out.setdefault("shape", {})["gradient"] = grad
            continue
        node = out
        parts = path.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = v
    return out


def resolve_layers(clip: Clip) -> list[Clip]:
    """The graphic's layers with controls applied and timing resolved (frames
    relative to the graphic's start; `hold` layers run to its end)."""
    g = clip.graphic
    if not g:
        return []
    values = control_values(clip)
    out: list[Clip] = []
    for layer in g.layers:
        d = apply_bindings(layer.model_dump(by_alias=True), values)
        start = max(0, int(d.get("start") or 0))
        if start >= clip.duration:
            continue
        if d.get("hold", True):
            d["duration"] = clip.duration - start
        else:
            d["duration"] = max(1, min(int(d.get("duration") or 1), clip.duration - start))
        d["start"] = start
        out.append(Clip(**d))
    return out


# ── counters ─────────────────────────────────────────────────────────


def counter_text(text: str, counter: Counter | None, f_rel: float) -> str:
    """Replace the first number in `text` with its value part-way through the count."""
    if not counter:
        return text
    m = _NUMBER.search(text)
    if not m:
        return text
    raw = m.group(0)
    try:
        target = float(raw.replace(",", ""))
    except ValueError:
        return text
    p = ease((f_rel - counter.delay) / max(1, counter.duration), counter.ease)
    v = counter.start + (target - counter.start) * p
    decimals = len(raw.split(".")[1]) if "." in raw else 0
    s = f"{v:,.{decimals}f}" if "," in raw else f"{v:.{decimals}f}"
    return text[: m.start()] + s + text[m.end():]


# ── per-unit text animators ──────────────────────────────────────────


def unit_rank(order: str, i: int, n: int) -> float:
    if order == "reverse":
        return n - 1 - i
    if order == "center":
        return abs(i - (n - 1) / 2)
    if order == "random":
        return zlib.crc32(f"{i}:{n}".encode()) % max(1, n)
    return i


def unit_progress(anim: TextAnim, i: int, n: int, f_rel: float, clip_duration: int) -> float:
    """0 → hidden, 1 → at rest, for unit i of n at clip-relative frame f_rel."""
    r = unit_rank(anim.order, i, n)
    p = (f_rel - anim.delay - r * anim.stagger + 1) / max(1, anim.duration)
    p = min(1.0, max(0.0, p))
    if anim.out:
        total = anim.duration + anim.stagger * (n - 1)
        start_out = clip_duration - total
        q = (f_rel - start_out - r * anim.stagger + 1) / max(1, anim.duration)
        p = min(p, 1.0 - min(1.0, max(0.0, q)))
    return p


def unit_effect(kind: str, p: float, cell_h: float) -> tuple[float, float, float, float, float]:
    """(dx px, dy px, scale, opacity, reveal fraction) for one unit at progress p."""
    e = 1 - (1 - p) ** 3
    if kind == "fade":
        return 0.0, 0.0, 1.0, p, 0.0
    if kind == "rise":
        return 0.0, (1 - e) * cell_h * 0.6, 1.0, min(1.0, p * 1.5), 0.0
    if kind == "drop":
        return 0.0, -(1 - e) * cell_h * 0.6, 1.0, min(1.0, p * 1.5), 0.0
    if kind == "pop":
        over_shoot = 1 + 0.18 * math.sin(math.pi * e) if p < 1 else 1.0
        return 0.0, 0.0, (0.4 + 0.6 * e) * over_shoot, min(1.0, p * 2), 0.0
    if kind == "slide-left":
        return (1 - e) * cell_h * 0.8, 0.0, 1.0, min(1.0, p * 1.5), 0.0
    if kind == "slide-right":
        return -(1 - e) * cell_h * 0.8, 0.0, 1.0, min(1.0, p * 1.5), 0.0
    if kind == "reveal":  # slides up from behind its own line (masked)
        return 0.0, 0.0, 1.0, 1.0 if p > 0 else 0.0, 1 - e
    return 0.0, 0.0, 1.0, p, 0.0


# ── building graphics ────────────────────────────────────────────────


def pack(clips: list[dict], *, start: int, duration: int, controls: list[dict] | None = None,
         name: str = "Graphic", template: str | None = None, clip_id: str | None = None) -> dict:
    """One graphic clip from loose clips (bottom first), shifting them to graphic time."""
    layers = []
    for c in clips:
        layer = copy.deepcopy(c)
        layer["start"] = int(layer.get("start", 0)) - start
        end = layer["start"] + int(layer.get("duration", duration))
        layer["hold"] = bool(layer.get("hold", True)) and end >= duration  # only layers that reach the end stretch
        layers.append(layer)
    return {"id": clip_id or f"g-{template or 'graphic'}", "type": "graphic", "name": name, "start": start,
            "duration": duration, "box": [0, 0, 1, 1],
            "graphic": {"layers": layers, "controls": controls or [], "template": template}}
