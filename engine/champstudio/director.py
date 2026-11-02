"""Director — a brief becomes a finished video (InVideo AI equivalent).

Stages, each its own job so a person can approve between them:
  1. plan        LLM writes title, script and a scene plan (strict JSON schema)
  2. storyboard  one still per scene (cheap) — approve or regenerate before paying for video
  3. produce     voiceover per scene, clips (image-to-video / Ken Burns / avatar / text card),
                 music bed, word timings for captions
  4. assemble    one timeline per aspect ratio (shared Timeline model → editable in the editor)
  5. render      one MP4 per aspect ratio
  chat           an instruction becomes a JSON patch on the plan; only affected scenes rerun

Project data lives in a `projects` row of kind "director".
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging

from . import brands, costs, jobs, library, llm, producers, projects
from .providers.base import ProviderError
from .render import media
from .timeline import Timeline, canvas_for

log = logging.getLogger("champstudio.director")

FPS = 30
WORDS_PER_SECOND = 2.2
PURPOSES = ["hook", "problem", "solution", "feature", "proof", "stat", "story", "intro", "cta", "outro"]
VISUAL_TYPES = ["ai_video", "ai_image", "avatar", "text_card", "upload"]
TRANSITIONS = ["cut", "crossfade", "dip-black", "wipe-left", "slide-left", "slide-up", "zoom"]
MOODS = ["corporate-upbeat", "calm-tech", "inspiring", "energetic", "chill", "dramatic", "none"]

PLAN_SCHEMA = {
    "type": "object",
    "required": ["title", "logline", "music_mood", "scenes", "cta"],
    "properties": {
        "title": {"type": "string", "maxLength": 80},
        "logline": {"type": "string", "maxLength": 240},
        "music_mood": {"type": "string", "enum": MOODS},
        "cta": {"type": "string", "maxLength": 60},
        "scenes": {
            "type": "array", "minItems": 1, "maxItems": 20,
            "items": {
                "type": "object",
                "required": ["purpose", "voiceover", "on_screen_text", "visual", "visual_type", "duration_s",
                             "transition"],
                "properties": {
                    "purpose": {"type": "string", "enum": PURPOSES},
                    "voiceover": {"type": "string", "maxLength": 400},
                    "on_screen_text": {"type": "string", "maxLength": 60},
                    "visual": {"type": "string", "maxLength": 600},
                    "camera": {"type": "string", "maxLength": 120},
                    "visual_type": {"type": "string", "enum": VISUAL_TYPES},
                    "duration_s": {"type": "number", "minimum": 1.5, "maximum": 15},
                    "transition": {"type": "string", "enum": TRANSITIONS},
                },
            },
        },
    },
}

SYSTEM_PLAN = """You are a senior B2B video director and copywriter. Turn the brief into a shootable plan.

Rules:
- Hook in the first scene (≤2.5 s of voiceover): a pain point, bold stat or question for the audience.
- Voiceover is spoken English at ~130 words/minute: each scene's duration_s ≈ words / 2.2 + 0.4.
  Budget the whole script: total words ≈ target seconds × 2.2 (a 30 s ad has ~60–65 words).
- Total duration must land within ±15% of the requested length.
- on_screen_text: ≤7 words, punchy, never a copy of the whole voiceover sentence. Empty string if none.
- visual: a detailed image/video prompt — subject, setting, composition, lighting, lens, mood, colour grade
  (lean on the brand colours given). NO text, letters, logos, UI copy or real people's likeness in visuals
  (generators garble text; logos are added later). Diverse, specific, cinematic, professional.
- camera: one short camera move for video scenes (e.g. "slow push-in", "orbit left", "static").
- visual_type: follow the production budget given. "text_card" suits stats and CTAs; "avatar" only when a
  presenter is available.
- Last scene is a CTA using the brand's call to action.
- Stay on brand tone. Be concrete; use numbers from the brief; never invent statistics that aren't given.
- transition: "cut" for energy, "crossfade" for calm, others sparingly."""

SYSTEM_EDIT = """You edit a video plan. Apply the user's instruction as JSON Patch operations on the plan.

- Paths look like /title, /music_mood, /cta, /scenes/2/voiceover, /scenes/0/visual, /scenes/- (append).
- Scene indices are 0-based. Use "replace" for changes, "add" to insert a scene object, "remove" to delete.
- New scene objects need every scene field. Keep everything else unchanged.
- Respect the same writing rules as the original plan (≤7-word on-screen text, no text in visuals).
- summary: one sentence describing what changed."""

PATCH_SCHEMA = {
    "type": "object",
    "required": ["ops", "summary"],
    "properties": {
        "summary": {"type": "string", "maxLength": 200},
        "ops": {"type": "array", "maxItems": 40, "items": {
            "type": "object", "required": ["op", "path"],
            "properties": {
                "op": {"type": "string", "enum": ["replace", "add", "remove"]},
                "path": {"type": "string", "maxLength": 80},
                "value": {"type": ["string", "number", "object", "null"]},
            }}},
    },
}

BUDGET_RULES = {
    "lean": "Use ai_image (stills with motion) or text_card for every scene. No ai_video.",
    "balanced": "Use ai_video for the hook and at most two other key scenes; ai_image or text_card elsewhere.",
    "premium": "Use ai_video for every scene except stat/CTA scenes, which may be text_card.",
}


# ── plan ─────────────────────────────────────────────────────────────


def _brief_text(brief: dict, kit: dict) -> str:
    lines = [
        f"Goal / requirement: {brief.get('prompt', '').strip()}",
        f"Audience: {brief.get('audience') or 'B2B decision makers'}",
        f"Platform: {brief.get('platform') or 'LinkedIn'}",
        f"Target length: {brief.get('length_s', 30)} seconds",
        f"Tone: {brief.get('tone') or kit.get('tone', '')}",
        f"Production budget: {brief.get('budget', 'balanced')} — {BUDGET_RULES.get(brief.get('budget', 'balanced'))}",
        f"Brand: {kit.get('name')}; colours primary {kit['colors']['primary']}, accent {kit['colors']['accent']}; "
        f"tagline '{kit.get('tagline', '')}'; default CTA '{kit.get('cta', '')}'.",
    ]
    if brief.get("presenter_asset_id"):
        lines.append("A presenter portrait is available: avatar scenes are allowed.")
    else:
        lines.append("No presenter available: never use avatar.")
    if brief.get("facts"):
        lines.append(f"Facts you may use: {brief['facts']}")
    return "\n".join(lines)


def _clean_plan(plan: dict, brief: dict) -> dict:
    plan = copy.deepcopy(plan)
    budget = brief.get("budget", "balanced")
    for i, s in enumerate(plan["scenes"]):
        s["id"] = s.get("id") or f"s{i + 1}"
        s.setdefault("camera", "slow push-in")
        if s["visual_type"] == "avatar" and not brief.get("presenter_asset_id"):
            s["visual_type"] = "ai_image"
        if s["visual_type"] == "upload" and not s.get("asset_id"):
            s["visual_type"] = "ai_image"
        if budget == "lean" and s["visual_type"] == "ai_video":
            s["visual_type"] = "ai_image"
        words = len(s["voiceover"].split())
        s["duration_s"] = round(max(float(s["duration_s"]), words / WORDS_PER_SECOND + 0.4, 1.5), 2)
    if budget == "balanced":
        videos = [s for s in plan["scenes"] if s["visual_type"] == "ai_video"]
        for s in videos[3:]:
            s["visual_type"] = "ai_image"
    _renumber(plan)
    return plan


def _renumber(plan: dict) -> None:
    used = set()
    for i, s in enumerate(plan["scenes"]):
        if not s.get("id") or s["id"] in used:
            s["id"] = f"s{i + 1}x{len(used)}"
        used.add(s["id"])


async def make_plan(brief: dict, kit: dict, project_id: str | None = None) -> dict:
    plan = await llm.structured("planner", SYSTEM_PLAN, _brief_text(brief, kit), PLAN_SCHEMA,
                                name="video_plan", temperature=0.6, project_id=project_id)
    return _clean_plan(plan, brief)


# ── state helpers ────────────────────────────────────────────────────


def _load(pid: str) -> dict:
    p = projects.get(pid)
    if not p or p["kind"] != "director":
        raise KeyError(pid)
    return p


def _store(pid: str, data: dict) -> dict:
    return projects.save(pid, data)


def _scene_state(data: dict, sid: str) -> dict:
    return data.setdefault("scene_state", {}).setdefault(sid, {})


def _kit(data: dict) -> dict:
    return brands.get(data.get("brand_kit_id"))


def _style_suffix(kit: dict) -> str:
    return (f"Professional commercial photography, colour palette leaning on {kit['colors']['primary']} and "
            f"{kit['colors']['accent']} accents, clean modern B2B aesthetic, no text, no logos")


# ── stages ───────────────────────────────────────────────────────────


async def stage_plan(pid: str, h: jobs.JobHandle | None = None) -> dict:
    p = _load(pid)
    data = p["data"]
    if h:
        h.stage("writing script and scene plan", 0.1)
    data["plan"] = await make_plan(data["brief"], _kit(data), project_id=pid)
    data["scene_state"] = {}
    data["stage"] = "planned"
    title = (data["plan"] or {}).get("title")
    if data.pop("auto_name", False) and title:
        projects.rename(pid, title[:80])
    _store(pid, data)
    return {"plan": data["plan"]}


async def stage_storyboard(pid: str, h: jobs.JobHandle | None = None, scene_ids: list[str] | None = None,
                           force: bool = False) -> dict:
    data = _load(pid)["data"]
    kit = _kit(data)
    aspect = data.get("aspects", ["16:9"])[0]
    scenes = [s for s in data["plan"]["scenes"] if s["visual_type"] in ("ai_video", "ai_image")
              and (scene_ids is None or s["id"] in scene_ids)]
    sem = asyncio.Semaphore(3)
    done = 0

    async def one(s):
        nonlocal done
        st = _scene_state(data, s["id"])
        if st.get("still_id") and not force and not st.get("dirty_visual"):
            return
        async with sem:
            asset = await producers.still(s["visual"], aspect=aspect, project_id=pid, style=_style_suffix(kit))
        st["still_id"] = asset["id"]
        st.pop("clip_id", None)
        st["dirty_visual"] = False
        done += 1
        if h:
            h.progress(done / max(1, len(scenes)))

    if h:
        h.stage("generating storyboard stills", 0.05)
    await asyncio.gather(*(one(s) for s in scenes))
    data["stage"] = "storyboard"
    _store(pid, data)
    return {"scenes": len(scenes)}


async def stage_produce(pid: str, h: jobs.JobHandle | None = None, quality: str | None = None) -> dict:
    data = _load(pid)["data"]
    kit = _kit(data)
    plan = data["plan"]
    quality = quality or data.get("quality", "draft")
    data["quality"] = quality
    aspect = data.get("aspects", ["16:9"])[0]
    voice = (kit.get("voice") or {}).get("voice")
    voice_model = (kit.get("voice") or {}).get("model")
    if data["brief"].get("voice"):
        voice = data["brief"]["voice"]
    total = len(plan["scenes"])

    # voiceovers + word timings
    if h:
        h.stage("recording voiceover", 0.05)
    for i, s in enumerate(plan["scenes"]):
        st = _scene_state(data, s["id"])
        if s["voiceover"].strip() and (not st.get("vo_id") or st.get("dirty_vo") or st.get("vo_text") != s["voiceover"]):
            asset, dur = await producers.voiceover(s["voiceover"], voice=voice, model=voice_model, project_id=pid)
            st.update(vo_id=asset["id"], vo_duration=dur, vo_text=s["voiceover"], dirty_vo=False)
            st["words"] = await producers.word_timings(asset["id"], s["voiceover"], project_id=pid)
        if h:
            h.progress(0.05 + 0.25 * (i + 1) / total)
    _store(pid, data)

    # visuals
    if h:
        h.stage("generating clips", 0.3)
    sem = asyncio.Semaphore(3)

    async def visual(s):
        st = _scene_state(data, s["id"])
        need = max(s["duration_s"], (st.get("vo_duration") or 0) + 0.4)
        kind = s["visual_type"]
        if kind == "ai_video":
            if st.get("clip_id") and st.get("clip_quality") == quality and not st.get("dirty_visual"):
                return
            if not st.get("still_id") or st.get("dirty_visual"):
                async with sem:
                    st["still_id"] = (await producers.still(s["visual"], aspect=aspect, project_id=pid,
                                                            style=_style_suffix(kit)))["id"]
            prompt = f"{s['visual']}. Camera: {s.get('camera') or 'slow push-in'}. Smooth, realistic motion."
            async with sem:
                st["clip_id"] = (await producers.clip(prompt, seconds=need, aspect=aspect, quality=quality,
                                                      still_id=st["still_id"], project_id=pid))["id"]
            st["clip_quality"] = quality
        elif kind == "ai_image":
            if not st.get("still_id") or st.get("dirty_visual"):
                async with sem:
                    st["still_id"] = (await producers.still(s["visual"], aspect=aspect, project_id=pid,
                                                            style=_style_suffix(kit)))["id"]
        elif kind == "avatar":
            portrait = data["brief"].get("presenter_asset_id")
            if portrait and st.get("vo_id") and (not st.get("clip_id") or st.get("avatar_vo") != st["vo_id"]):
                async with sem:
                    st["clip_id"] = (await producers.avatar(portrait, st["vo_id"], aspect=aspect, project_id=pid))["id"]
                st["avatar_vo"] = st["vo_id"]
        st["dirty_visual"] = False

    results = await asyncio.gather(*(visual(s) for s in plan["scenes"]), return_exceptions=True)
    failures = [f"{s['id']}: {r}" for s, r in zip(plan["scenes"], results) if isinstance(r, Exception)]
    for s, r in zip(plan["scenes"], results):
        if isinstance(r, Exception):  # degrade gracefully: a still with motion instead of a failed clip
            log.warning("scene %s visual failed: %s", s["id"], r)
            _scene_state(data, s["id"])["error"] = str(r)
    _store(pid, data)

    # music
    mood = plan.get("music_mood") or kit.get("music_mood", "none")
    if data["brief"].get("music_asset_id"):
        data["music_id"] = data["brief"]["music_asset_id"]
    elif mood != "none" and data.get("music_mood_used") != mood:
        if h:
            h.stage("composing music", 0.85)
        try:
            m = await producers.music(mood, project_id=pid)
            data["music_id"] = m["id"] if m else None
            data["music_mood_used"] = mood
        except ProviderError as e:
            log.warning("music generation failed: %s", e)
            data["music_id"] = None
    elif mood == "none":
        data["music_id"] = None

    if h:
        h.stage("assembling timelines", 0.92)
    data["timelines"] = {a: assemble(data, a) for a in data.get("aspects", ["16:9"])}
    data["stage"] = "produced"
    data["warnings"] = failures
    _store(pid, data)
    return {"warnings": failures}


async def stage_render(pid: str, h: jobs.JobHandle | None = None, preset: str = "h264",
                       aspects: list[str] | None = None) -> dict:
    from .render import export
    import tempfile
    from pathlib import Path

    p = _load(pid)
    data = p["data"]
    out: dict = {}
    aspects = aspects or data.get("aspects", ["16:9"])
    for i, a in enumerate(aspects):
        tl = Timeline(**(data.get("timelines") or {}).get(a) or assemble(data, a))
        if h:
            h.stage(f"rendering {a}", i / len(aspects))
        spec = export.PRESETS[preset]
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / f"out{spec['ext']}")
            await asyncio.to_thread(
                export.render, tl, path, preset=preset,
                progress=lambda q, _s, i=i: h.progress((i + q) / len(aspects)) if h else None)
            asset = library.save_asset_file(path, spec["mime"], prompt=data["plan"]["title"], tool="director",
                                            params={"project_id": pid, "aspect": a, "preset": preset})
        out[a] = asset["id"]
    data.setdefault("renders", {}).update(out)
    data["stage"] = "rendered"
    _store(pid, data)
    return {"renders": out}


# ── assembly ─────────────────────────────────────────────────────────


def _f(sec: float) -> int:
    return int(round(sec * FPS))


def _layout(aspect: str) -> dict:
    vertical = aspect in ("9:16", "4:5")
    return {
        "text_box": [0.08, 0.30, 0.84, 0.22] if vertical else [0.08, 0.34, 0.84, 0.24],
        "text_box_low": [0.08, 0.16, 0.84, 0.18] if vertical else [0.06, 0.66, 0.6, 0.16],
        "captions_box": [0.06, 0.66, 0.88, 0.14] if vertical else [0.1, 0.80, 0.8, 0.13],
        "logo_box": [0.78, 0.04, 0.18, 0.06] if vertical else [0.86, 0.05, 0.11, 0.08],
        "cta_box": [0.2, 0.535, 0.6, 0.075] if vertical else [0.3, 0.64, 0.4, 0.12],
        "title_size": 92 if vertical else 96,
        "end_logo_box": [0.3, 0.12, 0.4, 0.1] if vertical else [0.4, 0.1, 0.2, 0.18],
    }


def _logo_for(kit: dict, *, on_card: bool) -> str | None:
    """Logo asset for a scene: the light (white) logo on brand-colour cards, the primary logo
    elsewhere. Never put a brand-colour logo on a brand-colour background."""
    logos = kit.get("logos") or {}
    primary, light = logos.get("primary"), logos.get("light")
    if on_card:
        pick = light if light and light != primary else None
    else:
        pick = primary
    return pick if pick and library.get_asset(pick) else None


def assemble(data: dict, aspect: str) -> dict:
    kit = _kit(data)
    plan = data["plan"]
    w, h = canvas_for(aspect)
    L = _layout(aspect)
    tracks = {k: [] for k in ("v1", "v2", "v3", "v4", "v5", "a1", "a2")}
    t = 0
    captions_words = []
    for i, s in enumerate(plan["scenes"]):
        st = data.get("scene_state", {}).get(s["id"], {})
        vo = st.get("vo_duration") or 0
        dur = _f(max(s["duration_s"], vo + 0.45))
        trans = s.get("transition", "cut")
        tr = {"type": trans, "duration": 12} if i > 0 and trans != "cut" else None
        base = {"start": t, "duration": dur, "source": {"director": data.get("id"), "scene": s["id"]}}

        # V1 — picture
        if s["visual_type"] in ("ai_video", "avatar") and st.get("clip_id") and library.get_asset(st["clip_id"]):
            clip_len = media.probe(str(library.asset_path(st["clip_id"])))["duration"] * FPS
            speed = 1.0
            if clip_len < dur and clip_len / dur >= 0.75:
                speed = round(clip_len / dur, 3)  # gently slow down rather than freeze
            tracks["v1"].append({**base, "id": f"{s['id']}-pic", "type": "video", "asset_id": st["clip_id"],
                                 "speed": speed, "transition_in": tr, "name": f"Scene {i + 1}"})
        elif s["visual_type"] == "upload" and s.get("asset_id"):
            a = library.get_asset(s["asset_id"]) or {}
            tracks["v1"].append({**base, "id": f"{s['id']}-pic", "type": "video" if a.get("kind") == "video" else "image",
                                 "asset_id": s["asset_id"], "transition_in": tr, "name": f"Scene {i + 1}"})
        elif s["visual_type"] in ("ai_image", "ai_video", "avatar") and st.get("still_id"):
            zoom_in = i % 2 == 0
            tracks["v1"].append({**base, "id": f"{s['id']}-pic", "type": "image", "asset_id": st["still_id"],
                                 "transition_in": tr, "name": f"Scene {i + 1}",
                                 "keyframes": {"scale": [{"f": 0, "v": 1.0 if zoom_in else 1.14, "ease": "linear"},
                                                         {"f": dur, "v": 1.14 if zoom_in else 1.0}],
                                               "x": [{"f": 0, "v": -0.015 if zoom_in else 0.015, "ease": "linear"},
                                                     {"f": dur, "v": 0.015 if zoom_in else -0.015}]}})
        else:  # text card
            tracks["v1"].append({**base, "id": f"{s['id']}-pic", "type": "shape", "transition_in": tr,
                                 "name": f"Scene {i + 1} card",
                                 "shape": {"kind": "rect", "gradient": kit["gradients"]["hero"]}})

        # V2 — legibility scrim, V3 — on-screen text
        text = s.get("on_screen_text", "").strip()
        if text:
            is_card = s["visual_type"] == "text_card" or not st.get("still_id") and not st.get("clip_id")
            if not is_card:
                tracks["v2"].append({**base, "id": f"{s['id']}-scrim", "type": "shape", "name": "Scrim",
                                     "shape": {"kind": "rect", "gradient": ["#00000000", "#000000B0"]},
                                     "transform": {"opacity": 0.85}, "anim_in": {"type": "fade", "duration": 8}})
            big = s["purpose"] in ("hook", "stat", "cta", "intro") or is_card
            style = dict(kit["styles"]["title"] if big else kit["styles"]["subtitle"])
            style["size"] = L["title_size"] if big else max(56, style.get("size", 48))
            if s["purpose"] == "stat":
                style = dict(kit["styles"]["stat"])
                if is_card:
                    style["color"] = kit["colors"]["on_primary"]
            tracks["v3"].append({**base, "id": f"{s['id']}-text", "type": "text", "text": text, "style": style,
                                 "box": L["text_box"] if big else L["text_box_low"], "name": "On-screen text",
                                 "anim_in": {"type": "pop" if s["purpose"] in ("stat", "hook") else "slide-up",
                                             "duration": 12},
                                 "anim_out": {"type": "fade", "duration": 8}})
        is_card_scene = s["visual_type"] == "text_card" or not (st.get("still_id") or st.get("clip_id"))
        if s["purpose"] == "cta":
            cta = kit.get("cta") or plan.get("cta") or "Learn more"
            end_logo = _logo_for(kit, on_card=is_card_scene)
            if end_logo:
                tracks["v5"].append({**base, "id": f"{s['id']}-endlogo", "type": "image", "asset_id": end_logo,
                                     "box": L["end_logo_box"], "fit": "contain", "name": "End card logo",
                                     "anim_in": {"type": "fade", "duration": 10}})
            tracks["v3"].append({**base, "id": f"{s['id']}-cta", "type": "text", "text": cta,
                                 "style": kit["styles"]["button"], "box": L["cta_box"], "name": "CTA",
                                 "start": t + min(12, dur // 3), "duration": dur - min(12, dur // 3),
                                 "anim_in": {"type": "pop", "duration": 12}})

        # A1 — voiceover, captions words
        if st.get("vo_id"):
            lead = 6
            tracks["a1"].append({"id": f"{s['id']}-vo", "type": "audio", "asset_id": st["vo_id"], "role": "vo",
                                 "start": t + lead, "duration": max(1, _f(vo)), "name": f"VO {i + 1}"})
            for wd in st.get("words") or []:
                captions_words.append({"w": wd["word"], "s": t + lead + _f(wd["start"]),
                                       "e": t + lead + max(_f(wd["end"]), _f(wd["start"]) + 2)})
        if s["purpose"] != "cta":
            mark = _logo_for(kit, on_card=is_card_scene)
            if mark:
                tracks["v5"].append({"id": f"{s['id']}-logo", "type": "image", "asset_id": mark, "start": t,
                                     "duration": dur, "box": L["logo_box"], "fit": "contain",
                                     "transform": {"opacity": 0.9}, "name": "Logo"})
        t += dur

    total = t
    if captions_words and data["brief"].get("captions", True):
        tracks["v4"].append({"id": "captions", "type": "captions", "start": 0, "duration": total,
                             "box": L["captions_box"], "style": kit["styles"]["captions"], "name": "Captions",
                             "captions": {"words": captions_words, "mode": "karaoke", "max_chars": 28}})
    if data.get("music_id") and library.get_asset(data["music_id"]):
        mlen = max(1, _f(media.probe(str(library.asset_path(data["music_id"])))["duration"]))
        pos, n = 0, 0
        while pos < total:
            seg = min(mlen, total - pos)
            tracks["a2"].append({"id": f"music{n}", "type": "audio", "asset_id": data["music_id"], "role": "music",
                                 "start": pos, "duration": seg, "volume": 0.35, "duck_db": 9,
                                 "fade_in": 15 if n == 0 else 0, "fade_out": 45 if pos + seg >= total else 0,
                                 "name": "Music"})
            pos += seg
            n += 1
    names = {"v1": "Picture", "v2": "Scrim", "v3": "Text", "v4": "Captions", "v5": "Logo", "a1": "Voiceover",
             "a2": "Music"}
    tl = Timeline(fps=FPS, width=w, height=h, background=kit["colors"].get("dark", "#000000"),
                  tracks=[{"id": k, "kind": "audio" if k.startswith("a") else "video", "name": names[k], "clips": v}
                          for k, v in tracks.items()],
                  meta={"source": "director", "aspect": aspect, "title": plan.get("title")})
    return tl.model_dump(by_alias=True)


# ── chat edits ───────────────────────────────────────────────────────


def _resolve(doc, parts):
    for p in parts:
        doc = doc[int(p)] if isinstance(doc, list) else doc[p]
    return doc


def apply_patch(plan: dict, ops: list[dict]) -> tuple[dict, set[str], set[str], bool]:
    """Apply JSON-patch ops; returns (plan, visual_dirty_ids, vo_dirty_ids, structure_changed)."""
    plan = copy.deepcopy(plan)
    vis, vo = set(), set()
    structure = False
    allowed_top = {"title", "logline", "music_mood", "cta", "scenes"}
    for op in ops:
        path = op.get("path", "")
        parts = [p for p in path.strip("/").split("/") if p]
        if not parts or parts[0] not in allowed_top:
            raise ValueError(f"path not allowed: {path}")
        parent = _resolve(plan, parts[:-1])
        key = parts[-1]
        kind = op["op"]
        if isinstance(parent, list):
            if kind == "add":
                value = copy.deepcopy(op.get("value"))
                if isinstance(value, dict):
                    value.setdefault("id", f"n{len(plan['scenes']) + 1}{len(vis)}")
                    value.setdefault("camera", "slow push-in")
                    vis.add(value["id"])
                    vo.add(value["id"])
                idx = len(parent) if key == "-" else int(key)
                parent.insert(idx, value)
                structure = True
            elif kind == "remove":
                parent.pop(int(key))
                structure = True
            else:
                parent[int(key)] = op.get("value")
                structure = True
        else:
            if kind == "remove":
                parent.pop(key, None)
            else:
                parent[key] = op.get("value")
            if len(parts) >= 3 and parts[0] == "scenes":
                sid = plan["scenes"][int(parts[1])].get("id")
                if key in ("visual", "visual_type", "camera"):
                    vis.add(sid)
                if key == "voiceover":
                    vo.add(sid)
    _renumber(plan)
    errors = llm.validation_errors(plan, PLAN_SCHEMA)
    if errors:
        raise ValueError("; ".join(errors[:4]))
    return plan, vis, vo, structure


async def chat_edit(pid: str, message: str) -> dict:
    data = _load(pid)["data"]
    user = (f"Current plan JSON:\n{json.dumps(data['plan'])}\n\nInstruction: {message}\n"
            f"Production budget: {data['brief'].get('budget', 'balanced')}")
    patch = await llm.structured("planner", SYSTEM_EDIT, user, PATCH_SCHEMA, name="plan_patch", project_id=pid)
    plan, vis, vo, _ = apply_patch(data["plan"], patch["ops"])
    plan = _clean_plan(plan, data["brief"])
    for sid in vis:
        _scene_state(data, sid)["dirty_visual"] = True
    for sid in vo:
        _scene_state(data, sid)["dirty_vo"] = True
    data["plan"] = plan
    data.setdefault("history", []).append({"role": "user", "content": message})
    data["history"].append({"role": "assistant", "content": patch["summary"], "ops": patch["ops"]})
    _store(pid, data)
    return {"summary": patch["summary"], "ops": patch["ops"], "dirty_visual": sorted(vis),
            "dirty_vo": sorted(vo)}


def estimate(data: dict) -> dict:
    """Rough cost of producing the current plan (before paying for anything)."""
    from . import roles

    plan = data.get("plan") or {}
    quality = data.get("quality", "draft")
    role = roles.role("video_final" if quality == "final" else "video_draft")
    res = (role.get("params") or {}).get("resolution")
    usd = 0.0
    for s in plan.get("scenes", []):
        usd += costs.estimate_speech(len(s.get("voiceover", "")))
        if s["visual_type"] in ("ai_video", "ai_image"):
            usd += costs.IMAGE_PRICE_GUESS
        if s["visual_type"] == "ai_video":
            usd += costs.estimate_video(role["model"], s["duration_s"], res)
        if s["visual_type"] == "avatar":
            usd += costs.estimate_video(roles.model_for("avatar"), s["duration_s"], "720p")
        usd += costs.estimate_transcribe(s["duration_s"])
    if plan.get("music_mood", "none") != "none":
        usd += costs.MUSIC_PER_CLIP
    return {"usd": round(usd, 3), "quality": quality}


async def autopilot(pid: str, h: jobs.JobHandle, *, render: bool = True) -> dict:
    p = _load(pid)
    if not p["data"].get("plan"):
        await stage_plan(pid, h)
    await stage_storyboard(pid, h)
    await stage_produce(pid, h)
    if render:
        return await stage_render(pid, h)
    return {"stage": "produced"}
