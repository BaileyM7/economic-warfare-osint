"""ACLED (Armed Conflict Location & Event Data) adapter.

Data source
-----------
ACLED API: https://apidocs.acleddata.com/
Requires env vars:
  ACLED_API_KEY   — developer API key
  ACLED_EMAIL     — email registered with ACLED
  ACLED_PASSWORD  — (optional) account password; when present, the adapter
                    uses ACLED's OAuth2 password flow to obtain a bearer
                    token. Newer ACLED accounts require this — legacy
                    ``?key=&email=`` query-param auth is being deprecated.

Auth flow
---------
If ACLED_PASSWORD is set:
  POST https://acleddata.com/oauth/token
       grant_type=password
       username=<ACLED_EMAIL>
       password=<ACLED_PASSWORD>
       client_id=acled
  → bearer token → sent as ``Authorization: Bearer <token>`` on /acled/read.

If ACLED_PASSWORD is NOT set:
  Legacy URL-param auth: ``?key=<ACLED_API_KEY>&email=<ACLED_EMAIL>``.

Endpoint used
-------------
GET https://api.acleddata.com/acled/read
  &iso=<comma-separated ISO numeric codes>  (see SLICE_ISO_NUMERIC)
  &event_date=<YYYY-MM-DD|YYYY-MM-DD>
  &fields=<selected fields>
  &limit=<page_size>
  &page=<page>

Event-type → Domain mapping
---------------------------
ACLED event_type values and their Domain mapping:
  "Battles"                     → kinetic_limited
  "Violence against civilians"  → kinetic_limited
  "Explosions/Remote violence"  → kinetic_limited  (may escalate; see severity)
  "Riots"                       → kinetic_limited
  "Protests"                    → diplomatic
  "Strategic developments"      → info

Severity derivation
-------------------
ACLED ``fatalities`` field is used as a proxy.  Normalized to 0–10:
  0 fatalities   → 1.0
  1–9 fatalities → 2.0 + (fatalities / 9) * 4  (2–6)
  ≥10 fatalities → 6.0 + min(fatalities / 100, 1) * 4  (6–10)

Dedup key
---------
``acled:{data_id}`` — ACLED's unique integer event ID.
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

ACLED_BASE_URL = "https://api.acleddata.com/acled/read"
ACLED_PAGE_SIZE = 500

# ISO numeric codes for the 10 vertical-slice countries
# (ACLED uses ISO numeric, not alpha-3)
SLICE_ISO_NUMERIC: list[str] = [
    "156",  # CHN
    "158",  # TWN
    "840",  # USA
    "392",  # JPN
    "410",  # KOR
    "608",  # PHL
    "36",   # AUS
    "408",  # PRK
    "643",  # RUS
    "356",  # IND
]

# ACLED uses ISO numeric → our ISO3
_ISO_NUMERIC_TO_ISO3: dict[str, str] = {
    "156": "CHN",
    "158": "TWN",
    "840": "USA",
    "392": "JPN",
    "410": "KOR",
    "608": "PHL",
    "36":  "AUS",
    "408": "PRK",
    "643": "RUS",
    "356": "IND",
}

# ACLED country name → ISO3 fallback (for actor/assoc_actor fields)
_COUNTRY_NAME_TO_ISO3: dict[str, str] = {
    "China": "CHN",
    "Taiwan": "TWN",
    "United States": "USA",
    "Japan": "JPN",
    "South Korea": "KOR",
    "Philippines": "PHL",
    "Australia": "AUS",
    "North Korea": "PRK",
    "Russia": "RUS",
    "India": "IND",
}


def _event_type_to_domain(event_type: str) -> EventDomain:
    """Map ACLED event_type string to project Domain."""
    mapping: dict[str, EventDomain] = {
        "Battles": EventDomain.kinetic_limited,
        "Violence against civilians": EventDomain.kinetic_limited,
        "Explosions/Remote violence": EventDomain.kinetic_limited,
        "Riots": EventDomain.kinetic_limited,
        "Protests": EventDomain.diplomatic,
        "Strategic developments": EventDomain.info,
    }
    return mapping.get(event_type, EventDomain.kinetic_limited)


def _fatalities_to_severity(fatalities: int) -> float:
    """Convert fatality count to normalized severity 0–10."""
    if fatalities == 0:
        return 1.0
    if fatalities < 10:
        return round(2.0 + (fatalities / 9.0) * 4.0, 2)
    return round(min(6.0 + (fatalities / 100.0) * 4.0, 10.0), 2)


# ---------------------------------------------------------------------------
# Raw record model
# ---------------------------------------------------------------------------

class ACLEDRawRecord(BaseModel):
    """Typed representation of one ACLED API result row."""

    data_id: str                  # ACLED unique event ID
    event_id_cnty: str            # Country-specific event ID
    event_date: str               # YYYY-MM-DD
    year: str
    time_precision: int
    event_type: str
    sub_event_type: str
    actor1: str
    assoc_actor_1: str
    inter1: str
    actor2: str
    assoc_actor_2: str
    inter2: str
    interaction: str
    civilian_targeting: str
    iso: str                      # ISO numeric
    region: str
    country: str
    admin1: str
    admin2: str
    admin3: str
    location: str
    latitude: str
    longitude: str
    geo_precision: int
    source: str
    source_scale: str
    notes: str
    fatalities: int
    tags: str
    timestamp: str


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class ACLEDSource(Source):
    """ACLED API adapter for conflict events.

    Fetches paginated results for the 10 vertical-slice countries within the
    given date window.  Requires ``ACLED_API_KEY`` and ``ACLED_EMAIL`` env vars.
    """

    name: ClassVar[str] = "acled"
    display_name: ClassVar[str] = "ACLED Armed Conflict"

    ACLED_OAUTH_URL: ClassVar[str] = "https://acleddata.com/oauth/token"

    def _credentials(self) -> tuple[str, str, str | None]:
        """Return (api_key, email, password_or_None) from env or raise.

        ``api_key`` + ``email`` are always required. ``password`` is optional —
        when present, OAuth2 bearer-token auth is used instead of URL params.
        """
        api_key = os.environ.get("ACLED_API_KEY", "")
        email = os.environ.get("ACLED_EMAIL", "")
        if not api_key or not email:
            raise RuntimeError(
                "ACLED_API_KEY and ACLED_EMAIL env vars must be set"
            )
        password = os.environ.get("ACLED_PASSWORD") or None
        return api_key, email, password

    async def _get_bearer_token(self, email: str, password: str) -> str:
        """Exchange email/password for an OAuth2 bearer token."""
        response = await self._post_form(
            self.ACLED_OAUTH_URL,
            data={
                "grant_type": "password",
                "username": email,
                "password": password,
                "client_id": "acled",
            },
        )
        token: str = response.json().get("access_token", "")
        if not token:
            raise RuntimeError("ACLED OAuth2 token response missing access_token")
        return token

    async def fetch(
        self, since: datetime, until: datetime
    ) -> AsyncIterator[RawRecord]:
        """Yield ACLEDRawRecord for every event in the slice countries."""
        api_key, email, password = self._credentials()
        date_range = (
            f"{since.strftime('%Y-%m-%d')}|{until.strftime('%Y-%m-%d')}"
        )
        iso_filter = ":OR:".join(SLICE_ISO_NUMERIC)

        # If password is set, use OAuth2 bearer; otherwise fall back to legacy
        # URL-param auth. The newer ACLED accounts require OAuth2.
        headers: dict[str, str] = {}
        use_bearer = password is not None
        if use_bearer:
            token = await self._get_bearer_token(email, password)  # type: ignore[arg-type]
            headers["Authorization"] = f"Bearer {token}"

        page = 1
        while True:
            params: dict[str, Any] = {
                "iso": iso_filter,
                "event_date": date_range,
                "event_date_where": "BETWEEN",
                "limit": ACLED_PAGE_SIZE,
                "page": page,
                "fields": (
                    "data_id|event_id_cnty|event_date|year|time_precision|"
                    "event_type|sub_event_type|actor1|assoc_actor_1|inter1|"
                    "actor2|assoc_actor_2|inter2|interaction|civilian_targeting|"
                    "iso|region|country|admin1|admin2|admin3|location|"
                    "latitude|longitude|geo_precision|source|source_scale|"
                    "notes|fatalities|tags|timestamp"
                ),
            }
            # Only send key/email on the legacy path; on OAuth2 they're
            # redundant and the bearer token is authoritative.
            if not use_bearer:
                params["key"] = api_key
                params["email"] = email

            response = await self._get(ACLED_BASE_URL, params=params, headers=headers)
            data = response.json()
            rows = data.get("data", [])

            if not rows:
                break

            for row in rows:
                try:
                    yield ACLEDRawRecord(
                        data_id=str(row.get("data_id", "")),
                        event_id_cnty=str(row.get("event_id_cnty", "")),
                        event_date=str(row.get("event_date", "")),
                        year=str(row.get("year", "")),
                        time_precision=int(row.get("time_precision", 0)),
                        event_type=str(row.get("event_type", "")),
                        sub_event_type=str(row.get("sub_event_type", "")),
                        actor1=str(row.get("actor1", "")),
                        assoc_actor_1=str(row.get("assoc_actor_1", "")),
                        inter1=str(row.get("inter1", "")),
                        actor2=str(row.get("actor2", "")),
                        assoc_actor_2=str(row.get("assoc_actor_2", "")),
                        inter2=str(row.get("inter2", "")),
                        interaction=str(row.get("interaction", "")),
                        civilian_targeting=str(row.get("civilian_targeting", "")),
                        iso=str(row.get("iso", "")),
                        region=str(row.get("region", "")),
                        country=str(row.get("country", "")),
                        admin1=str(row.get("admin1", "")),
                        admin2=str(row.get("admin2", "")),
                        admin3=str(row.get("admin3", "")),
                        location=str(row.get("location", "")),
                        latitude=str(row.get("latitude", "")),
                        longitude=str(row.get("longitude", "")),
                        geo_precision=int(row.get("geo_precision", 0)),
                        source=str(row.get("source", "")),
                        source_scale=str(row.get("source_scale", "")),
                        notes=str(row.get("notes", "")),
                        fatalities=int(row.get("fatalities", 0)),
                        tags=str(row.get("tags", "")),
                        timestamp=str(row.get("timestamp", "")),
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("acled.row_parse_error", error=str(exc))

            # ACLED returns fewer rows than page_size on the last page
            if len(rows) < ACLED_PAGE_SIZE:
                break
            page += 1

    async def normalize(self, raw: RawRecord) -> Event:
        """Map an ACLEDRawRecord to the canonical Event ORM model."""
        assert isinstance(raw, ACLEDRawRecord)

        try:
            occurred_at = datetime.strptime(raw.event_date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            occurred_at = datetime.now(timezone.utc)

        domain = _event_type_to_domain(raw.event_type)
        severity = _fatalities_to_severity(raw.fatalities)

        # Actor → ISO3: use country field for the territorial ISO, actor for
        # the agent.  ACLED events are located in a country, actor1 is the
        # initiating entity.
        actor_iso3 = _ISO_NUMERIC_TO_ISO3.get(raw.iso) or _COUNTRY_NAME_TO_ISO3.get(
            raw.country
        )
        # target is often blank; try to infer from assoc_actor_2 / actor2 country
        target_iso3: str | None = None
        for name in (raw.actor2, raw.assoc_actor_2):
            for country_name, iso3 in _COUNTRY_NAME_TO_ISO3.items():
                if country_name.lower() in name.lower() and iso3 != actor_iso3:
                    target_iso3 = iso3
                    break
            if target_iso3:
                break

        dedup_key = f"acled:{raw.data_id}"
        payload: dict[str, Any] = {
            "_dedup_key": dedup_key,
            "data_id": raw.data_id,
            "event_id_cnty": raw.event_id_cnty,
            "event_type": raw.event_type,
            "sub_event_type": raw.sub_event_type,
            "actor1": raw.actor1,
            "actor2": raw.actor2,
            "location": raw.location,
            "admin1": raw.admin1,
            "country": raw.country,
            "latitude": raw.latitude,
            "longitude": raw.longitude,
            "fatalities": raw.fatalities,
            "source": raw.source,
            "source_scale": raw.source_scale,
            "tags": raw.tags,
        }

        return Event(
            source="acled",
            occurred_at=occurred_at,
            actor_iso3=actor_iso3,
            target_iso3=target_iso3,
            event_type=f"{raw.event_type} / {raw.sub_event_type}",
            domain=domain,  # type: ignore[arg-type]
            severity=severity,
            payload=payload,
            raw_text=raw.notes[:2000] if raw.notes else None,
        )
