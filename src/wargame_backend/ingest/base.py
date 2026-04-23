"""Abstract base class for all data-lake ingestion sources.

Every source adapter (GDELT, ACLED, World Bank, …) subclasses ``Source`` and
implements ``fetch`` and ``normalize``.  The ``run`` method orchestrates the
full fetch → normalize → upsert → lineage-record pipeline and is called by
``runner.py``.

Retry/backoff strategy
-----------------------
HTTP calls inside ``fetch`` are wrapped with :func:`tenacity.retry` using
exponential backoff.  Only 5xx and 429 responses trigger retries; 4xx client
errors are re-raised immediately to surface configuration problems quickly.

Rate limiting
-------------
Each source can declare a per-source concurrency limit via the env variable
``{SOURCE_NAME_UPPER}_CONCURRENCY`` (default 5).  This controls the asyncio
``Semaphore`` used in ``fetch``.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, ClassVar

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from wargame_backend.app.db.models import DataSource, DataSourceStatus, Event, EventDomain

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Shared HTTP client (connection-pooled, reused across adapter calls)
# ---------------------------------------------------------------------------
_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _make_client(**kwargs: Any) -> httpx.AsyncClient:
    """Create a shared async HTTP client with sensible defaults."""
    return httpx.AsyncClient(
        timeout=kwargs.pop("timeout", _DEFAULT_TIMEOUT),
        follow_redirects=True,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Retry predicate — only retry on 5xx or 429
# ---------------------------------------------------------------------------

class _RetryableHTTPError(Exception):
    """Wraps httpx.HTTPStatusError when the status warrants a retry."""


def _is_retryable(exc: BaseException) -> bool:
    """Return True if the exception should trigger a retry attempt.

    We retry on:
      * ``_RetryableHTTPError`` — our wrapper around 5xx / 429 responses.
      * ``httpx.ConnectError`` / ``httpx.ReadError`` / ``httpx.RemoteProtocolError``
        — transient network glitches (DNS blip, container-bridge hiccup,
        mid-stream disconnect). These used to kill the whole source run with
        ``ConnectError('[Errno -2] Name or service not known')`` even though
        the underlying issue was fully transient.
    """
    if isinstance(exc, _RetryableHTTPError):
        return True
    if isinstance(
        exc,
        (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError),
    ):
        return True
    return False


def raise_for_retryable(response: httpx.Response) -> None:
    """Raise ``_RetryableHTTPError`` for 429/5xx; propagate 4xx immediately."""
    if response.status_code == 429 or response.status_code >= 500:
        raise _RetryableHTTPError(
            f"HTTP {response.status_code} from {response.url}"
        )
    response.raise_for_status()


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class IngestionRunResult:
    """Summary returned by ``Source.run``."""

    source: str
    since: datetime
    until: datetime
    fetched: int = 0
    upserted: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


# ---------------------------------------------------------------------------
# RawRecord — thin wrapper so subclasses can declare their own Pydantic models
# ---------------------------------------------------------------------------

# Type alias; each adapter defines its own Pydantic model and yields it here
RawRecord = Any


# ---------------------------------------------------------------------------
# Abstract Source base class
# ---------------------------------------------------------------------------

class Source(ABC):
    """Abstract base for every ingest adapter.

    Subclass contract
    -----------------
    1. Set ``name`` (class variable) to a stable snake_case identifier.
    2. Set ``display_name`` (class variable) to a human-readable label.
    3. Implement ``fetch`` — yields ``RawRecord`` objects (adapter-specific Pydantic models).
    4. Implement ``normalize`` — maps a ``RawRecord`` to the canonical ``Event`` ORM model
       (without committing).

    The ``run`` method is provided by this base class and handles the full
    fetch → normalize → upsert → data_sources lineage pipeline.
    """

    name: ClassVar[str]          # e.g. "gdelt", "acled"
    display_name: ClassVar[str]  # e.g. "GDELT 2.0"

    def __init__(self) -> None:
        concurrency_env = f"{self.name.upper()}_CONCURRENCY"
        concurrency = int(os.environ.get(concurrency_env, "5"))
        self._semaphore = asyncio.Semaphore(concurrency)
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = _make_client()
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Enabled flag
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Return True unless ``{NAME_UPPER}_ENABLED=false`` is set in env."""
        key = f"{self.name.upper()}_ENABLED"
        return os.environ.get(key, "true").lower() not in ("false", "0", "no")

    # ------------------------------------------------------------------
    # Retry-wrapped HTTP helper for use inside fetch implementations
    # ------------------------------------------------------------------

    async def _get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Perform a GET request with retry/backoff; rate-limited by semaphore.

        Uses tenacity exponential backoff: 5xx / 429 responses are retried up
        to 5 times with delays 2 s, 4 s, 8 s, 16 s, 32 s (max 60 s).
        """
        @retry(
            retry=retry_if_exception(_is_retryable),
            wait=wait_exponential(multiplier=1, min=2, max=60),
            stop=stop_after_attempt(5),
            reraise=True,
        )
        async def _inner() -> httpx.Response:
            async with self._semaphore:
                client = await self._get_client()
                response = await client.get(url, params=params, headers=headers)
                raise_for_retryable(response)
                return response

        return await _inner()

    async def _post_form(
        self,
        url: str,
        *,
        data: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """POST form-urlencoded data with retry/backoff (OAuth2 token exchange)."""
        @retry(
            retry=retry_if_exception(_is_retryable),
            wait=wait_exponential(multiplier=1, min=2, max=60),
            stop=stop_after_attempt(5),
            reraise=True,
        )
        async def _inner() -> httpx.Response:
            async with self._semaphore:
                client = await self._get_client()
                response = await client.post(url, data=data, headers=headers)
                raise_for_retryable(response)
                return response

        return await _inner()

    # ------------------------------------------------------------------
    # Abstract methods
    # ------------------------------------------------------------------

    @abstractmethod
    def fetch(
        self, since: datetime, until: datetime
    ) -> AsyncIterator[RawRecord]:
        """Yield raw records from the source API/feed.

        Parameters
        ----------
        since:
            Start of the time window (inclusive, UTC-aware).
        until:
            End of the time window (exclusive, UTC-aware).

        Yields
        ------
        RawRecord
            Adapter-specific Pydantic model instance representing one
            source record before normalization.
        """
        ...  # pragma: no cover

    @abstractmethod
    async def normalize(self, raw: RawRecord) -> Event:
        """Map a single raw record to the canonical ``Event`` ORM model.

        The returned ``Event`` must NOT be attached to any session yet —
        ``run`` handles persistence.  The ``id`` field should be left at
        its default (``uuid.uuid4()``); ``ingested_at`` is set server-side.

        The adapter must populate a stable ``dedup_key`` in
        ``event.payload["_dedup_key"]`` so the upsert can be idempotent.
        """
        ...  # pragma: no cover

    # ------------------------------------------------------------------
    # Orchestrator
    # ------------------------------------------------------------------

    async def run(
        self,
        session: AsyncSession,
        since: datetime,
        until: datetime,
    ) -> IngestionRunResult:
        """Orchestrate fetch → normalize → upsert → lineage recording.

        Parameters
        ----------
        session:
            Async SQLAlchemy session (caller owns the transaction boundary).
        since:
            Window start (UTC-aware datetime).
        until:
            Window end (UTC-aware datetime).

        Returns
        -------
        IngestionRunResult
            Summary of the run including counts and any non-fatal errors.
        """
        result = IngestionRunResult(source=self.name, since=since, until=until)

        if not self.enabled:
            log.info("source.disabled", source=self.name)
            return result

        # Resolve / create data_sources lineage row
        data_source_id = await self._ensure_data_source(session)

        log.info("ingest.start", source=self.name, since=since, until=until)

        try:
            async for raw in self.fetch(since, until):
                result.fetched += 1
                try:
                    event = await self.normalize(raw)
                    event.data_source_id = data_source_id
                    event.source = self.name
                    dedup_key = event.payload.get("_dedup_key")
                    upserted = await self._upsert_event(session, event, dedup_key)
                    if upserted:
                        result.upserted += 1
                    else:
                        result.skipped += 1
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(f"normalize error: {exc!r}")
                    log.warning(
                        "ingest.normalize_error",
                        source=self.name,
                        error=str(exc),
                    )
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"fetch error: {exc!r}")
            log.error("ingest.fetch_error", source=self.name, error=str(exc))
        finally:
            await self._update_data_source(
                session, data_source_id, result.upserted
            )

        log.info(
            "ingest.complete",
            source=self.name,
            fetched=result.fetched,
            upserted=result.upserted,
            skipped=result.skipped,
            errors=len(result.errors),
        )
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _ensure_data_source(self, session: AsyncSession) -> uuid.UUID:
        """Return existing data_source id or insert a new row."""
        stmt = select(DataSource).where(DataSource.source_key == self.name)
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            return row.id  # type: ignore[return-value]

        new_ds = DataSource(
            source_key=self.name,
            display_name=self.display_name,
            status=DataSourceStatus.active,
            records_ingested=0,
            metadata_={},
        )
        session.add(new_ds)
        await session.flush()
        return new_ds.id  # type: ignore[return-value]

    async def _upsert_event(
        self,
        session: AsyncSession,
        event: Event,
        dedup_key: str | None,
    ) -> bool:
        """Insert event; skip (return False) if dedup_key already exists."""
        if dedup_key is not None:
            # Check for existing row via dedup key stored in payload
            from sqlalchemy import cast
            from sqlalchemy.dialects.postgresql import JSONB

            stmt = select(Event.id).where(
                Event.source == event.source,
                Event.payload["_dedup_key"].astext == dedup_key,
            )
            exists = (await session.execute(stmt)).scalar_one_or_none()
            if exists is not None:
                return False

        session.add(event)
        await session.flush()
        return True

    async def _update_data_source(
        self,
        session: AsyncSession,
        data_source_id: uuid.UUID,
        new_records: int,
    ) -> None:
        """Update last_ingest_at and increment records_ingested."""
        stmt = select(DataSource).where(DataSource.id == data_source_id)
        ds = (await session.execute(stmt)).scalar_one_or_none()
        if ds is None:
            return
        ds.last_ingest_at = datetime.now(timezone.utc)
        ds.records_ingested = (ds.records_ingested or 0) + new_records
        await session.flush()
