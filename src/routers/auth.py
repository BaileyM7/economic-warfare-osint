"""Demo + admin account login endpoint."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from src.analytics import log_login_attempt
from src.auth import check_admin_credentials, create_token, is_admin, require_auth

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    is_admin: bool = False


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _check_demo_credentials(username: str, password: str) -> bool:
    demo_user = os.environ.get("EMISSARY_DEMO_USERNAME", "analyst")
    demo_pass = os.environ.get("EMISSARY_DEMO_PASSWORD", "demo")
    return username == demo_user and password == demo_pass


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, request: Request):
    ip = _client_ip(request)

    if check_admin_credentials(req.username, req.password):
        log_login_attempt(req.username, success=True, client_ip=ip, detail="admin")
        return LoginResponse(
            access_token=create_token(req.username),
            username=req.username,
            is_admin=True,
        )

    if _check_demo_credentials(req.username, req.password):
        log_login_attempt(req.username, success=True, client_ip=ip)
        return LoginResponse(
            access_token=create_token(req.username),
            username=req.username,
            is_admin=False,
        )

    log_login_attempt(req.username, success=False, client_ip=ip, detail="invalid_credentials")
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")


@router.get("/me")
async def me(username: str = Depends(require_auth)):
    return {"username": username, "is_admin": is_admin(username)}
