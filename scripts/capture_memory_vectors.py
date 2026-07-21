"""Record real Voyage vectors for the long-term-memory integration test.

Same idea as scripts/capture_entity_vectors.py: the memory recall integration
test replays these instead of calling Voyage live, so it runs in CI (real Redis 8,
no key) with genuine semantic quality and no rate-limit flakiness.

    VOYAGE_API_KEY=... EMBEDDING_MODEL=voyage-large-2 uv run python -m scripts.capture_memory_vectors

Writes tests/integration/fixtures/memory_vectors.json. Re-run only when the
fixture memories or query change (keep these in lockstep with
tests/integration/test_memory_recall.py).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# The exact memories the integration test seeds + the recall query. Each memory's
# EMBEDDED string is `_mem_search_text` = "<text> <lowercased entity_names>".
MEMORIES = [
    {"text": "Fujian Jinhua is on the BIS Entity List (2018).", "entity_names": ["Fujian Jinhua"]},
    {
        "text": "Fujian Jinhua's DRAM production line was transferred from UMC.",
        "entity_names": ["Fujian Jinhua", "UMC"],
    },
    {
        "text": "Micron is the Western supplier displaced by Fujian Jinhua's DRAM capacity.",
        "entity_names": ["Fujian Jinhua", "Micron"],
    },
    {"text": "Maersk runs container shipping on the Asia-Europe lane.", "entity_names": ["Maersk"]},
]
QUERY = "what else is exposed to that supply chain?"

_OUT = Path(__file__).parent.parent / "tests" / "integration" / "fixtures" / "memory_vectors.json"


async def main() -> int:
    from src.common import embeddings
    from src.common.agent_memory import Memory, _mem_search_text, _sanitize_memory
    from src.common.config import config

    if not embeddings.embeddings_enabled():
        print(f"embeddings off: {embeddings.embeddings_status()['reason']}", file=sys.stderr)
        return 2

    # Reproduce EXACTLY what the code embeds: the sanitized memory's search text.
    strings = [
        _mem_search_text(_sanitize_memory(Memory(text=m["text"], entity_names=m["entity_names"])))
        for m in MEMORIES
    ] + [QUERY]

    vectors = await embeddings.embed_many(strings)  # ONE batched call
    if vectors is None:
        print("Voyage call failed/rate-limited — retry in a minute.", file=sys.stderr)
        return 1

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(
        json.dumps(
            {
                "model": config.embedding_model,
                "dims": len(vectors[0]),
                "vectors": dict(zip(strings, vectors)),
            }
        )
    )
    print(f"wrote {len(vectors)} vectors ({len(vectors[0])} dims) -> {_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
