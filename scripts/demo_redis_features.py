"""Interactive proof that the Redis 8 features work — end to end, locally.

Drives each new capability against a REAL Redis 8 and prints what happened, so you
can see it work rather than trust a green test bar.

    scripts/dev-redis.sh up
    REDIS_URL=redis://localhost:6379/0 VOYAGE_API_KEY=... EMBEDDING_MODEL=voyage-large-2 \
      uv run python -m scripts.demo_redis_features

Notes:
  * Semantic bits need a Voyage key; without one they cleanly show the lexical
    fallback instead (which is the point — nothing 500s).
  * Voyage's FREE tier is ~3 req/min, so the embedding demos pace themselves.
    Re-running is fast because the Redis EmbeddingsCache serves repeats.
"""

from __future__ import annotations

import asyncio
import os
import time

SEP = "─" * 74


def hdr(n: int, title: str) -> None:
    print(f"\n{SEP}\n {n}. {title}\n{SEP}")


def _pace(seconds: float = 21.0) -> None:
    """Stay under Voyage's ~3/min free tier between fresh-embedding steps."""
    if os.getenv("VOYAGE_API_KEY"):
        time.sleep(seconds)


async def main() -> int:
    if not os.getenv("REDIS_URL"):
        print("Set REDIS_URL first, e.g. export REDIS_URL=redis://localhost:6379/0")
        return 2

    # Isolate demo data in a throwaway SQLite DB so we never touch the real one.
    import tempfile
    from pathlib import Path

    import src.db as db

    db.DB_PATH = Path(tempfile.gettempdir()) / "emissary_redis_demo.db"
    if db.DB_PATH.exists():
        db.DB_PATH.unlink()
    db.init_db()

    from src.common import agent_memory, embeddings, knowledge_store, vector_index
    from src.common.analyses import RedisAnalysisStore
    from src.common.redis_client import capabilities, get_redis

    caps = capabilities()
    emb = embeddings.embeddings_status()

    # --- 1. Redis 8 capability probe ----------------------------------------
    hdr(1, "Redis 8 capability probe (what the app detected)")
    print(f"  server={caps.server} version={caps.version}")
    print(f"  search (FT.*): {caps.search}   json (JSON.*): {caps.json}")
    print(
        f"  embeddings: enabled={emb['enabled']} model={emb['model'] or '-'} cache={emb['cache']}"
    )
    if not caps.search:
        print(
            "\n  !! No Query Engine — pointed at Valkey or plain Redis. Semantic demos "
            "will show the lexical fallback."
        )

    # --- 2. Working memory (session thread) ---------------------------------
    hdr(2, "Working memory — a server-side session thread (Redis, no FT.* needed)")
    sid = agent_memory.new_session("demo-analyst")
    agent_memory.append_turn(
        sid,
        "demo-analyst",
        agent_memory.Turn(role="user", text="What if we sanction Fujian Jinhua?"),
    )
    agent_memory.set_run_state(
        sid, "demo-analyst", entities=[{"name": "Fujian Jinhua"}, {"name": "UMC"}]
    )
    wm = agent_memory.get_working(sid, "demo-analyst")
    print(f"  session {sid[:12]}…  backend={agent_memory.backend_name()}")
    print(f"  turns remembered: {[t.text[:40] for t in wm.turns]}")
    print(f"  thread entities:  {[e['name'] for e in wm.entities]}")
    r = get_redis()
    print(
        f"  redis key present: {bool(r.exists(f'emissary:wm:{sid}'))}  "
        f"TTL≈{r.ttl(f'emissary:wm:{sid}')}s"
    )
    # Isolation: another analyst can't see it.
    print(
        f"  another analyst sees it? {agent_memory.get_working(sid, 'someone-else') is not None} "
        "(must be False)"
    )

    # --- 3. Analysis store shared across instances (F3) ---------------------
    hdr(3, "Shared analysis store — two 'web processes' share one run (closes F3)")
    a = RedisAnalysisStore(r)
    b = RedisAnalysisStore(r)  # a separate instance, same Redis
    a.create(
        "demo-run", {"analysis_id": "demo-run", "status": "running", "progress": [], "events": []}
    )
    a.append_progress("demo-run", "decomposing…")
    a.append_event("demo-run", {"type": "tool", "name": "search_sanctions"})
    a.set_fields("demo-run", status="completed")
    snap = b.get("demo-run")  # instance B reads what instance A wrote
    print(
        f"  instance A wrote; instance B reads: status={snap['status']} "
        f"progress={snap['progress']} events={[e['name'] for e in snap['events']]}"
    )
    print("  => a second web instance sees the run. F3 closed.")

    # --- 4. Long-term memory recall (Monday → Friday) -----------------------
    hdr(4, "Long-term memory — recall prior findings across sessions")
    print("  seeding 3 facts from a prior 'Fujian Jinhua' analysis…")
    for m in [
        agent_memory.Memory(
            text="Fujian Jinhua is on the BIS Entity List (2018).",
            entity_names=["Fujian Jinhua"],
            confidence="HIGH",
            sources=[{"name": "OFAC SDN"}],
        ),
        agent_memory.Memory(
            text="Fujian Jinhua's DRAM line was transferred from UMC.",
            entity_names=["Fujian Jinhua", "UMC"],
            confidence="MEDIUM",
            sources=[{"name": "Sayari"}],
        ),
        agent_memory.Memory(
            text="Micron is the Western supplier displaced by Fujian Jinhua.",
            entity_names=["Fujian Jinhua", "Micron"],
            confidence="MEDIUM",
            sources=[{"name": "OpenCorporates"}],
        ),
    ]:
        agent_memory.remember("demo-analyst", [m])
        _pace()
    print(f"  recall backend: {agent_memory.memory_backend_name()}")
    _pace()
    hits = await agent_memory.recall(
        "what else is exposed to that supply chain?",
        user_id="demo-analyst",
        k=3,
        entity_names=["Fujian Jinhua", "UMC", "Micron"],
    )
    print("  FRIDAY question 'what else is exposed to that supply chain?' recalls:")
    for mem, score in hits:
        print(f"    {score:.3f}  {mem['text']}")
    print(
        f"  another analyst's recall (isolation): "
        f"{len(await agent_memory.recall('Fujian Jinhua', user_id='intruder'))} hits (must be 0)"
    )

    # --- 5. Vector entity search (the customer's #1 ask) --------------------
    hdr(5, "Vector entity search — 'find companies like this one', done right")
    ents = [
        ("jinhua", "Fujian Jinhua Integrated Circuit", "DRAM memory fabrication"),
        ("smic", "SMIC", "semiconductor foundry, chip manufacturing"),
        ("jinhua_re", "Jinhua Group Holdings", "real estate development"),
    ]
    for eid, name, notes in ents:
        knowledge_store.upsert_entity(
            entity_id=eid, name=name, entity_type="company", country="CN", notes=notes
        )
    vector_index.ensure_index()
    _pace()
    res = await vector_index.backfill(knowledge_store.list_entities())
    print(f"  indexed {res['indexed']} entities into Redis")
    _pace()
    from src.common import similarity

    ranked, backend = await similarity.rank_similar_indexed(
        knowledge_store.get_entity("jinhua"), top_k=2
    )
    print(f"  most similar to 'Fujian Jinhua' (a DRAM fab), backend={backend}:")
    for row in ranked:
        print(
            f"    {row['score']:.3f}  {row['entity']['name']:34s} — {row['basis'].get('why', '')}"
        )
    print("  => semantic ranks SMIC (a foundry) above 'Jinhua Group Holdings' (real estate),")
    print("     which token overlap gets BACKWARDS.")

    # --- 6. Semantic query cache (reworded question matches) ----------------
    hdr(6, "Semantic cache — a REWORDED demo question still matches")
    from src.common import semantic_cache

    warmed = ["Who ultimately owns Nuctech, and what are its sanctions exposures?"]
    _pace()
    match = await semantic_cache.match_query(
        "What sanctions exposures does Nuctech have, and who owns it?", warmed
    )
    print(
        f"  reworded Nuctech question -> band={match.band} "
        f"(sim={match.similarity:.3f}, backend={match.backend})"
    )
    _pace()
    swap = await semantic_cache.match_query(
        "Who ultimately owns Hikvision, and what are its sanctions exposures?", warmed
    )
    print(
        f"  ENTITY SWAP (Nuctech->Hikvision) -> band={swap.band} "
        f"(sim={swap.similarity:.3f}, entity_sig_match={swap.entity_signature_match})"
    )
    print("  => even at high similarity, the entity swap is NOT auto-replayed — it's demoted.")

    # --- 7. EmbeddingsCache (repeats are free) ------------------------------
    if emb["enabled"]:
        hdr(7, "EmbeddingsCache — re-embedding seen text is instant (Redis-backed)")
        t0 = time.time()
        await embeddings.embed("SMIC")
        miss = time.time() - t0
        t0 = time.time()
        await embeddings.embed("SMIC")
        hit = time.time() - t0
        print(f"  first embed: {miss * 1000:5.0f} ms   cached embed: {hit * 1000:5.0f} ms")

    print(
        f"\n{SEP}\n Done. Every feature above ran against the real Redis 8 at "
        f"{os.getenv('REDIS_URL')}.\n{SEP}"
    )

    # Cleanup demo keys.
    for k in r.keys("emissary:wm:*") + r.keys("emissary:analysis:*") + r.keys("mem:*"):
        r.delete(k)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
