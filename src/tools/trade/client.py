"""Async API clients for UN Comtrade and UNCTADstat trade data sources."""

from __future__ import annotations

import csv
import io
import logging
from typing import Any

from ...common.cache import get_cached, set_cached
from ...common.config import config
from ...common.http_client import fetch_json, fetch_text
from .models import (
    BilateralConnection,
    CommodityDependency,
    ShippingConnectivity,
    TradeFlow,
    TradePartnerSummary,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Country name → ISO-3166-1 numeric code mapping (UN Comtrade uses numeric)
# Also includes ISO-3 alpha codes for convenience lookups.
# ---------------------------------------------------------------------------
COUNTRY_NAME_TO_ISO3: dict[str, str] = {
    "afghanistan": "AFG",
    "albania": "ALB",
    "algeria": "DZA",
    "angola": "AGO",
    "argentina": "ARG",
    "armenia": "ARM",
    "australia": "AUS",
    "austria": "AUT",
    "azerbaijan": "AZE",
    "bahrain": "BHR",
    "bangladesh": "BGD",
    "belarus": "BLR",
    "belgium": "BEL",
    "bolivia": "BOL",
    "bosnia and herzegovina": "BIH",
    "brazil": "BRA",
    "brunei": "BRN",
    "bulgaria": "BGR",
    "cambodia": "KHM",
    "cameroon": "CMR",
    "canada": "CAN",
    "chile": "CHL",
    "china": "CHN",
    "colombia": "COL",
    "congo": "COG",
    "costa rica": "CRI",
    "croatia": "HRV",
    "cuba": "CUB",
    "cyprus": "CYP",
    "czech republic": "CZE",
    "czechia": "CZE",
    "denmark": "DNK",
    "drc": "COD",
    "democratic republic of the congo": "COD",
    "ecuador": "ECU",
    "egypt": "EGY",
    "el salvador": "SLV",
    "estonia": "EST",
    "ethiopia": "ETH",
    "finland": "FIN",
    "france": "FRA",
    "georgia": "GEO",
    "germany": "DEU",
    "ghana": "GHA",
    "greece": "GRC",
    "guatemala": "GTM",
    "honduras": "HND",
    "hong kong": "HKG",
    "hungary": "HUN",
    "iceland": "ISL",
    "india": "IND",
    "indonesia": "IDN",
    "iran": "IRN",
    "iraq": "IRQ",
    "ireland": "IRL",
    "israel": "ISR",
    "italy": "ITA",
    "japan": "JPN",
    "jordan": "JOR",
    "kazakhstan": "KAZ",
    "kenya": "KEN",
    "kuwait": "KWT",
    "kyrgyzstan": "KGZ",
    "laos": "LAO",
    "latvia": "LVA",
    "lebanon": "LBN",
    "libya": "LBY",
    "lithuania": "LTU",
    "luxembourg": "LUX",
    "macau": "MAC",
    "malaysia": "MYS",
    "mexico": "MEX",
    "mongolia": "MNG",
    "morocco": "MAR",
    "mozambique": "MOZ",
    "myanmar": "MMR",
    "namibia": "NAM",
    "nepal": "NPL",
    "netherlands": "NLD",
    "new zealand": "NZL",
    "nicaragua": "NIC",
    "nigeria": "NGA",
    "north korea": "PRK",
    "north macedonia": "MKD",
    "norway": "NOR",
    "oman": "OMN",
    "pakistan": "PAK",
    "panama": "PAN",
    "paraguay": "PRY",
    "peru": "PER",
    "philippines": "PHL",
    "poland": "POL",
    "portugal": "PRT",
    "qatar": "QAT",
    "romania": "ROU",
    "russia": "RUS",
    "saudi arabia": "SAU",
    "senegal": "SEN",
    "serbia": "SRB",
    "singapore": "SGP",
    "slovakia": "SVK",
    "slovenia": "SVN",
    "south africa": "ZAF",
    "south korea": "KOR",
    "spain": "ESP",
    "sri lanka": "LKA",
    "sudan": "SDN",
    "sweden": "SWE",
    "switzerland": "CHE",
    "syria": "SYR",
    "taiwan": "TWN",
    "tanzania": "TZA",
    "thailand": "THA",
    "trinidad and tobago": "TTO",
    "tunisia": "TUN",
    "turkey": "TUR",
    "turkmenistan": "TKM",
    "uae": "ARE",
    "united arab emirates": "ARE",
    "uganda": "UGA",
    "uk": "GBR",
    "united kingdom": "GBR",
    "ukraine": "UKR",
    "uruguay": "URY",
    "us": "USA",
    "usa": "USA",
    "united states": "USA",
    "uzbekistan": "UZB",
    "venezuela": "VEN",
    "vietnam": "VNM",
    "yemen": "YEM",
    "zambia": "ZMB",
    "zimbabwe": "ZWE",
}

# ISO-3 alpha → UN Comtrade numeric reporter codes (subset of most-queried)
ISO3_TO_COMTRADE_NUM: dict[str, str] = {
    "AFG": "4", "ALB": "8", "DZA": "12", "AGO": "24", "ARG": "32",
    "ARM": "51", "AUS": "36", "AUT": "40", "AZE": "31", "BHR": "48",
    "BGD": "50", "BLR": "112", "BEL": "56", "BOL": "68", "BIH": "70",
    "BRA": "76", "BRN": "96", "BGR": "100", "KHM": "116", "CMR": "120",
    "CAN": "124", "CHL": "152", "CHN": "156", "COL": "170", "COG": "178",
    "CRI": "188", "HRV": "191", "CUB": "192", "CYP": "196", "CZE": "203",
    "COD": "180", "DNK": "208", "ECU": "218", "EGY": "818", "SLV": "222",
    "EST": "233", "ETH": "231", "FIN": "246", "FRA": "251", "GEO": "268",
    "DEU": "276", "GHA": "288", "GRC": "300", "GTM": "320", "HND": "340",
    "HKG": "344", "HUN": "348", "ISL": "352", "IND": "356", "IDN": "360",
    "IRN": "364", "IRQ": "368", "IRL": "372", "ISR": "376", "ITA": "381",
    "JPN": "392", "JOR": "400", "KAZ": "398", "KEN": "404", "KWT": "414",
    "KGZ": "417", "LAO": "418", "LVA": "428", "LBN": "422", "LBY": "434",
    "LTU": "440", "LUX": "442", "MAC": "446", "MYS": "458", "MEX": "484",
    "MNG": "496", "MAR": "504", "MOZ": "508", "MMR": "104", "NAM": "516",
    "NPL": "524", "NLD": "528", "NZL": "554", "NIC": "558", "NGA": "566",
    "PRK": "408", "MKD": "807", "NOR": "579", "OMN": "512", "PAK": "586",
    "PAN": "591", "PRY": "600", "PER": "604", "PHL": "608", "POL": "616",
    "PRT": "620", "QAT": "634", "ROU": "642", "RUS": "643", "SAU": "682",
    "SEN": "686", "SRB": "688", "SGP": "702", "SVK": "703", "SVN": "705",
    "ZAF": "710", "KOR": "410", "ESP": "724", "LKA": "144", "SDN": "729",
    "SWE": "752", "CHE": "757", "SYR": "760", "TWN": "490", "TZA": "834",
    "THA": "764", "TTO": "780", "TUN": "788", "TUR": "792", "TKM": "795",
    "ARE": "784", "UGA": "800", "GBR": "826", "UKR": "804", "URY": "858",
    "USA": "842", "UZB": "860", "VEN": "862", "VNM": "704", "YEM": "887",
    "ZMB": "894", "ZWE": "716",
}


COMTRADE_NUM_TO_ISO3: dict[str, str] = {v: k for k, v in ISO3_TO_COMTRADE_NUM.items()}
COMTRADE_NUM_TO_ISO3["0"] = "WLD"


def _num_to_iso3(code: int | str | None) -> str:
    """Convert a Comtrade numeric reporter/partner code to ISO-3 alpha."""
    if code is None:
        return ""
    return COMTRADE_NUM_TO_ISO3.get(str(code), str(code))


def resolve_country(name_or_code: str) -> str:
    """Resolve a country name or code to ISO-3 alpha code.

    Accepts: full name, common abbreviation, ISO-3, or ISO-2.
    Returns the ISO-3 alpha code (e.g. 'USA', 'CHN').
    """
    val = name_or_code.strip()
    upper = val.upper()

    # Already an ISO-3 alpha code
    if upper in ISO3_TO_COMTRADE_NUM:
        return upper

    # Try lowercase lookup in name map
    lower = val.lower()
    if lower in COUNTRY_NAME_TO_ISO3:
        return COUNTRY_NAME_TO_ISO3[lower]

    # Fallback: return the uppercased input and hope Comtrade accepts it
    logger.warning("Could not resolve country '%s' — using as-is", val)
    return upper


def _comtrade_reporter_code(iso3: str) -> str:
    """Get the UN Comtrade numeric reporter code from ISO-3."""
    return ISO3_TO_COMTRADE_NUM.get(iso3, iso3)


# ---- Trade data TTL: 7 days (trade stats update infrequently) ----
_TRADE_CACHE_TTL = 60 * 60 * 24 * 7


# ===========================================================================
# UN Comtrade client
# ===========================================================================

# Full-data endpoint requires a paid subscription key.
# Preview endpoint is free (no key needed) but limited to 500 rows per request.
# We auto-select based on whether a key is configured.
_COMTRADE_PAID_BASE    = "https://comtradeapi.un.org/data/v1/get"
_COMTRADE_PREVIEW_BASE = "https://comtradeapi.un.org/public/v1/preview"
_COMTRADE_REF_BASE     = "https://comtradeapi.un.org/files/v1/app/reference"


def _comtrade_base() -> str:
    """Return the appropriate base URL depending on whether a key is set."""
    return _COMTRADE_PAID_BASE if config.comtrade_api_key else _COMTRADE_PREVIEW_BASE


def _comtrade_headers() -> dict[str, str]:
    """Build headers including the subscription key if available."""
    headers: dict[str, str] = {"Accept": "application/json"}
    key = config.comtrade_api_key
    if key:
        headers["Ocp-Apim-Subscription-Key"] = key
    return headers


async def fetch_comtrade_trade(
    reporter_iso3: str,
    year: int,
    partner_iso3: str = "",
    commodity_code: str = "",
    flow_code: str = "M,X",
) -> list[dict[str, Any]]:
    """Fetch trade data from UN Comtrade.

    Parameters
    ----------
    reporter_iso3 : ISO-3 alpha code for the reporting country
    year : reporting year
    partner_iso3 : optional ISO-3 for trade partner (empty = all)
    commodity_code : HS commodity code (empty = total/all)
    flow_code : M = imports, X = exports, M,X = both
    """
    cache_ns = "comtrade_trade"
    cached = get_cached(
        cache_ns,
        reporter=reporter_iso3,
        partner=partner_iso3,
        year=year,
        commodity=commodity_code,
        flow=flow_code,
    )
    if cached is not None:
        return cached  # type: ignore[return-value]

    reporter_num = _comtrade_reporter_code(reporter_iso3)

    url = f"{_comtrade_base()}/C/A/HS"

    params: dict[str, str] = {
        "reporterCode": reporter_num,
        "period": str(year),
        "flowCode": flow_code,
    }
    if partner_iso3:
        partner_num = _comtrade_reporter_code(partner_iso3)
        params["partnerCode"] = partner_num
    if commodity_code and commodity_code.upper() != "TOTAL":
        params["cmdCode"] = commodity_code

    try:
        data = await fetch_json(url, params=params, headers=_comtrade_headers())
    except Exception as exc:
        logger.error("Comtrade API error: %s", exc)
        return []

    records: list[dict[str, Any]] = data.get("data", [])

    set_cached(
        records,
        cache_ns,
        ttl=_TRADE_CACHE_TTL,
        reporter=reporter_iso3,
        partner=partner_iso3,
        year=year,
        commodity=commodity_code,
        flow=flow_code,
    )
    return records


def _parse_comtrade_records(records: list[dict[str, Any]]) -> list[TradeFlow]:
    """Convert raw Comtrade JSON records into TradeFlow models.

    The Comtrade Plus API v1 sometimes returns ``None`` for descriptive
    fields (reporterISO, partnerISO, cmdDesc).  We fall back through
    several alternatives and ultimately use a numeric→ISO3 reverse map.
    """
    flows: list[TradeFlow] = []
    for rec in records:
        flow_code = rec.get("flowCode") or ""
        flow_type = "import" if flow_code == "M" else "export"

        reporter = (
            rec.get("reporterISO")
            or rec.get("reporterDesc")
            or _num_to_iso3(rec.get("reporterCode"))
        )
        partner = (
            rec.get("partnerISO")
            or rec.get("partnerDesc")
            or _num_to_iso3(rec.get("partnerCode"))
        )
        cmd_desc = (
            rec.get("cmdDesc")
            or rec.get("cmdDescE")
            or rec.get("flowDesc")
            or str(rec.get("cmdCode") or "")
        )

        raw_value = rec.get("primaryValue")
        # Distinguish "reported zero trade" from "value not reported"
        trade_value: float
        if raw_value is None or raw_value == "":
            trade_value = 0.0  # not reported; downstream should treat as unreliable
            logger.debug("Comtrade: primaryValue missing for record %s", rec.get("cmdCode"))
        else:
            trade_value = float(raw_value)

        flows.append(
            TradeFlow(
                reporter_country=reporter or "UNKNOWN",
                partner_country=partner or "UNKNOWN",
                commodity_code=str(rec.get("cmdCode") or ""),
                commodity_desc=cmd_desc,
                trade_value_usd=trade_value,
                weight_kg=float(rec.get("netWgt") or 0) if rec.get("netWgt") else None,
                year=int(rec.get("period") or 0),
                flow_type=flow_type,
            )
        )
    return flows


async def get_bilateral_trade_flows(
    reporter: str, partner: str, year: int = 2023
) -> list[TradeFlow]:
    """Get bilateral trade flows between two countries."""
    reporter_iso = resolve_country(reporter)
    partner_iso = resolve_country(partner)
    records = await fetch_comtrade_trade(
        reporter_iso3=reporter_iso,
        year=year,
        partner_iso3=partner_iso,
        flow_code="M,X",
    )
    return _parse_comtrade_records(records)


async def get_commodity_trade_flows(
    commodity_code: str, reporter: str = "", year: int = 2023
) -> list[TradeFlow]:
    """Get trade flows for a specific commodity, optionally filtered by reporter."""
    reporter_iso = resolve_country(reporter) if reporter else ""
    if not reporter_iso:
        # Without a reporter we query a few major economies to build a picture
        major_reporters = ["USA", "CHN", "DEU", "JPN", "GBR"]
        all_flows: list[TradeFlow] = []
        for r in major_reporters:
            records = await fetch_comtrade_trade(
                reporter_iso3=r,
                year=year,
                commodity_code=commodity_code,
                flow_code="M,X",
            )
            all_flows.extend(_parse_comtrade_records(records))
        return all_flows

    records = await fetch_comtrade_trade(
        reporter_iso3=reporter_iso,
        year=year,
        commodity_code=commodity_code,
        flow_code="M,X",
    )
    return _parse_comtrade_records(records)


async def get_trade_partner_summary(
    country: str, flow: str = "import", year: int = 2023
) -> TradePartnerSummary:
    """Get a summary of top trade partners for a country."""
    iso3 = resolve_country(country)
    flow_code = "M" if flow == "import" else "X"

    records = await fetch_comtrade_trade(
        reporter_iso3=iso3, year=year, flow_code=flow_code
    )
    flows = _parse_comtrade_records(records)

    total_imports = sum(f.trade_value_usd for f in flows if f.flow_type == "import")
    total_exports = sum(f.trade_value_usd for f in flows if f.flow_type == "export")

    # Aggregate by partner
    partner_totals: dict[str, float] = {}
    for f in flows:
        partner_totals[f.partner_country] = (
            partner_totals.get(f.partner_country, 0) + f.trade_value_usd
        )

    # Sort by value descending and take top 20
    sorted_partners = sorted(partner_totals.items(), key=lambda x: x[1], reverse=True)[:20]

    # Aggregate by commodity
    commodity_totals: dict[str, dict[str, Any]] = {}
    for f in flows:
        key = f.commodity_code
        if key not in commodity_totals:
            commodity_totals[key] = {
                "commodity_code": f.commodity_code,
                "commodity_desc": f.commodity_desc,
                "value_usd": 0.0,
            }
        commodity_totals[key]["value_usd"] += f.trade_value_usd

    top_commodities = sorted(
        commodity_totals.values(), key=lambda x: x["value_usd"], reverse=True
    )[:20]

    return TradePartnerSummary(
        country=iso3,
        total_imports_usd=total_imports,
        total_exports_usd=total_exports,
        top_commodities=[
            {
                "commodity_code": c["commodity_code"],
                "commodity_desc": c["commodity_desc"],
                "value_usd": c["value_usd"],
            }
            for c in top_commodities
        ],
    )


async def get_supply_chain_dependency(
    country: str, commodity_code: str
) -> CommodityDependency:
    """Analyse how dependent a country is on a specific commodity import."""
    iso3 = resolve_country(country)

    # Get import data for the commodity
    commodity_records = await fetch_comtrade_trade(
        reporter_iso3=iso3,
        year=2023,
        commodity_code=commodity_code,
        flow_code="M",
    )
    commodity_flows = _parse_comtrade_records(commodity_records)

    # Get total imports to compute share
    total_records = await fetch_comtrade_trade(
        reporter_iso3=iso3,
        year=2023,
        commodity_code="TOTAL",
        flow_code="M",
    )
    total_flows = _parse_comtrade_records(total_records)
    total_imports = sum(f.trade_value_usd for f in total_flows)

    commodity_total = sum(f.trade_value_usd for f in commodity_flows)
    share_pct = (commodity_total / total_imports * 100) if total_imports > 0 else 0.0

    # Top suppliers
    supplier_totals: dict[str, float] = {}
    for f in commodity_flows:
        supplier_totals[f.partner_country] = (
            supplier_totals.get(f.partner_country, 0) + f.trade_value_usd
        )

    sorted_suppliers = sorted(supplier_totals.items(), key=lambda x: x[1], reverse=True)[:10]
    top_suppliers = [
        {
            "country": s[0],
            "value_usd": s[1],
            "share_pct": round(s[1] / commodity_total * 100, 2) if commodity_total > 0 else 0.0,
        }
        for s in sorted_suppliers
    ]

    desc = commodity_flows[0].commodity_desc if commodity_flows else ""

    return CommodityDependency(
        commodity_code=commodity_code,
        commodity_desc=desc,
        import_share_pct=round(share_pct, 2),
        top_suppliers=top_suppliers,
    )


# ===========================================================================
# UNCTADstat client — Liner Shipping Connectivity Index
# ===========================================================================

_UNCTAD_LSCI_URL = (
    "https://unctadstat-api.unctad.org/bulkdownload/US.LSCI/US_LSCI"
)
_UNCTAD_BILATERAL_URL = (
    "https://unctadstat-api.unctad.org/bulkdownload/US.LSBCI/US_LSBCI"
)


def _parse_unctad_csv(text: str) -> list[dict[str, str]]:
    """Parse a CSV string into a list of dicts, skipping metadata rows."""
    lines = text.strip().splitlines()

    # UNCTAD CSV files often have metadata header rows before the actual CSV.
    # Find the header row by looking for common column names.
    header_idx = 0
    for i, line in enumerate(lines):
        lower = line.lower()
        if "economy" in lower or "country" in lower or "reporter" in lower:
            header_idx = i
            break

    reader = csv.DictReader(lines[header_idx:])
    return [row for row in reader]


async def fetch_lsci_data() -> list[dict[str, str]]:
    """Fetch UNCTAD Liner Shipping Connectivity Index data."""
    cache_ns = "unctad_lsci"
    cached = get_cached(cache_ns, dataset="lsci")
    if cached is not None:
        return cached  # type: ignore[return-value]

    try:
        text = await fetch_text(_UNCTAD_LSCI_URL, timeout=60.0)
        rows = _parse_unctad_csv(text)
        set_cached(rows, cache_ns, ttl=_TRADE_CACHE_TTL, dataset="lsci")
        return rows
    except Exception as exc:
        logger.error("UNCTADstat LSCI fetch error: %s", exc)
        return []


async def fetch_bilateral_lsci_data() -> list[dict[str, str]]:
    """Fetch UNCTAD bilateral Liner Shipping Connectivity Index data."""
    cache_ns = "unctad_bilateral_lsci"
    cached = get_cached(cache_ns, dataset="bilateral_lsci")
    if cached is not None:
        return cached  # type: ignore[return-value]

    try:
        text = await fetch_text(_UNCTAD_BILATERAL_URL, timeout=60.0)
        rows = _parse_unctad_csv(text)
        set_cached(rows, cache_ns, ttl=_TRADE_CACHE_TTL, dataset="bilateral_lsci")
        return rows
    except Exception as exc:
        logger.error("UNCTADstat bilateral LSCI fetch error: %s", exc)
        return []


def _match_country_in_row(row: dict[str, str], iso3: str) -> bool:
    """Check if any economy/country field in a row matches the given ISO3 code."""
    for key in ("Economy", "economy", "Reporter", "reporter",
                "economyISO3", "reporterISO3", "Economy_ISO3",
                "Economy Label", "Country", "country"):
        val = row.get(key, "").strip().upper()
        if val == iso3:
            return True
        # Also try resolving the name
        if val and resolve_country(val) == iso3:
            return True
    return False


def _extract_year(row: dict[str, str]) -> int | None:
    """Extract the year value from a row."""
    for key in ("Year", "year", "Period", "period", "Time Period"):
        val = row.get(key, "").strip()
        if val.isdigit():
            return int(val)
    return None


def _extract_value(row: dict[str, str]) -> float | None:
    """Extract the numeric value from a row."""
    for key in ("Value", "value", "LSCI", "lsci", "Index", "LSBCI", "lsbci"):
        val = row.get(key, "").strip()
        if val:
            try:
                return float(val.replace(",", ""))
            except ValueError:
                continue
    return None


async def get_shipping_connectivity_data(country: str) -> ShippingConnectivity:
    """Get Liner Shipping Connectivity Index for a country."""
    iso3 = resolve_country(country)

    # LSCI score
    lsci_rows = await fetch_lsci_data()
    latest_score: float | None = None
    latest_year: int = 0

    for row in lsci_rows:
        if _match_country_in_row(row, iso3):
            yr = _extract_year(row)
            val = _extract_value(row)
            if yr and val and yr > latest_year:
                latest_year = yr
                latest_score = val

    # Bilateral connections
    bilateral_rows = await fetch_bilateral_lsci_data()
    connections: list[BilateralConnection] = []

    for row in bilateral_rows:
        if _match_country_in_row(row, iso3):
            # Find the partner country field
            partner = ""
            for key in ("Partner", "partner", "partnerISO3", "Partner_ISO3",
                        "Economy_Partner", "Partner Label"):
                if row.get(key, "").strip():
                    partner = row[key].strip()
                    break

            val = _extract_value(row)
            yr = _extract_year(row)

            # Only take the most recent year data
            if partner and val is not None:
                if yr and latest_year and yr < latest_year - 1:
                    continue
                connections.append(
                    BilateralConnection(
                        partner_country=partner,
                        lsci_bilateral=val,
                    )
                )

    # Sort by score descending and take top 20
    connections.sort(key=lambda c: c.lsci_bilateral or 0, reverse=True)
    connections = connections[:20]

    return ShippingConnectivity(
        country=iso3,
        lsci_score=latest_score,
        year=latest_year or 2023,
        bilateral_connections=connections,
    )
