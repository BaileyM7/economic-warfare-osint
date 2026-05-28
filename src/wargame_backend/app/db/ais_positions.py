"""Helpers for the live AIS position buffer.

The AISStream ingest adapter populates ``ais_positions`` during each
snapshot window; the vessels tool queries it for ``vessel_history`` /
``vessel_port_calls``. A retention prune keeps the table bounded so a
long-lived deployment doesn't drift past the Render Postgres free-tier
storage cap.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from wargame_backend.app.db.models import AISPosition

# Default retention window for the buffer. vessel_history's default lookback
# is 30 days, so anything older is unused by callers and safe to prune.
DEFAULT_RETENTION_DAYS = 30


async def bulk_insert_positions(session: AsyncSession, positions: list[dict[str, Any]]) -> int:
    """Insert a batch of raw position dicts. Returns the count inserted.

    Each dict must carry ``mmsi``, ``latitude``, ``longitude``, ``timestamp``.
    ``speed`` and ``course`` are optional. Rows with missing required fields
    are skipped silently — the upstream collector already discards malformed
    AISStream messages, this is belt-and-suspenders.
    """
    if not positions:
        return 0
    valid: list[dict[str, Any]] = []
    for pos in positions:
        mmsi = pos.get("mmsi")
        lat = pos.get("latitude")
        lon = pos.get("longitude")
        ts = pos.get("timestamp")
        if not mmsi or lat is None or lon is None or ts is None:
            continue
        valid.append(
            {
                "mmsi": str(mmsi),
                "latitude": float(lat),
                "longitude": float(lon),
                "speed": pos.get("speed"),
                "course": pos.get("course"),
                "timestamp": ts,
            }
        )
    if not valid:
        return 0
    await session.execute(AISPosition.__table__.insert(), valid)
    return len(valid)


async def read_positions_for_mmsi(
    session: AsyncSession,
    mmsi: str,
    since: datetime,
) -> list[dict[str, Any]]:
    """Return all position points for *mmsi* with timestamp >= *since*.

    Returned dicts use the canonical position keys consumed by
    ``infer_port_stops()``: ``latitude``, ``longitude``, ``speed``,
    ``course``, ``timestamp`` (epoch seconds).
    """
    stmt = (
        select(AISPosition)
        .where(AISPosition.mmsi == str(mmsi), AISPosition.timestamp >= since)
        .order_by(AISPosition.timestamp.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "latitude": row.latitude,
            "longitude": row.longitude,
            "speed": row.speed or 0.0,
            "course": row.course or 0,
            "timestamp": int(row.timestamp.timestamp()),
        }
        for row in rows
    ]


async def prune_positions_older_than(
    session: AsyncSession,
    cutoff: datetime,
) -> int:
    """Delete buffered positions whose timestamp is strictly before *cutoff*.

    Returns the row count deleted. Run after each ingest so the table
    stays bounded.
    """
    result = await session.execute(delete(AISPosition).where(AISPosition.timestamp < cutoff))
    return result.rowcount or 0


def default_prune_cutoff(now: datetime | None = None) -> datetime:
    """The default prune cutoff is ``now - DEFAULT_RETENTION_DAYS``."""
    return (now or datetime.now(timezone.utc)) - timedelta(days=DEFAULT_RETENTION_DAYS)
