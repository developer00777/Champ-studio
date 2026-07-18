"""Background removal via rembg (U²-Net, fully local).

First call downloads the model weights (~170 MB) to the rembg cache; the
Docker image pre-fetches them at build time.
"""

from __future__ import annotations

_session = None


def _get_session():
    global _session
    if _session is None:
        from rembg import new_session

        _session = new_session("u2net")
    return _session


def run(data: bytes, params: dict) -> list[tuple[bytes, str]]:
    from rembg import remove

    out = remove(data, session=_get_session())
    return [(out, "image/png")]
