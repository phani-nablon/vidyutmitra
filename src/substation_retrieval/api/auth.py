"""Sign-in and roles for the API and both UIs.

Same model as the VidyutMitra platform: username + password, three roles, and the API as the only place
access is enforced (the UI only hides what a role cannot do). Passwords are stored as
`scrypt:<salt>:<hash>` with Node's scryptSync defaults (N=16384, r=8, p=1, 64 bytes), so a VidyutMitra
`users.json` can be copied in as is. Entra ID SSO would replace the sign-in screen, not the role table.

The session is an HMAC-signed token in an HttpOnly cookie rather than a Bearer header: the drawing pages
and crops load through <img src>, which cannot carry a header.

  var/auth/users.json   accounts (created on first start with the three development accounts)
  var/auth/secret       signing key (random, created on first start; SR_AUTH_SECRET overrides it)

Roles
  viewer   read everything, run searches and chat
  editor   + edit, mark and submit drawings in review, ingest drawings
  admin    + engines, rebuild / import, delete drawings, manage users
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE = "sr_session"
ROLES = ("viewer", "editor", "admin")
RANK = {r: i for i, r in enumerate(ROLES)}
_lock = threading.Lock()

# Same accounts as the VidyutMitra dev build. The login page lists them only while one of them still has
# its default password, so changing the passwords (sr user-passwd) is enough to take them off the screen.
DEV_ACCOUNTS = [("admin", "Admin", "admin", "Admin@123"), ("draftsman", "Draftsman", "editor", "Editor@123"),
                ("viewer", "Viewer", "viewer", "Viewer@123")]
LOCAL_USER = {"username": "local", "name": "Local user", "role": "admin"}

# Requests anyone may make without a session.
PUBLIC = {"/api/auth/login", "/api/auth/logout", "/api/auth/me", "/api/auth/mode", "/api/health"}
# Writes a viewer may still make: searching is a POST but changes nothing.
VIEWER_POSTS = {"/api/search", "/api/chat", "/api/siteplan"}


# ------------------------------------------------------------ passwords
def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    h = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1, dklen=64, maxmem=64 * 1024 * 1024)
    return f"scrypt:{salt}:{h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, h = str(stored).split(":")
    except ValueError:
        return False
    return scheme == "scrypt" and hmac.compare_digest(hash_password(password, salt).split(":")[2], h)


# ------------------------------------------------------------ store
class UserStore:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.path = self.folder / "users.json"

    def _read(self) -> list[dict]:
        if not self.path.exists():
            self.folder.mkdir(parents=True, exist_ok=True)
            users = [{"username": u, "name": n, "role": r, "password": hash_password(p)} for u, n, r, p in DEV_ACCOUNTS]
            self._write(users)
            log.warning("auth: created %s with the development accounts; change their passwords (sr user-passwd)", self.path)
            return users
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, users: list[dict]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(users, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def list(self) -> list[dict]:
        with _lock:
            return [public(u) for u in self._read()]

    def get(self, username: str) -> dict | None:
        with _lock:
            return next((u for u in self._read() if u["username"].lower() == username.strip().lower()), None)

    def check(self, username: str, password: str) -> dict | None:
        u = self.get(username)
        return u if u and verify_password(password, u.get("password", "")) else None

    def upsert(self, username: str, role: str | None = None, password: str | None = None, name: str | None = None) -> dict:
        if role is not None and role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        username = username.strip()
        if not username:
            raise ValueError("username is empty")
        with _lock:
            users = self._read()
            u = next((x for x in users if x["username"].lower() == username.lower()), None)
            if u is None:
                if not password:
                    raise ValueError("a new user needs a password")
                u = {"username": username, "name": name or username, "role": role or "viewer"}
                users.append(u)
            if role:
                u["role"] = role
            if name:
                u["name"] = name
            if password:
                if len(password) < 8:
                    raise ValueError("password must be at least 8 characters")
                u["password"] = hash_password(password)
            self._write(users)
            return public(u)

    def remove(self, username: str) -> bool:
        with _lock:
            users = self._read()
            keep = [u for u in users if u["username"].lower() != username.lower()]
            if len(keep) == len(users):
                return False
            if not any(u["role"] == "admin" for u in keep):
                raise ValueError("cannot remove the last admin")
            self._write(keep)
            return True

    def dev_defaults_active(self) -> bool:
        return any(self.check(u, p) for u, _, _, p in DEV_ACCOUNTS)


def public(u: dict) -> dict:
    return {"username": u["username"], "name": u.get("name") or u["username"], "role": u["role"]}


# ------------------------------------------------------------ tokens
def _settings():
    from ..services import get_services
    return get_services().settings


def enabled(settings=None) -> bool:
    return bool((settings or _settings()).get("auth.enabled", True))


def store(settings=None) -> UserStore:
    return UserStore((settings or _settings()).var("auth"))


def _secret(settings) -> bytes:
    env = os.environ.get("SR_AUTH_SECRET")
    if env:
        return env.encode()
    p = settings.var("auth") / "secret"     # var() would make a suffix-less path a folder
    if not p.exists():
        p.write_text(secrets.token_hex(32), encoding="utf-8")
        try:
            p.chmod(0o600)
        except OSError:
            pass
    return p.read_text(encoding="utf-8").strip().encode()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def issue(user: dict, settings) -> str:
    hours = float(settings.get("auth.session_hours", 12))
    body = _b64(json.dumps({"u": user["username"], "r": user["role"], "n": user.get("name"), "exp": int(time.time() + hours * 3600)}).encode())
    sig = _b64(hmac.new(_secret(settings), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read(token: str | None, settings) -> dict | None:
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    good = _b64(hmac.new(_secret(settings), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, good):
        return None
    try:
        c = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except Exception:
        return None
    if c.get("exp", 0) < time.time():
        return None
    return {"username": c["u"], "role": c["r"], "name": c.get("n") or c["u"]}


def current_user(request: Request) -> dict | None:
    s = _settings()
    if not enabled(s):
        return dict(LOCAL_USER)
    return read(request.cookies.get(COOKIE), s)


def required_role(method: str, path: str) -> str | None:
    """Minimum role for an API call; None = public."""
    if path in PUBLIC:
        return None
    if path.startswith(("/api/admin", "/api/auth/users")) or method == "DELETE" or (path == "/api/engines" and method != "GET"):
        return "admin"
    if method in ("GET", "HEAD") or path in VIEWER_POSTS:
        return "viewer"
    return "editor"


def guard(request: Request) -> Response | None:
    """Middleware check: a JSON 401/403 for API calls the session does not allow, else None."""
    from fastapi.responses import JSONResponse
    path = request.url.path
    if not path.startswith("/api/"):
        return None
    need = required_role(request.method, path)
    if need is None:
        return None
    user = current_user(request)
    if user is None:
        return JSONResponse({"detail": "Not signed in"}, status_code=401)
    if RANK.get(user["role"], -1) < RANK[need]:
        return JSONResponse({"detail": f"Your role ({user['role']}) cannot do this; it needs {need}."}, status_code=403)
    request.state.user = user
    return None


# ------------------------------------------------------------ endpoints
class LoginIn(BaseModel):
    username: str
    password: str


@router.get("/mode")
def mode():
    s = _settings()
    on = enabled(s)
    return {"enabled": on, "dev_accounts": on and store(s).dev_defaults_active()}


@router.post("/login")
def login(body: LoginIn, response: Response):
    s = _settings()
    if not enabled(s):
        return {"user": LOCAL_USER}
    u = store(s).check(body.username, body.password)
    if not u:
        time.sleep(0.5)   # slows down password guessing a little; there is no lockout
        raise HTTPException(401, "Wrong username or password")
    response.set_cookie(COOKIE, issue(u, s), httponly=True, samesite="lax", path="/",
                        max_age=int(float(s.get("auth.session_hours", 12)) * 3600),
                        secure=bool(s.get("auth.secure_cookie", False)))
    return {"user": public(u)}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    u = current_user(request)
    if u is None:
        raise HTTPException(401, "Not signed in")
    return {"user": u, "auth": enabled()}


class UserIn(BaseModel):
    username: str
    name: Optional[str] = None
    role: Optional[str] = None
    password: Optional[str] = None


@router.get("/users")
def users():
    return store().list()


@router.post("/users")
def save_user(body: UserIn):
    try:
        return store().upsert(body.username, body.role, body.password, body.name)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/users/{username}")
def delete_user(username: str):
    try:
        if not store().remove(username):
            raise HTTPException(404, "no such user")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"deleted": username}
