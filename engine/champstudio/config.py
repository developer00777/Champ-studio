"""Runtime configuration for the Champ Studio engine.

Keys resolve in priority order: environment variable > settings.json in the
data directory. Settings written at runtime never overwrite env-provided keys.
The settings file is chmod 0600 and keys are never logged.
"""

from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path

ENGINE_VERSION = "1.1.0"
DEFAULT_PORT = 8787

_ENV_KEYS = {
    "openrouter": "OPENROUTER_API_KEY",
    "muapi": "MUAPI_API_KEY",
}

_lock = threading.Lock()


def data_dir() -> Path:
    d = Path(os.environ.get("CHAMP_DATA_DIR", str(Path.home() / ".champstudio")))
    d.mkdir(parents=True, exist_ok=True)
    (d / "assets").mkdir(exist_ok=True)
    return d


def _settings_path() -> Path:
    return data_dir() / "settings.json"


def _read_settings_file() -> dict:
    p = _settings_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def get_settings() -> dict:
    """Full settings dict (keys come back masked — raw keys never leave the engine)."""
    stored = _read_settings_file()
    out = {
        "defaults": stored.get("defaults", {}),
        "keys": {},
        "key_sources": {},
    }
    for name in _ENV_KEYS:
        raw = get_api_key(name)
        out["keys"][name] = _mask(raw)
        out["key_sources"][name] = (
            "env" if os.environ.get(_ENV_KEYS[name]) else ("settings" if raw else None)
        )
    return out


def get_api_key(provider: str) -> str | None:
    env = os.environ.get(_ENV_KEYS.get(provider, ""))
    if env:
        return env
    stored = _read_settings_file().get("keys", {}).get(provider)
    return stored or None


def update_settings(*, keys: dict | None = None, defaults: dict | None = None) -> None:
    with _lock:
        stored = _read_settings_file()
        if keys:
            stored.setdefault("keys", {})
            for name, value in keys.items():
                if name not in _ENV_KEYS:
                    continue
                if value:  # set/replace
                    stored["keys"][name] = value.strip()
                elif value == "":  # explicit clear
                    stored["keys"].pop(name, None)
        if defaults is not None:
            stored["defaults"] = {**stored.get("defaults", {}), **defaults}
        p = _settings_path()
        p.write_text(json.dumps(stored, indent=2))
        p.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _mask(key: str | None) -> str | None:
    if not key:
        return None
    if len(key) <= 8:
        return "•" * len(key)
    return f"{key[:4]}…{key[-4:]}"


def access_token() -> str | None:
    """Optional shared-deployment gate; when set, clients must send it as a Bearer token."""
    return os.environ.get("CHAMP_STUDIO_ACCESS_TOKEN") or None
