"""Sayari Graph Intelligence client for UBO ownership and trade data.

Uses the Sayari Python SDK which handles OAuth2 token lifecycle.
All SDK calls are synchronous — wrapped in asyncio.to_thread() for FastAPI.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.common.cache import get_cached, set_cached
from src.common.config import config

from .models import (
    SayariOwnerLink,
    SayariOwnershipChain,
    SayariTradeActivity,
    SayariTradeRecord,
    SayariVesselIntel,
)

logger = logging.getLogger(__name__)

_CACHE_NS = "sayari"
_RESOLVE_TTL = 86400  # 24h — entity IDs are stable
_UBO_TTL = 3600       # 1h
_TRADE_TTL = 3600     # 1h

# Singleton SDK client
_client = None


def _get_client():
    """Get or create the Sayari SDK client. Returns None if not configured."""
    global _client
    if _client is not None:
        return _client
    if not config.sayari_client_id or not config.sayari_client_secret:
        return None
    try:
        import warnings
        warnings.filterwarnings("ignore", category=UserWarning, module="sayari")
        from sayari import Sayari
        _client = Sayari(
            client_id=config.sayari_client_id,
            client_secret=config.sayari_client_secret,
        )
        return _client
    except Exception as exc:
        logger.warning("Failed to initialize Sayari client: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------

def _resolve_sync(name: str, imo: str | None = None) -> tuple[str | None, str]:
    """Synchronous resolution — run via to_thread().

    Uses entity search (not resolution) to find vessel entities, then falls
    back to company matches. This ensures we get the actual vessel in Sayari's
    graph rather than a random company with a similar name.
    """
    client = _get_client()
    if not client:
        return None, ""

    # Strategy 1: Search for the vessel entity directly
    try:
        search_result = client.search.search_entity(q=name, limit=10)
        if search_result.data:
            # Prefer vessel-type matches
            for r in search_result.data:
                if getattr(r, "type", "") == "vessel":
                    eid = getattr(r, "id", None) or getattr(r, "entity_id", None)
                    if eid:
                        return eid, r.label
            # Fallback: prefer company over other types
            for r in search_result.data:
                if getattr(r, "type", "") == "company":
                    eid = getattr(r, "id", None) or getattr(r, "entity_id", None)
                    if eid:
                        return eid, r.label
            # Last resort: first result
            first = search_result.data[0]
            eid = getattr(first, "id", None) or getattr(first, "entity_id", None)
            if eid:
                return eid, first.label
    except Exception:
        pass

    # Strategy 2: Fall back to resolution endpoint
    try:
        result = client.resolution.resolution(name=[name])
        if result.data:
            best = result.data[0]
            return best.entity_id, best.label
    except Exception:
        pass

    return None, ""


async def resolve_entity(name: str, imo: str | None = None) -> tuple[str | None, str]:
    """Resolve a vessel owner/operator name to a Sayari entity ID."""
    cache_key = {"action": "resolve", "name": name, "imo": imo or ""}
    cached = get_cached(_CACHE_NS, **cache_key)
    if cached is not None:
        return cached.get("entity_id"), cached.get("label", "")

    entity_id, label = await asyncio.to_thread(_resolve_sync, name, imo)

    set_cached(
        {"entity_id": entity_id, "label": label},
        _CACHE_NS, ttl=_RESOLVE_TTL, **cache_key,
    )
    return entity_id, label


# ---------------------------------------------------------------------------
# UBO / Ownership chain
# ---------------------------------------------------------------------------

def _ubo_sync(entity_id: str) -> SayariOwnershipChain:
    """Synchronous UBO lookup — run via to_thread()."""
    client = _get_client()
    if not client:
        return SayariOwnershipChain()

    result = client.traversal.ubo(entity_id, limit=20, max_depth=6)
    chain: list[SayariOwnerLink] = []
    max_depth = 0

    for path in (result.data or []):
        prev_eid = entity_id  # track parent within each path
        for i, node in enumerate(path.path or []):
            entity = getattr(node, "entity", None)
            if not entity:
                continue

            current_eid = getattr(entity, "id", "") or ""

            # Extract risk flags
            is_sanctioned = False
            is_pep = False
            risk = getattr(entity, "risk", None)
            if risk:
                categories = getattr(risk, "categories", None) or {}
                if isinstance(categories, dict):
                    is_sanctioned = bool(categories.get("sanctioned"))
                    is_pep = bool(categories.get("pep"))

            countries = getattr(entity, "countries", []) or []
            country = countries[0] if countries else None

            # Ownership percentage from relationship data
            pct = None
            relationship = getattr(node, "relationships", None)
            if relationship:
                shares = getattr(relationship, "shares", None)
                if shares:
                    pct_vals = getattr(shares, "percentage", None)
                    if pct_vals and isinstance(pct_vals, (list, tuple)) and pct_vals:
                        try:
                            pct = float(pct_vals[0])
                        except (ValueError, TypeError):
                            pass

            depth = i + 1
            if depth > max_depth:
                max_depth = depth

            link = SayariOwnerLink(
                entity_id=current_eid,
                name=entity.label or "Unknown",
                entity_type=entity.type or "unknown",
                country=country,
                ownership_percentage=pct,
                is_sanctioned=is_sanctioned,
                is_pep=is_pep,
                depth=depth,
                parent_entity_id=prev_eid,
            )
            # Deduplicate by entity_id (keep first occurrence with its parent)
            if not any(l.entity_id == link.entity_id for l in chain):
                chain.append(link)

            prev_eid = current_eid

    return SayariOwnershipChain(
        owner_entity_id=entity_id,
        chain=chain,
        max_depth_reached=max_depth,
    )


async def get_ubo_chain(entity_id: str) -> SayariOwnershipChain:
    """Get the beneficial ownership chain for an entity."""
    cached = get_cached(_CACHE_NS, action="ubo", entity_id=entity_id)
    if cached is not None:
        return SayariOwnershipChain.model_validate(cached)

    result = await asyncio.to_thread(_ubo_sync, entity_id)

    set_cached(
        result.model_dump(mode="json"),
        _CACHE_NS, ttl=_UBO_TTL, action="ubo", entity_id=entity_id,
    )
    return result


# ---------------------------------------------------------------------------
# Trade activity
# ---------------------------------------------------------------------------

# HS code prefix → commodity category mapping
_HS_CATEGORIES: dict[str, str] = {
    "01": "Animal Products", "02": "Meat", "03": "Seafood", "04": "Dairy",
    "05": "Animal Products", "06": "Plants", "07": "Vegetables", "08": "Fruits",
    "09": "Coffee/Tea/Spices", "10": "Cereals", "11": "Milling Products",
    "12": "Seeds/Oils", "13": "Gums/Resins", "14": "Vegetable Products",
    "15": "Fats/Oils", "16": "Prepared Foods", "17": "Sugar", "18": "Cocoa",
    "19": "Prepared Cereals", "20": "Prepared Foods", "21": "Prepared Foods",
    "22": "Beverages", "23": "Animal Feed", "24": "Tobacco",
    "25": "Minerals", "26": "Ores", "27": "Mineral Fuels/Oil",
    "28": "Chemicals", "29": "Organic Chemicals", "30": "Pharmaceuticals",
    "31": "Fertilizers", "32": "Dyes/Paints", "33": "Cosmetics",
    "34": "Soap/Wax", "35": "Adhesives", "36": "Explosives",
    "37": "Photographic", "38": "Chemicals",
    "39": "Plastics", "40": "Rubber",
    "41": "Leather", "42": "Leather Goods", "43": "Fur",
    "44": "Wood", "45": "Cork", "46": "Straw/Basketware",
    "47": "Pulp", "48": "Paper", "49": "Printed Materials",
    "50": "Silk", "51": "Wool", "52": "Cotton", "53": "Vegetable Fibers",
    "54": "Synthetic Filaments", "55": "Synthetic Staple", "56": "Nonwovens",
    "57": "Carpets", "58": "Fabrics", "59": "Technical Textiles",
    "60": "Knitted Fabrics", "61": "Apparel (Knitted)", "62": "Apparel (Woven)",
    "63": "Textile Articles",
    "64": "Footwear", "65": "Headgear", "66": "Umbrellas", "67": "Feathers",
    "68": "Stone/Cement", "69": "Ceramics", "70": "Glass",
    "71": "Precious Metals/Gems",
    "72": "Iron/Steel", "73": "Iron/Steel Articles", "74": "Copper",
    "75": "Nickel", "76": "Aluminum", "78": "Lead", "79": "Zinc",
    "80": "Tin", "81": "Base Metals", "82": "Tools", "83": "Metal Articles",
    "84": "Machinery", "85": "Electronics",
    "86": "Railway", "87": "Vehicles", "88": "Aircraft", "89": "Ships",
    "90": "Instruments", "91": "Clocks/Watches", "92": "Musical Instruments",
    "93": "Arms/Ammunition", "94": "Furniture", "95": "Toys/Games",
    "96": "Miscellaneous", "97": "Art/Antiques",
}


def _hs_to_category(hs_code: str | None) -> str:
    """Map an HS code to a short commodity category label."""
    if not hs_code or len(hs_code) < 2:
        return "Other"
    prefix = hs_code[:2]
    return _HS_CATEGORIES.get(prefix, "Other")


def _extract_entity_risks(entity) -> list[str]:
    """Extract high-priority risk flags from a SourceOrDestinationEntity."""
    risks = getattr(entity, "risks", {}) or {}
    flags = []
    for key, val in risks.items():
        if val is None:
            continue
        # Only surface actionable risk categories
        if any(term in key for term in ("sanctioned", "forced_labor", "export_controls",
                                         "pep", "military_civil_fusion", "state_owned")):
            flags.append(key)
    return flags[:10]  # cap to avoid noise


def _trade_sync(entity_name: str) -> SayariTradeActivity:
    """Synchronous trade lookup — run via to_thread()."""
    client = _get_client()
    if not client:
        return SayariTradeActivity()

    records: list[SayariTradeRecord] = []
    hs_codes: dict[str, str] = {}
    countries: set[str] = set()

    try:
        shipments = client.trade.search_shipments(q=entity_name, limit=15)
        for s in (shipments.data or []):
            # Extract supplier/buyer names (SDK: List[SourceOrDestinationEntity] with .names)
            supplier = ""
            supplier_risks: list[str] = []
            if s.supplier:
                first_sup = s.supplier[0]
                names = getattr(first_sup, "names", []) or []
                supplier = names[0] if names else ""
                supplier_risks = _extract_entity_risks(first_sup)

            buyer = ""
            buyer_risks: list[str] = []
            if s.buyer:
                first_buy = s.buyer[0]
                names = getattr(first_buy, "names", []) or []
                buyer = names[0] if names else ""
                buyer_risks = _extract_entity_risks(first_buy)

            # HS codes
            hs_code = ""
            hs_desc = ""
            if s.hs_codes:
                first_hs = s.hs_codes[0]
                hs_code = getattr(first_hs, "code", "") or ""
                hs_desc = getattr(first_hs, "description", "") or ""
                if hs_code:
                    hs_codes[hs_code] = hs_desc

            commodity_cat = _hs_to_category(hs_code)

            # Countries
            dep = s.departure_country
            arr = s.arrival_country
            dep_str = dep[0] if isinstance(dep, list) and dep else (dep or "")
            arr_str = arr[0] if isinstance(arr, list) and arr else (arr or "")
            if dep_str:
                countries.add(dep_str)
            if arr_str:
                countries.add(arr_str)

            # Date
            date_val = s.departure_date or s.arrival_date
            date_str = date_val[0] if isinstance(date_val, list) and date_val else (str(date_val) if date_val else "")

            # Weight and value
            weight_kg = None
            if s.weight:
                w = s.weight[0]
                weight_kg = getattr(w, "value", None)
            value_usd = None
            if s.monetary_value:
                mv = s.monetary_value[0]
                value_usd = getattr(mv, "value", None)

            records.append(SayariTradeRecord(
                supplier=supplier,
                buyer=buyer,
                supplier_risks=supplier_risks,
                buyer_risks=buyer_risks,
                hs_code=hs_code,
                hs_description=hs_desc,
                commodity_category=commodity_cat,
                departure_country=dep_str,
                arrival_country=arr_str,
                date=date_str,
                weight_kg=weight_kg,
                value_usd=value_usd,
            ))
    except Exception as exc:
        logger.warning("Sayari trade search error: %s", exc)

    # Build Sankey flow aggregation: departure → commodity → arrival
    flow_counts: dict[tuple[str, str, str], int] = {}
    for r in records:
        if r.departure_country and r.arrival_country and r.commodity_category:
            key = (r.departure_country, r.commodity_category, r.arrival_country)
            flow_counts[key] = flow_counts.get(key, 0) + 1

    # Build two-hop Sankey links with disambiguated country nodes.
    # Suffix source countries with " (origin)" and destination countries with
    # " (dest)" so a country appearing on both sides gets separate nodes.
    sankey_flows: list[dict] = []
    sankey_labels: dict[str, str] = {}
    for (dep, cat, arr), count in sorted(flow_counts.items(), key=lambda x: -x[1]):
        src_key = dep + " (origin)"
        dst_key = arr + " (dest)"
        sankey_labels[src_key] = dep
        sankey_labels[dst_key] = arr
        sankey_labels[cat] = cat
        sankey_flows.append({"from": src_key, "to": cat, "flow": count})
        sankey_flows.append({"from": cat, "to": dst_key, "flow": count})

    return SayariTradeActivity(
        records=records,
        top_hs_codes=[{"code": k, "description": v} for k, v in list(hs_codes.items())[:10]],
        trade_countries=sorted(countries),
        record_count=len(records),
        sankey_flows=sankey_flows,
        sankey_labels=sankey_labels,
    )


async def get_trade_activity(entity_name: str) -> SayariTradeActivity:
    """Get trade/shipping records for an entity."""
    cached = get_cached(_CACHE_NS, action="trade", name=entity_name)
    if cached is not None:
        return SayariTradeActivity.model_validate(cached)

    result = await asyncio.to_thread(_trade_sync, entity_name)

    set_cached(
        result.model_dump(mode="json"),
        _CACHE_NS, ttl=_TRADE_TTL, action="trade", name=entity_name,
    )
    return result


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def _get_related_companies_sync(entity_id: str) -> list[dict[str, Any]]:
    """Get companies related to a vessel entity.

    Returns list of dicts with: entity_id, label, sanctioned, pep, relationship_type.
    Relationship types are normalized from Sayari edge types (e.g. has_registered_owner → registered_owner).
    """
    client = _get_client()
    if not client:
        return []
    try:
        entity = client.entity.get_entity(entity_id)
        results: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        rels = getattr(entity, "relationships", None)
        if rels and hasattr(rels, "data"):
            for r in (rels.data or []):
                target = getattr(r, "target", None)
                if not target:
                    continue
                target_type = str(getattr(target, "type", ""))
                target_id = str(getattr(target, "id", ""))
                target_label = str(getattr(target, "label", ""))
                if not target_id or target_type != "company" or target_id in seen_ids:
                    continue
                seen_ids.add(target_id)

                # Extract relationship role from types → linked_to → attributes.position
                rel_type = ""
                types_dict = getattr(r, "types", None) or {}
                if isinstance(types_dict, dict):
                    for rel_infos in types_dict.values():
                        for ri in (rel_infos or []):
                            attrs = getattr(ri, "attributes", None) or {}
                            positions = attrs.get("position", [])
                            if positions:
                                rel_type = positions[0].get("value", "") if isinstance(positions[0], dict) else str(positions[0])
                                break
                        if rel_type:
                            break
                # Normalize to lowercase snake_case
                rel_type = rel_type.lower().replace(" ", "_")
                # Also extract country from target
                target_countries = getattr(target, "countries", []) or []
                target_country = target_countries[0] if target_countries else None

                is_sanctioned = bool(getattr(target, "sanctioned", False))
                is_pep = bool(getattr(target, "pep", False))
                # Extract risk scores from target entity
                target_risk = getattr(target, "risk", {}) or {}
                comp_risk_scores: dict[str, float] = {}
                if isinstance(target_risk, dict):
                    for rk in ("cpi_score", "basel_aml"):
                        rd = target_risk.get(rk)
                        if rd and hasattr(rd, "value") and rd.value is not None:
                            comp_risk_scores[rk] = float(rd.value)

                results.append({
                    "entity_id": target_id,
                    "label": target_label,
                    "sanctioned": is_sanctioned,
                    "pep": is_pep,
                    "relationship_type": rel_type,
                    "country": target_country,
                    "risk_scores": comp_risk_scores,
                })
        return results[:5]
    except Exception as exc:
        logger.warning("Sayari get_related_companies error: %s", exc)
        return []


async def get_vessel_intel(
    vessel_name: str,
    imo: str | None = None,
    owner_name: str | None = None,
) -> SayariVesselIntel:
    """Get Sayari intelligence for a vessel: UBO ownership + trade activity.

    Flow: resolve vessel → get related companies → get UBO from each related
    company → get trade data. Builds a tree-structured ownership chain with
    parent_entity_id pointers so the graph can render a proper hierarchy.
    """
    if not _get_client():
        return SayariVesselIntel(resolved=False)

    try:
        # Step 1: Resolve the vessel entity
        entity_id, label = await resolve_entity(vessel_name, imo)
        if not entity_id:
            return SayariVesselIntel(resolved=False)

        # Step 2: Get related companies (owner, operator, builder, etc.)
        related = await asyncio.to_thread(_get_related_companies_sync, entity_id)

        # Step 3: Build ownership chain as a tree
        chain_links: list[SayariOwnerLink] = []
        seen_ids: set[str] = set()
        owner_company_name = label

        # Add related companies as depth-1 links, parented to the vessel
        for comp in related:
            if comp["entity_id"] in seen_ids:
                continue
            seen_ids.add(comp["entity_id"])
            chain_links.append(SayariOwnerLink(
                entity_id=comp["entity_id"],
                name=comp["label"],
                entity_type="company",
                country=comp.get("country"),
                is_sanctioned=comp["sanctioned"],
                is_pep=comp["pep"],
                depth=1,
                relationship_type=comp["relationship_type"],
                parent_entity_id=entity_id,  # parent is the vessel
            ))

        # Identify the primary owner for naming/trade lookup
        owner_comp = None
        for comp in related:
            if comp["relationship_type"] in ("registered_owner", "owner", "beneficial_owner"):
                owner_comp = comp
                break
        if not owner_comp and related:
            owner_comp = related[0]
        if owner_comp:
            owner_company_name = owner_comp["label"]

        # Trace UBO chains from ALL related companies in parallel
        if related:
            ubo_tasks = [get_ubo_chain(comp["entity_id"]) for comp in related]
            ubo_results = await asyncio.gather(*ubo_tasks, return_exceptions=True)

            for comp, ubo_result in zip(related, ubo_results):
                if isinstance(ubo_result, Exception):
                    logger.warning("UBO trace failed for %s: %s", comp["label"], ubo_result)
                    continue
                for link in ubo_result.chain:
                    if link.entity_id in seen_ids:
                        continue
                    seen_ids.add(link.entity_id)
                    link.depth += 1  # offset since company is depth 1
                    # Only override parent for depth-1 UBO links (direct children
                    # of the company). Deeper links already have correct internal
                    # parent pointers from _ubo_sync.
                    if link.depth == 2 or link.parent_entity_id == comp["entity_id"]:
                        link.parent_entity_id = comp["entity_id"]
                    chain_links.append(link)

        ownership = SayariOwnershipChain(
            vessel_entity_id=entity_id,
            owner_entity_id=owner_comp["entity_id"] if owner_comp else None,
            owner_name=owner_company_name,
            chain=chain_links,
            max_depth_reached=max((l.depth for l in chain_links), default=0),
        )

        # Step 4: Get trade data — query both vessel name AND owner for best coverage
        trade_tasks = [get_trade_activity(vessel_name)]
        if owner_company_name and owner_company_name != vessel_name:
            trade_tasks.append(get_trade_activity(owner_company_name))
        trade_results = await asyncio.gather(*trade_tasks, return_exceptions=True)

        # Merge: prefer vessel-name results, supplement with owner results
        trade = SayariTradeActivity()
        seen_records: set[str] = set()
        all_hs: dict[str, str] = {}
        all_countries: set[str] = set()
        merged_records: list[SayariTradeRecord] = []

        for tr in trade_results:
            if isinstance(tr, Exception):
                continue
            for rec in tr.records:
                key = f"{rec.date}|{rec.departure_country}|{rec.arrival_country}|{rec.hs_code}"
                if key not in seen_records:
                    seen_records.add(key)
                    merged_records.append(rec)
            for hs in tr.top_hs_codes:
                all_hs[hs.get("code", "")] = hs.get("description", "")
            all_countries.update(tr.trade_countries)

        # Merge Sankey flows from all trade results
        all_sankey: list[dict] = []
        for tr in trade_results:
            if isinstance(tr, Exception):
                continue
            all_sankey.extend(tr.sankey_flows)

        trade = SayariTradeActivity(
            records=merged_records[:20],
            top_hs_codes=[{"code": k, "description": v} for k, v in list(all_hs.items())[:10]],
            trade_countries=sorted(all_countries),
            record_count=len(merged_records),
            sankey_flows=all_sankey,
        )

        # Collect risk scores from related companies
        risk_scores: dict[str, float] = {}
        for comp in related:
            for key in ("cpi_score", "basel_aml"):
                if key not in risk_scores and key in (comp.get("risk_scores") or {}):
                    risk_scores[key] = comp["risk_scores"][key]

        return SayariVesselIntel(
            resolved=True,
            owner_entity_id=owner_comp["entity_id"] if owner_comp else entity_id,
            owner_name=owner_company_name,
            ownership=ownership,
            trade=trade,
            risk_scores=risk_scores,
        )
    except Exception as exc:
        logger.warning("Sayari vessel intel error: %s", exc)
        return SayariVesselIntel(resolved=False)
