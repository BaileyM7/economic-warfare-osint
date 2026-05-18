"""SMS composition + Twilio dispatch.

format_sms_body() is a pure function. send_sms_alert() handles the full
path: allowlist check, opt-in check, daily cap check, Twilio call, log row.
All gates short-circuit to a structured SendResult so callers can branch
on outcome without inspecting exceptions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.common.config import config
from src.notifications.caps import (
    can_send_sms,
    record_notification,
)
from src.notifications.clients import get_twilio_client, is_user_allowlisted

log = logging.getLogger(__name__)

MAX_SMS_LEN = 160
SUFFIX_TEMPLATE = " More: {url}"


@dataclass
class SendResult:
    """Outcome of a single send attempt. status is one of NOTIFICATION_STATUSES."""

    status: str
    provider_message_id: str | None = None
    error: str | None = None


def format_sms_body(card: dict) -> str:
    """Render a single-segment SMS body (<=160 ASCII chars) for one risk card.

    Truncates the synthesis text rather than calling an LLM - keeps sends
    cheap, deterministic, and free of latency variance.
    """
    severity = card.get("severity", "INFO")
    entity = card.get("entity", "")
    synthesis = card.get("synthesis", "")
    url = card.get("short_url", "")
    suffix = SUFFIX_TEMPLATE.format(url=url) if url else ""
    prefix = f"[{severity}] {entity}: "
    budget = MAX_SMS_LEN - len(prefix) - len(suffix)
    if budget < 0:
        # Pathological case: prefix+suffix alone exceed 160. Truncate the
        # whole thing at MAX_SMS_LEN with no synthesis.
        return (prefix + suffix)[:MAX_SMS_LEN]
    reason = synthesis if len(synthesis) <= budget else synthesis[: max(0, budget - 3)] + "..."
    return f"{prefix}{reason}{suffix}"


def send_sms_alert(user: dict, card: dict) -> SendResult:
    """Send a single SMS for one card to one user, with full safety gating.

    Gates (in order, short-circuit on first failing):
      1. user is on the allowlist
      2. user has sms_enabled=1 AND a phone_number on file
      3. user is under the daily cap (queried from notification_log)
      4. twilio client is configured (kill-switch + creds present)
    On any gate failure, returns a SendResult with the appropriate status
    and (for the cap case) writes a row to notification_log.
    """
    username = user["username"]

    if not is_user_allowlisted(username):
        return SendResult(status="skipped_allowlist")

    if not user.get("sms_enabled") or not user.get("phone_number"):
        return SendResult(status="skipped_disabled")

    if not can_send_sms(username):
        record_notification(username, "sms", card_id=card["id"], status="skipped_cap")
        return SendResult(status="skipped_cap")

    client = get_twilio_client()
    if client is None:
        return SendResult(status="skipped_kill_switch")

    body = format_sms_body(card)
    try:
        msg = client.messages.create(
            from_=config.twilio_from_phone,
            to=user["phone_number"],
            body=body,
        )
        record_notification(
            username,
            "sms",
            card_id=card["id"],
            provider_message_id=msg.sid,
            status="sent",
        )
        return SendResult(status="sent", provider_message_id=msg.sid)
    except Exception as e:
        log.exception("twilio send failed for username=%s card_id=%s", username, card.get("id"))
        record_notification(
            username,
            "sms",
            card_id=card["id"],
            status="failed",
            error_text=str(e),
        )
        return SendResult(status="failed", error=str(e))
