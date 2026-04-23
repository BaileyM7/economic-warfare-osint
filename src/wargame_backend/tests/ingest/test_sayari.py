"""Tests for the Sayari Graph adapter."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from wargame_backend.app.db.models import EventDomain
from ingest.sayari import SayariRawRecord, SayariSource, _parse_iso


class TestHelpers:
    def test_parse_iso(self) -> None:
        assert _parse_iso("2026-04-15T08:00:00Z") == datetime(
            2026, 4, 15, 8, 0, 0, tzinfo=timezone.utc
        )


class TestNormalize:
    @pytest.mark.asyncio
    async def test_entity_event(self) -> None:
        raw = SayariRawRecord(
            entity_id="sayari-ent-9001",
            name="Beneficial Owner Co",
            entity_type="company",
            iso3="CHN",
            risk_factors=["sanction_proximity"],
            updated_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
        )
        event = await SayariSource().normalize(raw)
        assert event.actor_iso3 == "CHN"
        assert event.domain is EventDomain.economic
        assert event.event_type == "sayari_entity_company"
        assert event.payload["_dedup_key"] == (
            "sayari:sayari-ent-9001:2026-04-15"
        )

    def test_disabled_when_credentials_missing(self, monkeypatch) -> None:
        monkeypatch.delenv("SAYARI_API_KEY", raising=False)
        monkeypatch.delenv("SAYARI_CLIENT_SECRET", raising=False)
        assert SayariSource().enabled is False

    def test_enabled_when_credentials_present(self, monkeypatch) -> None:
        monkeypatch.setenv("SAYARI_API_KEY", "fake-id")
        monkeypatch.setenv("SAYARI_CLIENT_SECRET", "fake-secret")
        assert SayariSource().enabled is True
