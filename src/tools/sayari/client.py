"""Async client for the Sayari Graph API.

Handles OAuth2 client-credentials authentication, entity resolution,
graph traversal, entity profiles, and UBO (ultimate beneficial ownership).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from src.common.config import config
from src.tools.sayari.models import (
    SayariEntity,
    SayariOwnerLink,
    SayariOwnershipChain,
    SayariRelationship,
    SayariResolveResult,
    SayariTradeActivity,
    SayariTradeRecord,
    SayariTraversalResult,
    SayariUBOOwner,
    SayariUBOResult,
    SayariVesselIntel,
)

logger = logging.getLogger(__name__)

_BASE = "https://api.sayari.com"
_TOKEN_URL = f"{_BASE}/oauth/token"

_HS_CATEGORIES: dict[str, str] = {
    "25": "Minerals", "26": "Ores", "27": "Mineral Fuels/Oil",
    "28": "Chemicals", "29": "Organic Chemicals", "30": "Pharmaceuticals",
    "31": "Fertilizers", "36": "Explosives", "39": "Plastics",
    "44": "Wood", "71": "Precious Metals/Gems",
    "72": "Iron/Steel", "73": "Iron/Steel Articles", "74": "Copper",
    "75": "Nickel", "76": "Aluminum", "84": "Machinery",
    "85": "Electronics", "87": "Vehicles", "88": "Aircraft",
    "89": "Ships", "90": "Instruments", "93": "Arms/Ammunition",
}


def _hs_to_category(hs_code: str | None) -> str:
    if not hs_code or len(hs_code) < 2:
        return "Other"
    return _HS_CATEGORIES.get(hs_code[:2], "Other")


class SayariClient:
    """Async wrapper around the Sayari v1 REST API."""

    def __init__(self) -> None:
        self._access_token: str | None = None
        self._token_expires_at: float = 0
        self._lock = asyncio.Lock()

    async def _ensure_token(self) -> str:
        """Get or refresh the OAuth2 bearer token (client_credentials flow)."""
        async with self._lock:
            if self._access_token and time.time() < self._token_expires_at - 30:
                return self._access_token

            if not config.sayari_client_id or not config.sayari_client_secret:
                raise RuntimeError("SAYARI_CLIENT_ID / SAYARI_CLIENT_SECRET not configured")

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    _TOKEN_URL,
                    json={
                        "client_id": config.sayari_client_id,
                        "client_secret": config.sayari_client_secret,
                        "audience": "sayari.com",
                        "grant_type": "client_credentials",
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            self._access_token = data["access_token"]
            self._token_expires_at = time.time() + data.get("expires_in", 86400)
            logger.info("Sayari token acquired, expires in %ds", data.get("expires_in", 0))
            return self._access_token  # type: ignore[return-value]

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(
                f"{_BASE}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            return resp.json()

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.post(
                f"{_BASE}{path}",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            return resp.json()

    # ── public helpers ────────────────────────────────────────────────────

    @staticmethod
    def _parse_entity(raw: dict[str, Any]) -> SayariEntity:
        """Normalise a Sayari entity payload into our model."""
        eid = raw.get("id") or raw.get("entity_id") or ""
        raw_label = raw.get("label") or raw.get("name") or eid
        translated = raw.get("translated_label") or ""
        label = translated if translated else raw_label
        etype = raw.get("type") or raw.get("entity_type") or ""

        addresses: list[str] = []
        for addr in raw.get("addresses", []):
            if isinstance(addr, dict):
                addresses.append(addr.get("full", addr.get("value", str(addr))))
            elif isinstance(addr, str):
                addresses.append(addr)

        identifiers: list[str] = []
        for ident in raw.get("identifiers", []):
            if isinstance(ident, dict):
                identifiers.append(ident.get("value", str(ident)))
            elif isinstance(ident, str):
                identifiers.append(ident)

        country = raw.get("country") or raw.get("country_code") or raw.get("countries")
        if isinstance(country, list):
            country = country[0] if country else None

        sources = raw.get("sources", [])
        if isinstance(sources, list):
            sources = [s if isinstance(s, str) else s.get("name", str(s)) for s in sources]

        return SayariEntity(
            entity_id=eid,
            label=label,
            type=etype,
            country=country,
            addresses=addresses,
            identifiers=identifiers,
            sources=sources,
            pep=bool(raw.get("pep")),
            sanctioned=bool(raw.get("sanctioned") or raw.get("is_sanctioned")),
        )

    # ── resolve ───────────────────────────────────────────────────────────

    async def resolve(self, name: str, limit: int = 5, entity_type: str | None = None) -> SayariResolveResult:
        """Resolve a company/person name to Sayari entity IDs."""
        body: dict[str, Any] = {"name": [name], "limit": limit}
        if entity_type and entity_type in ("company", "person", "vessel"):
            body["type"] = [entity_type]
        data = await self._post(
            "/v1/resolution",
            body=body,
        )
        entities = [self._parse_entity(e) for e in data.get("data", [])]
        return SayariResolveResult(entities=entities, query=name)

    # ── entity profile ────────────────────────────────────────────────────

    async def get_entity(self, entity_id: str) -> SayariEntity:
        """Fetch the full profile of a single entity by ID."""
        data = await self._get(f"/v1/entity/{entity_id}")
        return self._parse_entity(data.get("data", data))

    # ── traversal (related entities) ──────────────────────────────────────

    async def get_traversal(
        self, entity_id: str, depth: int = 1, limit: int = 20, min_strength: int = 0
    ) -> SayariTraversalResult:
        """Get entities related to *entity_id* up to *depth* hops.

        Each item in Sayari's ``data`` array has ``source`` (entity-id str),
        ``target`` (full entity dict), and ``path`` (list of
        ``{field, entity}`` hops).  ``path[0].field`` gives the relationship type.
        """
        params: dict[str, Any] = {"limit": limit}
        if depth > 1:
            params["max_depth"] = depth
        if min_strength:
            params["min_strength"] = min_strength

        data = await self._get(f"/v1/traversal/{entity_id}", params=params)

        entities: list[SayariEntity] = []
        relationships: list[SayariRelationship] = []
        seen_ids: set[str] = set()

        for item in data.get("data", []):
            source_id = item.get("source") or entity_id
            target_raw = item.get("target", {})
            if not isinstance(target_raw, dict):
                continue

            entity = self._parse_entity(target_raw)
            if entity.entity_id and entity.entity_id not in seen_ids:
                entities.append(entity)
                seen_ids.add(entity.entity_id)

            rel_type = "related"
            path = item.get("path", [])
            if path and isinstance(path[0], dict):
                rel_type = path[0].get("field", "related")

            relationships.append(
                SayariRelationship(
                    source_id=source_id,
                    target_id=entity.entity_id,
                    relationship_type=rel_type,
                    attributes={},
                )
            )

        return SayariTraversalResult(
            root_id=entity_id,
            entities=entities,
            relationships=relationships,
        )

    # ── UBO (ultimate beneficial owners) ──────────────────────────────────

    async def get_ubo(self, entity_id: str) -> SayariUBOResult:
        """Fetch beneficial ownership chain for an entity.

        The UBO endpoint mirrors the traversal format: ``data`` is a list of
        ``{source, target, path}`` items.  ``target`` is the beneficial owner entity.
        """
        data = await self._get(f"/v1/ubo/{entity_id}")

        target_name = entity_id

        owners: list[SayariUBOOwner] = []
        seen: set[str] = set()
        for item in data.get("data", []):
            target_raw = item.get("target", {})
            if not isinstance(target_raw, dict):
                continue

            oid = target_raw.get("id") or ""
            if not oid or oid in seen:
                continue
            seen.add(oid)

            translated = target_raw.get("translated_label") or ""
            name = translated if translated else (target_raw.get("label") or oid)
            otype = target_raw.get("type") or ""
            countries = target_raw.get("countries", [])
            country = countries[0] if isinstance(countries, list) and countries else None

            path = item.get("path", [])
            path_length = len(path) if path else 1

            rel_type = ""
            if path and isinstance(path[0], dict):
                rel_type = path[0].get("field", "")

            owners.append(
                SayariUBOOwner(
                    entity_id=oid,
                    name=name,
                    type=otype,
                    country=country,
                    ownership_percentage=None,
                    path_length=path_length,
                    sanctioned=bool(target_raw.get("sanctioned")),
                    pep=bool(target_raw.get("pep")),
                )
            )

        return SayariUBOResult(
            target_id=entity_id,
            target_name=target_name,
            owners=owners,
        )


    # ── entity relationships (for vessel intel) ─────────────────────────

    async def get_entity_relationships(self, entity_id: str) -> list[dict[str, Any]]:
        """Get related companies from an entity profile."""
        data = await self._get(f"/v1/entity/{entity_id}")
        relationships_raw = data.get("relationships", {})
        if isinstance(relationships_raw, dict):
            relationships_raw = relationships_raw.get("data", [])
        results: list[dict[str, Any]] = []
        for r in (relationships_raw or []):
            if not isinstance(r, dict):
                continue
            target = r.get("target", {})
            if not isinstance(target, dict):
                continue
            target_id = target.get("id", "")
            if not target_id:
                continue
            countries = target.get("countries", [])
            country = countries[0] if isinstance(countries, list) and countries else None

            rel_type = ""
            types_dict = r.get("types", {})
            if isinstance(types_dict, dict):
                for _edge_type, infos in types_dict.items():
                    if not isinstance(infos, list):
                        continue
                    for info in infos:
                        if not isinstance(info, dict):
                            continue
                        attrs = info.get("attributes", {})
                        positions = attrs.get("position", [])
                        if positions and isinstance(positions[0], dict):
                            rel_type = positions[0].get("value", "")
                        elif positions:
                            rel_type = str(positions[0])
                        if rel_type:
                            break
                    if rel_type:
                        break

            results.append({
                "entity_id": target_id,
                "label": target.get("label", ""),
                "type": target.get("type", ""),
                "country": country,
                "relationship_type": rel_type.lower().replace(" ", "_"),
                "sanctioned": bool(target.get("sanctioned")),
                "pep": bool(target.get("pep")),
            })
        return results[:15]

    # ── UBO as ownership chain (for vessel intel) ─────────────────────

    async def get_ubo_chain(self, entity_id: str) -> SayariOwnershipChain:
        """Get UBO as a SayariOwnershipChain with parent pointers."""
        data = await self._get(f"/v1/ubo/{entity_id}")
        chain: list[SayariOwnerLink] = []
        max_depth = 0

        for item in data.get("data", []):
            path = item.get("path", [])
            if not path:
                target_raw = item.get("target", {})
                if isinstance(target_raw, dict) and target_raw.get("id"):
                    name = target_raw.get("translated_label") or target_raw.get("label") or target_raw["id"]
                    link = SayariOwnerLink(
                        entity_id=target_raw["id"], name=name,
                        entity_type=target_raw.get("type", ""),
                        country=(target_raw.get("countries", [None]) or [None])[0],
                        is_sanctioned=bool(target_raw.get("sanctioned")),
                        is_pep=bool(target_raw.get("pep")),
                        depth=1, parent_entity_id=entity_id,
                    )
                    if not any(l.entity_id == link.entity_id for l in chain):
                        chain.append(link)
                        max_depth = max(max_depth, 1)
                continue

            prev_eid = entity_id
            for i, node in enumerate(path):
                if not isinstance(node, dict):
                    continue
                entity_raw = node.get("entity", node)
                if not isinstance(entity_raw, dict):
                    continue
                current_eid = entity_raw.get("id", "")
                if not current_eid:
                    continue
                name = entity_raw.get("translated_label") or entity_raw.get("label") or current_eid
                countries = entity_raw.get("countries", [])
                country = countries[0] if isinstance(countries, list) and countries else None
                is_sanctioned = bool(entity_raw.get("sanctioned"))
                is_pep = bool(entity_raw.get("pep"))
                risk = entity_raw.get("risk", {})
                if isinstance(risk, dict):
                    cats = risk.get("categories", {})
                    if isinstance(cats, dict):
                        is_sanctioned = is_sanctioned or bool(cats.get("sanctioned"))
                        is_pep = is_pep or bool(cats.get("pep"))
                depth = i + 1
                max_depth = max(max_depth, depth)
                link = SayariOwnerLink(
                    entity_id=current_eid, name=name,
                    entity_type=entity_raw.get("type", ""), country=country,
                    is_sanctioned=is_sanctioned, is_pep=is_pep,
                    depth=depth, parent_entity_id=prev_eid,
                )
                if not any(l.entity_id == link.entity_id for l in chain):
                    chain.append(link)
                prev_eid = current_eid

        return SayariOwnershipChain(
            owner_entity_id=entity_id, chain=chain, max_depth_reached=max_depth,
        )

    # ── trade search ──────────────────────────────────────────────────

    async def search_trade(self, entity_name: str, limit: int = 20) -> SayariTradeActivity:
        """Search trade/shipment records by entity name."""
        records: list[SayariTradeRecord] = []
        countries: set[str] = set()
        hs_codes: dict[str, str] = {}
        sankey_flows: list[dict] = []

        for role_field in ("supplier_name", "buyer_name"):
            try:
                data = await self._post(
                    "/v1/trade/search/shipments",
                    {"filter": {role_field: entity_name}, "limit": min(limit, 50)},
                )
            except Exception as exc:
                logger.debug("Trade search (%s=%s) failed: %s", role_field, entity_name, exc)
                continue

            for shipment in data.get("data", []):
                dep = shipment.get("departure_country") or ""
                arr = shipment.get("arrival_country") or ""
                if dep:
                    countries.add(dep)
                if arr:
                    countries.add(arr)
                hs = shipment.get("hs_code") or ""
                hs_desc = shipment.get("hs_description", "") or ""
                if hs and hs not in hs_codes:
                    hs_codes[hs] = hs_desc or _hs_to_category(hs)
                supplier_risks = shipment.get("supplier_risk", [])
                buyer_risks = shipment.get("buyer_risk", [])
                if isinstance(supplier_risks, str):
                    supplier_risks = [supplier_risks] if supplier_risks else []
                if isinstance(buyer_risks, str):
                    buyer_risks = [buyer_risks] if buyer_risks else []

                records.append(SayariTradeRecord(
                    supplier=str(shipment.get("supplier_name") or shipment.get("supplier") or ""),
                    buyer=str(shipment.get("buyer_name") or shipment.get("buyer") or ""),
                    supplier_risks=supplier_risks, buyer_risks=buyer_risks,
                    hs_code=hs or None, hs_description=hs_desc or None,
                    commodity_category=_hs_to_category(hs) if hs else "",
                    departure_country=dep or None, arrival_country=arr or None,
                    date=shipment.get("arrival_date") or shipment.get("departure_date"),
                    weight_kg=shipment.get("weight_kg"),
                    value_usd=shipment.get("value_usd"),
                ))
                if dep and arr:
                    sankey_flows.append({
                        "source": dep, "target": arr, "value": 1,
                        "category": _hs_to_category(hs) if hs else "Goods",
                    })

        return SayariTradeActivity(
            records=records[:limit],
            top_hs_codes=[{"code": k, "description": v} for k, v in list(hs_codes.items())[:10]],
            trade_countries=sorted(countries),
            record_count=len(records),
            sankey_flows=sankey_flows,
        )


_client: SayariClient | None = None


def get_sayari_client() -> SayariClient:
    """Singleton accessor for the Sayari client."""
    global _client
    if _client is None:
        _client = SayariClient()
    return _client


# ---------------------------------------------------------------------------
# Vessel intelligence entry point
# ---------------------------------------------------------------------------


async def get_vessel_intel(
    vessel_name: str,
    imo: str | None = None,
    owner_name: str | None = None,
) -> SayariVesselIntel:
    """Get Sayari intelligence for a vessel: UBO ownership + trade activity.

    Resolve vessel → get related companies → trace UBO from each →
    get trade data. Builds a tree-structured ownership chain with
    parent_entity_id pointers for hierarchical graph rendering.
    """
    if not config.sayari_client_id or not config.sayari_client_secret:
        return SayariVesselIntel(resolved=False)

    client = get_sayari_client()

    try:
        resolved = await client.resolve(vessel_name, limit=5, entity_type="vessel")
        entity_id: str | None = None
        label = vessel_name

        if resolved.entities:
            for ent in resolved.entities:
                if ent.type == "vessel":
                    entity_id = ent.entity_id
                    label = ent.label
                    break
            if not entity_id:
                entity_id = resolved.entities[0].entity_id
                label = resolved.entities[0].label
        else:
            resolved = await client.resolve(vessel_name, limit=5)
            if resolved.entities:
                entity_id = resolved.entities[0].entity_id
                label = resolved.entities[0].label

        if not entity_id:
            return SayariVesselIntel(resolved=False)

        related: list[dict[str, Any]] = []
        try:
            related = await client.get_entity_relationships(entity_id)
            related = [r for r in related if r.get("type") == "company"][:5]
        except Exception as exc:
            logger.debug("get_entity_relationships failed: %s", exc)

        chain_links: list[SayariOwnerLink] = []
        seen_ids: set[str] = set()
        owner_company_name = label

        for comp in related:
            comp_eid = comp["entity_id"]
            if comp_eid in seen_ids:
                continue
            seen_ids.add(comp_eid)
            chain_links.append(SayariOwnerLink(
                entity_id=comp_eid, name=comp["label"], entity_type="company",
                country=comp.get("country"),
                is_sanctioned=comp.get("sanctioned", False),
                is_pep=comp.get("pep", False),
                depth=1,
                relationship_type=comp.get("relationship_type", ""),
                parent_entity_id=entity_id,
            ))

        owner_comp = None
        for comp in related:
            if comp.get("relationship_type") in ("registered_owner", "owner", "beneficial_owner"):
                owner_comp = comp
                break
        if not owner_comp and related:
            owner_comp = related[0]
        if owner_comp:
            owner_company_name = owner_comp["label"]

        if related:
            ubo_tasks = [client.get_ubo_chain(comp["entity_id"]) for comp in related]
            ubo_results = await asyncio.gather(*ubo_tasks, return_exceptions=True)
            for comp, ubo_result in zip(related, ubo_results):
                if isinstance(ubo_result, Exception):
                    logger.debug("UBO trace failed for %s: %s", comp["label"], ubo_result)
                    continue
                for link in ubo_result.chain:
                    if link.entity_id in seen_ids:
                        continue
                    seen_ids.add(link.entity_id)
                    link.depth += 1
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

        trade_tasks = [client.search_trade(vessel_name, limit=20)]
        if owner_company_name and owner_company_name.lower() != vessel_name.lower():
            trade_tasks.append(client.search_trade(owner_company_name, limit=20))
        trade_results = await asyncio.gather(*trade_tasks, return_exceptions=True)

        merged_records: list[SayariTradeRecord] = []
        seen_records: set[str] = set()
        all_hs: dict[str, str] = {}
        all_countries: set[str] = set()
        all_sankey: list[dict] = []

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
            all_sankey.extend(tr.sankey_flows)

        trade = SayariTradeActivity(
            records=merged_records[:20],
            top_hs_codes=[{"code": k, "description": v} for k, v in list(all_hs.items())[:10]],
            trade_countries=sorted(all_countries),
            record_count=len(merged_records),
            sankey_flows=all_sankey,
        )

        risk_scores: dict[str, float] = {}
        sanctioned_count = sum(1 for l in chain_links if l.is_sanctioned)
        pep_count = sum(1 for l in chain_links if l.is_pep)
        total = len(chain_links) or 1
        risk_scores["ownership_sanctioned_pct"] = round(sanctioned_count / total * 100, 1)
        risk_scores["ownership_pep_pct"] = round(pep_count / total * 100, 1)
        risk_scores["ownership_depth"] = float(total)
        risky_records = sum(1 for r in merged_records if r.supplier_risks or r.buyer_risks)
        total_records = len(merged_records) or 1
        risk_scores["trade_risk_pct"] = round(risky_records / total_records * 100, 1)
        risk_scores["trade_country_count"] = float(len(all_countries))

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
