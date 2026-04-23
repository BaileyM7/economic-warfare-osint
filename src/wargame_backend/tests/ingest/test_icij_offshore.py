"""Tests for the ICIJ Offshore Leaks adapter."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from wargame_backend.app.db.models import EventDomain
from ingest.icij_offshore import (
    ICIJOffshoreRawRecord,
    ICIJOffshoreSource,
    _parse_date,
)


class TestHelpers:
    def test_parse_date_iso(self) -> None:
        assert _parse_date("2024-04-15") == datetime(
            2024, 4, 15, tzinfo=timezone.utc
        )

    def test_parse_date_dd_mon_yyyy(self) -> None:
        assert _parse_date("15-Apr-2024") == datetime(
            2024, 4, 15, tzinfo=timezone.utc
        )


class TestNormalize:
    @pytest.mark.asyncio
    async def test_entity_event(self) -> None:
        raw = ICIJOffshoreRawRecord(
            node_id="node-1234567",
            name="Offshore Holdings Ltd.",
            iso3="CHN",
            jurisdiction="BVI",
            leak_source="Pandora Papers",
            incorporation_date=datetime(2018, 6, 1, tzinfo=timezone.utc),
        )
        event = await ICIJOffshoreSource().normalize(raw)
        assert event.actor_iso3 == "CHN"
        assert event.domain is EventDomain.economic
        assert event.event_type == "offshore_entity_pandora_papers"
        assert event.payload["_dedup_key"] == "icij_offshore:node-1234567"
