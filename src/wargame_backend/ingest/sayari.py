"""Sayari Graph corporate-intelligence adapter.

Data source
-----------
Sayari Graph exposes a search endpoint at
``https://api.sayari.com/v1/search/entity`` returning entities with
beneficial-ownership graph context.  We probe the slice country list,
get per-country recent results, and emit one Event per entity.

Auth
----
OAuth2 client_credentials.  Requires ``SAYARI_API_KEY`` (client_id) and
``SAYARI_CLIENT_SECRET``.  We exchange these for a bearer token at
``https://api.sayari.com/oauth/token`` once per fetch and cache it
per-adapter-instance.

Dedup key
---------
``sayari:{entity_id}:{updated_date}``
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any, ClassVar

import structlog
from pydantic import BaseModel, Field

from wargame_backend.app.db.models import Event, EventDomain
from ingest.base import Source, RawRecord

log = structlog.get_logger(__name__)

_TOKEN_URL = "https://api.sayari.com/oauth/token"
_SEARCH_URL = "https://api.sayari.com/v1/search/entity"

_ISO3_TO_ISO2: dict[str, str] = {
    "CHN": "CN", "TWN": "TW", "USA": "US", "JPN": "JP", "KOR": "KR",
    "PHL": "PH", "AUS": "AU", "PRK": "KP", "RUS": "RU", "IND": "IN",
}


class SayariRawRecord(BaseModel):
    """Typed representation of one Sayari entity hit."""

    entity_id: str
    name: str
    entity_type: str
    iso3: str
    risk_factors: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


def _parse_iso(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class SayariSource(Source):
    """Sayari Graph adapter — emits one Event per entity per slice country."""

    name: ClassVar[str] = "sayari"
    display_name: ClassVar[str] = "Sayari Graph"

    def __init__(self) -> None:
        super().__init__()
        self._bearer_token: str | None = None

    @property
    def enabled(self) -> bool:
        if not super().enabled:
            return False
        if not (
            os.environ.get("SAYARI_API_KEY")
            and os.environ.get("SAYARI_CLIENT_SECRET")
        ):
            log.info("sayari.disabled_no_credentials")
            return False
        return True

    async def _get_token(self) -> str:
        if self._bearer_token:
            return self._bearer_token
        client_id = os.environ.get("SAYARI_API_KEY", "")
        client_secret = os.environ.get("SAYARI_CLIENT_SECRET", "")
        try:
            response = await self._post_form(
                _TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("sayari.token_exchange_failed", error=str(exc))
            return ""
        token = str(payload.get("access_token") or "")
        self._bearer_token = token
        return token

    async def fetch(
        self, since: datetime, until: datetime
    ) -> AsyncIterator[RawRecord]:
        token = await self._get_token()
        if not token:
            return
        headers = {"Authorization": f"Bearer {token}"}

        for iso3, iso2 in _ISO3_TO_ISO2.items():
            params = {
                "country": iso2,
                "limit": 50,
                # Sayari supports recency filtering via the query params,
                # but the exact field name varies by tier; we filter
                # client-side after fetch as a safe default.
            }
            try:
                response = await self._get(_SEARCH_URL, params=params, headers=headers)
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                log.warning("sayari.fetch_failed", iso3=iso3, error=str(exc))
                continue

            for hit in payload.get("data") or []:
                entity_id = hit.get("id") or hit.get("entity_id")
                if not entity_id:
                    continue
                updated = _parse_iso(hit.get("updated_at") or hit.get("last_seen"))
                if updated is None or not (since <= updated < until):
                    continue
                yield SayariRawRecord(
                    entity_id=str(entity_id),
                    name=str(hit.get("label") or hit.get("name") or "(unnamed)"),
                    entity_type=str(hit.get("type") or "entity"),
                    iso3=iso3,
                    risk_factors=list(hit.get("risk_factors") or []),
                    updated_at=updated,
                    raw={
                        "id": entity_id,
                        "label": hit.get("label"),
                        "type": hit.get("type"),
                        "country": iso2,
                    },
                )

    async def normalize(self, raw: RawRecord) -> Event:
        assert isinstance(raw, SayariRawRecord)
        occurred_at = raw.updated_at or datetime.now(timezone.utc)
        date_suffix = occurred_at.date().isoformat()
        dedup_key = f"sayari:{raw.entity_id}:{date_suffix}"
        return Event(
            source="sayari",
            occurred_at=occurred_at,
            actor_iso3=raw.iso3,
            target_iso3=None,
            event_type=f"sayari_entity_{raw.entity_type.lower()}",
            domain=EventDomain.economic,
            severity=None,
            payload={
                "_dedup_key": dedup_key,
                "entity_id": raw.entity_id,
                "name": raw.name,
                "entity_type": raw.entity_type,
                "iso3": raw.iso3,
                "risk_factors": raw.risk_factors,
                "updated_at": occurred_at.isoformat(),
            },
            raw_text=f"Sayari {raw.entity_type}: {raw.name} ({raw.iso3})",
        )
