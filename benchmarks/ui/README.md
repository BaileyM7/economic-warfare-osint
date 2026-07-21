# Playwright UI layer — the before/after picture of "Ask Anything"

Drives the real Ask Anything box in a browser and captures what a user actually experiences:
submit → "Executive Assessment" wall-clock, a full-page **screenshot + video**, and the completed
assessment JSON (via network intercept) for richness scoring. Standalone — not in the app bundle.

## Setup (one-time)

```bash
cd benchmarks/ui
npm install          # installs @playwright/test
npx playwright install chromium
```

> Node isn't always on `PATH` here — if `npm` isn't found, use your Node 20 (e.g.
> `export PATH="$HOME/.local/node20/bin:$PATH"`).

## Run

```bash
# AFTER (features on), against the live site:
BASE_URL=https://YOUR-SITE EMISSARY_USER=demo EMISSARY_PASS=demo123 BENCH_LABEL=after \
  npx playwright test

# BEFORE (features off deploy):
BASE_URL=https://YOUR-SITE EMISSARY_USER=demo EMISSARY_PASS=demo123 BENCH_LABEL=before \
  npx playwright test
```

Outputs land in `benchmarks/results/ui/`:
- `askAnything-{label}.png` — the picture
- `askAnything-{label}.json` — perceived latency + structural richness + `was_replay`
- `suggest-{label}.json` — whether the "Did you mean?" chip appeared
- `playwright-report/` — the HTML report with the **video** of the run

A cold analysis is ~6–7 min; the test timeout is 15 min. Use a warmed demo query (the default) for a
fast replay when you just want the picture.

## Scope

The current frontend sends only `{query}` and ignores `session_id`/`replayed_from`, so this layer
fairly benchmarks **analyze latency/richness + the suggest chip**. The memory (Monday→Friday),
session-threading, and auto-replay before/after are measured by the API harness (`../run.py`) until
the frontend is wired to send `session_id` and surface those fields.
