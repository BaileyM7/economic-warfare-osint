"""Simple HMAC-signed bearer token auth for demo.

Admin users are configured via EMISSARY_ADMIN_USERS:
    EMISSARY_ADMIN_USERS="bailey:mypassword,boss:otherpassword"
"""
from __future__ import annotations

import hashlib
import hmac
import os
from fastapi import Header, HTTPException, status


def _secret() -> str:
    return os.environ.get("EMISSARY_AUTH_SECRET", "dev-secret-change-me")


def get_admin_users() -> dict[str, str]:
    """Parse EMISSARY_ADMIN_USERS env var into {username: password}."""
    raw = os.environ.get("EMISSARY_ADMIN_USERS", "")
    users: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if ":" not in pair:
            continue
        username, password = pair.split(":", 1)
        username, password = username.strip(), password.strip()
        if username and password:
            users[username] = password
    return users


def is_admin(username: str) -> bool:
    """Return True if `username` is configured as an admin user."""
    return username in get_admin_users()


def check_admin_credentials(username: str, password: str) -> bool:
    """Return True if (username, password) matches a configured admin."""
    users = get_admin_users()
    expected = users.get(username)
    if expected is None:
        return False
    return hmac.compare_digest(expected, password)


def create_token(username: str) -> str:
    """Create an HMAC-signed token of form: <username>.<signature>"""
    sig = hmac.new(_secret().encode(), username.encode(), hashlib.sha256).hexdigest()
    return f"{username}.{sig}"


def verify_token(token: str | None) -> str | None:
    """Verify a token signature; return username if valid, None otherwise."""
    if not token:
        return None
    parts = token.rsplit(".", 1)
    if len(parts) != 2:
        return None
    username, sig = parts
    expected = hmac.new(_secret().encode(), username.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    return username


async def require_auth(authorization: str | None = Header(None)) -> str:
    """FastAPI dependency — raises 401 if missing/invalid token, else returns username."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid Authorization header")
    token = authorization[7:]
    username = verify_token(token)
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return username


async def require_admin(authorization: str | None = Header(None)) -> str:
    """FastAPI dependency — raises 401/403 unless the caller is an admin user."""
    username = await require_auth(authorization)
    if not is_admin(username):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return username
