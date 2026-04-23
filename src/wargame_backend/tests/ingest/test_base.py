"""Tests for ingest.base — retry/backoff behavior, Source lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from wargame_backend.app.db.models import Event, EventDomain
from ingest.base import (
    IngestionRunResult,
    Source,
    _RetryableHTTPError,
    raise_for_retryable,
)


# ---------------------------------------------------------------------------
# Minimal concrete Source for testing
# ---------------------------------------------------------------------------

class _DummySource(Source):
    name = "dummy"
    display_name = "Dummy Test Source"

    def __init__(self, records: list[dict] | None = None) -> None:
        super().__init__()
        self._records = records or []
        self.normalize_call_count = 0

    async def fetch(
        self, since: datetime, until: datetime
    ) -> AsyncIterator[dict]:
        for r in self._records:
            yield r

    async def normalize(self, raw: dict) -> Event:
        self.normalize_call_count += 1
        return Event(
            source="dummy",
            occurred_at=datetime.now(timezone.utc),
            event_type="test",
            domain=EventDomain.diplomatic,
            payload={"_dedup_key": f"dummy:{raw['id']}", **raw},
        )


# ---------------------------------------------------------------------------
# raise_for_retryable
# ---------------------------------------------------------------------------

def _fake_response(status_code: int) -> httpx.Response:
    """Build a minimal mock httpx.Response with the given status code."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.url = "https://example.com/test"
    resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    )
    return resp


def test_raise_for_retryable_429_raises():
    """429 should raise _RetryableHTTPError."""
    resp = _fake_response(429)
    with pytest.raises(_RetryableHTTPError):
        raise_for_retryable(resp)


def test_raise_for_retryable_500_raises():
    """500 should raise _RetryableHTTPError."""
    resp = _fake_response(500)
    with pytest.raises(_RetryableHTTPError):
        raise_for_retryable(resp)


def test_raise_for_retryable_403_propagates():
    """403 should call raise_for_status (not wrap in _RetryableHTTPError)."""
    resp = _fake_response(403)
    with pytest.raises(httpx.HTTPStatusError):
        raise_for_retryable(resp)


def test_raise_for_retryable_200_ok():
    """200 should not raise."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    raise_for_retryable(resp)  # should not raise


# ---------------------------------------------------------------------------
# enabled property
# ---------------------------------------------------------------------------

def test_source_enabled_default(monkeypatch):
    """Source is enabled by default (no env var set)."""
    monkeypatch.delenv("DUMMY_ENABLED", raising=False)
    src = _DummySource()
    assert src.enabled is True


def test_source_disabled_via_env(monkeypatch):
    """Setting DUMMY_ENABLED=false disables the source."""
    monkeypatch.setenv("DUMMY_ENABLED", "false")
    src = _DummySource()
    assert src.enabled is False


def test_source_disabled_via_zero(monkeypatch):
    """Setting DUMMY_ENABLED=0 also disables the source."""
    monkeypatch.setenv("DUMMY_ENABLED", "0")
    src = _DummySource()
    assert src.enabled is False


# ---------------------------------------------------------------------------
# Semaphore respects CONCURRENCY env
# ---------------------------------------------------------------------------

def test_concurrency_env(monkeypatch):
    """DUMMY_CONCURRENCY env sets semaphore limit."""
    monkeypatch.setenv("DUMMY_CONCURRENCY", "3")
    src = _DummySource()
    assert src._semaphore._value == 3  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# IngestionRunResult
# ---------------------------------------------------------------------------

def test_ingestion_run_result_success():
    r = IngestionRunResult(
        source="dummy",
        since=datetime.now(timezone.utc),
        until=datetime.now(timezone.utc),
        fetched=10,
        upserted=8,
        skipped=2,
        errors=[],
    )
    assert r.success is True


def test_ingestion_run_result_failure():
    r = IngestionRunResult(
        source="dummy",
        since=datetime.now(timezone.utc),
        until=datetime.now(timezone.utc),
        errors=["something went wrong"],
    )
    assert r.success is False


# ---------------------------------------------------------------------------
# Retry behavior (unit — no real HTTP)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_retries_on_retryable_error():
    """_get should retry up to 5 times on _RetryableHTTPError."""
    src = _DummySource()
    call_count = 0

    async def _mock_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        raise _RetryableHTTPError("500 Server Error")

    mock_client = AsyncMock()
    mock_client.get = _mock_get
    mock_client.is_closed = False

    with patch.object(src, "_get_client", return_value=mock_client):
        with pytest.raises(_RetryableHTTPError):
            await src._get("https://example.com")

    # tenacity stop_after_attempt(5) means 5 total calls (1 + 4 retries)
    assert call_count == 5
