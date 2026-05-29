# Notifications Setup

## Overview

Emissary ships two notification channels backed by Twilio (SMS) and SendGrid
(email). SMS is **event-driven**: every `/api/risk-feed/refresh` enqueues a
background dispatch that fires one message per HIGH or CRITICAL card for
opt-in users. Email is **scheduled**: a Render Cron Job hits
`/api/notifications/send-weekly-digest` every Monday at 12:00 UTC and one
weekly brief goes to every user with `email_enabled = 1`.

## Architecture at a glance

- **SMS path**: `src/routers/risk_feed.py` -> background task ->
  [`dispatch_sms_for_new_cards`](../src/notifications/dispatcher.py) ->
  [`send_sms_alert`](../src/notifications/sms.py) -> Twilio. Dedupe via
  `notification_log.card_id`; per-user daily cap via
  [`caps.can_send_sms`](../src/notifications/caps.py).
- **Email path**: Render cron -> `curl` to `/api/notifications/send-weekly-digest`
  with `X-Cron-Token` header -> [`send_weekly_digest`](../src/notifications/email_digest.py)
  per opt-in user via FastAPI `BackgroundTasks`.
- **Safety**: every send is double-gated. `NOTIFICATIONS_ENABLED=false` is a
  hard kill-switch that turns both client factories into `None`. A
  comma-separated `NOTIFICATIONS_ALLOWLIST` of usernames further restricts who
  receives messages even when the feature is on — useful during the demo
  window when you do not want stray users in the DB to get pinged. See
  [`src/notifications/clients.py`](../src/notifications/clients.py).
- **Inbound SMS** (STOP / HELP / START) is handled at
  `/api/notifications/twilio/sms-webhook`. Twilio request signatures are
  validated via HMAC-SHA1 keyed by `TWILIO_AUTH_TOKEN`.

## Quick reference: every env var

All names below are read in [`src/common/config.py:73-114`](../src/common/config.py#L73-L114).
"render.yaml" column refers to lines in [`render.yaml`](../render.yaml).

| Name | Default | What it does | render.yaml | Required to send? |
|---|---|---|---|---|
| `NOTIFICATIONS_ENABLED` | `false` | Master kill-switch. When `false`, both `get_twilio_client()` and `get_sendgrid_client()` return `None` and every send short-circuits as `skipped_kill_switch`. | L44-45 (`value: "true"`) | Yes |
| `TWILIO_ACCOUNT_SID` | `""` | Twilio account SID (starts `AC...`). Without it, real Twilio client construction is skipped. | L46-47 (`sync: false`) | Yes (real SMS) |
| `TWILIO_AUTH_TOKEN` | `""` | Twilio auth token. Also the HMAC key used to validate inbound webhook signatures — webhook returns 503 if unset. | L48-49 (`sync: false`) | Yes (real SMS + webhook) |
| `TWILIO_FROM_PHONE` | `""` | E.164 sender number (e.g. `+15125551234`). Used as `from_` in `messages.create`. | L50-51 (`sync: false`) | Yes (real SMS) |
| `SENDGRID_API_KEY` | `""` | SendGrid API key (starts `SG.`). Without it the real SendGrid client is skipped. | L52-53 (`sync: false`) | Yes (real email) |
| `NEWSLETTER_FROM_EMAIL` | `noreply@emissary.demo` | `From:` address on the weekly digest. Must be a sender SendGrid has verified or the send will be rejected. | L54-55 (`sync: false`) | Yes (real email) |
| `NEWSLETTER_FROM_NAME` | `Emissary Weekly Brief` | `From:` display name. | L56-57 (`value: "Emissary Weekly Brief"`) | No |
| `SMS_DAILY_CAP_PER_USER` | `3` | Per-user 24h send ceiling enforced in [`caps.can_send_sms`](../src/notifications/caps.py). Excess sends are logged as `skipped_cap`. | L58-59 (`value: "3"`) | No |
| `NOTIFICATIONS_CRON_TOKEN` | `""` | Shared-secret required in the `X-Cron-Token` header on `/api/notifications/send-weekly-digest`. Missing token returns **503**; wrong token returns **403** (see [`notifications.py:43-57`](../src/routers/notifications.py#L43-L57)). | L60-61 (`generateValue: true`) | Yes (cron) |
| `NOTIFICATIONS_ALLOWLIST` | `""` | Comma-separated **usernames** (not phone numbers, not emails) allowed to receive notifications. Empty = nobody passes the gate; populated = exact-match check via [`is_user_allowlisted`](../src/notifications/clients.py). | L62-63 (`sync: false`) | Yes (production gate) |
| `APP_BASE_URL` | `https://emissary.onrender.com` | Used (a) to construct `/settings` and `/unsubscribe` links in outgoing email and (b) to reconstruct the URL Twilio signed when validating the inbound webhook. Must match the externally reachable URL exactly. | L64-65 (`sync: false`) | Yes (webhook + email links) |
| `TWILIO_STUB_MODE` | `false` | Local-dev only. Returns [`StubTwilioClient`](../src/notifications/stub_client.py) instead of the real client. Honors the kill-switch (requires `NOTIFICATIONS_ENABLED=true`). **Never set in prod.** | not in render.yaml | No (dev only) |
| `SENDGRID_STUB_MODE` | `false` | Local-dev only. Returns [`StubSendGridClient`](../src/notifications/stub_client_sendgrid.py). Same dual-guard semantics as `TWILIO_STUB_MODE`. **Never set in prod.** | not in render.yaml | No (dev only) |

> Note on the allowlist gate: it is checked **first** in both
> `send_sms_alert` and `send_weekly_digest`. If `NOTIFICATIONS_ALLOWLIST` is
> empty, nobody receives anything in production — even when everything else
> is configured. This is intentional for the demo deployment.

## Local development (no real credentials)

The stub clients let you exercise the full pipeline end-to-end with zero real
API calls and zero spent credit. They write to in-memory lists and to
`data/twilio_stub.jsonl` / `data/sendgrid_stub.jsonl` so you can inspect what
*would* have been sent.

### Minimal `.env`

```dotenv
NOTIFICATIONS_ENABLED=true
TWILIO_STUB_MODE=true
SENDGRID_STUB_MODE=true
TWILIO_FROM_PHONE=+15005550006
NOTIFICATIONS_ALLOWLIST=alice
APP_BASE_URL=https://emissary.demo
# Optional — without it, the weekly digest uses a deterministic
# fallback for the opening synthesis instead of calling Claude.
ANTHROPIC_API_KEY=sk-ant-...
```

`+15005550006` is Twilio's documented "magic success" test number — the stub
mirrors the rest of the magic-number error codes (see
[`stub_client.py:55-61`](../src/notifications/stub_client.py#L55-L61)) so you
can also exercise failure paths by setting `TWILIO_FROM_PHONE=+15005550001`
(invalid-number error 21211), etc.

### Run the canonical demo

```bash
uv run python scripts/stub_e2e_demo.py
```

PowerShell equivalent:

```powershell
uv run python scripts/stub_e2e_demo.py
```

The script ([`scripts/stub_e2e_demo.py`](../scripts/stub_e2e_demo.py)) boots
no web server. It seeds a temp SQLite DB with a user `alice`, dispatches a
sample set of risk cards through the SMS pipeline, then renders and
"sends" the weekly digest. Expected output covers:

1. Formatted SMS bodies (with `[HIGH] / [CRITICAL]` prefix, char count, ASCII check)
2. Stub Twilio JSONL audit log lines
3. `notification_log` rows for each SMS send
4. A dedupe demonstration (second dispatch sends 0 messages)
5. The rendered HTML + plain-text email digest
6. SendGrid stub send + `notification_log` row for the email channel

Audit logs end up at:

- `data/twilio_stub.jsonl` — one JSON object per stubbed SMS send
- `data/sendgrid_stub.jsonl` — one JSON object per stubbed email send

Both files are append-only; the stubs never truncate them. Delete the files
to reset the audit trail between runs.

### Verifying the API endpoints locally

Start the server (`uv run uvicorn src.api:app --reload`) and exercise the
user-facing preferences endpoints with an auth cookie/token:

```bash
# Read defaults (no row yet -> Preferences with all-false / defaults)
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/me/preferences

# Set up your dev profile
curl -X PUT -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","phone_number":"+15005550006",
       "sms_enabled":true,"email_enabled":true,"timezone":"America/New_York"}' \
  http://localhost:8000/api/me/preferences
```

`phone_number` is validated against the E.164 pattern
`^\+[1-9]\d{1,14}$` — see [`notifications.py:268`](../src/routers/notifications.py#L268).

## Twilio: account setup

1. Sign up at <https://www.twilio.com/try-twilio>. A trial account is
   sufficient for SMS validation.
2. From the console homepage <https://console.twilio.com/>, copy:
   - **Account SID** -> `TWILIO_ACCOUNT_SID`
   - **Auth Token** -> `TWILIO_AUTH_TOKEN`
3. Get a phone number: <https://console.twilio.com/us1/develop/phone-numbers/manage/incoming>.
   Trial accounts get one free US number. Copy it in E.164 format
   (e.g. `+15125550199`) into `TWILIO_FROM_PHONE`.
4. **Trial-mode caveat**: a trial account can only send to *verified* phone
   numbers. Verify your test number under
   <https://console.twilio.com/us1/develop/phone-numbers/manage/verified>.
   Without this, real sends will fail with error 21608.
5. (Optional) For automated CI tests against real Twilio test creds, use the
   test-credential SID/Token pair (separate from your live creds, found at
   <https://console.twilio.com/us1/account/keys-credentials/api-keys>). The
   stub client mirrors their magic-number behavior, so most tests do not
   need this.

## Twilio: webhook configuration

When a user replies `STOP` / `HELP` / `START` to a message Emissary sent,
Twilio POSTs to a webhook URL you configure. The handler is at:

```
POST {APP_BASE_URL}/api/notifications/twilio/sms-webhook
```

Configure it in the Twilio console:

1. Navigate to **Phone Numbers -> Manage -> Active numbers** and click your
   sending number.
2. Under **Messaging Configuration**, set:
   - "A message comes in" -> **Webhook**
   - URL: `https://<your-domain>/api/notifications/twilio/sms-webhook`
   - HTTP method: **HTTP POST**
3. Save.

Signature validation behavior ([`notifications.py:153-198`](../src/routers/notifications.py#L153-L198)):

- Request is rejected with **503** when `TWILIO_AUTH_TOKEN` is empty on the
  server (refuse rather than silently accept).
- Request is rejected with **403** when the `X-Twilio-Signature` header is
  missing or does not match.
- The signature is computed over `APP_BASE_URL + request.path + query` plus
  all POST form fields — so `APP_BASE_URL` **must** match exactly the URL
  Twilio is calling (incl. `https://` vs `http://`, no trailing slash
  inconsistencies). Render serves on https; if `APP_BASE_URL` is
  `http://...` the signature will not validate.
- `TWILIO_STUB_MODE=true` bypasses signature validation for local-dev only.

Inbound keyword handling ([`notifications.py:201-258`](../src/routers/notifications.py#L201-L258)):

| Keyword (case-insensitive, first word of body) | Effect | Reply |
|---|---|---|
| `STOP` | Sets `sms_enabled=0`, stamps `unsubscribed_at` | "You have been unsubscribed..." |
| `START` or `UNSTOP` | Sets `sms_enabled=1`, clears `unsubscribed_at` | "Subscribed. You will receive..." |
| `HELP` | No state change | Help text incl. preferences URL |
| Anything else from a known number | No state change | Help text |
| Anything from an unknown phone | No state change | "Number not recognized..." |

## SendGrid: sender verification

SendGrid will reject sends whose `From:` address is not a verified sender.
For a demo deployment, single-sender verification is sufficient (no domain
DNS setup needed).

1. Sign up at <https://signup.sendgrid.com/>. Free tier covers 100 emails/day.
2. **Verify a single sender**:
   <https://app.sendgrid.com/settings/sender_auth/senders/new>. Use the
   address you intend to put in `NEWSLETTER_FROM_EMAIL`. SendGrid emails you
   a verification link.
3. **Create an API key**:
   <https://app.sendgrid.com/settings/api_keys>. Choose **Restricted Access**
   with **Mail Send: Full Access** (and nothing else). Copy the key
   (`SG....`) once — it is not retrievable later — into `SENDGRID_API_KEY`.
4. (Optional, recommended for production beyond the demo) Set up domain
   authentication (SPF / DKIM / DMARC) at
   <https://app.sendgrid.com/settings/sender_auth>. Single-sender mail
   without domain auth is more likely to land in spam.

## Render: deployment env vars

[`render.yaml`](../render.yaml) declares one web service (`emissary`) and one
cron service (`emissary-weekly-digest`). The notifications block on the web
service is lines 43-65.

### On the `emissary` web service (Render dashboard -> Environment)

Vars marked `sync: false` in render.yaml are **not** stored in the blueprint
and **must** be entered manually in the dashboard:

- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_FROM_PHONE`
- `SENDGRID_API_KEY`
- `NEWSLETTER_FROM_EMAIL`
- `NOTIFICATIONS_ALLOWLIST`
- `APP_BASE_URL` (set to the actual deployed URL, e.g. `https://emissary.onrender.com`)

Vars with `value:` literals are baked into the blueprint and need no manual
action: `NOTIFICATIONS_ENABLED=true`, `NEWSLETTER_FROM_NAME=Emissary Weekly Brief`,
`SMS_DAILY_CAP_PER_USER=3`.

`NOTIFICATIONS_CRON_TOKEN` uses `generateValue: true` — Render generates a
random value at service-creation time and exposes it as an env var. You do
not need to set it manually; you also cannot read it from the blueprint, only
from the dashboard.

### On the `emissary-weekly-digest` cron service

The cron service does not need any secrets configured manually. Its two env
vars are pulled by cross-reference from the web service (render.yaml L80-90):

```yaml
- key: NOTIFICATIONS_CRON_TOKEN
  fromService: { type: web, name: emissary, envVarKey: NOTIFICATIONS_CRON_TOKEN }
- key: APP_BASE_URL
  fromService: { type: web, name: emissary, envVarKey: APP_BASE_URL }
```

This means the cron token only has to be configured in one place. Rotating
the token on the web service automatically propagates to the cron on the
next deploy.

### Schedule

The cron runs `0 12 * * 1` — Mondays at 12:00 UTC (≈ 07:00 ET in DST, 08:00 ET
in EST). The container is `docker.io/curlimages/curl:8.10.1` and the start
command is a single `curl -X POST` with the `X-Cron-Token` header
(render.yaml L75-79).

## Validation checklist (post-deploy)

Walk through this after the first deploy with real credentials in place.

| # | Step | Pass criteria |
|---|---|---|
| 1 | **Web service up.** `curl https://<app>/health` | 200 with `{"status":"ok"}` (or whatever the existing health endpoint returns) |
| 2 | **Preferences defaults.** `curl -H "Authorization: Bearer <token>" https://<app>/api/me/preferences` | 200, returns Preferences with all-false / defaults if the user has no row yet |
| 3 | **Create a test user with real contact info.** `PUT /api/me/preferences` with your real email + E.164 phone, both `enabled=true`. Add the username to `NOTIFICATIONS_ALLOWLIST` and redeploy (or set in dashboard and restart). | 200 echoes back the Preferences object |
| 4 | **Trigger SMS via the feed.** Call `POST /api/risk-feed/refresh`. Wait ~5 seconds for the BackgroundTask. | One SMS arrives per HIGH/CRITICAL card, up to `SMS_DAILY_CAP_PER_USER` (default 3). Body starts with `[HIGH]` or `[CRITICAL]`. |
| 5 | **Confirm `notification_log` rows.** Connect to `data/emissary.db` (SQLite on Render disk) and `SELECT id, username, channel, status, provider_message_id, card_id FROM notification_log ORDER BY id DESC LIMIT 10;` | One `sent` row per delivered SMS, `provider_message_id` populated with a Twilio SID (`SM...`). |
| 6 | **Reply STOP from the test phone.** Send `STOP` back to the Twilio number. | Auto-reply "You have been unsubscribed..."; `users.sms_enabled` flips to `0` and `unsubscribed_at` is stamped. |
| 7 | **Reply START to re-enable.** | Auto-reply "Subscribed..."; `users.sms_enabled` flips back to `1`. |
| 8 | **Manually trigger weekly digest.** From a shell with the cron token:<br>`curl -X POST -H "X-Cron-Token: $TOKEN" https://<app>/api/notifications/send-weekly-digest` | 200 returns `{"enqueued": N}` where N is the count of `email_enabled=1` users. The digest email arrives within a minute. |
| 9 | **Confirm email logged.** `SELECT id, username, digest_week, status, provider_message_id FROM notification_log WHERE channel='email' ORDER BY id DESC LIMIT 5;` | One `sent` row, `digest_week` matches current ISO week (e.g. `2026-W21`), `provider_message_id` populated from SendGrid's `X-Message-Id` header. |
| 10 | **Cron token rejection.** Repeat step 8 with a wrong token. | 403 `{"detail":"invalid cron token"}` |
| 11 | **Cron token unset.** Temporarily clear the env var on the web service. Repeat step 8. | 503 `{"detail":"NOTIFICATIONS_CRON_TOKEN not configured on server"}`. **Restore the token before continuing.** |

## Troubleshooting

**Webhook returns 403 on every Twilio POST.**
The `X-Twilio-Signature` HMAC did not match. Most common cause:
`APP_BASE_URL` does not exactly match the URL Twilio is calling.
Check for `http://` vs `https://`, missing/extra trailing slash, and the
hostname Twilio is actually hitting (look in the Twilio console's request
log for the message — it shows the exact URL signed). Secondary cause:
`TWILIO_AUTH_TOKEN` was rotated in the Twilio console but not updated on the
web service.

**Webhook returns 503.**
`TWILIO_AUTH_TOKEN` is unset on the server. This is the deliberate "refuse
rather than silently accept" branch in
[`_validate_twilio_signature`](../src/routers/notifications.py#L174-L175).

**Cron returns 503 ("NOTIFICATIONS_CRON_TOKEN not configured on server").**
The token env var is missing on the **web** service (not the cron). Render's
`generateValue: true` only fires on initial service creation. If the var was
deleted post-creation, regenerate one (any random hex string) and set it on
both services, or set it on the web service and trigger a cron redeploy so
the `fromService` lookup re-resolves.

**Cron returns 403 ("invalid cron token").**
Token mismatch. Confirm the cron service's `NOTIFICATIONS_CRON_TOKEN` env var
exactly matches the web service's. After rotating the web token, the cron
needs a redeploy to pick up the new value via `fromService`.

**SMS works locally with the stub but nothing happens on Render.**
Check, in order:
1. `NOTIFICATIONS_ENABLED=true` on the web service (it is, in the blueprint —
   confirm it was not overridden).
2. The test username is in `NOTIFICATIONS_ALLOWLIST` (comma-separated, no
   spaces, exact match). Empty allowlist = nobody passes.
3. The user has both `sms_enabled=1` AND `phone_number` set (in E.164).
4. Daily cap: `SELECT COUNT(*) FROM notification_log WHERE username='...' AND channel='sms' AND status='sent' AND sent_at >= datetime('now','-24 hours')` — if it's >= `SMS_DAILY_CAP_PER_USER`, you'll see `skipped_cap` rows for newer cards.
5. The card was deduped: `SELECT * FROM notification_log WHERE username='...' AND card_id='...'` — if a prior `sent` row exists, the dispatcher will skip on subsequent refreshes.

**Email arrives in spam.**
Single-sender verification gets you delivery, not deliverability.
For better inbox placement, complete domain authentication
(SPF / DKIM / DMARC) at <https://app.sendgrid.com/settings/sender_auth>.
This is out of scope for the demo deployment but required for production
volume.

**SMS body shows lowercase severity (e.g. `[high]`).**
This was a regression fixed by uppercasing in `format_sms_body`
([`sms.py:44`](../src/notifications/sms.py#L44)) and the digest template.
The regression test
[`tests/notifications/test_sms.py:45-53`](../tests/notifications/test_sms.py#L45-L53)
locks it down. If it reappears, that test will fail.

**`dispatch_sms_for_new_cards` runs but sends nothing and logs nothing.**
The earliest gates (`is_user_allowlisted`, opt-in, phone) do **not** write a
`notification_log` row — they short-circuit silently because they reflect
global / per-user state, not a per-card event. To debug, raise the log level
on `src.notifications.dispatcher` and `src.notifications.sms` and re-run the
refresh; both modules log `dispatch_sms ...` summaries at INFO and per-card
gating at DEBUG.

**Twilio trial: "The number +1... is unverified."**
Trial accounts can only send to phone numbers verified at
<https://console.twilio.com/us1/develop/phone-numbers/manage/verified>.
Upgrade the Twilio account (add a payment method) or verify the recipient
number.

## What's intentionally not implemented (yet)

These were scoped out during feature design. None of them are bugs.

- **Twilio Verify (phone OTP) for the preferences PUT.** The demo allowlist
  (`NOTIFICATIONS_ALLOWLIST` username gate) is the operative trust boundary;
  there is no inbound phone-ownership proof.
- **Real-time Twilio delivery status callbacks** (the separate `StatusCallback`
  URL that fires when a message goes `delivered` / `failed` / `undelivered`).
  Current `notification_log.status` reflects the create-time response only;
  a permanent failure after Twilio's queue-time accept will not flip our row.
- **User-configurable digest cadence.** Weekly only. No daily/biweekly option
  in the schema or UI.
- **Daily digest option.** Same.
- **Frontend a11y polish for the preferences modal.** Escape-to-close,
  focus trap, and ARIA labeling are pending.
- **GDPR `DELETE /api/me`** to purge a user's row + their `notification_log`
  history. The data is local SQLite on a single tenant, so this is a known
  gap rather than a live compliance issue, but it would need to ship before
  any external user lands.
- **Per-card deep-link short URLs.** `card["short_url"]` is sourced from the
  card itself; there is no `/r/<id>` redirector or click tracking. SMS bodies
  use whatever the upstream feed provides.
