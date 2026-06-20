# Phase 3 — Frontend Contract (backend → UI handoff)

Backend is done + verified for the Tuesday demo. This is the contract the UI
builds against. **No frontend was touched by the backend work** — it's all yours.

Two features:
1. **Ask-Anything + live agent swarm** — render the orchestrator's structured events.
2. **Self-serve briefs** — subscribe an email + "send me a brief now".

---

## 1. Ask-Anything + Agent Swarm

Open-ended search already runs the orchestrator. Nothing new to call — just poll
the new `events` field and render it.

### Flow (unchanged endpoints)
1. `POST /api/analyze` `{ "query": "<free text>" }` → `{ "analysis_id": "abcd1234", "status": "running" }`
2. Poll `GET /api/analyze/{analysis_id}` (~**0.8–1s** while `status === "running"`).
3. When `status === "completed"`, render `result` (the existing `OrchestratorView` already does this) + `FollowUpBar`.

### NEW: `AnalysisStatus.events` (drives the swarm)
`GET /api/analyze/{id}` now returns an **`events: object[]`** array (append-only;
poll returns the full list each time — diff by index or just re-render). Three shapes:

**phase** — pipeline stage transitions:
```json
{ "type": "phase", "name": "decompose" | "execute" | "synthesize" | "complete", "status": "start" | "done" }
```

**plan** — the decomposition (the "thinking"; emitted once after decompose):
```json
{ "type": "plan", "steps": [
  { "step": 1, "description": "Establish sanctions status + corporate identity ...",
    "tools": ["check_sanctions_status", "search_sanctions", "search_entity", "sayari_resolve",
              "get_supply_chain_exposure", "get_commodity_trade", "get_bilateral_trade",
              "get_risk_profile", "search_events"] },
  { "step": 2, "description": "Map ownership / beneficial owners ...", "tools": [...] }
]}
```

**tool** — one **running** then one **done|error** per tool call (the agents lighting up):
```json
{ "type": "tool", "step": 1, "name": "check_sanctions_status", "domain": "sanctions",
  "task": "Establish sanctions status ...", "status": "running" }
{ "type": "tool", "step": 1, "name": "check_sanctions_status", "domain": "sanctions",
  "status": "done", "summary": "6 results, medium", "ms": 873,
  "detail": { "items": ["Entity List (EL) - Bureau of Industry and Security"],
              "confidence": "high", "sources": ["Trade.gov CSL", "OFAC SDN"] } }
```
- Correlate running↔done by **`(name, step)`**. `task` is on the **running** event.
- `status`: `"running"` → `"done"` or `"error"` (`summary: "error"` on failure — render red, **not fatal**).
- `ms` = tool duration. `summary` = a chip like `"6 results, medium"` / `"4 results, high"` / `"error"`.
- **`detail`** (NEW — for the live "intelligence feed"): each agent's actual findings, uniform across all tools.
  `items: string[]` = up to 5 labelled rows (e.g. headlines, sanctions matches, owners); optional
  `confidence: string`, `sources: string[]` (≤3), and `error: string` on failure. Present on **done|error** events.
- **`domain`** (for the colour badges): one of
  `sanctions`, `corporate`, `market`, `trade`, `geopolitical`, `economic`, `sayari`, `news` (P2), `unknown`.

### Suggested UI (reuse what exists — no new deps)
- Show the **plan** first (steps + tool chips) = the swarm roster.
- Each `tool` event = an agent card/pill: pending → **running (pulse)** → done (chip `summary` + `ms`) / error.
  Group/colour by `domain`. Reusable: `wargame/components/ui/Loader` (pulse rings),
  `EventTimeline`, `DomainBadge`, `ExplainabilityCard`; CSS `pulse-glow` / `slide-in-*` in `index.css`.
- Drive the `[1/4]` style progress bar off the `phase` events.
- On `completed` → `OrchestratorView` (result) + `FollowUpBar`. (`progress: string[]` still exists for a plain log.)
- **Live intelligence feed (NEW):** render `detail.items` from each **done** event as they arrive — a running
  list below/within the swarm of "what each agent found" (domain + `name` + bullets + `sources`), so intel
  accumulates live instead of only appearing at the end. Each poll returns the full append-only `events`, so
  diff by `(name, step)` and append the newly-done ones.

> Real example payloads are in this doc; I verified them on a live `POST /api/analyze "What happens if we sanction Fujian Jinhua?"` run.

---

## 2. Self-serve briefs

All auth'd (send the bearer token). Live-verified with `SENDGRID_STUB_MODE`.

**Subscribe** — `POST /api/subscribe`
```json
// body
{ "email": "me@example.com", "phone": "+12025550123" }   // phone optional
// 200
{ "status": "subscribed", "username": "demo", "email": "me@example.com" }
// 400 if email invalid
```
Registers the logged-in user's email (`email_enabled=1`). Keyed by the auth'd username.

**Send a brief now** — `POST /api/brief/send-now` (no body)
```json
// 200 — returns the REAL send result (no swallowing)
{ "status": "sent", "email": "me@example.com", "error": null, "provider_message_id": "sg-..." }
// other statuses: "failed" (+ error), "skipped_kill_switch", "skipped_disabled", "skipped_allowlist"
// 400 if no email on file (subscribe first)
```
Build + send synchronously (a few seconds — show a spinner). Surface `status`/`error` to the user.

**Diagnostics (optional admin/debug)** — `GET /api/notifications/diagnostics`
Requires header `X-Cron-Token: <NOTIFICATIONS_CRON_TOKEN>` (not bearer auth). Returns booleans:
`notifications_enabled`, `sendgrid.{api_key_set, stub_mode, from_email, client_available}`,
`allowlist_mode` (`all_enrolled|restricted`), `cron_token_set`, `eligible_users`, `would_send`.

### Demo-config notes (so a real send works on stage)
- For a **real** email: `NOTIFICATIONS_ENABLED=true`, `SENDGRID_API_KEY` set, **`NEWSLETTER_FROM_EMAIL` must be a VERIFIED SendGrid sender** (else SendGrid 403 → `status:"failed"`), `SENDGRID_STUB_MODE` off.
- For a **safe demo without a live account**: run with `SENDGRID_STUB_MODE=true` → `status:"sent"`, logged to `data/sendgrid_stub.jsonl`.
- ⚠️ Your `.env` currently has a **non-empty `NOTIFICATIONS_ALLOWLIST`** (`allowlist_mode: restricted`) — only those usernames receive briefs. To let any subscriber receive on stage, **empty the allowlist** (now correctly means "allow everyone") or add the demo username to it.

---

## Backend changes (FYI; all behind these contracts, suite green at 278)
- `src/orchestrator/main.py` — `analyze(..., event_callback=...)`; per-tool + plan + phase events.
- `src/orchestrator/tool_registry.py` — `tool_domain(name)`.
- `src/routers/orchestrator.py` — `AnalysisStatus.events`; store wiring.
- `src/notifications/clients.py` — allowlist bugfix (empty = allow-all).
- `src/routers/notifications.py` — `GET /diagnostics`.
- `src/routers/briefs.py` (new) — `/api/subscribe`, `/api/brief/send-now`.
- Tests: `tests/test_orchestrator_events.py`, `tests/notifications/test_allowlist.py`, route-inventory + auth-contract updated.
