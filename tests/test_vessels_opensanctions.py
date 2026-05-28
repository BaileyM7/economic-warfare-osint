"""Tests for the OpenSanctions vessel-search functions.

These functions back ``src/tools/vessels/client.py`` for vessel-name and
identifier lookups. They are pure network adapters: feed a fixture JSON
response, assert the right entities come back and the caller can
post-filter by IMO/MMSI.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.tools.sanctions.client import (
    vessel_by_imo_opensanctions,
    vessel_by_mmsi_opensanctions,
    vessel_find_opensanctions,
)


_FIXTURE_RESULTS: list[dict[str, Any]] = [
    {
        "id": "NK-akinhalay",
        "caption": "AKIN HALAY",
        "schema": "Vessel",
        "datasets": ["us_ofac_sdn"],
        "properties": {
            "name": ["AKIN HALAY"],
            "imoNumber": ["9133892"],
            "mmsi": ["620999000"],
            "flag": ["CM"],
            "type": ["Crude Oil Tanker"],
            "sanctionedVessel": [True],
            "owner": ["Russian Oil Co LLC"],
        },
    },
    {
        "id": "NK-other",
        "caption": "OTHER VESSEL",
        "schema": "Vessel",
        "datasets": ["us_ofac_sdn"],
        "properties": {
            "name": ["OTHER VESSEL"],
            "imoNumber": ["1111111"],
            "mmsi": ["111111111"],
        },
    },
]


@pytest.fixture
def patched_fetch_json(monkeypatch: pytest.MonkeyPatch):
    """Patch the OpenSanctions HTTP call to return the fixture payload."""
    calls: list[dict[str, Any]] = []

    async def _fake_fetch_json(url: str, params=None, headers=None, **kwargs):
        calls.append({"url": url, "params": params, "headers": headers})
        return {"results": _FIXTURE_RESULTS, "total": {"value": 2}}

    monkeypatch.setattr(
        "src.tools.sanctions.client.fetch_json",
        _fake_fetch_json,
    )
    # Disable disk cache so tests don't bleed between runs.
    monkeypatch.setattr(
        "src.tools.sanctions.client.get_cached",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "src.tools.sanctions.client.set_cached",
        lambda *args, **kwargs: None,
    )
    return calls


@pytest.mark.asyncio
async def test_vessel_find_returns_raw_entities(patched_fetch_json):
    results = await vessel_find_opensanctions("AKIN HALAY")
    assert len(results) == 2
    first = results[0]
    assert first["caption"] == "AKIN HALAY"
    assert first["schema"] == "Vessel"
    # Properties pass through untouched — the vessel-client normalizer maps them.
    props = first["properties"]
    assert props["imoNumber"] == ["9133892"]
    assert props["mmsi"] == ["620999000"]
    assert props["sanctionedVessel"] == [True]


@pytest.mark.asyncio
async def test_vessel_find_filters_schema_to_vessel(patched_fetch_json):
    await vessel_find_opensanctions("test")
    assert patched_fetch_json[0]["params"]["schema"] == "Vessel"


@pytest.mark.asyncio
async def test_vessel_by_imo_filters_to_exact_match(patched_fetch_json):
    result = await vessel_by_imo_opensanctions("9133892")
    assert result is not None
    assert result["caption"] == "AKIN HALAY"


@pytest.mark.asyncio
async def test_vessel_by_imo_returns_none_when_no_exact_match(patched_fetch_json):
    result = await vessel_by_imo_opensanctions("9999999")
    assert result is None


@pytest.mark.asyncio
async def test_vessel_by_mmsi_filters_to_exact_match(patched_fetch_json):
    result = await vessel_by_mmsi_opensanctions("620999000")
    assert result is not None
    assert result["caption"] == "AKIN HALAY"


@pytest.mark.asyncio
async def test_vessel_by_mmsi_returns_none_when_no_exact_match(patched_fetch_json):
    result = await vessel_by_mmsi_opensanctions("000000000")
    assert result is None


@pytest.mark.asyncio
async def test_vessel_find_swallows_network_errors(monkeypatch: pytest.MonkeyPatch):
    async def _raise(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr("src.tools.sanctions.client.fetch_json", _raise)
    monkeypatch.setattr("src.tools.sanctions.client.get_cached", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.tools.sanctions.client.set_cached", lambda *args, **kwargs: None)

    assert await vessel_find_opensanctions("anything") == []
    assert await vessel_by_imo_opensanctions("9999999") is None
    assert await vessel_by_mmsi_opensanctions("999999999") is None
