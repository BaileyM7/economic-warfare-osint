#!/usr/bin/env bash
# record-all.sh — record every feature demo clip with auto-loom in silent mode.
#
# Prereqs:
#   - Backend serving the BUILT frontend on 127.0.0.1:8000
#     (uv run uvicorn src.api:app --port 8000; cd frontend && npm run build first)
#   - .claude/skills/auto-loom set up (scripts/setup.sh done, Chromium installed)
#   - ffmpeg/ffprobe on PATH (~/.local/ffmpeg/bin on this machine)
#   - For ask-anything: the demo query cache warmed (run the canonical query once)
#   - For wargame: WARGAME_ENABLED=1 with Postgres/Redis up
#
# Usage:
#   bash record-all.sh              # all slugs
#   bash record-all.sh risk-feed    # one slug

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
SKILL="$REPO/.claude/skills/auto-loom"
export PATH="$HOME/.local/node20/bin:$HOME/.local/ffmpeg/bin:$PATH"

SLUGS=("$@")
if [ ${#SLUGS[@]} -eq 0 ]; then
  SLUGS=(ask-anything risk-feed knowledge-graph monitoring coa wargame)
fi

FAILED=()
for slug in "${SLUGS[@]}"; do
  beats="$HERE/beats/$slug.beats.json"
  out="$HERE/demo-video-out/$slug"
  echo ""
  echo "=============================== $slug ==============================="
  node "$HERE/silent-audio.mjs" --beats "$beats" --out "$out/audio" || { FAILED+=("$slug (audio shim)"); continue; }
  # ask-anything's swarm/graph selectors only exist after the query is
  # submitted mid-recording, so the static preflight can't resolve them.
  extra=()
  if [ "$slug" = "ask-anything" ]; then
    extra+=(--skip-preflight)
  fi
  # run.mjs's final verify compares mp4 length against narration length; the
  # recording legitimately runs longer in silent mode (page-load lead-in that
  # transcode.sh trims), so treat "output exists" as success.
  node "$SKILL/scripts/run.mjs" \
    --beats "$beats" \
    --out "$out" \
    --env "$HERE/.env.local" \
    --skip-audio --no-captions "${extra[@]}"
  if [ ! -f "$out/$slug-demo.mp4" ]; then
    FAILED+=("$slug (record)")
    continue
  fi
done

echo ""
if [ ${#FAILED[@]} -gt 0 ]; then
  echo "FAILED: ${FAILED[*]}"
  exit 1
fi
echo "All recordings done. Next: bash transcode.sh"
