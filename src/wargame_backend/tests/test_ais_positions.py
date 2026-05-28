"""Tests for the ais_positions buffer helpers + AISStream position write.

Uses a local SQLite fixture that creates only the ``ais_positions`` table,
sidestepping the shared conftest's full ``create_all`` which fails on SQLite
because other tables (``countries``, ``sim_events``) use Postgres JSONB.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from wargame_backend.app.db.ais_positions import (
    bulk_insert_positions,
    default_prune_cutoff,
    prune_positions_older_than,
    read_positions_for_mmsi,
)
from wargame_backend.app.db.models import AISPosition
from ingest.aisstream import _message_to_position


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a session backed by an in-memory SQLite DB with only ais_positions."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: AISPosition.__table__.create(c))
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


# ---------------------------------------------------------------------------
# Helper construction
# ---------------------------------------------------------------------------


def _position(
    mmsi: str = "412111111",
    lat: float = 24.0,
    lon: float = 120.0,
    speed: float | None = 12.5,
    course: float | None = 90.0,
    minutes_ago: int = 0,
) -> dict:
    return {
        "mmsi": mmsi,
        "latitude": lat,
        "longitude": lon,
        "speed": speed,
        "course": course,
        "timestamp": datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    }


# ---------------------------------------------------------------------------
# bulk_insert_positions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_insert_persists_all_rows(db: AsyncSession) -> None:
    inserted = await bulk_insert_positions(
        db,
        [_position(minutes_ago=10), _position(minutes_ago=5), _position(minutes_ago=0)],
    )
    await db.flush()
    assert inserted == 3
    rows = (await db.execute(select(AISPosition))).scalars().all()
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_bulk_insert_skips_rows_missing_required_fields(db: AsyncSession) -> None:
    inserted = await bulk_insert_positions(
        db,
        [
            _position(),
            {
                "mmsi": "412222222",
                "latitude": None,
                "longitude": 120.0,
                "timestamp": datetime.now(timezone.utc),
            },
            {
                "mmsi": "",
                "latitude": 24.0,
                "longitude": 120.0,
                "timestamp": datetime.now(timezone.utc),
            },
            {"mmsi": "412333333", "latitude": 24.0, "longitude": 120.0},  # no timestamp
        ],
    )
    await db.flush()
    assert inserted == 1
    rows = (await db.execute(select(AISPosition))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_bulk_insert_empty_input_is_noop(db: AsyncSession) -> None:
    assert await bulk_insert_positions(db, []) == 0


# ---------------------------------------------------------------------------
# read_positions_for_mmsi
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_filters_by_mmsi(db: AsyncSession) -> None:
    await bulk_insert_positions(
        db,
        [
            _position(mmsi="412111111", minutes_ago=10),
            _position(mmsi="412222222", minutes_ago=5),
            _position(mmsi="412111111", minutes_ago=0),
        ],
    )
    await db.flush()
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    rows = await read_positions_for_mmsi(db, "412111111", since)
    assert len(rows) == 2
    # Returned dicts use the canonical position shape consumed by infer_port_stops.
    assert all("latitude" in r and "longitude" in r for r in rows)
    assert all("timestamp" in r and isinstance(r["timestamp"], int) for r in rows)


@pytest.mark.asyncio
async def test_read_filters_by_since(db: AsyncSession) -> None:
    await bulk_insert_positions(
        db,
        [
            _position(minutes_ago=60 * 24 * 5),  # 5 days old
            _position(minutes_ago=10),  # 10 min old
        ],
    )
    await db.flush()
    since = datetime.now(timezone.utc) - timedelta(days=1)
    rows = await read_positions_for_mmsi(db, "412111111", since)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_read_orders_chronologically(db: AsyncSession) -> None:
    await bulk_insert_positions(
        db,
        [
            _position(minutes_ago=5),
            _position(minutes_ago=15),
            _position(minutes_ago=10),
        ],
    )
    await db.flush()
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    rows = await read_positions_for_mmsi(db, "412111111", since)
    timestamps = [r["timestamp"] for r in rows]
    assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# prune_positions_older_than
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prune_removes_old_rows_only(db: AsyncSession) -> None:
    await bulk_insert_positions(
        db,
        [
            _position(minutes_ago=60 * 24 * 40),  # 40 days — over default retention
            _position(minutes_ago=60 * 24 * 5),  # 5 days — within retention
            _position(minutes_ago=10),  # 10 min — within retention
        ],
    )
    await db.flush()
    pruned = await prune_positions_older_than(db, default_prune_cutoff())
    await db.flush()
    remaining = (await db.execute(select(AISPosition))).scalars().all()
    assert pruned == 1
    assert len(remaining) == 2


# ---------------------------------------------------------------------------
# _message_to_position parser
# ---------------------------------------------------------------------------


def test_message_to_position_extracts_canonical_fields() -> None:
    msg = {
        "MetaData": {"MMSI": 412111111, "time_utc": "2026-05-27 12:00:00 +0000 UTC"},
        "Message": {
            "PositionReport": {
                "Latitude": 24.5,
                "Longitude": 120.5,
                "Sog": 12.5,
                "Cog": 90.0,
            }
        },
    }
    pos = _message_to_position(msg)
    assert pos is not None
    assert pos["mmsi"] == "412111111"
    assert pos["latitude"] == 24.5
    assert pos["longitude"] == 120.5
    assert pos["speed"] == 12.5
    assert pos["course"] == 90.0
    # Go-format timestamp must be parsed, not silently replaced with now().
    assert pos["timestamp"].year == 2026
    assert pos["timestamp"].month == 5
    assert pos["timestamp"].day == 27
    assert pos["timestamp"].hour == 12
    assert pos["timestamp"].tzinfo is not None


def test_parse_aisstream_timestamp_handles_go_format() -> None:
    """The actual format AISStream emits — the bug that clustered all
    positions at the ingest moment because fromisoformat couldn't handle it."""
    from ingest.aisstream import _parse_aisstream_timestamp

    dt = _parse_aisstream_timestamp("2026-05-27 12:34:56.123456 +0000 UTC")
    assert dt.year == 2026
    assert dt.minute == 34
    assert dt.microsecond == 123456
    assert dt.tzinfo is not None


def test_parse_aisstream_timestamp_handles_go_format_without_microseconds() -> None:
    from ingest.aisstream import _parse_aisstream_timestamp

    dt = _parse_aisstream_timestamp("2026-05-27 12:34:56 +0000 UTC")
    assert dt.year == 2026
    assert dt.second == 56
    assert dt.tzinfo is not None


def test_parse_aisstream_timestamp_handles_iso() -> None:
    from ingest.aisstream import _parse_aisstream_timestamp

    dt = _parse_aisstream_timestamp("2026-05-27T12:34:56+00:00")
    assert dt.year == 2026
    assert dt.hour == 12
    assert dt.tzinfo is not None


def test_parse_aisstream_timestamp_falls_back_to_now_on_garbage() -> None:
    """Unparseable input must return a UTC-aware datetime close to now() —
    the safety net so the buffer write never explodes on a weird message."""
    from ingest.aisstream import _parse_aisstream_timestamp

    before = datetime.now(timezone.utc)
    dt = _parse_aisstream_timestamp("not a timestamp")
    after = datetime.now(timezone.utc)
    assert dt.tzinfo is not None
    assert before <= dt <= after


def test_parse_aisstream_timestamp_handles_none_and_empty() -> None:
    from ingest.aisstream import _parse_aisstream_timestamp

    assert _parse_aisstream_timestamp(None).tzinfo is not None
    assert _parse_aisstream_timestamp("").tzinfo is not None
    assert _parse_aisstream_timestamp("   ").tzinfo is not None


def test_message_to_position_handles_iso_timestamp() -> None:
    msg = {
        "MetaData": {"MMSI": "412111111", "time_utc": "2026-05-27T12:00:00+00:00"},
        "Message": {"PositionReport": {"Latitude": 24.5, "Longitude": 120.5}},
    }
    pos = _message_to_position(msg)
    assert pos is not None
    assert pos["timestamp"].year == 2026
    assert pos["timestamp"].month == 5


def test_message_to_position_returns_none_for_malformed() -> None:
    # Missing MMSI
    assert (
        _message_to_position(
            {"MetaData": {}, "Message": {"PositionReport": {"Latitude": 24.0, "Longitude": 120.0}}}
        )
        is None
    )
    # Missing position
    assert _message_to_position({"MetaData": {"MMSI": "412111111"}, "Message": {}}) is None
    # Missing lat
    assert (
        _message_to_position(
            {"MetaData": {"MMSI": "412111111"}, "Message": {"PositionReport": {"Longitude": 120.0}}}
        )
        is None
    )
