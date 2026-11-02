"""Users, roles and OIDC single sign-on (Entra ID, Google Workspace, Keycloak, Okta…).

Off by default (laptop mode = one implicit admin). Enable by setting:

    CHAMP_OIDC_ISSUER=https://login.microsoftonline.com/<tenant>/v2.0
    CHAMP_OIDC_CLIENT_ID=...            CHAMP_OIDC_CLIENT_SECRET=...
    CHAMP_PUBLIC_URL=https://studio.example.com   (redirect URI = <url>/api/auth/callback)
    CHAMP_ADMIN_EMAILS=a@x.com,b@x.com  (become admin on first login)
    CHAMP_DEFAULT_ROLE=editor           (everyone else; viewer|reviewer|editor|admin)
    CHAMP_ALLOWED_DOMAINS=championsmail.com   (optional e-mail domain allowlist)

Authorization-code flow with PKCE; identity comes from the provider's
userinfo endpoint using the access token fetched server-to-server. Sessions
are HMAC-signed cookies (secret kept 0600 in the data dir).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import stat
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import db
from .config import data_dir

LOCAL_USER = {"id": "local", "email": "local", "name": "Local", "role": "admin"}
TOKEN_USER = {"id": "team-token", "email": "team-token", "name": "Team token", "role": "editor"}
COOKIE = "champ_session"
SESSION_TTL = 12 * 3600

router = APIRouter()
_pending: dict[str, dict] = {}   # state -> {verifier, nonce, next, ts}
_discovery: dict | None = None


def oidc_enabled() -> bool:
    return bool(os.environ.get("CHAMP_OIDC_ISSUER") and os.environ.get("CHAMP_OIDC_CLIENT_ID"))


def _secret() -> bytes:
    p = data_dir() / "session.key"
    if not p.exists():
        p.write_bytes(secrets.token_bytes(32))
        p.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return p.read_bytes()


def sign_session(user_id: str, ttl: int = SESSION_TTL) -> str:
    exp = int(time.time()) + ttl
    msg = f"{user_id}.{exp}".encode()
    sig = base64.urlsafe_b64encode(hmac.new(_secret(), msg, hashlib.sha256).digest()).decode().rstrip("=")
    return f"{user_id}.{exp}.{sig}"


def verify_session(token: str) -> str | None:
    try:
        user_id, exp, sig = token.rsplit(".", 2)
    except ValueError:
        return None
    msg = f"{user_id}.{exp}".encode()
    good = base64.urlsafe_b64encode(hmac.new(_secret(), msg, hashlib.sha256).digest()).decode().rstrip("=")
    if not hmac.compare_digest(sig, good) or int(exp) < time.time():
        return None
    return user_id


def get_user(user_id: str) -> dict | None:
    with db.conn() as c:
        r = c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(r) if r else None


def list_users() -> list[dict]:
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM users ORDER BY email").fetchall()]


def set_role(user_id: str, role: str) -> dict | None:
    if role not in ("viewer", "reviewer", "editor", "admin"):
        raise ValueError("bad role")
    with db.conn() as c:
        c.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    return get_user(user_id)


def upsert_user(email: str, name: str = "") -> dict:
    email = email.lower().strip()
    with db.conn() as c:
        r = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if r:
            if name and name != r["name"]:
                c.execute("UPDATE users SET name = ? WHERE id = ?", (name, r["id"]))
            return {**dict(r), "name": name or r["name"]}
        admins = {e.strip().lower() for e in os.environ.get("CHAMP_ADMIN_EMAILS", "").split(",") if e.strip()}
        first = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0
        role = "admin" if (email in admins or (first and not admins)) else os.environ.get("CHAMP_DEFAULT_ROLE", "editor")
        uid = db.new_id()
        c.execute("INSERT INTO users (id, email, name, role, created_at) VALUES (?,?,?,?,?)",
                  (uid, email, name, role, db.now()))
        return {"id": uid, "email": email, "name": name, "role": role}


async def user_from_request(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE)
    auth = request.headers.get("Authorization", "")
    if not token and auth.startswith("Bearer cs_"):
        token = auth.removeprefix("Bearer cs_")
    if token:
        uid = verify_session(token)
        if uid:
            return get_user(uid)
    if oidc_enabled():
        from . import config

        supplied = auth.removeprefix("Bearer ").strip()
        if config.access_token() and supplied == config.access_token():
            return TOKEN_USER
        raise HTTPException(401, "Sign in required")
    return None


# ── OIDC flow ────────────────────────────────────────────────────────


async def _disco() -> dict:
    global _discovery
    if _discovery is None:
        issuer = os.environ["CHAMP_OIDC_ISSUER"].rstrip("/")
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{issuer}/.well-known/openid-configuration")
            r.raise_for_status()
            _discovery = r.json()
    return _discovery


def _redirect_uri() -> str:
    return os.environ.get("CHAMP_PUBLIC_URL", "http://localhost:8787").rstrip("/") + "/api/auth/callback"


@router.get("/api/auth/status")
async def status(request: Request):
    user = None
    try:
        user = await user_from_request(request)
    except HTTPException:
        pass
    return {"sso": oidc_enabled(), "user": user or (None if oidc_enabled() else LOCAL_USER)}


@router.get("/api/auth/login")
async def login(next: str = "/"):
    if not oidc_enabled():
        raise HTTPException(400, "SSO is not configured")
    d = await _disco()
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    now = time.time()
    for k in [k for k, v in _pending.items() if now - v["ts"] > 600]:
        _pending.pop(k, None)
    _pending[state] = {"verifier": verifier, "nonce": nonce, "next": next if next.startswith("/") else "/", "ts": now}
    q = urlencode({
        "response_type": "code", "client_id": os.environ["CHAMP_OIDC_CLIENT_ID"],
        "redirect_uri": _redirect_uri(), "scope": "openid email profile", "state": state, "nonce": nonce,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    return RedirectResponse(f"{d['authorization_endpoint']}?{q}")


@router.get("/api/auth/callback")
async def callback(code: str, state: str):
    pending = _pending.pop(state, None)
    if not pending:
        raise HTTPException(400, "login expired — try again")
    d = await _disco()
    async with httpx.AsyncClient(timeout=20) as c:
        tok = await c.post(d["token_endpoint"], data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": _redirect_uri(),
            "client_id": os.environ["CHAMP_OIDC_CLIENT_ID"],
            "client_secret": os.environ.get("CHAMP_OIDC_CLIENT_SECRET", ""),
            "code_verifier": pending["verifier"],
        })
        if tok.status_code >= 400:
            raise HTTPException(401, "token exchange failed")
        access = tok.json().get("access_token")
        info = await c.get(d["userinfo_endpoint"], headers={"Authorization": f"Bearer {access}"})
        if info.status_code >= 400:
            raise HTTPException(401, "userinfo failed")
        claims = info.json()
    email = claims.get("email") or claims.get("preferred_username")
    if not email:
        raise HTTPException(401, "identity provider returned no e-mail")
    domains = [x.strip().lower() for x in os.environ.get("CHAMP_ALLOWED_DOMAINS", "").split(",") if x.strip()]
    if domains and email.split("@")[-1].lower() not in domains:
        raise HTTPException(403, "your e-mail domain is not allowed")
    user = upsert_user(email, claims.get("name", ""))
    db.audit("login", user["id"], {"email": email}, actor=user["id"])
    resp = RedirectResponse(pending["next"])
    secure = os.environ.get("CHAMP_PUBLIC_URL", "").startswith("https://")
    resp.set_cookie(COOKIE, sign_session(user["id"]), httponly=True, samesite="lax", secure=secure,
                    max_age=SESSION_TTL)
    return resp


@router.post("/api/auth/logout")
async def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp
