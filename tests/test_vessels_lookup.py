"""Fixture-fallback tests for vessel_by_mmsi / vessel_by_imo.

These resolvers prefer OpenSanctions but must fall back to the curated fixture
set (src/tools/vessels/fixtures/vessels.json) when OS has no hit — the behaviour
the vessel-track endpoint relies on for known commercial vessels. Mocked, no
network. Uses the LANA fixture (mmsi 319111800, imo 1012237).
"""

from __future__ import annotations

from src.tools.vessels import client as vc


async def test_vessel_by_mmsi_falls_back_to_fixture(monkeypatch):
    async def _no_os_hit(mmsi):
        return None

    monkeypatch.setattr(vc, "vessel_by_mmsi_opensanctions", _no_os_hit)
    vessel = await vc.vessel_by_mmsi("319111800")
    assert vessel is not None
    assert vessel["name"] == "LANA"


async def test_vessel_by_imo_falls_back_to_fixture(monkeypatch):
    async def _no_os_hit(imo):
        return None

    monkeypatch.setattr(vc, "vessel_by_imo_opensanctions", _no_os_hit)
    vessel = await vc.vessel_by_imo("1012237")
    assert vessel is not None
    assert vessel["name"] == "LANA"


async def test_vessel_by_mmsi_survives_opensanctions_error(monkeypatch):
    async def _boom(mmsi):
        raise RuntimeError("OpenSanctions down")

    monkeypatch.setattr(vc, "vessel_by_mmsi_opensanctions", _boom)
    # Must not raise — falls back to the fixture.
    vessel = await vc.vessel_by_mmsi("319111800")
    assert vessel is not None and vessel["name"] == "LANA"


async def test_vessel_by_mmsi_empty_returns_none():
    assert await vc.vessel_by_mmsi("") is None
    assert await vc.vessel_by_imo("") is None
