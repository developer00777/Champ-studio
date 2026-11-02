"""Template engine — Champ Studio Template format v1 ("CST").

A template is scenes of layers plus a JSON Schema of typed `slots`
(`x-kind`: text | media | color | voiceover | music | list | number | logo).
The same schema drives the UI form, validation, and the LLM's structured
output when a brief fills the template. Layers point at slots ("{{headline}}"
or {"$slot": "bg"}) and brand values ({"$brand": "styles.title"}); boxes are
per aspect ratio ({"*": [x,y,w,h], "9:16": [...]}).

    fill (AI or form) → prepare (generate media, voiceover, music, timings)
                      → compile (template × slots × brand × aspect → Timeline)
                      → render / open in editor

Compiled clips keep `source = {template, scene, layer, slot}` back-references
so slots can be re-filled after manual edits.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from pathlib import Path

import jsonschema

from . import brands, db, graphics, library, llm, producers
from .providers.base import ProviderError
from .render import media
from .timeline import Timeline, canvas_for

log = logging.getLogger("champstudio.templates")

LIBRARY_DIR = Path(__file__).resolve().parent / "assets" / "templates"
FPS = 30
ASPECTS = ["16:9", "9:16", "1:1", "4:5"]
CATEGORIES = {
    "social-ads": "Social ads",
    "product-promo": "Product & SaaS promo",
    "explainers": "Explainers",
    "data-leadership": "Data & thought leadership",
    "social-proof": "Social proof",
    "events": "Events & webinars",
    "recruitment": "Recruitment",
    "sales-enablement": "Lead gen & sales enablement",
    "brand-utility": "Brand utilities",
    "company-news": "Seasonal & company news",
    "motion-graphics": "Motion graphics",
}

LAYER_TYPES = ["media", "text", "shape", "logo", "color", "list", "stat"]

# Schema for template documents themselves (authoring validation).
TEMPLATE_SCHEMA = {
    "type": "object",
    "required": ["cst", "id", "name", "category", "slots", "scenes"],
    "properties": {
        "cst": {"const": "1.0"},
        "id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{2,60}$"},
        "name": {"type": "string", "minLength": 3, "maxLength": 60},
        "category": {"enum": list(CATEGORIES)},
        "description": {"type": "string", "maxLength": 300},
        "tags": {"type": "array", "items": {"type": "string"}},
        "aspects": {"type": "array", "items": {"enum": ASPECTS}},
        "slots": {"type": "object", "required": ["type", "properties"],
                  "properties": {"type": {"const": "object"}, "properties": {"type": "object"}}},
        "defaults": {"type": "object"},
        "scenes": {"type": "array", "minItems": 1, "items": {
            "type": "object", "required": ["id", "layers"],
            "properties": {
                "id": {"type": "string"},
                "duration": {"type": "object"},
                "vo": {},
                "transition": {"type": "object"},
                "layers": {"type": "array", "items": {"type": "object", "required": ["type"],
                                                      "properties": {"type": {"enum": LAYER_TYPES}}}},
            }}},
        "audio": {"type": "object"},
        "captions": {"type": "object"},
        "overlay": {"type": "boolean"},
        "graphic": {"type": "boolean"},
    },
}


class TemplateError(ValueError):
    pass


# ── library ──────────────────────────────────────────────────────────


def _bundled() -> dict[str, dict]:
    out = {}
    if LIBRARY_DIR.is_dir():
        for p in sorted(LIBRARY_DIR.glob("*.json")):
            try:
                t = json.loads(p.read_text(encoding="utf-8"))
                out[t["id"]] = {**t, "origin": "library"}
            except (json.JSONDecodeError, KeyError) as e:
                log.error("bad template %s: %s", p.name, e)
    return out


def _user() -> dict[str, dict]:
    with db.conn() as c:
        rows = c.execute("SELECT data FROM user_templates ORDER BY created_at DESC").fetchall()
    out = {}
    for r in rows:
        t = db.loads(r["data"], {})
        if t.get("id"):
            out[t["id"]] = {**t, "origin": "team"}
    return out


def all_templates() -> dict[str, dict]:
    return {**_bundled(), **_user()}


def get(tid: str) -> dict:
    t = all_templates().get(tid)
    if not t:
        raise KeyError(tid)
    return t


def summary(t: dict) -> dict:
    return {k: t.get(k) for k in ("id", "name", "category", "description", "tags", "aspects", "origin")} | {
        "overlay": bool(t.get("overlay")),
        "graphic": bool(t.get("graphic")),
        "scenes": len(t.get("scenes", [])),
        "slots": list((t.get("slots") or {}).get("properties", {}).keys()),
        "duration_hint": estimate_duration(t),
    }


def validate_template(t: dict) -> list[str]:
    errors = llm.validation_errors(t, TEMPLATE_SCHEMA)
    if errors:
        return errors
    try:
        jsonschema.Draft202012Validator.check_schema(t["slots"])
    except jsonschema.SchemaError as e:
        return [f"slots schema: {e.message}"]
    slot_names = set(t["slots"]["properties"])
    refs = set(re.findall(r"\{\{\s*([a-z0-9_]+)", json.dumps(t["scenes"])))
    refs |= {m for m in re.findall(r'"\$slot":\s*"([a-z0-9_]+)"', json.dumps(t["scenes"]) + json.dumps(t.get("audio", {})))}
    missing = sorted(r for r in refs if r not in slot_names and r != "brand")
    if missing:
        errors.append(f"layers reference unknown slots: {missing}")
    defaults = t.get("defaults") or {}
    loose = {**_relaxed(t["slots"]), "required": []}  # defaults may leave media/voiceover empty
    bad = llm.validation_errors(_defaults_for_validation(t, defaults), loose)
    errors += [f"defaults: {e}" for e in bad]
    return errors


def save_user_template(t: dict) -> dict:
    t = {**t, "cst": "1.0"}
    errors = validate_template(t)
    if errors:
        raise TemplateError("; ".join(errors[:5]))
    with db.conn() as c:
        c.execute("DELETE FROM user_templates WHERE id = ?", (t["id"],))
        c.execute("INSERT INTO user_templates (id, data, created_at) VALUES (?,?,?)",
                  (t["id"], db.dumps({k: v for k, v in t.items() if k != "origin"}), db.now()))
    return {**t, "origin": "team"}


def delete_user_template(tid: str) -> bool:
    with db.conn() as c:
        return bool(c.execute("DELETE FROM user_templates WHERE id = ?", (tid,)).rowcount)


def estimate_duration(t: dict) -> float:
    total = 0.0
    for s in t.get("scenes", []):
        d = s.get("duration") or {}
        total += float(d.get("sec") or d.get("min") or 3)
    return round(total, 1)


# ── slots ────────────────────────────────────────────────────────────


def slot_kinds(t: dict) -> dict[str, str]:
    return {k: v.get("x-kind", "text") for k, v in t["slots"]["properties"].items()}


def _relaxed(schema: dict) -> dict:
    """Slots schema where media/logo/music values may be objects or asset ids."""
    s = copy.deepcopy(schema)
    for name, prop in s.get("properties", {}).items():
        if prop.get("x-kind") in ("media", "logo", "music", "audio"):
            s["properties"][name] = {}
    return s


def _defaults_for_validation(t: dict, values: dict) -> dict:
    return {k: v for k, v in values.items() if k in t["slots"]["properties"]}


def with_defaults(t: dict, values: dict | None) -> dict:
    out = copy.deepcopy(t.get("defaults") or {})
    for k, prop in t["slots"]["properties"].items():
        if k not in out and "default" in prop and not isinstance(prop["default"], dict):
            out[k] = prop["default"]
    out.update({k: v for k, v in (values or {}).items() if v not in (None, "")})
    return out


def validate_values(t: dict, values: dict) -> list[str]:
    return llm.validation_errors(_defaults_for_validation(t, values), _relaxed(t["slots"]))


def llm_schema(t: dict) -> dict:
    """What the LLM fills: text-ish slots as authored; media slots become a
    generation prompt; asset-only kinds (logo, uploaded audio) are skipped."""
    props, required = {}, []
    for name, prop in t["slots"]["properties"].items():
        kind = prop.get("x-kind", "text")
        if kind in ("logo", "audio"):
            continue
        if kind == "media":
            props[name] = {"type": "object", "description": prop.get("description", "visual"),
                           "required": ["prompt", "media_type"],
                           "properties": {"prompt": {"type": "string", "maxLength": 500},
                                          "media_type": {"type": "string", "enum": prop.get("x-media", ["image", "video"])}}}
        elif kind == "music":
            props[name] = {"type": "string", "enum": list(producers.MUSIC_PROMPTS)}
        else:
            props[name] = {k: v for k, v in prop.items() if k != "default"}
        if name in (t["slots"].get("required") or []) or kind in ("media", "voiceover"):
            required.append(name)
    return {"type": "object", "required": required, "properties": props}


SYSTEM_FILL = """You fill a video template's slots from a brief, for a B2B brand.

- Respect every maxLength strictly; on-screen text is short and punchy (headlines ≤7 words).
- voiceover slots are spoken English (~150 wpm) matched to the scene they narrate; never read the
  on-screen text verbatim.
- media prompts describe a photographic/cinematic visual: subject, setting, lighting, composition, mood.
  NEVER ask for text, letters, logos or real people's likeness inside visuals.
- Use only facts given in the brief; never invent statistics. If a stat slot needs a number and none is
  given, use a qualitative phrase that fits the pattern if possible.
- Match the brand tone given."""


async def ai_fill(t: dict, brief: str, kit: dict, *, project_id: str | None = None) -> dict:
    schema = llm_schema(t)
    user = (f"Template: {t['name']} — {t.get('description', '')}\n"
            f"Scenes: {[s.get('role', s['id']) for s in t['scenes']]}\n"
            f"Brand: {kit.get('name')} — tone: {kit.get('tone', '')}; tagline: {kit.get('tagline', '')}; "
            f"default CTA: {kit.get('cta', '')}\n\nBrief:\n{brief}")
    values = await llm.structured("planner", SYSTEM_FILL, user, schema, name="template_slots",
                                  temperature=0.6, project_id=project_id)
    return values


# ── prepare (generate assets) ────────────────────────────────────────


def _text(template_str: str, values: dict, kit: dict) -> str:
    def sub(m):
        key = m.group(1)
        if key.startswith("brand."):
            v = brands.lookup(kit, key[6:], "")
        else:
            v = values.get(key, "")
        if isinstance(v, list):
            return ", ".join(str(x) for x in v)
        return "" if v is None else str(v)

    return re.sub(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}", sub, template_str).strip()


def _scene_vo(scene: dict, values: dict, kit: dict) -> str:
    vo = scene.get("vo")
    if not vo:
        return ""
    if isinstance(vo, dict) and "$slot" in vo:
        v = values.get(vo["$slot"], "")
        return v if isinstance(v, str) else ""
    return _text(str(vo), values, kit)


async def prepare(t: dict, values: dict, kit: dict, *, aspect: str = "16:9", project_id: str | None = None,
                  voiceover: bool = True, allow_video: bool = False, music: bool = True) -> dict:
    """Generate every asset the slots ask for. Returns {"values", "vo": {scene_id: {...}}, "music_id"}."""
    values = with_defaults(t, values)
    kinds = slot_kinds(t)
    style = (f"clean modern B2B commercial look, colour accents {kit['colors']['primary']} and "
             f"{kit['colors']['accent']}, no text, no logos")
    sem = asyncio.Semaphore(3)

    async def media_slot(name: str):
        v = values.get(name)
        if isinstance(v, str) and library.get_asset(v):
            values[name] = {"asset_id": v}
            return
        if not isinstance(v, dict) or v.get("asset_id"):
            return
        prompt = v.get("prompt") or v.get("query")
        if not prompt:
            return
        async with sem:
            still = await producers.still(prompt, aspect=aspect, project_id=project_id, style=style)
            out = {"asset_id": still["id"], "prompt": prompt}
            if v.get("media_type") == "video" and allow_video:
                try:
                    clip = await producers.clip(f"{prompt}. Slow cinematic camera move.", seconds=5, aspect=aspect,
                                                still_id=still["id"], project_id=project_id)
                    out = {"asset_id": clip["id"], "prompt": prompt, "still_id": still["id"]}
                except ProviderError as e:
                    log.warning("template clip failed, using still: %s", e)
        values[name] = out

    await asyncio.gather(*(media_slot(n) for n, k in kinds.items() if k == "media"))

    vo: dict[str, dict] = {}
    if voiceover:
        voice = (kit.get("voice") or {}).get("voice")
        for scene in t["scenes"]:
            text = _scene_vo(scene, values, kit)
            if not text:
                continue
            asset, dur = await producers.voiceover(text, voice=voice, model=(kit.get("voice") or {}).get("model"),
                                                   project_id=project_id)
            words = await producers.word_timings(asset["id"], text, project_id=project_id)
            vo[scene["id"]] = {"asset_id": asset["id"], "duration": dur, "words": words, "text": text}

    music_id = None
    music_spec = (t.get("audio") or {}).get("music")
    mood = None
    if isinstance(music_spec, dict) and "$slot" in music_spec:
        mood = values.get(music_spec["$slot"])
    elif isinstance(music_spec, str):
        mood = music_spec
    if isinstance(mood, str) and library.get_asset(mood):
        music_id = mood
    elif music and mood and mood != "none":
        try:
            m = await producers.music(mood, project_id=project_id)
            music_id = m["id"] if m else None
        except ProviderError as e:
            log.warning("music failed: %s", e)
    return {"values": values, "vo": vo, "music_id": music_id}


# ── compile ──────────────────────────────────────────────────────────


def _box(spec, aspect: str, default=(0.0, 0.0, 1.0, 1.0)) -> list[float]:
    if spec is None:
        return list(default)
    if isinstance(spec, list):
        return spec
    return list(spec.get(aspect) or spec.get("vertical" if aspect in ("9:16", "4:5") else "horizontal")
                or spec.get("*") or default)


def _frames(sec: float) -> int:
    return max(1, int(round(sec * FPS)))


MOTIONS = {
    "kenburns-in": {"scale": (1.0, 1.15)},
    "kenburns-out": {"scale": (1.15, 1.0)},
    "pan-left": {"scale": (1.12, 1.12), "x": (0.03, -0.03)},
    "pan-right": {"scale": (1.12, 1.12), "x": (-0.03, 0.03)},
    "pan-up": {"scale": (1.12, 1.12), "y": (0.03, -0.03)},
    "none": {},
}


def _motion(name: str | None, dur: int) -> dict:
    spec = MOTIONS.get(name or "none", {})
    return {prop: [{"f": 0, "v": a, "ease": "linear"}, {"f": dur, "v": b}] for prop, (a, b) in spec.items()}


def _style(spec, values, kit) -> dict:
    resolved = brands.resolve(spec or {"$brand": "styles.subtitle"}, kit, values)
    return {k: v for k, v in (resolved or {}).items() if v is not None}


def _value_present(values: dict, name: str | None) -> bool:
    if not name:
        return True
    v = values.get(name)
    if isinstance(v, dict):
        return bool(v.get("asset_id"))
    if isinstance(v, list):
        return bool(v)
    return v not in (None, "", False)


_BRAND_FILLS = ("colors.primary", "colors.primary_dark", "colors.accent", "gradients.hero", "gradients.sunset")


def _on_brand_colour(layers_below: list[dict]) -> bool:
    """True when the layers under a logo are a brand-colour fill (so the white logo is needed)."""
    for layer in reversed(layers_below):
        ref = json.dumps(layer.get("shape") or layer.get("color") or "")
        if layer["type"] in ("shape", "color") and (layer.get("box") in (None, {"*": [0, 0, 1, 1]})):
            return any(f'"{b}"' in ref for b in _BRAND_FILLS)
        if layer["type"] == "media" and not layer.get("box"):
            return False
    return False


def _sec(v) -> int:
    return int(round(float(v) * FPS))


def _motion_extras(base: dict, layer: dict, scene_id: str) -> None:
    """Motion-graphics layer fields (authored in seconds) → clip fields (frames)."""
    if layer.get("transform"):
        base["transform"] = {**base.get("transform", {}), **layer["transform"]}
    if layer.get("keyframes"):  # {"scale": [[sec, value, ease?], ...]}, relative to the layer start
        base["keyframes"] = {name: [{"f": _sec(k[0]), "v": k[1], "ease": k[2] if len(k) > 2 else "ease"} for k in kfs]
                             for name, kfs in layer["keyframes"].items()}
    for key in ("text_anim", "counter"):
        if layer.get(key):
            spec = dict(layer[key])
            for k in ("duration", "stagger", "delay"):
                if k in spec:
                    spec[k] = _sec(spec[k])
            base[key] = spec
    if layer.get("pin"):
        base["pin"] = {**layer["pin"], "to": f"{scene_id}-{layer['pin']['to']}"}
    for key in ("mask", "hold", "bind"):
        if key in layer:
            base[key] = layer[key]


# ── graphics (MOGRT-style) ───────────────────────────────────────────

_CONTROL_KINDS = ("text", "number", "color")


def _control_kind(prop: dict) -> str:
    if prop.get("enum"):
        return "choice"
    if prop.get("type") == "boolean":
        return "toggle"
    kind = prop.get("x-kind", "text")
    if kind in ("media", "logo"):
        return "media"
    if kind == "color":
        return "color"
    if kind == "number" or prop.get("type") in ("number", "integer"):
        return "number"
    return "text"


def graphic_controls(t: dict, values: dict, kit: dict) -> list[dict]:
    """One exposed control per slot, seeded with the slot's current value."""
    out = []
    for name, prop in t["slots"]["properties"].items():
        kind = _control_kind(prop)
        if prop.get("x-kind") in ("voiceover", "music", "audio"):
            continue
        v = values.get(name)
        if v is None:
            v = {"color": kit["colors"].get("primary", "#FF6B35"), "number": 0, "toggle": False}.get(kind, "")
            if kind == "media":
                v = None
        if isinstance(v, list):
            v = "\n".join(str(x) for x in v)
        out.append({"id": name, "label": prop.get("description") or name.replace("_", " ").capitalize(), "kind": kind,
                    "value": v, "options": list(prop.get("enum") or []),
                    "multiline": prop.get("x-kind") == "list" or int(prop.get("maxLength") or 0) > 60,
                    **({"min": prop["minimum"]} if "minimum" in prop else {}),
                    **({"max": prop["maximum"]} if "maximum" in prop else {})})
    return out


def _placeholder_values(t: dict, values: dict) -> dict:
    out = dict(values)
    for name, prop in t["slots"]["properties"].items():
        if _control_kind(prop) in (*_CONTROL_KINDS, "choice", "toggle") and prop.get("x-kind") != "list":
            out[name] = "{{" + name + "}}"
    return out


def _collect_binds(node, path: str, out: dict) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("id", "source", "bind", "keyframes", "graphic", "pin"):
                continue
            _collect_binds(v, f"{path}.{k}" if path else k, out)
    elif isinstance(node, list) and path == "shape.gradient":
        for i, v in enumerate(node):
            if isinstance(v, str) and "{{" in v:
                out[f"gradient.{i}"] = v
    elif isinstance(node, str) and "{{" in node:
        out[path] = node


def graphic_clip(t: dict, values: dict, kit: dict, aspect: str = "16:9", *, start: int = 0) -> dict:
    """Compile a template into ONE graphic clip whose slots are exposed controls
    (what Premiere does when a .mogrt is dropped on the timeline)."""
    values = with_defaults(t, values)
    tl = _compile_timeline(t, {"values": _placeholder_values(t, values), "vo": {}, "music_id": None}, kit, aspect)
    # layers the template leaves out for these values ("if" / "unless") stay out of the graphic
    real = _compile_timeline(t, {"values": values, "vo": {}, "music_id": None}, kit, aspect)
    keep = {c.id for tr in real.video_tracks() for c in tr.clips}
    clips = [c.model_dump(by_alias=True) for tr in tl.video_tracks() if tr.id != "captions" for c in tr.clips
             if c.id in keep]
    controls = graphic_controls(t, values, kit)
    current = {c["id"]: c["value"] for c in controls}
    used: set[str] = set()
    for c in clips:
        bind: dict = dict(c.get("bind") or {})  # explicit template binds (e.g. bar lengths)
        _collect_binds(c, "", bind)
        slot = (c.get("source") or {}).get("slot")
        if slot and c["type"] in ("image", "video"):
            bind["asset_id"] = "{{" + slot + "}}"
        used |= {m for v in bind.values() for m in re.findall(r"\{\{\s*([a-zA-Z0-9_]+)", v)}
        c.update(graphics.apply_bindings({**c, "bind": bind}, current))
        c["bind"] = bind
    duration = max((c["start"] + c["duration"] for c in clips), default=_frames(estimate_duration(t)))
    g = graphics.pack(clips, start=0, duration=duration, controls=[c for c in controls if c["id"] in used],
                      name=t["name"], template=t["id"], clip_id=f"g-{t['id']}")
    g["start"] = start
    return g


def compile_timeline(t: dict, prepared: dict, kit: dict, aspect: str = "16:9") -> Timeline:
    tl = _compile_timeline(t, prepared, kit, aspect)
    if not t.get("graphic"):
        return tl
    g = graphic_clip(t, prepared["values"], kit, aspect)
    rest = [tr.model_dump(by_alias=True) for tr in tl.tracks if tr.kind == "audio" or tr.id == "captions"]
    data = tl.model_dump(by_alias=True)
    data["tracks"] = [{"id": "v1", "kind": "video", "name": "V1", "clips": [g]}] + rest
    return Timeline(**data)


def _compile_timeline(t: dict, prepared: dict, kit: dict, aspect: str = "16:9") -> Timeline:
    values = prepared["values"]
    vo = prepared.get("vo") or {}
    w, h = canvas_for(aspect)
    tracks: dict[str, list] = {}
    order: list[str] = []

    def put(track: str, clip: dict):
        if track not in tracks:
            tracks[track] = []
            order.append(track)
        tracks[track].append(clip)

    t0 = 0
    caption_words = []
    for si, scene in enumerate(t["scenes"]):
        d = scene.get("duration") or {"mode": "fixed", "sec": 3}
        v = vo.get(scene["id"])
        sec = float(d.get("sec") or d.get("min") or 3)
        if v:
            sec = max(sec if d.get("mode") == "fixed" else float(d.get("min") or 1.5),
                      v["duration"] + float(d.get("pad", 0.5)))
        if d.get("max"):
            sec = min(sec, float(d["max"])) if not v else sec
        dur = _frames(sec)
        trans = scene.get("transition")
        for li, layer in enumerate(scene["layers"]):
            if not _value_present(values, layer.get("if")):
                continue
            if layer.get("unless") and _value_present(values, layer["unless"]):
                continue
            start = t0 + _frames(layer.get("start", 0)) if layer.get("start") else t0
            end_sec = layer.get("end")
            ldur = (_frames(end_sec) if end_sec is not None else dur) - (start - t0)
            ldur = max(1, min(ldur, t0 + dur - start))
            base = {"id": f"{scene['id']}-{layer.get('id', li)}", "start": start, "duration": ldur,
                    "box": _box(layer.get("box"), aspect), "name": layer.get("name") or layer["type"],
                    "source": {"template": t["id"], "scene": scene["id"], "layer": li,
                               "slot": (layer.get("src") or {}).get("$slot") if isinstance(layer.get("src"), dict) else None}}
            for k in ("anim_in", "anim_out"):
                if layer.get(k):
                    base[k] = {"type": layer[k]["type"], "duration": _frames(layer[k].get("sec", 0.4))}
            if "opacity" in layer:
                base["transform"] = {"opacity": layer["opacity"]}
            _motion_extras(base, layer, scene["id"])
            if li == 0 and si > 0 and trans and trans.get("type", "cut") != "cut":
                base["transition_in"] = {"type": trans["type"], "duration": _frames(trans.get("sec", 0.4))}
            track = f"v{li + 1}"
            lt = layer["type"]
            if lt == "media":
                ref = brands.resolve(layer.get("src"), kit, values)
                aid = ref.get("asset_id") if isinstance(ref, dict) else (ref if isinstance(ref, str) else None)
                if aid and library.get_asset(aid):
                    kind = library.get_asset(aid)["kind"]
                    clip = {**base, "type": "video" if kind == "video" else "image", "asset_id": aid,
                            "fit": layer.get("fit", "cover")}
                    if kind != "video":
                        clip["keyframes"] = _motion(layer.get("motion", "kenburns-in"), ldur)
                    elif layer.get("with_audio"):
                        aid_clip = f"{base['id']}-sound"
                        clip["linked"] = aid_clip
                        put("a0", {"id": aid_clip, "type": "audio", "asset_id": aid, "role": "dialogue",
                                   "start": start, "duration": ldur, "linked": base["id"], "name": "Clip sound"})
                    put(track, clip)
                else:  # placeholder until media exists: brand gradient
                    put(track, {**base, "type": "shape", "name": "Media placeholder",
                                "shape": {"kind": "rect", "gradient": kit["gradients"]["hero"]}})
            elif lt in ("text", "stat"):
                raw = layer.get("text", "")
                text = _text(raw, values, kit) if isinstance(raw, str) else str(brands.resolve(raw, kit, values) or "")
                if not text:
                    continue
                style = _style(layer.get("style") or ({"$brand": "styles.stat"} if lt == "stat" else None), values, kit)
                put(track, {**base, "type": "text", "text": text, "style": style})
            elif lt == "list":
                items = brands.resolve(layer.get("items"), kit, values) or []
                if isinstance(items, str):
                    items = [x.strip() for x in items.split("\n") if x.strip()]
                bx, by, bw, bh = base["box"]
                n = max(1, len(items))
                gap = layer.get("stagger", 0.6)
                style = _style(layer.get("style"), values, kit)
                if items:
                    from .render import text as _textmod
                    from .timeline import TextStyle as _TS

                    longest = max((str(x) for x in items), key=len)
                    ts = _TS(**style)
                    _, _, px, _ = _textmod._fit(longest, ts, int(bw * w), int(bh / n * h), h / 1080)
                    style = {**style, "size": round(px / (h / 1080), 1), "fit": "none"}
                for ii, item in enumerate(items):
                    ist = start + _frames(gap * ii)
                    put(f"{track}-{ii}", {**base, "id": f"{base['id']}-{ii}", "type": "text", "text": str(item),
                                          "style": {**style, "align": style.get("align", "left")},
                                          "start": ist, "duration": max(1, t0 + dur - ist),
                                          "box": [bx, by + bh * ii / n, bw, bh / n],
                                          "anim_in": base.get("anim_in") or {"type": "slide-left", "duration": 10}})
            elif lt == "shape":
                shape = brands.resolve(layer.get("shape") or {}, kit, values)
                put(track, {**base, "type": "shape", "shape": shape})
            elif lt == "color":
                put(track, {**base, "type": "color", "color": brands.resolve(layer.get("color"), kit, values)
                            or kit["colors"]["dark"]})
            elif lt == "logo":
                default_src = {"$brand": "logos.light"} if _on_brand_colour(scene["layers"][:li]) else {"$brand": "logos.primary"}
                ref = brands.resolve(layer.get("src") or default_src, kit, values)
                if not ref:  # no light variant: fall back to the primary logo
                    ref = brands.resolve({"$brand": "logos.primary"}, kit, values)
                aid = ref.get("asset_id") if isinstance(ref, dict) else ref
                if aid and library.get_asset(aid):
                    put(track, {**base, "type": "image", "asset_id": aid, "fit": "contain"})
                elif layer.get("fallback_text", True):
                    fb = layer.get("fallback_style") or {"$brand": "styles.title", "size": 64}
                    put(track, {**base, "type": "text", "text": kit.get("name", ""),
                                "style": _style(fb, values, kit)})
        if v:
            put("a1", {"id": f"{scene['id']}-vo", "type": "audio", "asset_id": v["asset_id"], "role": "vo",
                       "start": t0 + 6, "duration": _frames(v["duration"]), "name": "Voiceover",
                       "source": {"template": t["id"], "scene": scene["id"], "slot": "vo"}})
            for wd in v.get("words") or []:
                caption_words.append({"w": wd["word"], "s": t0 + 6 + _frames(wd["start"]),
                                      "e": t0 + 6 + max(_frames(wd["end"]), _frames(wd["start"]) + 2)})
        t0 += dur

    cap = t.get("captions") or {}
    if caption_words and cap.get("enabled", True):
        put("captions", {"id": "captions", "type": "captions", "start": 0, "duration": t0, "name": "Captions",
                         "box": _box(cap.get("box") or {"*": [0.1, 0.8, 0.8, 0.13], "vertical": [0.06, 0.68, 0.88, 0.13]},
                                     aspect),
                         "style": _style(cap.get("style") or {"$brand": "styles.captions"}, values, kit),
                         "captions": {"words": caption_words, "mode": cap.get("mode", "karaoke"), "max_chars": 28}})
    audio = t.get("audio") or {}
    mid = prepared.get("music_id")
    if mid and library.get_asset(mid):
        mlen = _frames(media.probe(str(library.asset_path(mid)))["duration"])
        pos, n = 0, 0
        while pos < t0:
            seg = min(mlen, t0 - pos)
            put("a2", {"id": f"music{n}", "type": "audio", "asset_id": mid, "role": "music", "start": pos,
                       "duration": seg, "volume": audio.get("music_volume", 0.35),
                       "duck_db": audio.get("duck_db", 9) if vo else None,
                       "fade_in": 12 if n == 0 else 0, "fade_out": 40 if pos + seg >= t0 else 0, "name": "Music"})
            pos += seg
            n += 1
    video_ids = [k for k in order if not k.startswith("a")]
    audio_ids = sorted((k for k in order if k.startswith("a")), key=lambda k: {"a1": 0, "a0": 1}.get(k, 2))
    bg = brands.resolve(t.get("background") or {"$brand": "colors.dark"}, kit, values) or "#000000"
    return Timeline(fps=FPS, width=w, height=h, background=bg,
                    tracks=[{"id": k, "kind": "video", "name": k.upper(), "clips": tracks[k]} for k in video_ids]
                    + [{"id": k, "kind": "audio", "name": {"a1": "Voiceover", "a0": "Clip sound"}.get(k, "Music"), "clips": tracks[k]}
                       for k in audio_ids],
                    meta={"source": "template", "template": t["id"], "aspect": aspect, "values": values})


def preview_timeline(t: dict, kit: dict, aspect: str) -> Timeline:
    """Timeline with defaults and placeholders only (no generation) for thumbnails."""
    values = with_defaults(t, {})
    return compile_timeline(t, {"values": values, "vo": {}, "music_id": None}, kit, aspect)
