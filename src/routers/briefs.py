"""Self-serve brief subscription + on-demand send (Phase 3).

User-facing counterparts to the admin-only enrollment + the weekly cron:
- POST /api/subscribe       — a logged-in user registers their own email for briefs
- POST /api/brief/send-now  — send a brief to the caller right now (synchronous),
                              returning the REAL send result (sent / failed / skipped)

Mounted at /api with require_auth applied at include time (see src/api.py). The
synchronous send-now is the demo-friendly path: it returns the actual SendResult
so a failure (e.g. an unverified SendGrid sender) is loud, not swallowed.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.auth import require_auth
from src.db import get_db
from src.notifications.email_digest import build_week_data, send_weekly_digest
from src.notifications.synthesis import generate_opening_synthesis

router = APIRouter(prefix="/api", tags=["briefs"])


class SubscribeRequest(BaseModel):
    email: str
    phone: str | None = None


@router.post("/subscribe")
async def subscribe(req: SubscribeRequest, username: str = Depends(require_auth)):
    """Self-serve: the logged-in user registers their email to receive briefs."""
    email = req.email.strip()
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="A valid email address is required")
    phone = (req.phone or "").strip() or None
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, email, phone_number, sms_enabled, email_enabled) "
            "VALUES (?, ?, ?, ?, 1) "
            "ON CONFLICT(username) DO UPDATE SET "
            "email = excluded.email, "
            "phone_number = COALESCE(excluded.phone_number, users.phone_number), "
            "email_enabled = 1, unsubscribed_at = NULL",
            (username, email, phone, 1 if phone else 0),
        )
        conn.commit()
    finally:
        conn.close()
    return {"status": "subscribed", "username": username, "email": email}


@router.post("/brief/send-now")
async def send_brief_now(username: str = Depends(require_auth)):
    """Send a brief to the calling user immediately; return the real result.

    Returns the SendResult status so failures surface (sent / failed /
    skipped_kill_switch / skipped_disabled / skipped_allowlist) — no swallowing.
    """
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT username, email, phone_number, sms_enabled, email_enabled, timezone "
            "FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    finally:
        conn.close()
    if not row or not row["email"]:
        raise HTTPException(status_code=400, detail="No email on file — POST /api/subscribe first")

    user = dict(row)
    # Reuse the notifications router's lazy Anthropic client.
    from src.routers.notifications import _get_anthropic_client

    week_data = await build_week_data(username)
    week_data.opening_synthesis = generate_opening_synthesis(week_data, _get_anthropic_client())
    result = send_weekly_digest(user, week_data)
    return {
        "status": result.status,
        "email": user["email"],
        "error": getattr(result, "error", None),
        "provider_message_id": getattr(result, "provider_message_id", None),
    }
