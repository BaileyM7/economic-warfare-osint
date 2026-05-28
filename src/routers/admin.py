"""Admin endpoints — usage analytics + notifications enrollment."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.auth import require_admin
from src.db import get_db, log_activity, query_usage_summary

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/usage")
async def usage_summary(days: int = Query(30, ge=1, le=365)):
    """Return aggregate usage stats for the last N days.

    Response shape:
        - logins_per_day:  [{day, success, failure, unique_users}]
        - top_features:    [{feature, hits, unique_users}]
        - top_users:       [{username, events, last_seen}]
        - recent_logins:   [{timestamp, username, status_code, client_ip, detail}]
    """
    return query_usage_summary(days=days)


# --- Notifications enrollment -------------------------------------------
#
# Admin-only flow: admins enter users' contact info on their behalf.
# End-users have no UI to set their own phone/email — strictly admin-
# controlled. Replaces the previously-existing user-self-PUT endpoint.
#
# The admin router is gated at api.py via include_router(dependencies=[
# Depends(require_admin)]). The per-endpoint Depends(require_admin) below
# is intentional — FastAPI dedupes the dependency, and we need the admin's
# username inside the handler for log_activity audit calls.

# E.164 phone format: '+' followed by 1-15 digits, leading digit non-zero.
_E164_PATTERN = r"^\+[1-9]\d{1,14}$"


class EnrollmentRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    email: str | None = Field(None, max_length=320)
    phone_number: str | None = Field(None, pattern=_E164_PATTERN)
    sms_enabled: bool = False
    email_enabled: bool = False
    timezone: str = Field("America/New_York", max_length=64)


class EnrollmentResponse(BaseModel):
    username: str
    email: str | None
    phone_number: str | None
    sms_enabled: bool
    email_enabled: bool
    timezone: str
    created_at: str | None
    unsubscribed_at: str | None


def _row_to_enrollment(row) -> EnrollmentResponse:
    return EnrollmentResponse(
        username=row["username"],
        email=row["email"],
        phone_number=row["phone_number"],
        sms_enabled=bool(row["sms_enabled"]),
        email_enabled=bool(row["email_enabled"]),
        timezone=row["timezone"] or "America/New_York",
        created_at=row["created_at"],
        unsubscribed_at=row["unsubscribed_at"],
    )


@router.get("/enrollments", response_model=list[EnrollmentResponse])
def list_enrollments(admin: str = Depends(require_admin)) -> list[EnrollmentResponse]:
    """Return every enrolled user (one row per username in `users`)."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT username, email, phone_number, sms_enabled, email_enabled, "
            "timezone, created_at, unsubscribed_at FROM users ORDER BY username"
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_enrollment(r) for r in rows]


@router.post("/enrollments", response_model=EnrollmentResponse)
def enroll_user(req: EnrollmentRequest, admin: str = Depends(require_admin)) -> EnrollmentResponse:
    """Create or update an enrollment for `req.username` (upsert)."""
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, email, phone_number, sms_enabled, email_enabled, timezone) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(username) DO UPDATE SET "
            "email = excluded.email, phone_number = excluded.phone_number, "
            "sms_enabled = excluded.sms_enabled, email_enabled = excluded.email_enabled, "
            "timezone = excluded.timezone",
            (
                req.username,
                req.email,
                req.phone_number,
                int(req.sms_enabled),
                int(req.email_enabled),
                req.timezone,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT username, email, phone_number, sms_enabled, email_enabled, "
            "timezone, created_at, unsubscribed_at FROM users WHERE username = ?",
            (req.username,),
        ).fetchone()
    finally:
        conn.close()
    log_activity(
        event_type="notifications_enrollment",
        message=f"{admin} enrolled {req.username} (sms={req.sms_enabled}, email={req.email_enabled})",
        source=admin,
        related_id=req.username,
    )
    return _row_to_enrollment(row)


@router.delete("/enrollments/{username}")
def unenroll_user(username: str, admin: str = Depends(require_admin)) -> dict:
    """Hard-delete the enrollment row for `username`."""
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()
        deleted = cur.rowcount
    finally:
        conn.close()
    if deleted == 0:
        raise HTTPException(404, f"No enrollment for username={username!r}")
    log_activity(
        event_type="notifications_unenrollment",
        message=f"{admin} unenrolled {username}",
        source=admin,
        related_id=username,
    )
    return {"username": username, "deleted": True}
