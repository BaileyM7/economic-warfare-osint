"""Local-development stub of the SendGrid email client.

Activated by setting NOTIFICATIONS_ENABLED=true AND SENDGRID_STUB_MODE=true.
When active, get_sendgrid_client() returns an instance of StubSendGridClient
instead of the real SendGridAPIClient -- no real API calls are made, no email
is sent, no SendGrid free-tier quota is consumed.

Mirrors the design of stub_client.py (Twilio):
- Realistic response shape (object with .status_code + .headers + .body)
- Auditable: every "send" appended to data/sendgrid_stub.jsonl
- In-memory list (sent_mails) for test introspection
- Honors the kill-switch: NOTIFICATIONS_ENABLED=false still returns None
"""

from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

log = logging.getLogger(__name__)

# Where the JSONL audit file lives. data/ matches the SQLite DB convention
# and is already in .gitignore (per existing data/emissary.db handling).
STUB_LOG_PATH = Path(__file__).resolve().parents[2] / "data" / "sendgrid_stub.jsonl"

# In-memory record of every stubbed send this process has performed.
# Tests introspect via `from src.notifications.stub_client_sendgrid import sent_mails`.
# Use the list directly for assertions; call clear_sent_mails() between tests.
sent_mails: list[StubSendGridResponse] = []
_sent_lock = Lock()


@dataclass
class StubSendGridResponse:
    """Shaped like sendgrid's Response object -- the bits send_weekly_digest reads.

    Real SendGrid returns a Response with .status_code (typically 202),
    .headers (dict containing X-Message-Id), and .body (bytes, usually empty
    on success). The extras at the bottom are stub-only and surface what
    would have been sent for test/demo inspection -- they're NOT on the
    real Response object so code that depends on them only works when the
    stub is active (which is the right scoping).
    """

    status_code: int = 202
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    # Stub-specific extras for test/demo inspection (not on real Response):
    to_email: str = ""
    subject: str = ""
    plain_text_excerpt: str = ""
    html_excerpt: str = ""


def _gen_message_id() -> str:
    """SendGrid X-Message-Id is opaque; stub generates a recognizable one.

    Real SendGrid IDs look like 'jK9wJZmHTo6mYupbN-DGfQ.filterdrecv-...' --
    long, alphanumeric with dashes and dots. We don't try to perfectly mimic
    that; 'sg-stub-<hex>' makes it obvious in logs / audit trails that this
    came from the stub and not a real SendGrid send.
    """
    return f"sg-stub-{secrets.token_hex(8)}"


def _append_to_log(record: dict[str, Any]) -> None:
    """Append one JSONL line to data/sendgrid_stub.jsonl. Best-effort; never raises."""
    try:
        STUB_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with STUB_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:
        # Logging a stub send is best-effort -- if we can't write, log to
        # Python logging instead so it's still observable in dev.
        log.exception("sendgrid stub: failed to write JSONL audit line")


class StubSendGridClient:
    """Drop-in stub of sendgrid.SendGridAPIClient.

    Only implements .send(mail) -- that's the only method the email digest
    path uses. Anything else is intentionally unset so a future code path
    that reaches into other resources gets a loud AttributeError, not
    silent no-ops.
    """

    def __init__(self, api_key: str = "SG.stub-api-key"):
        self.api_key = api_key

    def send(self, mail: Any) -> StubSendGridResponse:
        """Stub of SendGridAPIClient.send(mail).

        Returns a StubSendGridResponse shaped like the real Response, with
        a synthesized X-Message-Id in .headers. Also records the send to
        the in-memory list and JSONL audit log.
        """
        # Extract a readable summary of the Mail object. The real Mail.get()
        # returns a serializable dict (see test_send_weekly_digest_sends_and_logs
        # in test_email_digest.py for the assumed shape).
        try:
            payload = mail.get() if hasattr(mail, "get") else {}
        except Exception:
            payload = {}

        subject = payload.get("subject", "")
        # personalizations[0].to[0].email is the recipient
        to_email = ""
        try:
            to_email = payload.get("personalizations", [{}])[0].get("to", [{}])[0].get("email", "")
        except (IndexError, AttributeError, TypeError):
            pass

        # content is a list of {type, value} blocks; pull excerpts for the log
        plain_text_excerpt = ""
        html_excerpt = ""
        for block in payload.get("content", []):
            ctype = block.get("type", "")
            value = block.get("value", "") or ""
            if ctype == "text/plain":
                plain_text_excerpt = value[:200]
            elif ctype == "text/html":
                html_excerpt = value[:200]

        message_id = _gen_message_id()
        resp = StubSendGridResponse(
            status_code=202,
            headers={
                "X-Message-Id": message_id,
                "Content-Type": "application/json",
            },
            body=b"",
            to_email=to_email,
            subject=subject,
            plain_text_excerpt=plain_text_excerpt,
            html_excerpt=html_excerpt,
        )

        with _sent_lock:
            sent_mails.append(resp)

        _append_to_log(
            {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "message_id": message_id,
                "to": to_email,
                "subject": subject,
                # chars (not full body) keeps the audit log compact while still
                # making it easy to spot "empty body" regressions.
                "plain_text_chars": len(plain_text_excerpt),
                "html_chars": len(html_excerpt),
                "status_code": 202,
            }
        )

        log.info(
            "STUB SENDGRID: would send to %s | subject=%r | id=%s",
            to_email,
            subject,
            message_id,
        )

        return resp


def clear_sent_mails() -> None:
    """Wipe the in-memory sent_mails list. Tests should call this in setup
    to ensure isolation. The on-disk JSONL log is NOT cleared -- it's an
    intentional audit trail that survives process restarts."""
    with _sent_lock:
        sent_mails.clear()
