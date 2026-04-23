"""SEC EDGAR filings adapter.

Data source
-----------
EDGAR's full-text search endpoint at ``https://efts.sec.gov/LATEST/search-index``
returns filings matching a keyword + date range.  We probe a small set of
high-signal phrases ("Taiwan", "China sanctions", "TSMC", "PRC export
controls") on each run and emit one Event per filing within the window.

Auth
----
None — but EDGAR enforces a `User-Agent` policy.  We send the
``SEC_EDGAR_USER_AGENT`` env var (or a sensible default) on every request.
Rate limit: 10 req/s.

Dedup key
---------
``sec_edgar:{accession}`` where ``accession`` is the SEC-assigned filing ID.
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

_EFTS_URL = "https://efts.sec.gov/LATEST/search-index"

# Keyword → (mention iso3, label).  Each query produces one set of hits;
# we tag the resulting events with the implied target country.
_QUERIES: list[tuple[str, str | None, str]] = [
    ("Taiwan AND (risk OR exposure)", "TWN", "TWN risk-disclosure mentions"),
    ('"PRC export controls"', "CHN", "PRC export-control mentions"),
    ('"China sanctions"', "CHN", "China sanctions mentions"),
    ('"TSMC"', "TWN", "TSMC mentions"),
    ('"semiconductor export"', "CHN", "semiconductor-export mentions"),
]

_DEFAULT_UA = "swarm-research contact@example.com"


class SECEdgarRawRecord(BaseModel):
    """Typed representation of one EDGAR full-text search hit."""

    accession: str
    form_type: str
    filing_date: datetime | None = None
    company_name: str
    cik: str
    keyword: str
    target_iso3: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


def _parse_filing_date(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class SECEdgarSource(Source):
    """SEC EDGAR filings adapter — emits one Event per matched filing."""

    name: ClassVar[str] = "sec_edgar"
    display_name: ClassVar[str] = "SEC EDGAR"

    async def fetch(
        self, since: datetime, until: datetime
    ) -> AsyncIterator[RawRecord]:
        ua = os.environ.get("SEC_EDGAR_USER_AGENT", _DEFAULT_UA)
        headers = {"User-Agent": ua, "Accept": "application/json"}

        date_range = f"custom&startdt={since.strftime('%Y-%m-%d')}&enddt={until.strftime('%Y-%m-%d')}"
        for keyword, target_iso3, _label in _QUERIES:
            params = {
                "q": keyword,
                "dateRange": "custom",
                "startdt": since.strftime("%Y-%m-%d"),
                "enddt": until.strftime("%Y-%m-%d"),
                "forms": "8-K,10-K,20-F,10-Q",
            }
            try:
                response = await self._get(_EFTS_URL, params=params, headers=headers)
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "sec_edgar.fetch_failed", keyword=keyword, error=str(exc)
                )
                continue

            hits = (payload.get("hits") or {}).get("hits") or []
            for hit in hits:
                source = hit.get("_source") or {}
                accession = (
                    hit.get("_id")
                    or source.get("adsh")
                    or source.get("accession_no")
                )
                if not accession:
                    continue
                filing_date = _parse_filing_date(source.get("file_date"))
                if filing_date is None or not (since <= filing_date < until):
                    continue
                companies = source.get("display_names") or []
                ciks = source.get("ciks") or []
                yield SECEdgarRawRecord(
                    accession=str(accession),
                    form_type=str(source.get("form") or "?"),
                    filing_date=filing_date,
                    company_name=str(companies[0] if companies else "?"),
                    cik=str(ciks[0] if ciks else ""),
                    keyword=keyword,
                    target_iso3=target_iso3,
                    raw={
                        "accession": accession,
                        "form": source.get("form"),
                        "file_date": source.get("file_date"),
                        "display_names": companies,
                        "ciks": ciks,
                    },
                )

    async def normalize(self, raw: RawRecord) -> Event:
        assert isinstance(raw, SECEdgarRawRecord)
        occurred_at = raw.filing_date or datetime.now(timezone.utc)
        dedup_key = f"sec_edgar:{raw.accession}"
        return Event(
            source="sec_edgar",
            occurred_at=occurred_at,
            actor_iso3="USA",  # filings are with the SEC
            target_iso3=raw.target_iso3,
            event_type=f"edgar_filing_{raw.form_type.lower().replace('-', '_')}",
            domain=EventDomain.economic,
            severity=None,
            payload={
                "_dedup_key": dedup_key,
                "accession": raw.accession,
                "form_type": raw.form_type,
                "company_name": raw.company_name,
                "cik": raw.cik,
                "keyword": raw.keyword,
                "filing_date": raw.filing_date.isoformat() if raw.filing_date else None,
            },
            raw_text=(
                f"EDGAR {raw.form_type}: {raw.company_name} "
                f"(matched '{raw.keyword}')"
            ),
        )
