"""Request dependencies shared by every router: access control and the acting user.

Local mode (no token, no SSO): everyone is the implicit admin "local".
Shared mode: `CHAMP_STUDIO_ACCESS_TOKEN` gates the API (one team token), and
when OIDC is configured (`auth.py`) a session cookie or bearer JWT identifies
the user, whose role (viewer < reviewer < editor < admin) is checked per route.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from . import config

ROLE_RANK = {"viewer": 0, "reviewer": 1, "editor": 2, "admin": 3}


async def require_access(request: Request) -> None:
    from . import auth  # late import: auth depends on db/config only

    user = await auth.user_from_request(request)
    if user is not None:
        request.state.user = user
        return
    token = config.access_token()
    if not token:
        request.state.user = auth.LOCAL_USER
        return
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if supplied != token:
        raise HTTPException(401, "Missing or invalid access token")
    request.state.user = auth.TOKEN_USER


guarded = Depends(require_access)


def actor(request: Request) -> dict:
    return getattr(request.state, "user", None) or {"id": "local", "email": "local", "role": "admin"}


def require_role(minimum: str):
    async def check(request: Request) -> None:
        await require_access(request)
        user = actor(request)
        if ROLE_RANK.get(user.get("role", "viewer"), 0) < ROLE_RANK[minimum]:
            raise HTTPException(403, f"needs {minimum} role")

    return Depends(check)
