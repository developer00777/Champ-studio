"""Cost estimates, the cost ledger, and budget caps.

Estimates come from a static table of the default models (OpenRouter catalog,
2026-10-30), refreshed from the live video catalog when a key is set. Every
paid call is recorded in the ledger with its actual cost when the provider
reports one. Budgets live in Settings → defaults.budget:
{"monthly_usd": 50, "per_project_usd": 15}; 0 or missing means no cap.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from . import config, db

# USD per million tokens (prompt, completion)
LLM_PRICES: dict[str, tuple[float, float]] = {
    "anthropic/claude-haiku-5.5": (0.10, 0.50),
    "openai/gpt-6-luna": (0.10, 0.50),
    "deepseek/deepseek-v4.1-flash": (0.045, 0.50),
    "inception/mercury-2.5": (0.04, 0.15),
    "xiaomi/mimo-v2.6-flash": (0.14, 0.28),
    "qwen/qwen3.8-omni-flash": (0.15, 0.47),
}

# USD per output second, keyed by resolution; "audio" adds when generate_audio is on
VIDEO_PRICES: dict[str, dict[str, float]] = {
    "google/veo-3.1-lite": {"720p": 0.03, "1080p": 0.05, "audio": 0.02},
    "google/veo-3.1-fast": {"720p": 0.08, "1080p": 0.10, "4K": 0.25, "audio": 0.02},
    "google/veo-3.1": {"720p": 0.20, "1080p": 0.20, "4K": 0.40, "audio": 0.20},
    "x-ai/grok-imagine-video-1.5-lite": {"480p": 0.02, "720p": 0.03, "1080p": 0.14},
    "kwaivgi/kling-v3.0-std": {"720p": 0.084, "audio": 0.042},
    "alibaba/wan-3.0": {"480p": 0.05, "720p": 0.10, "1080p": 0.20},
    "heygen/avatar-iv": {"720p": 0.05, "1080p": 0.05},
}

IMAGE_PRICE_GUESS = 0.03      # per image; image prices aren't in the public catalog
SPEECH_PER_1K_CHARS = 0.015   # conservative; confirmed live on the bench test
TRANSCRIBE_PER_MIN = 0.006
MUSIC_PER_CLIP = 0.04         # lyria-3-clip-preview, 30 s


class BudgetExceeded(RuntimeError):
    """Raised before a paid call that would cross a budget cap."""


def estimate_llm(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pin, pout = LLM_PRICES.get(model, (0.20, 1.00))
    return (prompt_tokens * pin + completion_tokens * pout) / 1_000_000


def estimate_video(model: str, seconds: float, resolution: str | None = "720p",
                   audio: bool = False) -> float:
    table = VIDEO_PRICES.get(model)
    if not table:
        return round(seconds * 0.15, 4)  # unknown model: assume a mid-tier price
    res = resolution or "720p"
    per_s = table.get(res) or table.get("720p") or max(v for k, v in table.items() if k != "audio")
    if audio:
        per_s += table.get("audio", 0.0)
    return round(per_s * seconds, 4)


def estimate_speech(chars: int) -> float:
    return round(chars / 1000 * SPEECH_PER_1K_CHARS, 4)


def estimate_transcribe(seconds: float) -> float:
    return round(seconds / 60 * TRANSCRIBE_PER_MIN, 4)


def update_video_prices_from_catalog(models: list[dict]) -> None:
    """Fold live `pricing_skus` from /videos/models into the table (best effort)."""
    for m in models:
        skus = (m.get("pricing_skus") or (m.get("meta") or {}).get("pricing_skus")) or {}
        if not skus:
            continue
        table: dict[str, float] = {}
        for key, val in skus.items():
            try:
                v = float(val)
            except (TypeError, ValueError):
                continue
            if key.startswith("cents_per_video_output_second_"):
                table[key.rsplit("_", 1)[-1]] = v / 100
            elif key.startswith("duration_seconds_") and "with_audio" not in key and "reference" not in key:
                table[key.rsplit("_", 1)[-1]] = v
            elif key in ("duration_seconds", "duration_seconds_without_audio"):
                table.setdefault("720p", v)
        if table:
            VIDEO_PRICES[m["id"]] = {**VIDEO_PRICES.get(m["id"], {}), **table}


# ── ledger ───────────────────────────────────────────────────────────


def record(*, kind: str, model: str, cost: float, role: str = "", estimated: bool = False,
           project_id: str | None = None, job_id: str | None = None, meta: dict | None = None) -> None:
    with db.conn() as c:
        c.execute(
            """INSERT INTO ledger (id, ts, role, model, kind, cost, estimated, project_id, job_id, meta)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (db.new_id(), time.time(), role, model, kind, float(cost or 0), int(estimated),
             project_id, job_id, db.dumps(meta or {})),
        )


def _month_start() -> float:
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc).timestamp()


def spent(*, project_id: str | None = None, since: float | None = None) -> float:
    q, args = "SELECT COALESCE(SUM(cost), 0) AS s FROM ledger WHERE 1=1", []
    if project_id:
        q += " AND project_id = ?"
        args.append(project_id)
    if since is not None:
        q += " AND ts >= ?"
        args.append(since)
    with db.conn() as c:
        return float(c.execute(q, args).fetchone()["s"])


def budget() -> dict:
    b = (config.get_settings().get("defaults") or {}).get("budget") or {}
    return {
        "monthly_usd": float(b.get("monthly_usd") or 0),
        "per_project_usd": float(b.get("per_project_usd") or 0),
    }


def check_budget(estimate: float, *, project_id: str | None = None) -> None:
    b = budget()
    if b["monthly_usd"]:
        month = spent(since=_month_start())
        if month + estimate > b["monthly_usd"]:
            raise BudgetExceeded(
                f"Monthly budget ${b['monthly_usd']:.2f} would be exceeded "
                f"(spent ${month:.2f}, this needs about ${estimate:.2f})"
            )
    if project_id and b["per_project_usd"]:
        proj = spent(project_id=project_id)
        if proj + estimate > b["per_project_usd"]:
            raise BudgetExceeded(
                f"Project budget ${b['per_project_usd']:.2f} would be exceeded "
                f"(spent ${proj:.2f}, this needs about ${estimate:.2f})"
            )


def summary(days: int = 30) -> dict:
    since = time.time() - days * 86400
    with db.conn() as c:
        by_kind = c.execute(
            "SELECT kind, COUNT(*) AS n, SUM(cost) AS cost FROM ledger WHERE ts >= ? GROUP BY kind",
            (since,),
        ).fetchall()
        by_model = c.execute(
            """SELECT model, COUNT(*) AS n, SUM(cost) AS cost FROM ledger WHERE ts >= ?
               GROUP BY model ORDER BY cost DESC LIMIT 20""",
            (since,),
        ).fetchall()
    return {
        "days": days,
        "total": round(sum((r["cost"] or 0) for r in by_kind), 4),
        "month_to_date": round(spent(since=_month_start()), 4),
        "budget": budget(),
        "by_kind": [dict(r) for r in by_kind],
        "by_model": [dict(r) for r in by_model],
    }
