"""MarineCadastre AIS vessel-position adapter.

Data source
-----------
NOAA / USCG publish AIS positions as zipped daily CSV files at
``https://coast.noaa.gov/htdata/CMSP/AISDataHandler/{year}/AIS_{YYYY_MM_DD}.zip``
covering US coastal waters.  We pull the day's file, filter to vessels
within the watch boxes around Pearl Harbor and Guam (Pacific Fleet
forward operating bases), aggregate by flag, and emit one Event per
(date, base, flag).

Auth
----
None.

Dedup key
---------
``marinecadastre_ais:{date}:{base}:{flag_iso2}``
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

from wargame_backend.app.db.models import Event, EventDomain
from ingest.base import Source, RawRecord

log = structlog.get_logger(__name__)

# Watch boxes around US Pacific bases (lat min/max, lon min/max).
_WATCH_BASES: list[dict[str, Any]] = [
    {
        "base": "PearlHarbor",
        "lat_min": 21.30,
        "lat_max": 21.40,
        "lon_min": -157.99,
        "lon_max": -157.92,
    },
    {
        "base": "Guam",
        "lat_min": 13.40,
        "lat_max": 13.50,
        "lon_min": 144.60,
        "lon_max": 144.75,
    },
]

# AIS MID prefix (first 3 digits of MMSI) → ISO3 (slice only).  This is
# the standard mapping for vessel flag-of-registration.
_MID_TO_ISO3: dict[str, str] = {
    "412": "CHN", "413": "CHN", "414": "CHN",
    "416": "TWN",
    "338": "USA", "366": "USA", "367": "USA", "368": "USA", "369": "USA",
    "431": "JPN", "432": "JPN",
    "440": "KOR", "441": "KOR",
    "445": "PRK",
    "548": "PHL",
    "503": "AUS",
    "273": "RUS",
    "419": "IND",
}

_DAILY_URL = (
    "https://coast.noaa.gov/htdata/CMSP/AISDataHandler/"
    "{year}/AIS_{year}_{month:02d}_{day:02d}.zip"
)


def _flag_from_mmsi(mmsi: str) -> str | None:
    return _MID_TO_ISO3.get(mmsi[:3]) if mmsi and mmsi.isdigit() else None


def _in_box(lat: float, lon: float, box: dict[str, Any]) -> bool:
    return (
        box["lat_min"] <= lat <= box["lat_max"]
        and box["lon_min"] <= lon <= box["lon_max"]
    )


class MarineCadastreRawRecord(BaseModel):
    """Aggregated (date, base, flag) zone-presence count."""

    date: str  # YYYY-MM-DD
    base: str
    flag_iso3: str
    ping_count: int
    raw: dict[str, Any] = Field(default_factory=dict)


def _days_in_window(since: datetime, until: datetime) -> list[datetime]:
    days: list[datetime] = []
    cur = since.replace(hour=0, minute=0, second=0, microsecond=0)
    while cur < until:
        days.append(cur)
        cur += timedelta(days=1)
    return days


class MarineCadastreAISSource(Source):
    """MarineCadastre AIS adapter — emits one Event per (day, base, flag)."""

    name: ClassVar[str] = "marinecadastre_ais"
    display_name: ClassVar[str] = "MarineCadastre AIS"

    async def fetch(
        self, since: datetime, until: datetime
    ) -> AsyncIterator[RawRecord]:
        for day in _days_in_window(since, until):
            url = _DAILY_URL.format(year=day.year, month=day.month, day=day.day)
            try:
                response = await self._get(url)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "marinecadastre.fetch_failed",
                    url=url,
                    error=str(exc),
                )
                continue

            counts: dict[tuple[str, str], int] = {}
            try:
                with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
                    csv_name = next(
                        (n for n in zf.namelist() if n.lower().endswith(".csv")),
                        None,
                    )
                    if csv_name is None:
                        continue
                    with zf.open(csv_name) as fp:
                        reader = csv.DictReader(
                            io.TextIOWrapper(fp, encoding="utf-8", errors="ignore")
                        )
                        for row in reader:
                            try:
                                lat = float(row.get("LAT") or row.get("Lat") or 0.0)
                                lon = float(row.get("LON") or row.get("Lon") or 0.0)
                            except ValueError:
                                continue
                            mmsi = str(row.get("MMSI") or row.get("Mmsi") or "")
                            flag = _flag_from_mmsi(mmsi)
                            if flag is None:
                                continue
                            for box in _WATCH_BASES:
                                if _in_box(lat, lon, box):
                                    key = (box["base"], flag)
                                    counts[key] = counts.get(key, 0) + 1
                                    break
            except (zipfile.BadZipFile, KeyError, StopIteration) as exc:
                log.warning(
                    "marinecadastre.parse_failed", url=url, error=str(exc)
                )
                continue

            date = day.strftime("%Y-%m-%d")
            for (base, flag), count in counts.items():
                yield MarineCadastreRawRecord(
                    date=date,
                    base=base,
                    flag_iso3=flag,
                    ping_count=count,
                    raw={"date": date, "base": base, "flag": flag, "count": count},
                )

    async def normalize(self, raw: RawRecord) -> Event:
        assert isinstance(raw, MarineCadastreRawRecord)
        try:
            occurred_at = datetime.strptime(raw.date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            occurred_at = datetime.now(timezone.utc)
        dedup_key = f"marinecadastre_ais:{raw.date}:{raw.base}:{raw.flag_iso3}"
        return Event(
            source="marinecadastre_ais",
            occurred_at=occurred_at,
            actor_iso3=raw.flag_iso3,
            target_iso3="USA",  # all watch boxes are US bases
            event_type=f"ais_us_base_presence_{raw.base.lower()}",
            domain=EventDomain.kinetic_limited,
            severity=None,
            payload={
                "_dedup_key": dedup_key,
                "date": raw.date,
                "base": raw.base,
                "flag_iso3": raw.flag_iso3,
                "ping_count": raw.ping_count,
            },
            raw_text=(
                f"MarineCadastre {raw.base} {raw.flag_iso3}-flag pings: "
                f"{raw.ping_count} ({raw.date})"
            ),
        )
