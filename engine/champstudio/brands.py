"""Brand kits: colours, fonts, logos, text styles, voice and tone.

Templates and Director reference brand values as {"$brand": "colors.primary"}
paths; `resolve()` looks them up. Two kits are seeded on first use:
Champions Group (orange + white, per the 2026-04 brand guidelines) and a
neutral kit. Subsidiary kits (LakeB2B, Ampliz, SPAN, Recruit Champ…) are
added in the Brand Kits screen.
"""

from __future__ import annotations

import copy

from . import db

SEED_KITS: list[dict] = [
    {
        "id": "champions-group",
        "name": "Champions Group",
        "colors": {
            "primary": "#F26722", "primary_dark": "#D94F0A", "accent": "#FF8544",
            "background": "#FFFFFF", "surface": "#FFF4EC", "text": "#1A1A1A",
            "text_muted": "#6B6B6B", "on_primary": "#FFFFFF", "dark": "#1A1A1A",
        },
        "gradients": {"hero": ["#F26722", "#D94F0A"], "sunset": ["#FF8544", "#F26722"]},
        "fonts": {"heading": {"family": "Inter", "weight": 800}, "body": {"family": "Inter", "weight": 500}},
        "logos": {"primary": None, "light": None, "dark": None},
        "styles": {
            "title": {"font": "Inter", "weight": 800, "size": 96, "color": "#FFFFFF", "shadow": True},
            "subtitle": {"font": "Inter", "weight": 600, "size": 48, "color": "#FFFFFF"},
            "lower_third": {"font": "Inter", "weight": 700, "size": 40, "color": "#FFFFFF",
                            "bg_color": "#F26722", "align": "left"},
            "button": {"font": "Inter", "weight": 700, "size": 44, "color": "#FFFFFF", "bg_color": "#F26722",
                       "bg_padding": 0.6, "bg_radius": 0.4},
            "captions": {"font": "Inter", "weight": 800, "size": 58, "color": "#FFFFFF",
                         "stroke_color": "#1A1A1A", "stroke_width": 5, "highlight_color": "#FF8544",
                         "highlight_bg": "#1A1A1AE6", "uppercase": False},
            "stat": {"font": "Inter", "weight": 800, "size": 200, "color": "#F26722"},
        },
        "voice": {"model": None, "voice": "Kore"},
        "music_mood": "corporate-upbeat",
        "tone": "Authoritative, warm, visionary, empowering, precise. Lead with outcomes and concrete numbers. "
                "A multi-venture innovation group — never 'holding company' or 'conglomerate'.",
        "tagline": "Empowering Innovation. Building Resilience.",
        "website": "",
        "cta": "Talk to our team",
        "watermark": False,
    },
    {
        "id": "neutral",
        "name": "Neutral",
        "colors": {
            "primary": "#2563EB", "primary_dark": "#1D4ED8", "accent": "#22D3EE",
            "background": "#0B0D12", "surface": "#151922", "text": "#FFFFFF",
            "text_muted": "#9CA3AF", "on_primary": "#FFFFFF", "dark": "#0B0D12",
        },
        "gradients": {"hero": ["#2563EB", "#7C3AED"], "sunset": ["#22D3EE", "#2563EB"]},
        "fonts": {"heading": {"family": "Montserrat", "weight": 800}, "body": {"family": "Inter", "weight": 500}},
        "logos": {"primary": None, "light": None, "dark": None},
        "styles": {
            "title": {"font": "Montserrat", "weight": 800, "size": 96, "color": "#FFFFFF", "shadow": True},
            "subtitle": {"font": "Inter", "weight": 600, "size": 48, "color": "#E5E7EB"},
            "lower_third": {"font": "Inter", "weight": 700, "size": 40, "color": "#FFFFFF",
                            "bg_color": "#2563EB", "align": "left"},
            "button": {"font": "Inter", "weight": 700, "size": 44, "color": "#FFFFFF", "bg_color": "#2563EB",
                       "bg_padding": 0.6, "bg_radius": 0.4},
            "captions": {"font": "Montserrat", "weight": 800, "size": 58, "color": "#FFFFFF",
                         "stroke_color": "#000000", "stroke_width": 5, "highlight_color": "#22D3EE"},
            "stat": {"font": "Montserrat", "weight": 800, "size": 200, "color": "#22D3EE"},
        },
        "voice": {"model": None, "voice": "Kore"},
        "music_mood": "calm-tech",
        "tone": "Clear, confident and friendly. Short sentences. Benefits before features.",
        "tagline": "",
        "website": "",
        "cta": "Learn more",
        "watermark": False,
    },
]


def _ensure_seeded() -> None:
    with db.conn() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM brand_kits").fetchone()["n"]
        if n:
            return
        for kit in SEED_KITS:
            c.execute("INSERT INTO brand_kits (id, name, data, created_at, updated_at) VALUES (?,?,?,?,?)",
                      (kit["id"], kit["name"], db.dumps(kit), db.now(), db.now()))


def list_kits() -> list[dict]:
    _ensure_seeded()
    with db.conn() as c:
        rows = c.execute("SELECT data FROM brand_kits ORDER BY name").fetchall()
    return [db.loads(r["data"], {}) for r in rows]


def get(kit_id: str | None) -> dict:
    _ensure_seeded()
    with db.conn() as c:
        r = c.execute("SELECT data FROM brand_kits WHERE id = ?", (kit_id or "champions-group",)).fetchone()
        if not r:
            r = c.execute("SELECT data FROM brand_kits ORDER BY created_at LIMIT 1").fetchone()
    return db.loads(r["data"], {}) if r else copy.deepcopy(SEED_KITS[0])


def light_logo(asset_id: str) -> str | None:
    """White silhouette of a logo with transparency — the on-brand-colour variant
    (brand rule: never a brand-colour logo on a brand-colour background)."""
    from PIL import Image

    from . import library

    path = library.asset_path(asset_id)
    if not path:
        return None
    try:
        im = Image.open(path).convert("RGBA")
    except Exception:  # noqa: BLE001 — SVG or unreadable: skip
        return None
    alpha = im.getchannel("A")
    if alpha.getextrema()[0] == 255:  # no transparency: a silhouette would be a white box
        return None
    white = Image.new("RGBA", im.size, (255, 255, 255, 0))
    white.putalpha(alpha)
    import io

    buf = io.BytesIO()
    white.save(buf, "PNG")
    return library.save_asset(buf.getvalue(), "image/png", tool="logo-light", parent_id=asset_id)["id"]


def save(kit: dict) -> dict:
    _ensure_seeded()
    base = copy.deepcopy(SEED_KITS[1])
    kit = _merge(base, kit)
    kit["id"] = kit.get("id") or db.new_id()
    logos = kit.setdefault("logos", {})
    if logos.get("primary") and (not logos.get("light") or logos.get("light") == logos["primary"]):
        logos["light"] = light_logo(logos["primary"]) or logos.get("light")
    with db.conn() as c:
        exists = c.execute("SELECT 1 FROM brand_kits WHERE id = ?", (kit["id"],)).fetchone()
        if exists:
            c.execute("UPDATE brand_kits SET name = ?, data = ?, updated_at = ? WHERE id = ?",
                      (kit["name"], db.dumps(kit), db.now(), kit["id"]))
        else:
            c.execute("INSERT INTO brand_kits (id, name, data, created_at, updated_at) VALUES (?,?,?,?,?)",
                      (kit["id"], kit["name"], db.dumps(kit), db.now(), db.now()))
    return kit


def delete(kit_id: str) -> bool:
    with db.conn() as c:
        return bool(c.execute("DELETE FROM brand_kits WHERE id = ?", (kit_id,)).rowcount)


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def lookup(kit: dict, path: str, default=None):
    node = kit
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    return node


def resolve(value, kit: dict, slots: dict | None = None):
    """Replace {"$brand": "a.b"} / {"$slot": "name"} references anywhere in `value`."""
    if isinstance(value, dict):
        if set(value) == {"$brand"}:
            return copy.deepcopy(lookup(kit, value["$brand"]))
        if set(value) == {"$slot"} and slots is not None:
            return copy.deepcopy(slots.get(value["$slot"]))
        if "$brand" in value and len(value) > 1:   # brand style + local overrides
            base = lookup(kit, value["$brand"]) or {}
            rest = {k: resolve(v, kit, slots) for k, v in value.items() if k != "$brand"}
            return {**copy.deepcopy(base), **rest} if isinstance(base, dict) else base
        return {k: resolve(v, kit, slots) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, kit, slots) for v in value]
    return value
