"""Demo account login endpoint."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from src.auth import create_token, require_auth

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    demo_user = os.environ.get("EMISSARY_DEMO_USERNAME", "analyst")
    demo_pass = os.environ.get("EMISSARY_DEMO_PASSWORD", "demo")
    if req.username != demo_user or req.password != demo_pass:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return LoginResponse(access_token=create_token(req.username), token_type="bearer", username=req.username)


@router.get("/me")
async def me(username: str = Depends(require_auth)):
    return {"username": username}
