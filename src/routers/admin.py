"""Admin endpoints — usage analytics + notifications enrollment."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.auth import require_admin
from src.common.config import config
from src.db import get_db, log_activity, query_usage_summary

# Note: src.notifications.* imports are deferred to the function bodies that
# need them. twilio/sendgrid/anthropic SDKs are heavy at import time and the
# rest of this router doesn't touch them — mirrors the existing lazy-import
# pattern elsewhere in the project.

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


def _load_user_or_404(username: str) -> dict:
    """Look up a user from the users table; raise 404 if absent."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT username, email, phone_number, sms_enabled, email_enabled, timezone "
            "FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(404, f"No enrollment for username={username!r}")
    return dict(row)


def _build_sample_week_data(username: str):
    """Synthetic WeekData with hand-crafted sample cards (mirrors scripts/stub_e2e_demo.py).

    Used by the test-email endpoint so the admin gets a real-looking preview
    of the digest format without depending on the user's actual watchlist.
    """
    # Deferred imports: keep this module's top-level imports light
    from src.notifications.email_digest import TickerDelta, WeekData
    from src.notifications.scenarios import select_scenario_for_week

    now = datetime.now(timezone.utc).replace(microsecond=0)
    now_iso = now.isoformat()
    year, week, _ = now.isocalendar()
    week_iso = f"{year}-W{week:02d}"
    week_start = now - timedelta(days=now.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)

    top_cards = [
        {
            "id": "test-cosco",
            "severity": "critical",
            "entity": "COSCO Shipping",
            "category": "sanctions",
            "source": "OFAC",
            "fetched_at": now_iso,
            "synthesis": "[SAMPLE] New OFAC SDN designation citing IRGC ties and dual-use cargo manifests",
            "short_url": "ew.app/r/sample-1",
        },
        {
            "id": "test-sinopec",
            "severity": "high",
            "entity": "Sinopec",
            "category": "markets",
            "source": "yfinance",
            "ticker": "SNP",
            "fetched_at": now_iso,
            "synthesis": "[SAMPLE] Down 7.2% on China demand fears; refinery margin compression confirmed",
            "short_url": "ew.app/r/sample-2",
        },
        {
            "id": "test-russia-oil",
            "severity": "high",
            "entity": "Russian oil price cap evasion",
            "category": "sanctions",
            "source": "OpenSanctions",
            "fetched_at": now_iso,
            "synthesis": "[SAMPLE] Three shell entities flagged moving Urals volume above $60 cap via Dubai",
            "short_url": "ew.app/r/sample-3",
        },
    ]

    market_deltas = [
        TickerDelta(symbol="SNP", entity_name="Sinopec", start=65.40, end=60.71, pct_change=-7.2),
        TickerDelta(symbol="PTR", entity_name="PetroChina", start=42.10, end=42.34, pct_change=0.6),
    ]

    sanctions = [c for c in top_cards if c.get("category") == "sanctions"]
    scenario = select_scenario_for_week(week_iso)

    return WeekData(
        username=username,
        week_iso=week_iso,
        week_start=week_start,
        week_end=week_end,
        top_cards=top_cards,
        market_deltas=market_deltas,
        sanctions=sanctions,
        scenario=scenario,
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


# --- Test send endpoints --------------------------------------------------
#
# Admin-triggered "send a test message right now" buttons. Same code path as
# real sends, with two differences:
#   1. The payload is synthetic (a fixed test card / hardcoded WeekData).
#   2. Daily SMS cap is bypassed (admins testing repeatedly shouldn't hit it).
# Allowlist + kill-switch + log writes all behave normally.


@router.post("/enrollments/{username}/test-sms")
def test_sms(username: str, admin: str = Depends(require_admin)) -> dict:
    user = _load_user_or_404(username)
    if not user.get("phone_number") or not user.get("sms_enabled"):
        raise HTTPException(400, f"User {username} has no phone or sms_enabled=0")

    # Deferred imports (twilio SDK is heavy)
    from src.notifications import caps as caps_mod
    from src.notifications import sms as sms_mod
    from src.notifications.sms import send_sms_alert

    test_card = {
        "id": f"test-sms-{secrets.token_hex(4)}",
        "severity": "HIGH",
        "entity": "TEST",
        "synthesis": f"Admin test send by {admin} at {_now_iso()}",
        "short_url": f"{config.app_base_url.rstrip('/')}/admin",
    }

    # Bypass daily cap for the duration of this call only.
    # sms.py did `from src.notifications.caps import can_send_sms` at module
    # load time, so the live reference is `sms_mod.can_send_sms` -- patching
    # caps_mod alone would NOT change what send_sms_alert sees. We patch BOTH
    # locations to be safe (caps_mod for any future direct callers, sms_mod
    # for the actual lookup inside send_sms_alert).
    with (
        patch.object(caps_mod, "can_send_sms", lambda _username: True),
        patch.object(sms_mod, "can_send_sms", lambda _username: True),
    ):
        result = send_sms_alert(user, test_card)

    log_activity(
        event_type="notifications_test_sms",
        message=f"{admin} sent test SMS to {username} -> status={result.status}",
        source=admin,
        related_id=username,
    )

    return {
        "status": result.status,
        "provider_message_id": result.provider_message_id,
        "error": result.error,
    }


@router.post("/enrollments/{username}/test-email")
def test_email(username: str, admin: str = Depends(require_admin)) -> dict:
    user = _load_user_or_404(username)
    if not user.get("email") or not user.get("email_enabled"):
        raise HTTPException(400, f"User {username} has no email or email_enabled=0")

    # Deferred imports (sendgrid + anthropic SDKs are heavy)
    from src.notifications.email_digest import send_weekly_digest
    from src.notifications.synthesis import generate_opening_synthesis

    week_data = _build_sample_week_data(username)
    # No LLM call -- pass None and use the deterministic fallback template
    week_data.opening_synthesis = generate_opening_synthesis(week_data, anthropic_client=None)

    result = send_weekly_digest(user, week_data)

    log_activity(
        event_type="notifications_test_email",
        message=f"{admin} sent test email to {username} -> status={result.status}",
        source=admin,
        related_id=username,
    )

    return {
        "status": result.status,
        "provider_message_id": result.provider_message_id,
        "error": result.error,
    }


# --- Entity vector index maintenance ------------------------------------
#
# Startup drains only the *dirty* set (entities changed since the last index
# write); it does not cover entities that already existed before the index was
# ever built. After first standing up Redis 8 + Voyage, the index is empty, so
# there is nothing to drain — this endpoint does the one-time (or on-demand)
# full backfill over every saved entity. Safe to re-run: unchanged entities are
# skipped via the text_hash guard, so it's idempotent and cheap on repeat.


@router.post("/reindex-entities")
async def reindex_entities(admin: str = Depends(require_admin)) -> dict:
    """Backfill the entity vector index from the SQLite system of record.

    No-op (with a reason) when the index is unavailable — Redis 8 / Voyage off —
    so it degrades exactly like every other semantic feature instead of 500ing.
    """
    from src.common import knowledge_store, vector_index

    if not vector_index.is_available():
        return {"status": "unavailable", **vector_index.status()}

    entities = knowledge_store.list_entities()
    result = await vector_index.backfill(entities)
    log_activity(
        event_type="admin_reindex_entities",
        message=f"{admin} reindexed entities -> {result}",
        source=admin,
    )
    return {
        "status": "ok",
        "total_entities": len(entities),
        **result,
        "index": vector_index.status(),
    }
