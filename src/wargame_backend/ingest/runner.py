"""CLI entry point for running data-lake ingest adapters.

Usage
-----
    python -m src.backend.ingest.runner --sources=gdelt,acled,worldbank --since=2025-01-01

Options
-------
--sources   Comma-separated list of adapter names, or ``all`` to run every
            enabled adapter.
--since     ISO-8601 date or datetime (UTC assumed if no tz).  Defaults to
            24 hours ago.
--until     ISO-8601 date or datetime.  Defaults to now.
--dry-run   Print which adapters would run without executing.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from typing import Sequence

import structlog

from wargame_backend.app.db.session import AsyncSessionLocal
from ingest.base import IngestionRunResult, Source
from ingest.gdelt import GDELTSource
from ingest.acled import ACLEDSource
from ingest.worldbank import WorldBankSource
from ingest.fred import FREDSource
from ingest.un_comtrade import UNComtradeSource
from ingest.imf import IMFSource
from ingest.ofac_sdn import OFACSDNSource
from ingest.open_sanctions import OpenSanctionsSource
from ingest.opencorporates import OpenCorporatesSource
from ingest.sec_edgar import SECEdgarSource
from ingest.gleif import GLEIFSource
from ingest.datalastic import DatalasticSource
from ingest.trade_gov import TradeGovSource
from ingest.marinecadastre_ais import MarineCadastreAISSource
from ingest.icij_offshore import ICIJOffshoreSource
from ingest.sayari import SayariSource
from ingest.eia import EIASource
from ingest.yfinance import YFinanceSource

log = structlog.get_logger(__name__)

# Registry: name → source instance
_ALL_SOURCES: dict[str, Source] = {
    s.name: s
    for s in [
        GDELTSource(),
        ACLEDSource(),
        WorldBankSource(),
        FREDSource(),
        UNComtradeSource(),
        IMFSource(),
        OFACSDNSource(),
        OpenSanctionsSource(),
        OpenCorporatesSource(),
        SECEdgarSource(),
        GLEIFSource(),
        DatalasticSource(),
        TradeGovSource(),
        MarineCadastreAISSource(),
        ICIJOffshoreSource(),
        SayariSource(),
        EIASource(),
        YFinanceSource(),
    ]
}


def _parse_dt(value: str) -> datetime:
    """Parse an ISO-8601 date or datetime string; attach UTC if no tz."""
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    raise ValueError(f"Cannot parse datetime: {value!r}")


def _resolve_sources(names: str) -> list[Source]:
    """Resolve a comma-separated source list or 'all' to Source instances."""
    if names.lower() == "all":
        return list(_ALL_SOURCES.values())
    result: list[Source] = []
    for name in names.split(","):
        name = name.strip()
        if name not in _ALL_SOURCES:
            log.warning("runner.unknown_source", name=name)
            continue
        result.append(_ALL_SOURCES[name])
    return result


async def _run_source(
    source: Source,
    since: datetime,
    until: datetime,
) -> IngestionRunResult:
    """Run a single source inside its own DB session."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            try:
                result = await source.run(session, since, until)
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "runner.source_error",
                    source=source.name,
                    error=str(exc),
                )
                return IngestionRunResult(
                    source=source.name,
                    since=since,
                    until=until,
                    errors=[str(exc)],
                )
            finally:
                await source.close()
    return result


async def ingest_all(
    sources: list[Source],
    since: datetime,
    until: datetime,
) -> list[IngestionRunResult]:
    """Run all provided sources concurrently.

    Parameters
    ----------
    sources:
        List of ``Source`` instances to run.
    since:
        Window start (UTC-aware).
    until:
        Window end (UTC-aware).

    Returns
    -------
    list[IngestionRunResult]
        One result per source, in arbitrary completion order.
    """
    tasks = [_run_source(s, since, until) for s in sources if s.enabled]
    results: list[IngestionRunResult] = await asyncio.gather(*tasks)
    return list(results)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ingest.runner",
        description="Swarm data-lake ingestion CLI",
    )
    parser.add_argument(
        "--sources",
        default="all",
        help="Comma-separated adapter names or 'all'.  Default: all.",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="Start datetime ISO-8601.  Default: 24 hours ago.",
    )
    parser.add_argument(
        "--until",
        default=None,
        help="End datetime ISO-8601.  Default: now.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print which adapters would run without executing.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; returns exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    since = _parse_dt(args.since) if args.since else now - timedelta(hours=24)
    until = _parse_dt(args.until) if args.until else now

    sources = _resolve_sources(args.sources)
    enabled = [s for s in sources if s.enabled]

    if args.dry_run:
        print(f"Would run {len(enabled)} adapter(s):")
        for s in enabled:
            print(f"  - {s.name} ({s.display_name})")
        print(f"Window: {since.isoformat()} → {until.isoformat()}")
        return 0

    log.info(
        "runner.start",
        sources=[s.name for s in enabled],
        since=since,
        until=until,
    )

    results = asyncio.run(ingest_all(enabled, since, until))

    exit_code = 0
    for r in results:
        status = "OK" if r.success else "ERRORS"
        print(
            f"[{status}] {r.source}: "
            f"fetched={r.fetched} upserted={r.upserted} skipped={r.skipped} "
            f"errors={len(r.errors)}"
        )
        if r.errors:
            for err in r.errors[:5]:
                print(f"       {err}")
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
