# Emissary — Redis features: before/after benchmark

Measures the **effectiveness (richness + quality) and response time** of the Redis-backed features
(vector entity search, agent memory, semantic cache, shared analysis store) and produces a
**BEFORE (features off) vs AFTER (features on)** comparison. Two layers:

- **API harness** (`run.py` + `compare.py`) — the systematic metrics across all features, incl. the
  memory/session features the UI can't drive yet.
- **Playwright UI layer** (`ui/`) — drives the real "Ask Anything" box for a visual before/after of
  what a user actually experiences (latency + rendered richness + screenshots).

Nothing here is part of the app bundle or the `pytest tests/` suite — it's a standalone tool.

## Before/after is a config toggle (same code)

Every feature reads an env var, probes capability, and falls back. `GET /api/health` reports the
live state of every toggle — the **ground-truth oracle**, stamped into every result set. The current
live prod runs *older* code without the new endpoints, so a fair before/after needs **this branch
deployed** and run in two states:

| env | BEFORE (off) | AFTER (on) |
|---|---|---|
| `REDIS_URL` | unset / Valkey | a real **Redis 8** (FT.* + JSON) |
| `VOYAGE_API_KEY` | unset | set |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMS` | — | `voyage-large-2` / `1536` |
| `SIMILARITY_BACKEND` | `lexical` | `hybrid` |
| `ANALYSES_BACKEND` | `memory` | `redis` |
| `EMISSARY_SEMANTIC_REPLAY` | `0` | `1` |

`compare.py` warns if the two runs report the *same* state (so you can't diff two "afters").

## Run it

```bash
# 1. capture BEFORE (features off) — cheap scenarios shown; add s3,s4,s5 for the full run
python -m benchmarks.run --base-url https://YOUR-SITE --label before \
    --user demo --password demo123 --scenarios s1,s2,s3,s4,s5 --out benchmarks/results/before.json

# 2. flip the service to the AFTER env, redeploy, then capture AFTER
python -m benchmarks.run --base-url https://YOUR-SITE --label after \
    --user demo --password demo123 --scenarios s1,s2,s3,s4,s5 --out benchmarks/results/after.json

# 3. diff into a report (runs the blind pairwise LLM judge on the analyze answers)
python -m benchmarks.compare benchmarks/results/before.json benchmarks/results/after.json \
    --out benchmarks/results/report.md
```

The LLM judge uses your local `ANTHROPIC_API_KEY` (independent of the target site). Pass `--no-judge`
to skip it.

## Scenarios

| id | what | cost |
|---|---|---|
| `s1` | entity similarity — does SMIC outrank the "Jinhua Group Holdings" decoy? | cheap |
| `s2` | semantic suggest — paraphrase hit-rate + entity-swap **safety** | cheap |
| `s3` | full cold analyses (4 demo queries) — structural richness + latency + judge | **slow, $$** |
| `s4` | warm replay latency (re-ask a warmed query) | cheap |
| `s5` | long-term memory Monday→Friday — recall@k + Friday-answer richness | **slow, $$** |

**Cost/time:** a full cold before/after ≈ 4 queries × 2 states × ~6–7 min ≈ ~1 hour + Anthropic +
Voyage. Run `s3`/`s5` once per state. Voyage's **free tier is ~3 req/min** — it throttles the AFTER
embeddings; use a paid key for a clean run, or accept degradation (the harness records the real
`backend_used`, so a throttled AFTER is visible rather than silently wrong).

## Smoke-test locally first (cheap)

```bash
scripts/dev-redis.sh up
# BEFORE (no Redis/Voyage): start the app with nothing set, then:
python -m benchmarks.run --base-url http://localhost:8000 --label before \
    --user analyst --password demo --scenarios s1,s2 --no-judge
# AFTER: restart the app with REDIS_URL + VOYAGE_API_KEY + the AFTER env, then re-run --label after
python -m benchmarks.compare benchmarks/results/before.json benchmarks/results/after.json --no-judge
```

Confirm the two `/api/health` stamps differ (before: lexical/memory; after: redis/vector).

## Playwright UI layer

See `ui/README.md`. It logs in via the real UI, runs a demo query in the Ask Anything box, captures
the user-perceived wall-clock + a screenshot + the DebugPanel JSON, and intercepts the completed
analyze payload. **Scope:** the current frontend sends only `{query}` and ignores the new
`session_id`/`replayed_from` fields, so the UI layer fairly benchmarks **analyze + suggest** only;
the memory/session before/after stays in the API harness until the frontend is wired to send
`session_id`.
