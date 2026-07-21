"""Record real Voyage vectors for the integration test's fixtures.

The vector-search integration test replays these instead of calling Voyage live,
so it runs in CI (real Redis 8, no key) with genuine semantic quality and no
rate-limit flakiness. Re-run this only when the fixture entities or the embedding
model change.

    VOYAGE_API_KEY=... EMBEDDING_MODEL=voyage-large-2 uv run python -m scripts.capture_entity_vectors

Writes tests/integration/fixtures/entity_vectors.json.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# The exact entities + query strings the integration test embeds. Keep in lockstep
# with ENTITIES / the query strings in tests/integration/test_vector_search_quality.py.
ENTITIES = [
    {
        "entity_id": "jinhua",
        "name": "Fujian Jinhua Integrated Circuit",
        "entity_type": "company",
        "country": "CN",
        "aliases": [],
        "identifiers": {},
        "notes": "DRAM memory fabrication",
    },
    {
        "entity_id": "smic",
        "name": "SMIC",
        "entity_type": "company",
        "country": "CN",
        "aliases": [],
        "identifiers": {},
        "notes": "semiconductor foundry, chip manufacturing",
    },
    {
        "entity_id": "jinhua_re",
        "name": "Jinhua Group Holdings",
        "entity_type": "company",
        "country": "CN",
        "aliases": [],
        "identifiers": {},
        "notes": "real estate development",
    },
    {
        "entity_id": "maersk",
        "name": "Maersk",
        "entity_type": "company",
        "country": "DK",
        "aliases": [],
        "identifiers": {},
        "notes": "container shipping and logistics",
    },
    {
        "entity_id": "tsmc",
        "name": "TSMC",
        "entity_type": "company",
        "country": "TW",
        "aliases": [],
        "identifiers": {},
        "notes": "advanced semiconductor foundry",
    },
]
QUERY_STRINGS = ["semiconductor manufacturer", "chip foundry"]

_OUT = Path(__file__).parent.parent / "tests" / "integration" / "fixtures" / "entity_vectors.json"


async def main() -> int:
    from src.common import embeddings
    from src.common import similarity as sim
    from src.common.config import config

    if not embeddings.embeddings_enabled():
        print(f"embeddings off: {embeddings.embeddings_status()['reason']}", file=sys.stderr)
        return 2

    texts = [sim.entity_text(e) for e in ENTITIES] + QUERY_STRINGS
    vectors = await embeddings.embed_many(texts)
    if vectors is None:
        print("Voyage call failed/rate-limited — retry in a minute.", file=sys.stderr)
        return 1

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(
        json.dumps(
            {
                "model": config.embedding_model,
                "dims": len(vectors[0]),
                "vectors": dict(zip(texts, vectors)),
            }
        )
    )
    print(f"wrote {len(vectors)} vectors ({len(vectors[0])} dims) -> {_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
