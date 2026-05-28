"""Tests for the AISStream.io adapter — aggregator + normalize + enabled gate."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from wargame_backend.app.db.models import EventDomain
from ingest.aisstream import (
    AISStreamRawRecord,
    AISStreamSource,
    _aggregate,
    _mmsi_to_iso3,
    _zone_for_position,
)


# --- MID → ISO3 -------------------------------------------------------------


def test_mmsi_to_iso3_resolves_slice_countries() -> None:
    assert _mmsi_to_iso3("412123456") == "CHN"
    assert _mmsi_to_iso3("416777777") == "TWN"
    assert _mmsi_to_iso3("366111222") == "USA"
    assert _mmsi_to_iso3("273000111") == "RUS"


def test_mmsi_to_iso3_drops_non_slice_flag() -> None:
    # Liberia (636), Panama (351), Marshall Islands (538) — all non-slice.
    assert _mmsi_to_iso3("636000000") is None
    assert _mmsi_to_iso3("351111111") is None


def test_mmsi_to_iso3_handles_garbage() -> None:
    assert _mmsi_to_iso3("") is None
    assert _mmsi_to_iso3(None) is None
    assert _mmsi_to_iso3("12") is None


# --- bbox containment ------------------------------------------------------


def test_zone_for_position_inside_taiwan_strait() -> None:
    assert _zone_for_position(24.0, 120.0) == "TWN_strait_buffer"


def test_zone_for_position_inside_spratlys() -> None:
    assert _zone_for_position(10.0, 114.0) == "SCS_spratlys"


def test_zone_for_position_outside_returns_none() -> None:
    assert _zone_for_position(0.0, 0.0) is None
    assert _zone_for_position(40.0, 140.0) is None


# --- Aggregator -------------------------------------------------------------


def _msg(mmsi: str, lat: float, lon: float, name: str = "TEST") -> dict:
    return {
        "MetaData": {"MMSI": mmsi, "ShipName": name},
        "Message": {"PositionReport": {"Latitude": lat, "Longitude": lon}},
    }


def test_aggregator_counts_distinct_mmsis_per_zone_flag() -> None:
    messages = [
        _msg("412111111", 24.0, 120.0),  # CHN in TWN strait
        _msg("412111111", 24.5, 120.5),  # same MMSI, dedup
        _msg("412222222", 24.0, 120.0),  # CHN in TWN strait
        _msg("416333333", 24.0, 120.0),  # TWN in TWN strait
        _msg("412444444", 10.0, 114.0),  # CHN in Spratlys
        _msg("636000001", 24.0, 120.0),  # Liberia — dropped
        _msg("412555555", 40.0, 140.0),  # outside any zone — dropped
    ]
    buckets = _aggregate(messages)
    assert len(buckets[("TWN_strait_buffer", "CHN")]["mmsis"]) == 2
    assert len(buckets[("TWN_strait_buffer", "TWN")]["mmsis"]) == 1
    assert len(buckets[("SCS_spratlys", "CHN")]["mmsis"]) == 1
    assert ("TWN_strait_buffer", "LBR") not in buckets


def test_aggregator_skips_messages_without_position() -> None:
    messages = [
        # ShipStaticData without PositionReport
        {"MetaData": {"MMSI": "412111111", "ShipName": "X"}, "Message": {}},
        # PositionReport without lat/lon
        {
            "MetaData": {"MMSI": "412111111"},
            "Message": {"PositionReport": {}},
        },
    ]
    assert _aggregate(messages) == {}


# --- normalize() ------------------------------------------------------------


@pytest.mark.asyncio
async def test_normalize_emits_aisstream_event() -> None:
    raw = AISStreamRawRecord(
        zone="TWN_strait_buffer",
        flag_iso3="CHN",
        period_iso="2026-05-27T12:00:00+00:00",
        ping_count=7,
        prior_ping_count=4,
        ping_count_w_w_pct=75.0,
        raw_sample={"name": "PRC TANKER", "mmsi": "412123456"},
    )
    event = await AISStreamSource().normalize(raw)
    assert event.source == "aisstream"
    assert event.actor_iso3 == "CHN"
    assert event.target_iso3 is None
    assert event.domain is EventDomain.kinetic_limited
    assert event.event_type == "ais_zone_presence_TWN_strait_buffer"
    assert event.payload["zone"] == "TWN_strait_buffer"
    assert event.payload["ping_count"] == 7
    assert event.payload["ping_count_w_w_pct"] == 75.0
    assert event.payload["measurement"] == "ais_position_snapshot"
    assert event.payload["_dedup_key"].startswith("aisstream:CHN:TWN_strait_buffer:")


@pytest.mark.asyncio
async def test_normalize_falls_back_to_now_on_bad_period(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = AISStreamRawRecord(
        zone="TWN_strait_buffer",
        flag_iso3="CHN",
        period_iso="not-a-real-iso-string",
        ping_count=1,
    )
    event = await AISStreamSource().normalize(raw)
    assert event.occurred_at.tzinfo is not None


# --- enabled gate -----------------------------------------------------------


def test_disabled_when_api_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AISSTREAM_API_KEY", raising=False)
    assert AISStreamSource().enabled is False


def test_enabled_when_api_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AISSTREAM_API_KEY", "fake-key")
    assert AISStreamSource().enabled is True


# --- fetch() end-to-end with mocked WebSocket --------------------------------


@pytest.mark.asyncio
async def test_fetch_yields_raw_records_from_mocked_collector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AISSTREAM_API_KEY", "fake-key")
    monkeypatch.setenv("AISSTREAM_SAMPLE_SECONDS", "1")

    async def _fake_collect(self, *, api_key, bboxes, sample_seconds):
        return [
            _msg("412111111", 24.0, 120.0, "PRC SHIP"),
            _msg("412222222", 24.0, 120.0),
        ]

    monkeypatch.setattr(AISStreamSource, "_collect_messages", _fake_collect)

    source = AISStreamSource()
    until = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    records = [r async for r in source.fetch(until, until)]
    assert len(records) == 1
    rec = records[0]
    assert isinstance(rec, AISStreamRawRecord)
    assert rec.zone == "TWN_strait_buffer"
    assert rec.flag_iso3 == "CHN"
    assert rec.ping_count == 2
