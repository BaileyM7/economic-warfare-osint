"""Shared in-memory analysis status/result store (process-local; see fragility map F3).

Holds orchestrator analysis state keyed by analysis-id. Lives here so both the
orchestrator router (which writes status/results) and the briefings router (which
reads cached results via `set_analyses_ref`) reference the SAME object without
either importing from `src.api`.

Bounded so that a long-running session or many concurrent analyses can't blow
past the Render instance memory cap (512 MB on starter). Each entry holds full
GDELT/Comtrade JSON payloads in `result`, so the cap matters.
"""

from __future__ import annotations

from typing import Any

from cachetools import TTLCache

ANALYSES_MAX = 50
ANALYSES_TTL_SEC = 3600
ANALYSES_PROGRESS_CAP = 200
# Structured agent-swarm events (one running + one done per tool call). Bounded
# generously — a heavy analysis fires a few dozen tool calls.
ANALYSES_EVENTS_CAP = 400

analyses: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=ANALYSES_MAX, ttl=ANALYSES_TTL_SEC)
