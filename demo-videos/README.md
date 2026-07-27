# demo-videos — landing-page feature clips

Silent, Playwright-driven screen recordings of each Emissary feature, made with the
[auto-loom](../.claude/skills/auto-loom/SKILL.md) skill and embedded on the marketing
landing page (`frontend/src/pages/landing/`).

## How it works

auto-loom paces each storyboard beat off an audio clip's duration. We run it **without
ElevenLabs**: `silent-audio.mjs` fabricates a near-silent noise clip per beat (from the
beat's `durationSec`) plus the `durations.json` the renderer reads, then `run.mjs
--skip-audio --no-captions` records the browser and muxes the MP4. `transcode.sh` strips
the audio entirely and emits web-ready 1280w H.264 into `frontend/public/videos/`.

To add narration later: get an ElevenLabs key into the skill's `.env.local`, drop the
`durationSec` fields, and run `run.mjs` without `--skip-audio` — the beat `text` lines
were written to work as voiceover.

## Recording

```bash
# 1. Build the frontend and serve it (videos record the real production build)
cd frontend && npm run build && cd ..
uv run uvicorn src.api:app --port 8000

# 2. Pre-warm state the clips depend on:
#    - ask-anything: run the canonical Fujian Jinhua query once (live, ~5 min);
#      recording then captures the fast cache replay.
#    - risk-feed: click "Refresh feed" once so cards exist.
#    - wargame: WARGAME_ENABLED=1 with Postgres/Redis up.

# 3. Record + publish
cd demo-videos
bash record-all.sh            # or: bash record-all.sh <slug>
bash transcode.sh
```

Storyboards live in `beats/*.beats.json` (auto-loom v2 format plus our per-beat
`durationSec`). They log in via the scripted `auth` block using `.env.local`
(`DEMO_USERNAME` / `DEMO_PASSWORD`) — the login never appears on camera.

## Local engine extensions

The vendored skill copy at `.claude/skills/auto-loom` carries two local patches:

- `type` / `press` actions (visible typing for the Ask Anything clip) in
  `scripts/render.mjs` + `scripts/validate.mjs`
- highlight color switched from green to brand blue `#5b9bd4`, sharp corners, in
  `assets/presenter.js`

Selectors are `data-vn` hooks added to the app JSX (`ask-input`, `ask-run`, `swarm`,
`graph-views`, `feed-refresh`, `risk-card`, `kpis`, `activity-log`, `coa-board`,
`brief-table`, `wg-play-demo`).
