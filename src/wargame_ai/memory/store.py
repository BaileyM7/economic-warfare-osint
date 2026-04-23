"""Agent memory store backed by pgvector.

Each country agent accumulates a running stream of memory records: past
decisions, observed events, and seed context drawn from the data lake.  The
store provides:

* :meth:`remember` — embed a text chunk + insert into ``agent_memory``.
* :meth:`recall` — cosine-similarity top-k retrieval for a query string.
* :meth:`seed_from_events` — bulk-load initial memories from the last 60
  days of data-lake ``events`` touching each country.

The implementation is async-SQLAlchemy throughout so the simulation loop can
use it alongside the rest of the backend.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentMemory, Event, MemoryType

from wargame_ai.memory.embeddings import Embedder

log = structlog.get_logger(__name__)


@dataclass
class MemoryRecord:
    """Lightweight DTO returned by :meth:`AgentMemoryStore.recall`."""

    id: uuid.UUID
    country_iso3: str
    content: str
    memory_type: str
    turn: int
    metadata: dict[str, Any]
    score: float  # cosine similarity (higher = more similar)


class AgentMemoryStore:
    """CRUD + retrieval facade for the ``agent_memory`` table."""

    def __init__(self, session: AsyncSession, embedder: Embedder) -> None:
        self.session = session
        self.embedder = embedder

    # ------------------------------------------------------------------ #
    # Writes                                                               #
    # ------------------------------------------------------------------ #

    async def remember(
        self,
        *,
        sim_id: uuid.UUID,
        country_code: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        memory_type: MemoryType = MemoryType.observation,
        turn: int = 0,
    ) -> uuid.UUID:
        """Embed ``content`` and insert a single memory row.

        Returns:
            The UUID of the newly inserted row.
        """
        if not content.strip():
            raise ValueError("memory content cannot be empty")

        vectors = await self.embedder.embed_texts([content])
        vec = vectors[0]

        row = AgentMemory(
            sim_id=sim_id,
            country_iso3=country_code.upper(),
            content=content,
            embedding=vec,
            memory_type=memory_type,
            turn=turn,
            metadata_=metadata or {},
        )
        self.session.add(row)
        await self.session.flush()
        return row.id

    async def remember_batch(
        self,
        *,
        sim_id: uuid.UUID,
        country_code: str,
        items: list[tuple[str, dict[str, Any]]],
        memory_type: MemoryType = MemoryType.observation,
        turn: int = 0,
    ) -> list[uuid.UUID]:
        """Embed + insert many records at once (fewer LLM calls, faster seed)."""
        if not items:
            return []
        texts = [t for t, _ in items]
        vectors = await self.embedder.embed_texts(texts)
        ids: list[uuid.UUID] = []
        for (content, meta), vec in zip(items, vectors, strict=True):
            row = AgentMemory(
                sim_id=sim_id,
                country_iso3=country_code.upper(),
                content=content,
                embedding=vec,
                memory_type=memory_type,
                turn=turn,
                metadata_=meta,
            )
            self.session.add(row)
            ids.append(row.id)
        await self.session.flush()
        return ids

    # ------------------------------------------------------------------ #
    # Reads                                                                #
    # ------------------------------------------------------------------ #

    async def recall(
        self,
        *,
        sim_id: uuid.UUID,
        country_code: str,
        query: str,
        k: int = 8,
    ) -> list[MemoryRecord]:
        """Return the top-k memory records most similar to ``query``.

        Uses pgvector's ``<=>`` cosine-distance operator; the score is
        ``1 - distance`` so "higher is better" in the returned ``MemoryRecord``.
        """
        vectors = await self.embedder.embed_texts([query])
        qvec = vectors[0]

        # ``AgentMemory.embedding <=> :vec`` is cosine distance in pgvector
        stmt = (
            select(
                AgentMemory,
                (1 - AgentMemory.embedding.cosine_distance(qvec)).label("similarity"),
            )
            .where(AgentMemory.sim_id == sim_id)
            .where(AgentMemory.country_iso3 == country_code.upper())
            .order_by(AgentMemory.embedding.cosine_distance(qvec))
            .limit(k)
        )
        result = await self.session.execute(stmt)
        records: list[MemoryRecord] = []
        for row, similarity in result.all():
            records.append(
                MemoryRecord(
                    id=row.id,
                    country_iso3=row.country_iso3,
                    content=row.content,
                    memory_type=row.memory_type.value
                    if hasattr(row.memory_type, "value")
                    else str(row.memory_type),
                    turn=row.turn,
                    metadata=dict(row.metadata_ or {}),
                    score=float(similarity),
                )
            )
        return records

    # ------------------------------------------------------------------ #
    # Seeding                                                              #
    # ------------------------------------------------------------------ #

    async def seed_from_events(
        self,
        *,
        sim_id: uuid.UUID,
        country_code: str,
        lookback_days: int = 60,
        limit: int = 200,
    ) -> int:
        """Seed a country's memory from recent data-lake events.

        Pulls events where the country was actor or target in the last
        ``lookback_days`` days and inserts them as ``observation`` memories.

        Returns:
            The number of memory rows inserted.
        """
        since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        code = country_code.upper()

        stmt = (
            select(Event)
            .where(Event.occurred_at >= since)
            .where((Event.actor_iso3 == code) | (Event.target_iso3 == code))
            .order_by(Event.occurred_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        events = list(result.scalars().all())
        if not events:
            return 0

        items: list[tuple[str, dict[str, Any]]] = []
        for e in events:
            line = self._format_event_as_memory(e, code)
            meta = {
                "source": e.source,
                "event_id": str(e.id),
                "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
                "actor": e.actor_iso3,
                "target": e.target_iso3,
                "domain": e.domain.value if e.domain else None,
            }
            items.append((line, meta))

        await self.remember_batch(
            sim_id=sim_id,
            country_code=code,
            items=items,
            memory_type=MemoryType.intel,
            turn=0,
        )
        return len(items)

    @staticmethod
    def _format_event_as_memory(event: Event, self_code: str) -> str:
        """Render an ``Event`` row into a compact memory line."""
        actor = event.actor_iso3 or "?"
        target = event.target_iso3 or "?"
        role = "actor" if actor == self_code else "target"
        domain = event.domain.value if event.domain else "unknown"
        text_snippet = (event.raw_text or event.event_type or "").strip()
        return (
            f"[{role} | {domain} | {event.occurred_at:%Y-%m-%d}] "
            f"{actor}→{target}: {text_snippet[:240]}"
        )

    # ------------------------------------------------------------------ #
    # Health / debug                                                       #
    # ------------------------------------------------------------------ #

    async def count(self, *, sim_id: uuid.UUID, country_code: str) -> int:
        """Return the number of memory rows for a (sim, country) pair."""
        stmt = text(
            "SELECT COUNT(*) FROM agent_memory "
            "WHERE sim_id = :sim_id AND country_iso3 = :code"
        )
        row = await self.session.execute(
            stmt, {"sim_id": sim_id, "code": country_code.upper()}
        )
        return int(row.scalar_one())
