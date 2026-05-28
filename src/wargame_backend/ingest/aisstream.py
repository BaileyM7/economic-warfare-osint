"""AISStream.io vessel-tracking adapter.

Data source
-----------
AISStream.io exposes a free WebSocket carrying the global AIS firehose at
``wss://stream.aisstream.io/v0/stream``. We subscribe with BoundingBoxes
covering the Taiwan-strait + ADIZ buffer and the SCS Spratlys ring, then
collect ``PositionReport`` + ``ShipStaticData`` messages for a bounded
sample window (default 300 s, configurable via ``AISSTREAM_SAMPLE_SECONDS``).

Each sample yields one ``AISStreamRawRecord`` per ``(zone, flag_iso3)`` pair
where ``ping_count`` counts the distinct MMSIs seen broadcasting from that
zone during the window. Flag country is derived from the leading 3 digits
of MMSI (Maritime Identification Digit / MID).

Event shape
-----------
Payload keys: ``ping_count``, ``prior_ping_count``, ``ping_count_w_w_pct``,
``sample``, ``zone``, ``flag_iso3``, ``measurement`` (= ``ais_position_snapshot``).

Auth
----
Requires ``AISSTREAM_API_KEY`` env var (passed in the JSON subscribe message).
The adapter disables itself when the key is missing.

Dedup key
---------
``aisstream:{flag_iso3}:{zone}:{period_iso}`` — one aggregated reading per
flag per zone per ingest run, keyed by the run's ``until`` instant.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any, ClassVar

import structlog
from pydantic import BaseModel, Field

from wargame_backend.app.db.ais_positions import (
    bulk_insert_positions,
    default_prune_cutoff,
    prune_positions_older_than,
)
from wargame_backend.app.db.models import Event, EventDomain
from wargame_backend.app.db.session import AsyncSessionLocal
from ingest.base import Source, RawRecord

log = structlog.get_logger(__name__)

_WS_URL = "wss://stream.aisstream.io/v0/stream"
_DEFAULT_SAMPLE_SECONDS = 300
_RECONNECT_ATTEMPTS = 2

# AISStream BoundingBox format: [[lat_min, lon_min], [lat_max, lon_max]].
# Two watch zones: the Taiwan-strait + ADIZ buffer and the SCS Spratlys ring.
_WATCH_ZONES: list[dict[str, Any]] = [
    {
        "zone": "TWN_strait_buffer",
        "bbox": [[21.0, 117.0], [26.0, 122.0]],
    },
    {
        "zone": "SCS_spratlys",
        "bbox": [[7.0, 110.0], [13.0, 118.0]],
    },
]

# MMSI Maritime Identification Digit (MID — first 3 digits) → ISO3 country.
# Covers only the 10-country slice; vessels with non-slice MIDs (Liberia,
# Panama, Marshall Islands etc.) are dropped from aggregation.
_MID_TO_ISO3: dict[str, str] = {
    # CHN — three allocated MIDs
    "412": "CHN",
    "413": "CHN",
    "414": "CHN",
    # TWN
    "416": "TWN",
    # USA — four allocated MIDs
    "338": "USA",
    "366": "USA",
    "367": "USA",
    "368": "USA",
    "369": "USA",
    # JPN
    "431": "JPN",
    "432": "JPN",
    # KOR
    "440": "KOR",
    "441": "KOR",
    # PHL
    "548": "PHL",
    # AUS
    "503": "AUS",
    # PRK
    "445": "PRK",
    # RUS
    "273": "RUS",
    # IND
    "419": "IND",
}


def _mmsi_to_iso3(mmsi: str | int | None) -> str | None:
    """Resolve flag country from MMSI via its MID (first 3 digits)."""
    if mmsi is None:
        return None
    s = str(mmsi).strip()
    if len(s) < 3:
        return None
    return _MID_TO_ISO3.get(s[:3])


def _zone_for_position(lat: float, lon: float) -> str | None:
    """Pick which watch zone (if any) a (lat, lon) falls inside."""
    for zone in _WATCH_ZONES:
        lat_min, lon_min = zone["bbox"][0]
        lat_max, lon_max = zone["bbox"][1]
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return zone["zone"]
    return None


class AISStreamRawRecord(BaseModel):
    """Aggregated zone+flag observation: distinct MMSIs seen during the window."""

    zone: str
    flag_iso3: str
    period_iso: str
    ping_count: int
    prior_ping_count: int | None = None
    ping_count_w_w_pct: float | None = None
    raw_sample: dict[str, Any] = Field(default_factory=dict)


def _message_to_position(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Extract a raw AIS position dict from one AISStream PositionReport message.

    Returns ``None`` if the message lacks an MMSI or lat/lon. Speed and course
    are optional (older AIS messages omit them); the buffer columns are
    nullable to accommodate.
    """
    meta = msg.get("MetaData") or {}
    mmsi = meta.get("MMSI")
    position = (msg.get("Message") or {}).get("PositionReport") or {}
    lat = position.get("Latitude")
    lon = position.get("Longitude")
    if not mmsi or lat is None or lon is None:
        return None

    ts_str = (meta.get("time_utc") or "").strip()
    timestamp: datetime
    if ts_str:
        try:
            timestamp = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            timestamp = datetime.now(timezone.utc)
    else:
        timestamp = datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    return {
        "mmsi": str(mmsi),
        "latitude": float(lat),
        "longitude": float(lon),
        "speed": position.get("Sog"),
        "course": position.get("Cog"),
        "timestamp": timestamp,
    }


def _aggregate(messages: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Aggregate a list of AISStream messages into per-(zone, flag) buckets.

    Returns a mapping of ``(zone, flag_iso3) → {"mmsis": set, "sample": dict}``.
    Distinct MMSI count becomes ``ping_count``; the sample is one
    representative vessel name/MMSI per bucket for the Event payload.
    """
    buckets: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"mmsis": set(), "sample": {}}
    )
    for msg in messages:
        meta = msg.get("MetaData") or {}
        mmsi = str(meta.get("MMSI") or "").strip()
        if not mmsi:
            continue
        flag_iso3 = _mmsi_to_iso3(mmsi)
        if flag_iso3 is None:
            continue
        position = (msg.get("Message") or {}).get("PositionReport") or {}
        lat = position.get("Latitude")
        lon = position.get("Longitude")
        if lat is None or lon is None:
            continue
        zone = _zone_for_position(float(lat), float(lon))
        if zone is None:
            continue
        bucket = buckets[(zone, flag_iso3)]
        bucket["mmsis"].add(mmsi)
        if not bucket["sample"]:
            bucket["sample"] = {
                "name": (meta.get("ShipName") or "").strip(),
                "mmsi": mmsi,
            }
    return buckets


class AISStreamSource(Source):
    """AISStream.io adapter — emits one Event per (zone, flag) per run."""

    name: ClassVar[str] = "aisstream"
    display_name: ClassVar[str] = "AISStream.io AIS"

    @property
    def enabled(self) -> bool:
        if not super().enabled:
            return False
        if not os.environ.get("AISSTREAM_API_KEY"):
            log.info("aisstream.disabled_no_api_key")
            return False
        return True

    async def fetch(self, since: datetime, until: datetime) -> AsyncIterator[RawRecord]:
        sample_seconds = int(os.environ.get("AISSTREAM_SAMPLE_SECONDS", _DEFAULT_SAMPLE_SECONDS))
        api_key = os.environ.get("AISSTREAM_API_KEY", "")
        bboxes = [zone["bbox"] for zone in _WATCH_ZONES]

        messages = await self._collect_messages(
            api_key=api_key,
            bboxes=bboxes,
            sample_seconds=sample_seconds,
        )

        # Side channel: persist raw positions to the ais_positions buffer so
        # vessel_history() / vessel_port_calls() can read tracks per MMSI.
        # Uses its own short-lived session so this is independent of the main
        # Event-write transaction managed by Source.run().
        await self._persist_positions(messages)

        buckets = _aggregate(messages)
        period_iso = until.replace(microsecond=0).isoformat()
        for (zone, flag_iso3), bucket in buckets.items():
            yield AISStreamRawRecord(
                zone=zone,
                flag_iso3=flag_iso3,
                period_iso=period_iso,
                ping_count=len(bucket["mmsis"]),
                # Prior count is computed by the extractor against earlier
                # snapshots; left None here so the first run isn't noisy.
                prior_ping_count=None,
                ping_count_w_w_pct=None,
                raw_sample=bucket["sample"],
            )

    async def _persist_positions(self, messages: list[dict[str, Any]]) -> None:
        """Bulk-insert raw positions into the buffer and prune old rows.

        Swallows DB errors so a transient buffer failure doesn't sink the
        whole ingest run — the per-zone Event aggregates are still useful
        on their own.
        """
        positions = [p for p in (_message_to_position(m) for m in messages) if p]
        if not positions:
            return
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    inserted = await bulk_insert_positions(session, positions)
                    pruned = await prune_positions_older_than(session, default_prune_cutoff())
                log.info(
                    "aisstream.positions_persisted",
                    inserted=inserted,
                    pruned=pruned,
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("aisstream.positions_persist_failed", error=str(exc))

    async def _collect_messages(
        self,
        *,
        api_key: str,
        bboxes: list[list[list[float]]],
        sample_seconds: int,
    ) -> list[dict[str, Any]]:
        """Open the WebSocket, subscribe, collect messages for the sample window.

        Reconnects up to ``_RECONNECT_ATTEMPTS`` times on transport errors —
        accumulated partial data is preserved. On total failure the caller
        gets an empty list (extractor falls through with no signal).
        """
        try:
            import websockets  # type: ignore[import-not-found]
        except ImportError:
            log.warning("aisstream.websockets_lib_unavailable")
            return []

        subscribe_msg = json.dumps(
            {
                "APIKey": api_key,
                "BoundingBoxes": bboxes,
                "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
            }
        )

        collected: list[dict[str, Any]] = []
        remaining = sample_seconds
        for attempt in range(_RECONNECT_ATTEMPTS + 1):
            if remaining <= 0:
                break
            try:
                started = asyncio.get_event_loop().time()
                async with websockets.connect(_WS_URL) as ws:
                    await ws.send(subscribe_msg)
                    try:
                        async with asyncio.timeout(remaining):
                            async for raw in ws:
                                try:
                                    collected.append(json.loads(raw))
                                except json.JSONDecodeError:
                                    continue
                    except TimeoutError:
                        pass
                # Completed (or timed out) cleanly — done.
                break
            except Exception as exc:  # noqa: BLE001
                elapsed = asyncio.get_event_loop().time() - started
                remaining = max(0, remaining - int(elapsed))
                log.warning(
                    "aisstream.connection_error",
                    attempt=attempt,
                    error=str(exc),
                    remaining_seconds=remaining,
                )
        return collected

    async def normalize(self, raw: RawRecord) -> Event:
        assert isinstance(raw, AISStreamRawRecord)
        try:
            occurred_at = datetime.fromisoformat(raw.period_iso)
            if occurred_at.tzinfo is None:
                occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        except ValueError:
            occurred_at = datetime.now(timezone.utc)

        dedup_key = f"aisstream:{raw.flag_iso3}:{raw.zone}:{raw.period_iso}"
        return Event(
            source="aisstream",
            occurred_at=occurred_at,
            actor_iso3=raw.flag_iso3,
            target_iso3=None,
            event_type=f"ais_zone_presence_{raw.zone}",
            domain=EventDomain.kinetic_limited,
            severity=None,
            payload={
                "_dedup_key": dedup_key,
                "measurement": "ais_position_snapshot",
                "flag_iso3": raw.flag_iso3,
                "zone": raw.zone,
                "ping_count": raw.ping_count,
                "prior_ping_count": raw.prior_ping_count,
                "ping_count_w_w_pct": raw.ping_count_w_w_pct,
                "sample": raw.raw_sample,
            },
            raw_text=(
                f"AISStream {raw.zone} {raw.flag_iso3}-flag distinct MMSIs: {raw.ping_count}"
            ),
        )
