"""Per-source signal extractors.

Each module in this package exposes a class that implements
:class:`ai.sim.signals.SignalExtractor` for one ingest source.

Wiring convention: the ``default_extractors`` factory below returns the
production set; callers (the SimRunner) construct a single
``SignalCollector`` from this list and pass it into the perception loop.
Extractors that depend on a specific adapter being credentialed degrade
to returning ``None`` when their underlying ``events`` table is empty for
the requested ISO3 — they do NOT raise.

NOTE: not every implemented ingest adapter has a signal extractor.  The
five enrichment-only sources (MarineCadastre, GLEIF, OpenCorporates,
Sayari, ICIJ) populate the ``events`` table for ad-hoc query / RAG but
do NOT push signals into the per-turn agent prompt — they're
slow-moving entity-resolution data, not turn-by-turn intelligence.
"""

from __future__ import annotations

from wargame_ai.sim.signals import SignalExtractor

from wargame_ai.sim.extractors.acled import ACLEDExtractor
from wargame_ai.sim.extractors.comtrade import ComtradeExtractor
from wargame_ai.sim.extractors.datalastic import DatalasticExtractor
from wargame_ai.sim.extractors.eia import EIAExtractor
from wargame_ai.sim.extractors.fred import FREDExtractor
from wargame_ai.sim.extractors.gdelt import GDELTExtractor
from wargame_ai.sim.extractors.imf import IMFExtractor
from wargame_ai.sim.extractors.ofac_sdn import OFACSDNExtractor
from wargame_ai.sim.extractors.opensanctions import OpenSanctionsExtractor
from wargame_ai.sim.extractors.sec_edgar import SECEdgarExtractor
from wargame_ai.sim.extractors.trade_gov import TradeGovExtractor
from wargame_ai.sim.extractors.worldbank import WorldBankExtractor
from wargame_ai.sim.extractors.yfinance import YFinanceExtractor


def default_extractors() -> list[SignalExtractor]:
    """Return the full production set of signal extractors.

    Order is irrelevant — the collector ranks by magnitude — but we list
    them roughly by signal density for the Taiwan-2027 vertical slice
    (highest first) for readability.
    """
    return [
        GDELTExtractor(),
        ACLEDExtractor(),
        ComtradeExtractor(),
        DatalasticExtractor(),
        OpenSanctionsExtractor(),
        OFACSDNExtractor(),
        TradeGovExtractor(),
        SECEdgarExtractor(),
        FREDExtractor(),
        EIAExtractor(),
        IMFExtractor(),
        YFinanceExtractor(),
        WorldBankExtractor(),
    ]


__all__ = [
    "ACLEDExtractor",
    "ComtradeExtractor",
    "DatalasticExtractor",
    "EIAExtractor",
    "FREDExtractor",
    "GDELTExtractor",
    "IMFExtractor",
    "OFACSDNExtractor",
    "OpenSanctionsExtractor",
    "SECEdgarExtractor",
    "TradeGovExtractor",
    "WorldBankExtractor",
    "YFinanceExtractor",
    "default_extractors",
]
