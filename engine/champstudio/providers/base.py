"""Shared provider types."""

from __future__ import annotations

from dataclasses import dataclass, field


class ProviderError(RuntimeError):
    """Raised for any provider-side failure; message is safe to show users."""


@dataclass
class GeneratedMedia:
    kind: str                     # "image" | "video"
    data: bytes | None = None     # inline bytes (images)
    url: str | None = None        # remote URL to download (videos, MuAPI images)
    mime: str = "image/png"
    model: str = ""
    provider: str = ""
    prompt: str = ""
    cost: float | None = None
    meta: dict = field(default_factory=dict)


MIME_SUFFIX = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "application/json": ".json",
    "text/css": ".css",
}


def suffix_for(mime: str) -> str:
    return MIME_SUFFIX.get(mime, ".bin")
