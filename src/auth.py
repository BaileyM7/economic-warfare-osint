"""Simple HMAC-signed bearer token auth for demo."""
from __future__ import annotations

import hashlib
import hmac
import os
from fastapi import Header, HTTPException, status


def _secret() -> str:
    return os.environ.get("EMISSARY_AUTH_SECRET", "dev-secret-change-me")


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
