"""GDELT 2.0 Events CSV feed adapter.

Data source
-----------
GDELT 2.0 publishes a 15-minute CSV feed at:
  http://data.gdeltproject.org/gdeltv2/YYYYMMDDHHMMSS.export.CSV.zip

The master file list lives at:
  http://data.gdeltproject.org/gdeltv2/lastupdate.txt

Each CSV row represents one news-derived geopolitical event using the CAMEO
coding scheme.  We pull every update file whose timestamp falls within [since,
until), decompress, and filter to rows where actor1 or actor2 is one of the 10
vertical-slice countries.

CAMEO → Domain mapping is handled by ``ingest.cameo.cameo_to_domain``.

Severity derivation
-------------------
GDELT's Goldstein scale runs from -10 (most conflictual) to +10 (most
cooperative).  We normalize it to 0–10 by: ``severity = (10 - goldstein) / 2``.
This means goldstein=-10 → severity=10 and goldstein=+10 → severity=0.

Dedup key
---------
``{source}:{GlobalEventID}`` — GDELT assigns a unique integer ID per event row.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar

import structlog
from pydantic import BaseModel, Field

from wargame_backend.app.db.models import Event
from ingest.base import Source, RawRecord, raise_for_retryable
from ingest.cameo import cameo_to_domain

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Vertical-slice country filter (ISO2 codes as used in GDELT actor fields)
# ---------------------------------------------------------------------------
SLICE_ISO2: frozenset[str] = frozenset(
    {
        "CH",  # CHN
        "TW",  # TWN
        "US",  # USA
        "JA",  # JPN (GDELT uses JA not JP)
        "KS",  # KOR (South Korea)
        "RP",  # PHL
        "AS",  # AUS
        "KN",  # PRK (North Korea)
        "RS",  # RUS
        "IN",  # IND
    }
)

# ---------------------------------------------------------------------------
# GDELT column indices (zero-based) for the 2.0 export CSV
# Full schema: https://www.gdeltproject.org/data/documentation/GDELT-Event_Codebook-V2.0.pdf
# ---------------------------------------------------------------------------
COL = {
    "GlobalEventID": 0,
    "SQLDATE": 1,
    "Actor1Code": 2,
    "Actor1Name": 3,
    "Actor1CountryCode": 5,
    "Actor2Code": 6,
    "Actor2Name": 7,
    "Actor2CountryCode": 9,
    "EventCode": 26,
    "EventBaseCode": 27,
    "EventRootCode": 28,
    "GoldsteinScale": 30,
    "NumMentions": 31,
    "NumSources": 32,
    "NumArticles": 33,
    "AvgTone": 34,
    "Actor1Geo_CountryCode": 37,
    "Actor2Geo_CountryCode": 42,
    "ActionGeo_CountryCode": 47,
    "DATEADDED": 59,
    "SOURCEURL": 60,
}


# ---------------------------------------------------------------------------
# Raw record model
# ---------------------------------------------------------------------------

class GDELTRawRecord(BaseModel):
    """Typed representation of one GDELT 2.0 CSV row."""

    global_event_id: str
    sql_date: str                 # YYYYMMDD
    actor1_country_code: str      # GDELT ISO2-like code
    actor2_country_code: str
    event_code: str               # CAMEO code string
    goldstein_scale: float | None
    avg_tone: float | None
    source_url: str
    num_mentions: int
    actor1_name: str
    actor2_name: str
    # Keep the full row for payload
    raw_row: dict[str, Any]


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

GDELT_LASTUPDATE_URL = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"


def _parse_lastupdate(text: str) -> list[tuple[str, str]]:
    """Parse GDELT lastupdate.txt into list of (size, url) for export files."""
    results: list[tuple[str, str]] = []
    for line in text.strip().splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2].endswith(".export.CSV.zip"):
            results.append((parts[0], parts[2]))
    return results


def _gdelt_file_url_for_dt(dt: datetime) -> str:
    """Build the GDELT 2.0 export zip URL for a specific 15-min slot."""
    # Round down to nearest 15 minutes
    minute = (dt.minute // 15) * 15
    snapped = dt.replace(minute=minute, second=0, microsecond=0)
    ts = snapped.strftime("%Y%m%d%H%M%S")
    return f"http://data.gdeltproject.org/gdeltv2/{ts}.export.CSV.zip"


def _gdelt_file_urls_in_window(since: datetime, until: datetime) -> list[str]:
    """Return all 15-min GDELT export URLs covering [since, until)."""
    urls: list[str] = []
    slot = since.replace(
        minute=(since.minute // 15) * 15, second=0, microsecond=0
    )
    while slot < until:
        urls.append(_gdelt_file_url_for_dt(slot))
        slot += timedelta(minutes=15)
    return urls


def _safe_float(val: str) -> float | None:
    try:
        return float(val) if val.strip() else None
    except ValueError:
        return None


def _safe_int(val: str, default: int = 0) -> int:
    try:
        return int(val) if val.strip() else default
    except ValueError:
        return default


def _row_to_raw(row: list[str]) -> GDELTRawRecord | None:
    """Convert a CSV row list to a GDELTRawRecord; return None on bad rows."""
    try:
        if len(row) < 61:
            return None
        a1cc = row[COL["Actor1CountryCode"]].strip()
        a2cc = row[COL["Actor2CountryCode"]].strip()
        # Filter: at least one actor must be in the slice
        if a1cc not in SLICE_ISO2 and a2cc not in SLICE_ISO2:
            return None

        return GDELTRawRecord(
            global_event_id=row[COL["GlobalEventID"]].strip(),
            sql_date=row[COL["SQLDATE"]].strip(),
            actor1_country_code=a1cc,
            actor2_country_code=a2cc,
            event_code=row[COL["EventCode"]].strip(),
            goldstein_scale=_safe_float(row[COL["GoldsteinScale"]]),
            avg_tone=_safe_float(row[COL["AvgTone"]]),
            source_url=row[COL["SOURCEURL"]].strip() if len(row) > COL["SOURCEURL"] else "",
            num_mentions=_safe_int(row[COL["NumMentions"]]),
            actor1_name=row[COL["Actor1Name"]].strip(),
            actor2_name=row[COL["Actor2Name"]].strip(),
            raw_row={
                "GlobalEventID": row[COL["GlobalEventID"]],
                "SQLDATE": row[COL["SQLDATE"]],
                "Actor1CountryCode": a1cc,
                "Actor2CountryCode": a2cc,
                "EventCode": row[COL["EventCode"]],
                "GoldsteinScale": row[COL["GoldsteinScale"]],
                "AvgTone": row[COL["AvgTone"]],
                "NumMentions": row[COL["NumMentions"]],
                "NumSources": row[COL["NumSources"]],
                "SOURCEURL": row[COL["SOURCEURL"]] if len(row) > COL["SOURCEURL"] else "",
            },
        )
    except Exception:  # noqa: BLE001
        return None


# GDELT ISO2 → ISO3 mapping for the 10 slice countries
_GDELT_TO_ISO3: dict[str, str] = {
    "CH": "CHN",
    "TW": "TWN",
    "US": "USA",
    "JA": "JPN",
    "KS": "KOR",
    "RP": "PHL",
    "AS": "AUS",
    "KN": "PRK",
    "RS": "RUS",
    "IN": "IND",
}


class GDELTSource(Source):
    """GDELT 2.0 Events CSV feed adapter.

    Pulls 15-minute export ZIP files, filters to vertical-slice countries,
    and normalizes to the canonical Event model using CAMEO → Domain mapping.
    """

    name: ClassVar[str] = "gdelt"
    display_name: ClassVar[str] = "GDELT 2.0"

    async def fetch(
        self, since: datetime, until: datetime
    ) -> AsyncIterator[RawRecord]:
        """Yield GDELTRawRecord for every filtered row in [since, until)."""
        urls = _gdelt_file_urls_in_window(since, until)
        log.info("gdelt.fetch_urls", count=len(urls), since=since, until=until)

        for url in urls:
            try:
                response = await self._get(url)
                zip_bytes = response.content
                with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                    csv_name = next(
                        (n for n in zf.namelist() if n.endswith(".CSV")), None
                    )
                    if csv_name is None:
                        log.warning("gdelt.no_csv_in_zip", url=url)
                        continue
                    with zf.open(csv_name) as csv_file:
                        reader = csv.reader(
                            io.TextIOWrapper(csv_file, encoding="utf-8"),
                            delimiter="\t",
                        )
                        for row in reader:
                            record = _row_to_raw(row)
                            if record is not None:
                                yield record
            except Exception as exc:  # noqa: BLE001
                log.warning("gdelt.file_error", url=url, error=str(exc))
                continue

    async def normalize(self, raw: RawRecord) -> Event:
        """Map a GDELTRawRecord to the canonical Event ORM model."""
        assert isinstance(raw, GDELTRawRecord)

        # Parse SQLDATE YYYYMMDD
        try:
            occurred_at = datetime.strptime(raw.sql_date, "%Y%m%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            occurred_at = datetime.now(timezone.utc)

        domain = cameo_to_domain(raw.event_code, raw.goldstein_scale)

        # Severity: normalize Goldstein (-10..+10) → (0..10)
        severity: float | None = None
        if raw.goldstein_scale is not None:
            severity = round((10.0 - raw.goldstein_scale) / 2.0, 2)
            severity = max(0.0, min(10.0, severity))

        actor_iso3 = _GDELT_TO_ISO3.get(raw.actor1_country_code)
        target_iso3 = _GDELT_TO_ISO3.get(raw.actor2_country_code)

        dedup_key = f"gdelt:{raw.global_event_id}"
        payload = dict(raw.raw_row)
        payload["_dedup_key"] = dedup_key

        raw_text = (
            f"{raw.actor1_name} → {raw.actor2_name} [{raw.event_code}]"
            if raw.actor1_name or raw.actor2_name
            else None
        )

        return Event(
            source="gdelt",
            occurred_at=occurred_at,
            actor_iso3=actor_iso3,
            target_iso3=target_iso3,
            event_type=raw.event_code,
            domain=domain,  # type: ignore[arg-type]
            severity=severity,
            payload=payload,
            raw_text=raw_text,
        )
