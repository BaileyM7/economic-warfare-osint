"""Embedding provider abstraction.

Two implementations ship in this module:

* :class:`VoyageEmbedder` — production embedder using Voyage AI (voyage-3).
* :class:`HashEmbedder` — deterministic, zero-dependency embedder for tests.

Both conform to the :class:`Embedder` protocol: an async ``embed_texts()``
that returns a list of fixed-dimension float vectors.
"""

from __future__ import annotations

import hashlib
import struct
from typing import Protocol


class Embedder(Protocol):
    """Async embedding provider interface."""

    dimensions: int

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover
        """Embed a batch of texts.  Returns ``len(texts)`` vectors."""
        ...


class HashEmbedder:
    """Deterministic, hash-based embedder — for tests and offline dev.

    Not semantically meaningful; simply produces a reproducible fingerprint
    in ``[-1, 1]`` so cosine-similarity queries return consistent ordering.
    """

    def __init__(self, dimensions: int = 1536) -> None:
        self.dimensions = dimensions

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Produce a deterministic pseudo-embedding per input text."""
        return [self._hash_one(t) for t in texts]

    def _hash_one(self, text: str) -> list[float]:
        # Stream SHA-256 blocks to generate enough bytes for `dimensions` floats.
        vec: list[float] = []
        counter = 0
        while len(vec) < self.dimensions:
            digest = hashlib.sha256(f"{text}:{counter}".encode("utf-8")).digest()
            # Each 4-byte chunk → one int32 → scaled into [-1, 1]
            for i in range(0, 32, 4):
                if len(vec) >= self.dimensions:
                    break
                (value,) = struct.unpack(">i", digest[i : i + 4])
                vec.append(value / 2**31)
            counter += 1
        return vec


class VoyageEmbedder:
    """Voyage AI production embedder (voyage-3, 1536 dims).

    Uses HTTP calls via ``httpx`` so it does not require the voyage-python SDK.
    """

    _ENDPOINT = "https://api.voyageai.com/v1/embeddings"

    def __init__(self, api_key: str, model: str = "voyage-3", dimensions: int = 1536) -> None:
        if not api_key:
            raise ValueError("VoyageEmbedder requires a non-empty api_key")
        self.api_key = api_key
        self.model = model
        self.dimensions = dimensions

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Call Voyage AI's embeddings endpoint."""
        import httpx  # local import keeps HashEmbedder import-cost low

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
        return [item["embedding"] for item in data.get("data", [])]
