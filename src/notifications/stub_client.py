"""Local-development stub of the Twilio messages client.

Activated by setting NOTIFICATIONS_ENABLED=true AND TWILIO_STUB_MODE=true.
When active, get_twilio_client() returns an instance of StubTwilioClient
instead of the real TwilioClient — no real API calls are made, no SMS is
sent, no Twilio credit is consumed.

Design goals:
- Realistic responses: returns objects shaped like Twilio's MessageInstance
  (same attribute names, same types, same "queued" status at create time)
  so any code reading more than .sid keeps working when real Twilio is
  swapped back in.
- Magic-number-compatible: mirrors Twilio's documented test-credential
  numbers so the same numbers produce the same outcomes whether you're
  using the stub OR real Twilio test creds (+15005550001 → 21211, etc.).
- Auditable: every "send" is appended as a JSONL line to
  data/twilio_stub.jsonl AND held in an in-memory list (sent_messages)
  for test introspection.
- Honors the kill-switch: NOTIFICATIONS_ENABLED=false still returns None
  from the factory regardless of TWILIO_STUB_MODE.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from twilio.base.exceptions import TwilioRestException

log = logging.getLogger(__name__)

# Where the JSONL audit file lives. data/ matches the SQLite DB convention
# and is already in .gitignore (per existing data/emissary.db handling).
STUB_LOG_PATH = Path(__file__).resolve().parents[2] / "data" / "twilio_stub.jsonl"

# E.164: '+' then 1-15 digits, leading digit non-zero
_E164 = re.compile(r"^\+[1-9]\d{1,14}$")

# In-memory record of every stubbed send this process has performed.
# Tests introspect via `from src.notifications.stub_client import sent_messages`.
# Use the list directly for assertions; call clear_sent_messages() between tests.
sent_messages: list[StubMessageInstance] = []
_sent_lock = Lock()


# Maps the Twilio "magic numbers" to (error_code, error_msg). +15005550006
# is documented as "success", so it's intentionally NOT in this dict.
_MAGIC_ERRORS: dict[str, tuple[int, str]] = {
    "+15005550001": (21211, "Invalid 'To' Phone Number"),
    "+15005550002": (21612, "Cannot route to this number"),
    "+15005550003": (21408, "Permission to send to this region not enabled"),
    "+15005550004": (21610, "Attempt to send to unsubscribed recipient"),
    "+15005550009": (21614, "'To' number is not a valid mobile number"),
}


@dataclass
class StubMessageInstance:
    """Shaped to match twilio.rest.api.v2010.account.message.MessageInstance.

    Only the fields our SMS path consumes are populated with realistic values;
    the rest are populated with reasonable approximations so a future reader
    that reaches for `.price` or `.api_version` doesn't get a surprise.
    """

    sid: str
    account_sid: str
    body: str
    from_: str
    to: str
    status: str = "queued"  # matches real Twilio at create-time
    direction: str = "outbound-api"
    num_segments: str = "1"
    num_media: str = "0"
    api_version: str = "2010-04-01"
    error_code: int | None = None
    error_message: str | None = None
    price: str | None = None
    price_unit: str | None = None
    date_sent: datetime | None = None
    messaging_service_sid: str | None = None
    date_created: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    date_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    uri: str = ""
    subresource_uris: dict[str, str] = field(default_factory=dict)


def _gen_sid(prefix: str = "SM") -> str:
    """Generate a Twilio-shaped SID: '<prefix>' + 32 lowercase hex chars."""
    return f"{prefix}{secrets.token_hex(16)}"


def _validate_phone(value: str, field_name: str, error_code: int) -> None:
    """Raise TwilioRestException matching real Twilio behavior for bad E.164."""
    if not value or not _E164.match(value):
        raise TwilioRestException(
            status=400,
            uri="/2010-04-01/Accounts/STUB/Messages.json",
            msg=f"Invalid '{field_name}' Phone Number: {value!r}",
            code=error_code,
            method="POST",
        )


def _append_to_log(record: dict[str, Any]) -> None:
    """Append one JSONL line to data/twilio_stub.jsonl. Best-effort; never raises."""
    try:
        STUB_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with STUB_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:
        # Logging a stub send is best-effort — if we can't write, log to
        # Python logging instead so it's still observable in dev.
        log.exception("stub: failed to write JSONL audit line")


class _StubMessages:
    """The .messages attribute of StubTwilioClient — exposes .create()."""

    def __init__(self, account_sid: str):
        self._account_sid = account_sid

    def create(self, *, to: str, from_: str, body: str, **_extra: Any) -> StubMessageInstance:
        """Stub of Twilio's messages.create(). Honors magic numbers + validates phones."""
        # From validation first (mirrors real Twilio: it rejects bad From before To).
        _validate_phone(from_, "From", error_code=21620)

        # To validation + magic-number short-circuits.
        if to in _MAGIC_ERRORS:
            code, msg = _MAGIC_ERRORS[to]
            raise TwilioRestException(
                status=400,
                uri="/2010-04-01/Accounts/STUB/Messages.json",
                msg=msg,
                code=code,
                method="POST",
            )
        _validate_phone(to, "To", error_code=21211)

        # Build the response object.
        sid = _gen_sid("SM")
        msg_obj = StubMessageInstance(
            sid=sid,
            account_sid=self._account_sid,
            body=body,
            from_=from_,
            to=to,
            uri=f"/2010-04-01/Accounts/{self._account_sid}/Messages/{sid}.json",
            subresource_uris={
                "media": f"/2010-04-01/Accounts/{self._account_sid}/Messages/{sid}/Media.json",
                "feedback": f"/2010-04-01/Accounts/{self._account_sid}/Messages/{sid}/Feedback.json",
            },
        )

        # Record in-memory and to the audit log.
        with _sent_lock:
            sent_messages.append(msg_obj)
        _append_to_log(
            {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "sid": msg_obj.sid,
                "to": to,
                "from": from_,
                "body": body,
                "status": msg_obj.status,
            }
        )

        log.info(
            "STUB SMS: would send to %s from %s | sid=%s | body=%r",
            to,
            from_,
            sid,
            body,
        )

        return msg_obj


class StubTwilioClient:
    """Drop-in stub of twilio.rest.Client.

    Only implements what the SMS path uses (.messages.create). Anything else
    is intentionally unset so a future code path that reaches into other
    sub-resources gets an AttributeError loudly, not silent no-ops.
    """

    def __init__(self, account_sid: str = "ACstubaccountsid0000000000000000"):
        self.account_sid = account_sid
        self.messages = _StubMessages(account_sid)


def clear_sent_messages() -> None:
    """Wipe the in-memory sent_messages list. Tests should call this in setup
    to ensure isolation. The on-disk JSONL log is NOT cleared — it's an
    intentional audit trail that survives process restarts."""
    with _sent_lock:
        sent_messages.clear()
