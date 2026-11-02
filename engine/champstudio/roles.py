"""Model-role registry — which OpenRouter model does which job.

Defaults are the cheapest models that do each job well (catalog checked
2026-10-30). Any role can be overridden in Settings → defaults.roles, e.g.
{"roles": {"planner": {"model": "openai/gpt-6-luna"}}}. Every role has a
fallback that is tried automatically when the primary fails.
"""

from __future__ import annotations

from . import config

DEFAULT_ROLES: dict[str, dict] = {
    # text
    "planner": {
        "model": "anthropic/claude-haiku-5.5",
        "fallback": "openai/gpt-6-luna",
        "label": "Scripts, scene plans, template fill, chat edits",
    },
    "bulk": {
        "model": "deepseek/deepseek-v4.1-flash",
        "fallback": "inception/mercury-2.5",
        "label": "Cheap bulk text: tagging, filler-word classification, caption cleanup",
    },
    "vision": {
        "model": "xiaomi/mimo-v2.6-flash",
        "fallback": "qwen/qwen3.8-omni-flash",
        "label": "Understanding images and footage",
    },
    # media
    "image": {
        "model": "google/gemini-3.1-flash-lite-image",
        "fallback": "bytedance-seed/seedream-5-0-flash",
        "label": "Scene stills, first frames",
    },
    "video_draft": {
        "model": "google/veo-3.1-lite",
        "fallback": "x-ai/grok-imagine-video-1.5-lite",
        "label": "Draft clips (720p, no audio)",
        "params": {"resolution": "720p", "generate_audio": False},
    },
    "video_final": {
        "model": "google/veo-3.1-fast",
        "fallback": "kwaivgi/kling-v3.0-std",
        "label": "Final clips (1080p)",
        "params": {"resolution": "1080p"},
    },
    "video_long": {
        "model": "alibaba/wan-3.0",
        "fallback": "bytedance/seedance-2.5",
        "label": "Long clips up to 30 s",
    },
    "avatar": {
        "model": "heygen/avatar-iv",
        "fallback": None,
        "label": "Talking-head presenter (portrait + voice)",
    },
    "speech": {
        "model": "google/gemini-3.8-flash-tts",
        "fallback": None,
        "label": "Voiceover",
        "params": {"voice": "Kore"},
    },
    "transcribe": {
        "model": "openai/whisper-large-v3",
        "fallback": "elevenlabs/scribe-v2",
        "label": "Transcription with word timestamps",
    },
    "music": {
        "model": "google/lyria-3-clip-preview",
        "fallback": None,
        "label": "Music beds (30 s clips)",
    },
}


def roles() -> dict[str, dict]:
    """Defaults merged with user overrides."""
    overrides = (config.get_settings().get("defaults") or {}).get("roles") or {}
    out = {}
    for name, spec in DEFAULT_ROLES.items():
        merged = {**spec, **(overrides.get(name) or {})}
        if "params" in spec or "params" in (overrides.get(name) or {}):
            merged["params"] = {**spec.get("params", {}), **(overrides.get(name, {}).get("params") or {})}
        out[name] = merged
    return out


def role(name: str) -> dict:
    r = roles().get(name)
    if not r:
        raise KeyError(f"unknown model role '{name}'")
    return r


def model_for(name: str) -> str:
    return role(name)["model"]


def candidates(name: str) -> list[str]:
    """Primary then fallback (deduped, Nones dropped)."""
    r = role(name)
    out: list[str] = []
    for m in (r.get("model"), r.get("fallback")):
        if m and m not in out:
            out.append(m)
    return out
