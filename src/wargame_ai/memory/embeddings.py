"""Embedding provider abstraction.

:class:`VoyageEmbedder` is the only implementation. Conforms to the
:class:`Embedder` protocol: an async ``embed_texts()`` returning one
fixed-dimension float vector per input.

**There is deliberately no offline/stub embedder.** There used to be a
``HashEmbedder`` that derived vectors from SHA-256 when ``VOYAGE_API_KEY`` was
unset — which is the prod state — so `recall()` ran cosine similarity over
hashes and returned confidently-ranked, *semantically meaningless* memories with
no signal that anything was wrong. Silent garbage is worse than a hard failure:
callers can handle "memory is off", but they cannot detect "memory is lying".
With no key, the caller passes ``memory_store=None`` and the sim loop skips
recall/remember entirely (see ``SimLoop._recall`` / ``_remember``).
"""

from __future__ import annotations

from typing import Protocol

# Output width per Voyage model, verified against the live API. Keeping this map
# is what stops EMBEDDING_MODEL and EMBEDDING_DIMS drifting apart: the pair used
# to default to voyage-3 + 1536, which is impossible (voyage-3 emits 1024), and
# nobody noticed because the deployed config overrode the model to voyage-large-2
# — whose 1536 *does* match — and because no real embedder ran anyway.
#
# The `agent_memory.embedding` pgvector column is fixed at DEFAULT_MODEL's width,
# so switching models here needs a DB migration, not just an env change.
MODEL_DIMS: dict[str, int] = {
    "voyage-3": 1024,
    "voyage-3-lite": 512,
    "voyage-3-large": 1024,
    "voyage-large-2": 1536,
    "voyage-code-3": 1024,
    "voyage-finance-2": 1024,
    "voyage-law-2": 1024,
    "voyage-multilingual-2": 1024,
}

# Matches the deployed EMBEDDING_MODEL and the agent_memory column width.
DEFAULT_MODEL = "voyage-large-2"
DEFAULT_DIMS = MODEL_DIMS[DEFAULT_MODEL]


def dims_for_model(model: str, fallback: int = DEFAULT_DIMS) -> int:
    """Known output width for `model`, else `fallback` (for models added later)."""
    return MODEL_DIMS.get(model.strip(), fallback)


class Embedder(Protocol):
    """Async embedding provider interface."""

    dimensions: int

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover
        """Embed a batch of texts.  Returns ``len(texts)`` vectors."""
        ...


class VoyageEmbedder:
    """Voyage AI production embedder (defaults to voyage-large-2, 1536 dims).

    Uses HTTP calls via ``httpx`` so it does not require the voyage-python SDK.

    ``dimensions`` defaults to whatever ``model`` actually emits, so the two can
    never be configured into an impossible pair by accident.
    """

    _ENDPOINT = "https://api.voyageai.com/v1/embeddings"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        dimensions: int | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("VoyageEmbedder requires a non-empty api_key")
        self.api_key = api_key
        self.model = model
        self.dimensions = dims_for_model(model) if dimensions is None else dimensions

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Call Voyage AI's embeddings endpoint."""
        import httpx  # local import keeps module import-cost low

        if not texts:
            return []
        payload = {"model": self.model, "input": texts, "input_type": "document"}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(self._ENDPOINT, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        vectors = [item["embedding"] for item in data.get("data", [])]

        # Fail loudly on a dimension mismatch rather than writing mis-shaped
        # vectors into the pgvector column (which would either error deep in the
        # insert or, worse, silently corrupt the index). A mismatch means the
        # configured model does not emit `dimensions` floats — the config is
        # wrong, not the data.
        if vectors and len(vectors[0]) != self.dimensions:
            raise ValueError(
                f"{self.model} returned {len(vectors[0])}-dim vectors but this embedder "
                f"is configured for {self.dimensions}. EMBEDDING_MODEL and EMBEDDING_DIMS "
                f"disagree — and the agent_memory.embedding column is fixed at "
                f"{DEFAULT_DIMS}, so changing model also needs a DB migration."
            )
        return vectors
