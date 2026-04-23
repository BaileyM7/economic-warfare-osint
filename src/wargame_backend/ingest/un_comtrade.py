"""UN Comtrade bilateral trade flows adapter.

Data source
-----------
UN Comtrade publishes bilateral monthly trade flows at
``https://comtradeapi.un.org/data/v1/get/{typeCode}/{freqCode}/{clCode}``.
We pull HS-2 commodity-level monthly imports/exports for every ordered pair
of slice countries and compute month-over-month percentage change so the
signal extractor at ``ai/sim/extractors/comtrade.py`` can surface dramatic
swings (e.g. CHN→TWN semiconductor exports −18% MoM).

Auth
----
Requires ``UN_COMTRADE_KEY`` env var; the adapter sends it in the
``Ocp-Apim-Subscription-Key`` header.  When the key is missing the adapter
disables itself (returns no records) rather than failing the ingest run.

Dedup key
---------
``comtrade:{reporter_iso3}:{partner_iso3}:{period}:{commodity_code}:{flow}``
— the natural primary key for one published cell.
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

# Vertical-slice country pairs we pull.  Comtrade uses M.49 numeric codes;
# we keep the ISO3 here and translate at request time.
SLICE_ISO3: tuple[str, ...] = (
    "CHN", "TWN", "USA", "JPN", "KOR", "PHL", "AUS", "PRK", "RUS", "IND",
)

# ISO3 → M.49 numeric code (UN Comtrade's "reporter"/"partner" parameter)
_ISO3_TO_M49: dict[str, str] = {
    "CHN": "156",
    "TWN": "490",  # "Other Asia, nes" — Comtrade reports TWN under this code
    "USA": "842",
    "JPN": "392",
    "KOR": "410",
    "PHL": "608",
    "AUS": "036",
    "PRK": "408",
    "RUS": "643",
    "IND": "699",
}

# A small set of strategically interesting HS-2 commodity classes.  Pulling
# every chapter (~99) for every pair (~90) every run is wasteful; this
# focused list keeps the request volume bounded and the signal density high.
_WATCHED_HS2: dict[str, str] = {
    "85": "electrical machinery",   # incl. semiconductors
    "84": "machinery / nuclear reactors",
    "27": "mineral fuels & oils",
    "29": "organic chemicals",
    "87": "vehicles",
    "90": "optical / precision instruments",
    "72": "iron & steel",
}

_API_BASE = "https://comtradeapi.un.org/data/v1/get"


class ComtradeRawRecord(BaseModel):
    """Typed representation of one Comtrade monthly cell + computed MoM delta."""

    reporter_iso3: str
    partner_iso3: str
    period: str  # YYYYMM
    commodity_code: str  # HS-2
    commodity_label: str
    flow: str  # "M" (import) or "X" (export)
    trade_value_usd: float
    prior_trade_value_usd: float | None = None
    mom_pct_change: float | None = None
    raw_row: dict[str, Any] = Field(default_factory=dict)


def _period_for(dt: datetime) -> str:
    return dt.strftime("%Y%m")


def _previous_period(period: str) -> str:
    year = int(period[:4])
    month = int(period[4:])
    if month == 1:
        year -= 1
        month = 12
    else:
        month -= 1
    return f"{year:04d}{month:02d}"


def _periods_in_window(since: datetime, until: datetime) -> list[str]:
    """Inclusive list of YYYYMM periods touched by [since, until)."""
    periods: list[str] = []
    cur = since.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cur < until:
        periods.append(_period_for(cur))
        # Bump to next month
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)
    return periods


def _safe_pct_change(current: float, prior: float | None) -> float | None:
    if prior is None or prior == 0:
        return None
    return round(((current - prior) / prior) * 100.0, 2)


class UNComtradeSource(Source):
    """UN Comtrade bilateral monthly trade-flows adapter.

    On each run, for every (reporter, partner, commodity, flow) cell in the
    requested window, we also fetch the previous month's value so the
    normalized Event payload includes ``mom_pct_change``.  This is what the
    Comtrade signal extractor reads at agent-perception time.
    """

    name: ClassVar[str] = "un_comtrade"
    display_name: ClassVar[str] = "UN Comtrade"

    @property
    def enabled(self) -> bool:
        if not super().enabled:
            return False
        if not os.environ.get("UN_COMTRADE_KEY"):
            log.info("comtrade.disabled_no_api_key")
            return False
        return True

    async def fetch(
        self, since: datetime, until: datetime
    ) -> AsyncIterator[RawRecord]:
        api_key = os.environ.get("UN_COMTRADE_KEY", "")
        headers = {"Ocp-Apim-Subscription-Key": api_key}

        for period in _periods_in_window(since, until):
            prior = _previous_period(period)
            for reporter in SLICE_ISO3:
                # Comtrade lets us request many partners in one call by
                # passing a comma-separated list — minimizes round-trips.
                partners = ",".join(
                    _ISO3_TO_M49[p]
                    for p in SLICE_ISO3
                    if p != reporter and p in _ISO3_TO_M49
                )
                cmd_codes = ",".join(_WATCHED_HS2.keys())
                params = {
                    "reporterCode": _ISO3_TO_M49[reporter],
                    "partnerCode": partners,
                    "period": f"{prior},{period}",
                    "cmdCode": cmd_codes,
                    "flowCode": "M,X",
                    "freqCode": "M",
                    "clCode": "HS",
                    "typeCode": "C",
                }
                # Comtrade endpoint shape: /data/v1/get/{typeCode}/{freqCode}/{clCode}
                url = f"{_API_BASE}/C/M/HS"
                try:
                    response = await self._get(url, params=params, headers=headers)
                    payload = response.json()
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "comtrade.fetch_failed",
                        reporter=reporter,
                        period=period,
                        error=str(exc),
                    )
                    continue

                rows = payload.get("data") or []
                # Bucket rows by (partner, commodity, flow, period) so we can
                # compute MoM delta within the response.
                by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
                for row in rows:
                    partner_m49 = str(row.get("partnerCode", ""))
                    partner_iso3 = _m49_to_iso3(partner_m49)
                    if partner_iso3 is None:
                        continue
                    cmd = str(row.get("cmdCode", ""))
                    flow = str(row.get("flowCode", ""))
                    p = str(row.get("period", ""))
                    by_key[(partner_iso3, cmd, flow, p)] = row

                # Emit one record per (partner, commodity, flow) for the
                # *current* period, attaching the prior period's value if
                # we got it back in the same response.
                for (partner_iso3, cmd, flow, p), row in by_key.items():
                    if p != period:
                        continue
                    current = float(row.get("primaryValue") or 0.0)
                    prior_row = by_key.get((partner_iso3, cmd, flow, prior))
                    prior_value = (
                        float(prior_row.get("primaryValue")) if prior_row else None
                    )
                    yield ComtradeRawRecord(
                        reporter_iso3=reporter,
                        partner_iso3=partner_iso3,
                        period=period,
                        commodity_code=cmd,
                        commodity_label=_WATCHED_HS2.get(cmd, cmd),
                        flow=flow,
                        trade_value_usd=current,
                        prior_trade_value_usd=prior_value,
                        mom_pct_change=_safe_pct_change(current, prior_value),
                        raw_row={k: row.get(k) for k in (
                            "reporterCode", "partnerCode", "period", "cmdCode",
                            "flowCode", "primaryValue", "qty", "qtyUnitCode",
                        )},
                    )

    async def normalize(self, raw: RawRecord) -> Event:
        assert isinstance(raw, ComtradeRawRecord)
        # Period YYYYMM → first-of-month UTC.  Comtrade is a monthly cadence;
        # we anchor to the first of the period for ordering.
        try:
            occurred_at = datetime.strptime(raw.period + "01", "%Y%m%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            occurred_at = datetime.now(timezone.utc)

        dedup_key = (
            f"comtrade:{raw.reporter_iso3}:{raw.partner_iso3}:"
            f"{raw.period}:{raw.commodity_code}:{raw.flow}"
        )
        # For an export (flow=X), reporter is the actor and partner is target.
        # For an import (flow=M), the partner is the actor (sending the goods)
        # and the reporter is the target.
        if raw.flow == "X":
            actor_iso3, target_iso3 = raw.reporter_iso3, raw.partner_iso3
        else:
            actor_iso3, target_iso3 = raw.partner_iso3, raw.reporter_iso3

        return Event(
            source="un_comtrade",
            occurred_at=occurred_at,
            actor_iso3=actor_iso3,
            target_iso3=target_iso3,
            event_type=f"trade_{raw.flow.lower()}_hs{raw.commodity_code}",
            domain=EventDomain.economic,
            severity=None,
            payload={
                "_dedup_key": dedup_key,
                "reporter_iso3": raw.reporter_iso3,
                "partner_iso3": raw.partner_iso3,
                "period": raw.period,
                "commodity_code": raw.commodity_code,
                "commodity": raw.commodity_label,
                "flow": raw.flow,
                "trade_value_usd": raw.trade_value_usd,
                "prior_trade_value_usd": raw.prior_trade_value_usd,
                "mom_pct_change": raw.mom_pct_change,
                "raw": raw.raw_row,
            },
            raw_text=(
                f"{raw.reporter_iso3}↔{raw.partner_iso3} {raw.commodity_label} "
                f"{raw.flow} {raw.period}: ${raw.trade_value_usd:,.0f}"
            ),
        )


def _m49_to_iso3(m49: str) -> str | None:
    for iso3, code in _ISO3_TO_M49.items():
        if code == m49:
            return iso3
    return None
