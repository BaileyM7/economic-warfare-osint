"""End-to-end demonstration of the notifications pipeline using stub mode.

Boots no web server — calls the modules directly. Shows:
  1. What gets dispatched as SMS for a sample set of risk cards
  2. The stub Twilio JSONL audit log entries that would be produced
  3. The notification_log rows that land in SQLite
  4. The rendered weekly email digest (HTML + plain text)

Run:
    NOTIFICATIONS_ENABLED=true TWILIO_STUB_MODE=true \\
    NOTIFICATIONS_ALLOWLIST=alice TWILIO_FROM_PHONE=+15005550006 \\
    uv run python scripts/stub_e2e_demo.py

(PowerShell:
    $env:NOTIFICATIONS_ENABLED='true'; $env:TWILIO_STUB_MODE='true';
    $env:NOTIFICATIONS_ALLOWLIST='alice'; $env:TWILIO_FROM_PHONE='+15005550006';
    uv run python scripts/stub_e2e_demo.py)
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make 'src.*' importable when this script is run directly from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows default console is cp1252; rendered HTML contains Unicode chars
# (em-dash, arrows, etc.). Force UTF-8 so the demo prints cleanly.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Force stub-mode env BEFORE importing project modules, so config picks it up.
os.environ.setdefault("NOTIFICATIONS_ENABLED", "true")
os.environ.setdefault("TWILIO_STUB_MODE", "true")
os.environ.setdefault("SENDGRID_STUB_MODE", "true")
os.environ.setdefault("NOTIFICATIONS_ALLOWLIST", "alice")
os.environ.setdefault("TWILIO_FROM_PHONE", "+15005550006")
os.environ.setdefault("APP_BASE_URL", "https://emissary.demo")
# Redirect the SQLite DB to a temp file so we don't touch data/emissary.db.
_TEST_DB = Path(tempfile.gettempdir()) / "stub_e2e_demo.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()

# Same trick the test harness uses: patch DB_PATH BEFORE anything imports get_db.
import src.db as _db  # noqa: E402

_db.DB_PATH = _TEST_DB

# Also redirect the stub JSONL log to a temp file so we can show its contents
# cleanly without mixing with whatever the user already has.
import src.notifications.stub_client as _stub  # noqa: E402

_STUB_LOG = Path(tempfile.gettempdir()) / "stub_e2e_demo_twilio.jsonl"
if _STUB_LOG.exists():
    _STUB_LOG.unlink()
_stub.STUB_LOG_PATH = _STUB_LOG

# Same redirect for the SendGrid stub log.
import src.notifications.stub_client_sendgrid as _stub_sg  # noqa: E402

_STUB_SG_LOG = Path(tempfile.gettempdir()) / "stub_e2e_demo_sendgrid.jsonl"
if _STUB_SG_LOG.exists():
    _STUB_SG_LOG.unlink()
_stub_sg.STUB_LOG_PATH = _STUB_SG_LOG

from src.common.config import config  # noqa: E402
from src.db import get_db, init_db  # noqa: E402
from src.notifications import clients as _clients  # noqa: E402
from src.notifications import sms as _sms  # noqa: E402
from src.notifications.dispatcher import dispatch_sms_for_new_cards  # noqa: E402
from src.notifications.email_digest import (  # noqa: E402
    TickerDelta,
    WeekData,
    render_digest,
    send_weekly_digest,
)
from src.notifications.scenarios import select_scenario_for_week  # noqa: E402
from src.notifications.sms import format_sms_body  # noqa: E402
from src.notifications.stub_client import clear_sent_messages, sent_messages  # noqa: E402
from src.notifications.stub_client_sendgrid import (  # noqa: E402
    clear_sent_mails,
    sent_mails,
)
from src.notifications.synthesis import generate_opening_synthesis  # noqa: E402


def banner(title: str) -> None:
    line = "=" * 72
    print(f"\n{line}\n  {title}\n{line}")


def section(title: str) -> None:
    print(f"\n--- {title} ---")


# -- Setup ------------------------------------------------------------------

banner("STUB E2E DEMO — Twilio SMS + SendGrid email pipeline")
print(
    f"Config: NOTIFICATIONS_ENABLED={config.notifications_enabled}, "
    f"TWILIO_STUB_MODE={config.twilio_stub_mode}, "
    f"allowlist={config.notifications_allowlist!r}"
)
print(f"Temp DB:       {_TEST_DB}")
print(f"Temp stub log: {_STUB_LOG}")

init_db()

# Clear any prior caches from the import-time singleton.
_clients.get_twilio_client.cache_clear()
_clients.get_sendgrid_client.cache_clear()
clear_sent_messages()
clear_sent_mails()

# Seed 'alice' as the demo recipient.
conn = get_db()
try:
    conn.execute(
        "INSERT INTO users (username, email, phone_number, sms_enabled, email_enabled, timezone) "
        "VALUES ('alice', 'alice@example.com', '+15005550006', 1, 1, 'America/New_York')"
    )
    conn.commit()
finally:
    conn.close()
print(
    "\n[setup] User 'alice' seeded: email=alice@example.com, phone=+15005550006, "
    "sms_enabled=1, email_enabled=1"
)

# -- Sample cards -----------------------------------------------------------

NOW = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
SAMPLE_CARDS = [
    {
        "id": "card-cosco-2026-05-18",
        "severity": "critical",  # lowercase — matches real feed pipeline
        "entity": "COSCO Shipping",
        "category": "sanctions",
        "source": "OFAC",
        "fetched_at": NOW,
        "synthesis": "New OFAC SDN designation citing IRGC ties and dual-use cargo manifests",
        "short_url": "ew.app/r/x9k",
    },
    {
        "id": "card-sinopec-2026-05-18",
        "severity": "high",
        "entity": "Sinopec",
        "category": "markets",
        "source": "yfinance",
        "ticker": "SNP",
        "price_start": 65.40,
        "price_end": 60.71,
        "pct_change": -7.2,
        "fetched_at": NOW,
        "synthesis": "Down 7.2% on China demand fears; refinery margin compression confirmed",
        "short_url": "ew.app/r/m2p",
    },
    {
        "id": "card-petrochina-2026-05-18",
        "severity": "low",  # below threshold — should NOT trigger SMS
        "entity": "PetroChina",
        "category": "markets",
        "source": "yfinance",
        "ticker": "PTR",
        "price_start": 42.10,
        "price_end": 42.34,
        "pct_change": 0.6,
        "fetched_at": NOW,
        "synthesis": "Marginal upward movement, no actionable signal",
        "short_url": "ew.app/r/p1c",
    },
    {
        "id": "card-russia-oil-2026-05-18",
        "severity": "high",
        "entity": "Russian oil price cap evasion",
        "category": "sanctions",
        "source": "OpenSanctions",
        "fetched_at": NOW,
        "synthesis": "Three shell entities flagged moving Urals volume above $60 cap via Dubai",
        "short_url": "ew.app/r/q8r",
    },
]

# ============================================================================
# PART 1 — SMS path
# ============================================================================

banner("PART 1 — SMS path (event-driven)")

section("Sample risk cards (4 cards)")
for c in SAMPLE_CARDS:
    print(
        f"  [{c['severity'].upper():>8}] {c['entity']:<30} | {c['category']:<10} | {c['synthesis'][:60]}"
    )

section("Formatted SMS bodies (what would be sent over Twilio)")
for c in SAMPLE_CARDS:
    if c["severity"].upper() not in ("HIGH", "CRITICAL"):
        print(f"  [{c['severity'].upper()}] {c['entity']} -> SKIPPED (below threshold)")
        continue
    body = format_sms_body(c)
    print(f"  [{c['severity'].upper()}] {c['entity']}:")
    print(f"    body  = {body!r}")
    print(f"    chars = {len(body)} / 160  (segments: {(len(body) + 159) // 160})")
    print(f"    ascii = {body.isascii()}")

section("Dispatching via the full pipeline (dispatcher -> caps -> stub)")
# Patch the imported symbol in sms_mod the same way tests do — needed because
# sms.py imports get_twilio_client at module-load time.
_sms.get_twilio_client = _clients.get_twilio_client
dispatch_sms_for_new_cards("alice", SAMPLE_CARDS)
print(f"  dispatcher returned. {len(sent_messages)} stub message(s) recorded in-memory.")

section("Stub JSONL audit log (contents of data/twilio_stub.jsonl)")
if _STUB_LOG.exists():
    for line in _STUB_LOG.read_text(encoding="utf-8").splitlines():
        print(f"  {line}")
else:
    print("  (no entries)")

section("notification_log rows (what the SMS sends produced)")
conn = get_db()
try:
    rows = conn.execute(
        "SELECT id, username, channel, card_id, status, provider_message_id, "
        "       substr(sent_at,1,19) AS sent_at "
        "FROM notification_log ORDER BY id"
    ).fetchall()
finally:
    conn.close()

print(f"  {'id':>3} | {'user':<10} | {'chan':<5} | {'status':<10} | {'sid':<34} | {'card_id':<30}")
print(
    f"  {'-' * 3:>3} | {'-' * 10:<10} | {'-' * 5:<5} | {'-' * 10:<10} | {'-' * 34:<34} | {'-' * 30:<30}"
)
for r in rows:
    sid_short = r["provider_message_id"] or "—"
    card = (r["card_id"] or "—")[:30]
    print(
        f"  {r['id']:>3} | {r['username']:<10} | {r['channel']:<5} | {r['status']:<10} | {sid_short:<34} | {card:<30}"
    )

section("Dedupe demonstration — dispatching SAME cards a second time")
clear_sent_messages()
dispatch_sms_for_new_cards("alice", SAMPLE_CARDS)
print(
    f"  Second dispatch: {len(sent_messages)} stub message(s) actually sent "
    f"(should be 0 — dedupe via notification_log.card_id)."
)

# ============================================================================
# PART 2 — Email digest
# ============================================================================

banner("PART 2 — Email digest (Monday 12:00 UTC scheduled)")

# Build a WeekData manually. In production, build_week_data(username) calls
# _build_live_feed_for_user, which queries the in-memory feed cache. For the
# demo, we wire up the same shape directly from our SAMPLE_CARDS.
top_cards = sorted(
    SAMPLE_CARDS,
    key=lambda c: (
        {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(c["severity"].upper(), 0),
        c.get("fetched_at", ""),
    ),
    reverse=True,
)[:5]

market_deltas = [
    TickerDelta(
        symbol=c["ticker"], start=c["price_start"], end=c["price_end"], pct_change=c["pct_change"]
    )
    for c in SAMPLE_CARDS
    if c.get("category") == "markets"
]
sanctions = [c for c in SAMPLE_CARDS if c.get("category") == "sanctions"]
scenario = select_scenario_for_week("2026-W21")

today = datetime(2026, 5, 18, tzinfo=timezone.utc)
week_data = WeekData(
    username="alice",
    week_iso="2026-W21",
    week_start=today,
    week_end=today + timedelta(days=6),
    top_cards=top_cards,
    market_deltas=market_deltas,
    sanctions=sanctions,
    scenario=scenario,
)

section("LLM opening synthesis (with deterministic fallback if no ANTHROPIC_API_KEY)")
# Honor the user's existing key if set; otherwise fallback runs.
try:
    if os.getenv("ANTHROPIC_API_KEY"):
        from anthropic import Anthropic

        anthropic_client = Anthropic()
        print("  ANTHROPIC_API_KEY detected -> calling Claude Haiku for live synthesis")
    else:
        anthropic_client = None
        print("  ANTHROPIC_API_KEY not set -> using deterministic fallback template")
    week_data.opening_synthesis = generate_opening_synthesis(week_data, anthropic_client)
    print(f"\n  > {week_data.opening_synthesis}")
except Exception as e:
    print(f"  (LLM call failed: {e}; fallback used)")
    week_data.opening_synthesis = generate_opening_synthesis(week_data, None)
    print(f"\n  > {week_data.opening_synthesis}")

section("Scenario spotlight (rotating by ISO week)")
print(f"  headline: {scenario['headline']}")
print(f"  summary:  {scenario['summary']}")

print()
section("Rendered HTML email (first 60 lines)")
html, text = render_digest(
    week_data,
    preferences_url=f"{config.app_base_url}/settings",
    unsubscribe_url=f"{config.app_base_url}/unsubscribe?u=alice",
)
for i, line in enumerate(html.splitlines()[:60], 1):
    print(f"  {i:>2}| {line}")
print(f"  ... (HTML total: {len(html.splitlines())} lines, {len(html)} bytes)")

section("Rendered plain-text email")
for line in text.splitlines():
    print(f"  | {line}")

# -- Dispatch the email through the SendGrid stub ----------------------------
# This exercises the same code path SendGrid would in production: Mail
# construction inside send_weekly_digest, dispatch to the (stubbed) client,
# header parsing for X-Message-Id, and the notification_log write.

section("Dispatching weekly digest via the SendGrid stub")
# Patch the imported symbol in email_digest the same way the SMS path does --
# send_weekly_digest imports get_sendgrid_client at module-load time.
import src.notifications.email_digest as _digest_mod  # noqa: E402

_digest_mod.get_sendgrid_client = _clients.get_sendgrid_client

# alice's row was seeded earlier with email='alice@example.com', email_enabled=1.
alice_user = {
    "username": "alice",
    "email": "alice@example.com",
    "email_enabled": 1,
}
email_result = send_weekly_digest(alice_user, week_data)
print(
    f"  send_weekly_digest -> status={email_result.status!r} "
    f"msg_id={email_result.provider_message_id!r}"
)
print(f"  In-memory sent_mails: {len(sent_mails)} entry(ies)")
if sent_mails:
    last = sent_mails[-1]
    print(f"  Last send: to={last.to_email!r} subject={last.subject!r}")
    print(f"  Status code: {last.status_code}, headers: {dict(last.headers)}")

section("SendGrid stub JSONL audit log (contents of data/sendgrid_stub.jsonl)")
if _STUB_SG_LOG.exists():
    for line in _STUB_SG_LOG.read_text(encoding="utf-8").splitlines():
        print(f"  {line}")
else:
    print("  (no entries)")

section("notification_log rows for the email channel")
conn = get_db()
try:
    email_rows = conn.execute(
        "SELECT id, username, channel, digest_week, status, provider_message_id "
        "FROM notification_log WHERE channel = 'email' ORDER BY id"
    ).fetchall()
finally:
    conn.close()
for r in email_rows:
    print(
        f"  id={r['id']} user={r['username']} week={r['digest_week']} "
        f"status={r['status']} msg_id={r['provider_message_id']}"
    )

section("Summary")
print(f"  Total SMS cards considered:  {len(SAMPLE_CARDS)}")
print(
    f"  Cards triggering SMS:        {sum(1 for c in SAMPLE_CARDS if c['severity'].upper() in ('HIGH', 'CRITICAL'))}"
)
print(f"  notification_log rows:       {len(rows)}")
print(
    f"  Stub audit JSONL lines:      {sum(1 for _ in _STUB_LOG.read_text().splitlines()) if _STUB_LOG.exists() else 0}"
)
print(f"  Email HTML size:             {len(html)} bytes ({len(html.splitlines())} lines)")
print(f"  Email plain-text size:       {len(text)} bytes ({len(text.splitlines())} lines)")
print(
    f"  Email stub sends:            {len(sent_mails)} | audit lines: "
    f"{sum(1 for _ in _STUB_SG_LOG.read_text().splitlines()) if _STUB_SG_LOG.exists() else 0}"
)

banner("DEMO COMPLETE — no real Twilio or SendGrid calls were made.")
print("Re-run anytime with: uv run python scripts/stub_e2e_demo.py")
print("Persistent JSONL audit (when run with real .env): data/twilio_stub.jsonl")
print("                                                  data/sendgrid_stub.jsonl")
