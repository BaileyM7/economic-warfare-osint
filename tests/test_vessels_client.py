"""Tests for the rewritten vessels client (OpenSanctions + fixture fallback)."""

from __future__ import annotations

from typing import Any

import pytest

from src.tools.vessels import client as vc


_OPENSANCTIONS_ENTITY: dict[str, Any] = {
    "id": "NK-akinhalay",
    "caption": "AKIN HALAY",
    "schema": "Vessel",
    "datasets": ["us_ofac_sdn"],
    "properties": {
        "name": ["AKIN HALAY"],
        "imoNumber": ["9133892"],
        "mmsi": ["620999000"],
        "callSign": ["5BNS3"],
        "flag": ["CM"],
        "type": ["Crude Oil Tanker"],
        "lengthMeters": ["228"],
        "deadweightTonnage": ["63000"],
        "sanctionedVessel": [True],
        "owner": ["Russian Oil Co LLC"],
    },
}


def test_normalize_vessel_maps_opensanctions_fields():
    vessel = vc._normalize_vessel(_OPENSANCTIONS_ENTITY)
    assert vessel["name"] == "AKIN HALAY"
    assert vessel["imo"] == "9133892"
    assert vessel["mmsi"] == "620999000"
    assert vessel["flag"] == "CM"
    assert vessel["vessel_type"] == "Crude Oil Tanker"
    assert vessel["deadweight"] == 63000
    assert vessel["sanctioned"] is True
    assert vessel["status"] == "Sanctioned"
    assert vessel["owner"] == "Russian Oil Co LLC"
    assert vessel["source"] == "OpenSanctions Vessels"
    # Position fields are unavailable from OpenSanctions in Phase 1.
    assert vessel["latitude"] == 0.0
    assert vessel["longitude"] == 0.0
    assert vessel["last_position_epoch"] == 0


def test_normalize_vessel_empty_input_returns_empty_dict():
    assert vc._normalize_vessel({}) == {}


def test_normalize_vessel_owner_dict_form():
    raw = {
        **_OPENSANCTIONS_ENTITY,
        "properties": {
            **_OPENSANCTIONS_ENTITY["properties"],
            "owner": [{"id": "NK-owner", "caption": "Acme Holdings Ltd"}],
        },
    }
    assert vc._normalize_vessel(raw)["owner"] == "Acme Holdings Ltd"


def test_normalize_vessel_clean_dataset_not_marked_sanctioned():
    raw = {
        "id": "x",
        "caption": "Clean Ship",
        "schema": "Vessel",
        "datasets": ["maritime_open_data"],
        "properties": {"name": ["Clean Ship"], "imoNumber": ["1234567"]},
    }
    vessel = vc._normalize_vessel(raw)
    assert vessel["sanctioned"] is False
    assert vessel["status"] == "Unknown"


@pytest.mark.asyncio
async def test_vessel_find_returns_opensanctions_normalized(monkeypatch: pytest.MonkeyPatch):
    """AKIN HALAY is not in the fixture, so OpenSanctions wins for that name."""

    async def _fake_search(name: str, limit: int = 5):
        assert name == "AKIN HALAY"
        return [_OPENSANCTIONS_ENTITY]

    monkeypatch.setattr(vc, "vessel_find_opensanctions", _fake_search)
    results = await vc.vessel_find("AKIN HALAY")
    assert len(results) == 1
    assert results[0]["sanctioned"] is True
    assert results[0]["source"] == "OpenSanctions Vessels"


@pytest.mark.asyncio
async def test_vessel_find_prefers_fixture_over_opensanctions(
    monkeypatch: pytest.MonkeyPatch,
):
    """Option A: fixture wins for known commercial vessels.

    The bug this guards against: OpenSanctions' fuzzy /search/default ranker
    can return an unrelated sanctioned tanker (HATTI) at rank 1 for an
    unrelated query like "ever given" because HATTI's indexed metadata
    happens to score higher than the actual EVER GIVEN record. The old code
    would take that result and the UI showed HATTI's particulars for the
    EVER GIVEN query.
    """
    hatti = {
        "id": "NK-hatti",
        "caption": "HATTI",
        "schema": "Vessel",
        "datasets": ["us_ofac_sdn"],
        "properties": {
            "name": ["HATTI"],
            "imoNumber": ["9247883"],
            "mmsi": ["577396000"],
            "flag": ["SL"],
            "type": ["Oil Tanker"],
            "owner": ["EVYAP INTERNATIONALDIS"],
            "sanctionedVessel": [True],
        },
    }
    call_count = 0

    async def _fake_search(name: str, limit: int = 5):
        nonlocal call_count
        call_count += 1
        return [hatti]

    monkeypatch.setattr(vc, "vessel_find_opensanctions", _fake_search)
    results = await vc.vessel_find("ever given")
    assert any(r["name"] == "EVER GIVEN" for r in results)
    assert all(r["source"] == "fixture" for r in results)
    # OS shouldn't even be called when the fixture has a match.
    assert call_count == 0


@pytest.mark.asyncio
async def test_vessel_find_rejects_opensanctions_name_mismatch(
    monkeypatch: pytest.MonkeyPatch,
):
    """Defense in depth: even when fixture has no match, OS results whose name
    doesn't lexically overlap with the query should be rejected.

    Without this, a query for an obscure vessel not in the fixture could
    still surface HATTI-style false matches.
    """
    hatti = {
        "id": "NK-hatti",
        "caption": "HATTI",
        "schema": "Vessel",
        "datasets": ["us_ofac_sdn"],
        "properties": {
            "name": ["HATTI"],
            "imoNumber": ["9247883"],
            "mmsi": ["577396000"],
        },
    }

    async def _fake_search(name: str, limit: int = 5):
        return [hatti]

    monkeypatch.setattr(vc, "vessel_find_opensanctions", _fake_search)
    # "OBSCURE TANKER" isn't in the fixture and shouldn't match HATTI either.
    results = await vc.vessel_find("OBSCURE TANKER")
    assert results == []


def test_vessel_name_matches_helper():
    """_vessel_name_matches: significant-token (>=4 chars) overlap with the
    vessel's name, mirroring _ofac_hit_matches_company_label semantics."""
    assert vc._vessel_name_matches("EVER GIVEN", {"name": "EVER GIVEN"}) is True
    assert vc._vessel_name_matches("ever given", {"name": "EVER GIVEN"}) is True
    assert vc._vessel_name_matches("EVER GIVEN", {"name": "HATTI"}) is False
    assert vc._vessel_name_matches("EVER", {"name": "EVER GIVEN"}) is True
    # Short-token fallback when no significant tokens (>=4 chars) exist.
    assert vc._vessel_name_matches("M/V", {"name": "Some Vessel"}) is False
    assert vc._vessel_name_matches("", {"name": "EVER GIVEN"}) is False


@pytest.mark.asyncio
async def test_vessel_find_falls_back_to_fixture(monkeypatch: pytest.MonkeyPatch):
    async def _empty_search(name: str, limit: int = 5):
        return []

    monkeypatch.setattr(vc, "vessel_find_opensanctions", _empty_search)
    # EVER GIVEN is in the fixture set.
    results = await vc.vessel_find("EVER GIVEN")
    assert any(r["name"] == "EVER GIVEN" for r in results)
    assert all(r["source"] == "fixture" for r in results)


@pytest.mark.asyncio
async def test_vessel_find_treats_opensanctions_stub_as_miss(
    monkeypatch: pytest.MonkeyPatch,
):
    """OpenSanctions sometimes returns a caption-only stub for vessels of
    media interest (EVER GIVEN, NORD STREAM, etc.). Without this guard the
    stub short-circuits the fixture fallback and the UI shows all-blank
    particulars."""

    stub = {
        "id": "stub-1",
        "caption": "EVER GIVEN",
        "schema": "Vessel",
        "datasets": ["news_mentions"],
        "properties": {"name": ["EVER GIVEN"]},  # no imoNumber, no mmsi
    }

    async def _stub_only_search(name: str, limit: int = 5):
        return [stub]

    monkeypatch.setattr(vc, "vessel_find_opensanctions", _stub_only_search)
    results = await vc.vessel_find("EVER GIVEN")
    # Should fall through to fixture (which has IMO + MMSI for EVER GIVEN).
    assert len(results) > 0
    assert all(r["source"] == "fixture" for r in results)
    assert results[0]["imo"] == "9811000"
    assert results[0]["mmsi"] == "353136000"


@pytest.mark.asyncio
async def test_vessel_find_empty_when_nothing_anywhere(monkeypatch: pytest.MonkeyPatch):
    async def _empty_search(name: str, limit: int = 5):
        return []

    monkeypatch.setattr(vc, "vessel_find_opensanctions", _empty_search)
    assert await vc.vessel_find("THIS NAME DOES NOT EXIST QQQ") == []


@pytest.mark.asyncio
async def test_vessel_by_mmsi_falls_back_to_fixture(monkeypatch: pytest.MonkeyPatch):
    async def _none_lookup(mmsi: str):
        return None

    monkeypatch.setattr(vc, "vessel_by_mmsi_opensanctions", _none_lookup)
    # EVER GIVEN's MMSI from the fixture
    result = await vc.vessel_by_mmsi("353136000")
    assert result is not None
    assert result["name"] == "EVER GIVEN"


@pytest.mark.asyncio
async def test_vessel_by_imo_falls_back_to_fixture(monkeypatch: pytest.MonkeyPatch):
    async def _none_lookup(imo: str):
        return None

    monkeypatch.setattr(vc, "vessel_by_imo_opensanctions", _none_lookup)
    # EVER GIVEN's IMO from the fixture
    result = await vc.vessel_by_imo("9811000")
    assert result is not None
    assert result["name"] == "EVER GIVEN"


@pytest.mark.asyncio
async def test_vessel_history_empty_when_wargame_import_fails(monkeypatch: pytest.MonkeyPatch):
    """When wargame_backend can't be imported, vessel_history degrades to [].

    The test forces the lazy import inside vessel_history() to raise so it
    works in both environments — with and without the optional `wargame`
    extra installed (CI's unit job runs without it).
    """
    import builtins

    real_import = builtins.__import__

    def _fail_wargame(name, *args, **kwargs):
        if name.startswith("wargame_backend"):
            raise ImportError(f"forced failure for {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fail_wargame)
    assert await vc.vessel_history("353136000", days=30) == []


@pytest.mark.asyncio
async def test_vessel_history_returns_empty_for_empty_mmsi():
    assert await vc.vessel_history("", days=30) == []


@pytest.mark.asyncio
async def test_vessel_port_calls_infers_from_buffer(monkeypatch: pytest.MonkeyPatch):
    """vessel_port_calls = infer_port_stops(vessel_history())."""
    fake_positions = [
        {"latitude": 1.27, "longitude": 103.85, "speed": 0.0, "timestamp": 1000},
        {"latitude": 1.27, "longitude": 103.85, "speed": 0.0, "timestamp": 2000},
    ]

    async def _fake_history(mmsi: str, days: int = 30):
        return fake_positions

    monkeypatch.setattr(vc, "vessel_history", _fake_history)
    calls = await vc.vessel_port_calls("353136000", days=90)
    assert len(calls) == 1
    assert calls[0]["position_count"] == 2


def test_infer_port_stops_still_works():
    positions = [
        {"latitude": 1.27, "longitude": 103.85, "speed": 0.0, "timestamp": 1000},
        {"latitude": 1.27, "longitude": 103.85, "speed": 0.5, "timestamp": 2000},
        {"latitude": 1.27, "longitude": 103.85, "speed": 0.0, "timestamp": 3000},
    ]
    stops = vc.infer_port_stops(positions)
    assert len(stops) == 1
    assert stops[0]["position_count"] == 3
