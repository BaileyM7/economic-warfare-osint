"""Backfill / rebuild the Redis entity vector index from SQLite.

SQLite is the system of record; the index is derived, so it can always be rebuilt
from scratch. Run this after enabling embeddings for the first time, or after
changing EMBEDDING_MODEL (which needs --force, since every stored vector becomes
invalid when the model changes).

    uv run python -m scripts.reindex_entities              # index what's changed
    uv run python -m scripts.reindex_entities --force      # drop + rebuild
    uv run python -m scripts.reindex_entities --dry-run    # report, touch nothing

Idempotent: entities whose text is unchanged are skipped without spending a Voyage
call (each doc stores a sha256 of its indexed text). Exits non-zero on any error so
it is usable from a deploy hook.

Note on cost/time: Voyage's free tier is ~3 requests/minute. Entities are embedded
64-per-call, so a few hundred entities is a handful of calls — but a large graph on
the free tier will be rate-limited, and the seam degrades (logs + skips) rather
than hanging.
"""

from __future__ import annotations

import argparse
import asyncio
import sys


async def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild the Redis entity vector index.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="drop the index and re-embed everything (required after a model change)",
    )
    parser.add_argument("--batch", type=int, default=64, help="entities per embedding call")
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would happen, change nothing"
    )
    args = parser.parse_args()

    from src.common import knowledge_store as ks
    from src.common import vector_index
    from src.common.config import config
    from src.common.embeddings import embeddings_status
    from src.common.redis_client import capabilities

    caps = capabilities()
    emb = embeddings_status()

    print(f"redis:      ping={caps.ping} search={caps.search} ({caps.server} {caps.version})")
    print(f"embeddings: enabled={emb['enabled']} model={emb['model'] or config.embedding_model}")

    if not caps.search:
        print(
            "\nERROR: this Redis has no Query Engine (FT.*). Vector search needs a real "
            "Redis 8 — Render's Key Value service runs Valkey, which has no modules.",
            file=sys.stderr,
        )
        return 2
    if not emb["enabled"]:
        print(f"\nERROR: embeddings are off ({emb['reason']}).", file=sys.stderr)
        return 2

    entities = ks.list_entities()
    print(f"entities in SQLite: {len(entities)}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    if args.force:
        print("--force: dropping the index (every vector will be recomputed)")
        vector_index.drop_index()

    if not vector_index.ensure_index():
        print("\nERROR: could not create the index.", file=sys.stderr)
        return 2

    result = await vector_index.backfill(entities, batch_size=args.batch)
    print(f"\nindexed={result['indexed']} skipped={result['skipped']} errors={result['errors']}")
    if result["skipped"] and not args.force:
        print("  (skipped = text unchanged since last index — no embedding spend)")

    print(f"index status: {vector_index.status()}")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
