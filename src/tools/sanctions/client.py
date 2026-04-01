"""Async API clients for OpenSanctions and OFAC SDN data sources."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from src.common.cache import get_cached, set_cached
from src.common.config import config
from src.common.http_client import fetch_json, fetch_text
from src.tools.screening.client import search_csl

from .models import (
    ProximityEdge,
    ProximityNode,
    ProximityResult,
    RecentDesignation,
    SanctionEntry,
    SanctionSearchResult,
    SanctionStatus,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
OPENSANCTIONS_BASE = "https://api.opensanctions.org"
OFAC_SDN_CSV_URL = "https://www.treasury.gov/ofac/downloads/sdn.csv"
OFAC_ALT_CSV_URL = "https://www.treasury.gov/ofac/downloads/alt.csv"
OFAC_ADD_CSV_URL = "https://www.treasury.gov/ofac/downloads/add.csv"

_CACHE_NS_OPENSANCTIONS = "opensanctions"
_CACHE_NS_OFAC = "ofac"
_CACHE_TTL_SEARCH = 3600  # 1 hour for search results
_CACHE_TTL_SDN = 86400  # 24 hours for SDN list download


# ---------------------------------------------------------------------------
# OpenSanctions client
# ---------------------------------------------------------------------------


class OpenSanctionsClient:
    """Client for the OpenSanctions API — used ONLY for proximity/graph queries.

    All primary sanctions searching now goes through Trade.gov CSL + OFAC SDN.
    OpenSanctions is retained solely for get_entity_relationships / get_entity
    which provide the entity-graph data required by get_proximity().
    """

    async def search_entities(
        self,
        query: str,
        limit: int = 10,
        entity_type: str = "any",
    ) -> list[SanctionEntry]:
        """Search OpenSanctions for entities matching *query*.

        This is used only by get_proximity() to seed the graph walk.
        """
        if not config.opensanctions_api_key:
            logger.debug("OpenSanctions API key not configured; skipping search")
            return []

        cache_params = {"q": query, "limit": limit, "entity_type": entity_type}
        cached = get_cached(_CACHE_NS_OPENSANCTIONS, action="search", **cache_params)
        if cached is not None and len(cached) > 0:
            return [SanctionEntry.model_validate(e) for e in cached]

        params: dict[str, Any] = {"q": query, "limit": limit}
        if entity_type and entity_type != "any":
            schema_map = {
                "person": "Person",
                "company": "Company",
                "organization": "Organization",
                "vessel": "Vessel",
                "aircraft": "Aircraft",
            }
            schema = schema_map.get(entity_type.lower())
            if schema:
                params["schema"] = schema

        try:
            headers = {"Authorization": f"ApiKey {config.opensanctions_api_key}"}
            data = await fetch_json(
                f"{OPENSANCTIONS_BASE}/search/default", params=params, headers=headers
            )
        except Exception as exc:
            logger.warning("OpenSanctions search unavailable (query=%s): %s", query, type(exc).__name__)
            return []

        entries = self._parse_search_results(data)

        if entries:
            set_cached(
                [e.model_dump(mode="json") for e in entries],
                _CACHE_NS_OPENSANCTIONS,
                ttl=_CACHE_TTL_SEARCH,
                action="search",
                **cache_params,
            )
        return entries

    async def get_entity(self, entity_id: str) -> SanctionEntry | None:
        """Fetch a single entity by its OpenSanctions ID."""
        if not config.opensanctions_api_key:
            return None

        cached = get_cached(_CACHE_NS_OPENSANCTIONS, action="entity", id=entity_id)
        if cached is not None:
            return SanctionEntry.model_validate(cached)

        try:
            headers = {"Authorization": f"ApiKey {config.opensanctions_api_key}"}
            data = await fetch_json(
                f"{OPENSANCTIONS_BASE}/entities/{entity_id}", headers=headers
            )
        except Exception as exc:
            logger.warning("OpenSanctions get_entity unavailable (id=%s): %s", entity_id, type(exc).__name__)
            return None

        entry = self._parse_entity(data)
        if entry:
            set_cached(
                entry.model_dump(mode="json"),
                _CACHE_NS_OPENSANCTIONS,
                ttl=_CACHE_TTL_SEARCH,
                action="entity",
                id=entity_id,
            )
        return entry

    async def get_entity_relationships(
        self,
        entity_id: str,
    ) -> list[dict[str, Any]]:
        """Retrieve relationships/neighbors for an entity from OpenSanctions.

        Uses the entity detail endpoint which includes related entities in its
        ``referents`` and properties.
        """
        if not config.opensanctions_api_key:
            return []

        cached = get_cached(
            _CACHE_NS_OPENSANCTIONS, action="relationships", id=entity_id
        )
        if cached is not None:
            return cached

        try:
            headers = {"Authorization": f"ApiKey {config.opensanctions_api_key}"}
            data = await fetch_json(
                f"{OPENSANCTIONS_BASE}/entities/{entity_id}", headers=headers
            )
        except Exception as exc:
            logger.warning("OpenSanctions relationships unavailable (id=%s): %s", entity_id, type(exc).__name__)
            return []

        relationships: list[dict[str, Any]] = []

        # Extract relationships from entity properties
        props = data.get("properties", {})

        # Common relationship properties in FtM schema
        rel_keys = [
            "ownershipOwner",
            "ownershipAsset",
            "directorshipDirector",
            "directorshipOrganization",
            "membershipMember",
            "membershipOrganization",
            "associateOf",
            "parent",
            "subsidiaries",
            "holder",
            "asset",
        ]
        for key in rel_keys:
            values = props.get(key, [])
            for val in values:
                if isinstance(val, str):
                    relationships.append(
                        {
                            "related_id": val,
                            "relationship_type": key,
                            "source_id": entity_id,
                        }
                    )
                elif isinstance(val, dict) and val.get("id"):
                    relationships.append(
                        {
                            "related_id": val["id"],
                            "related_name": val.get("caption", ""),
                            "relationship_type": key,
                            "source_id": entity_id,
                        }
                    )

        # Also look at referents (merged entity IDs)
        for ref_id in data.get("referents", []):
            relationships.append(
                {
                    "related_id": ref_id,
                    "relationship_type": "sameAs",
                    "source_id": entity_id,
                }
            )

        set_cached(
            relationships,
            _CACHE_NS_OPENSANCTIONS,
            ttl=_CACHE_TTL_SEARCH,
            action="relationships",
            id=entity_id,
        )
        return relationships

    # --- Helpers ---

    def _parse_search_results(self, data: dict[str, Any]) -> list[SanctionEntry]:
        """Parse the OpenSanctions search response into SanctionEntry list."""
        entries: list[SanctionEntry] = []
        for result in data.get("results", []):
            entry = self._parse_entity(result)
            if entry:
                entry.score = result.get("score")
                entries.append(entry)
        return entries

    def _parse_entity(self, data: dict[str, Any]) -> SanctionEntry | None:
        """Parse a single OpenSanctions entity dict into a SanctionEntry."""
        entity_id = data.get("id")
        if not entity_id:
            return None

        props = data.get("properties", {})
        caption = data.get("caption", "")
        names = props.get("name", [])
        name = caption or (names[0] if names else entity_id)

        # Aliases: all names except the primary
        aliases = [n for n in names if n != name]
        aliases.extend(props.get("alias", []))
        aliases.extend(props.get("weakAlias", []))

        # Entity type mapping
        schema = data.get("schema", "").lower()
        type_map = {
            "person": "person",
            "company": "company",
            "organization": "organization",
            "legalentity": "company",
            "vessel": "vessel",
            "aircraft": "aircraft",
        }
        entity_type = type_map.get(schema, "unknown")

        # Programs / sanctions lists
        programs = props.get("program", [])
        topics = data.get("datasets", [])
        if not programs:
            programs = props.get("topics", [])

        # Addresses
        addr_parts = props.get("address", [])
        countries = props.get("country", [])
        addresses = addr_parts if addr_parts else countries

        # Identifiers
        identifiers: dict[str, str] = {}
        for id_key in [
            "passportNumber",
            "idNumber",
            "registrationNumber",
            "innCode",
            "taxNumber",
            "ogrnCode",
            "swiftBic",
            "imoNumber",
        ]:
            vals = props.get(id_key, [])
            if vals:
                identifiers[id_key] = vals[0]

        # Designation date
        designation_date: datetime | None = None
        date_strs = props.get("createdAt", []) or props.get("modifiedAt", [])
        if date_strs:
            try:
                designation_date = datetime.fromisoformat(
                    date_strs[0].replace("Z", "+00:00")
                )
            except (ValueError, IndexError):
                pass

        # List source
        datasets = data.get("datasets", [])
        list_source = ", ".join(datasets) if isinstance(datasets, list) else "OpenSanctions"

        return SanctionEntry(
            id=entity_id,
            name=name,
            aliases=list(set(aliases)),
            entity_type=entity_type,
            programs=programs,
            addresses=addresses,
            identifiers=identifiers,
            list_source=list_source,
            designation_date=designation_date,
            remarks="; ".join(props.get("notes", [])) or None,
        )


# ---------------------------------------------------------------------------
# OFAC SDN client
# ---------------------------------------------------------------------------


class OFACClient:
    """Client for OFAC Specially Designated Nationals (SDN) list.

    Downloads and parses the consolidated CSV from Treasury.gov.
    """

    _sdn_entries: list[dict[str, str]] | None = None
    _alt_names: dict[str, list[str]] | None = None
    _addresses: dict[str, list[str]] | None = None

    async def _ensure_loaded(self) -> None:
        """Download and parse the SDN CSV if not already in memory/cache."""
        if self._sdn_entries is not None:
            return

        # Try cache first
        cached_sdn = get_cached(_CACHE_NS_OFAC, action="sdn_csv")
        cached_alt = get_cached(_CACHE_NS_OFAC, action="alt_csv")
        cached_add = get_cached(_CACHE_NS_OFAC, action="add_csv")

        if cached_sdn is not None:
            self._sdn_entries = cached_sdn
            self._alt_names = cached_alt or {}
            self._addresses = cached_add or {}
            return

        await self._download_and_parse()

    async def _download_and_parse(self) -> None:
        """Download SDN, ALT, and ADD CSV files and parse them."""
        # Download SDN main list
        try:
            sdn_text = await fetch_text(OFAC_SDN_CSV_URL, timeout=60.0)
        except Exception as exc:
            logger.warning("Failed to download OFAC SDN CSV: %s", exc)
            self._sdn_entries = []
            self._alt_names = {}
            self._addresses = {}
            return

        self._sdn_entries = self._parse_sdn_csv(sdn_text)

        # Download alternate names
        try:
            alt_text = await fetch_text(OFAC_ALT_CSV_URL, timeout=60.0)
            self._alt_names = self._parse_alt_csv(alt_text)
        except Exception:
            logger.warning("Failed to download OFAC ALT CSV, continuing without aliases")
            self._alt_names = {}

        # Download addresses
        try:
            add_text = await fetch_text(OFAC_ADD_CSV_URL, timeout=60.0)
            self._addresses = self._parse_add_csv(add_text)
        except Exception:
            logger.warning("Failed to download OFAC ADD CSV, continuing without addresses")
            self._addresses = {}

        # Cache parsed data
        set_cached(
            self._sdn_entries, _CACHE_NS_OFAC, ttl=_CACHE_TTL_SDN, action="sdn_csv"
        )
        set_cached(
            self._alt_names, _CACHE_NS_OFAC, ttl=_CACHE_TTL_SDN, action="alt_csv"
        )
        set_cached(
            self._addresses, _CACHE_NS_OFAC, ttl=_CACHE_TTL_SDN, action="add_csv"
        )

    def _parse_sdn_csv(self, text: str) -> list[dict[str, str]]:
        """Parse the OFAC SDN CSV (no header row).

        Columns: ent_num, SDN_Name, SDN_Type, Program, Title, Call_Sign,
                 Vess_type, Tonnage, GRT, Vess_flag, Vess_owner, Remarks
        """
        entries: list[dict[str, str]] = []
        reader = csv.reader(io.StringIO(text))
        for row in reader:
            if len(row) < 6:
                continue
            entries.append(
                {
                    "ent_num": row[0].strip(),
                    "name": row[1].strip(),
                    "type": row[2].strip(),
                    "program": row[3].strip(),
                    "title": row[4].strip() if len(row) > 4 else "",
                    "call_sign": row[5].strip() if len(row) > 5 else "",
                    "vessel_type": row[6].strip() if len(row) > 6 else "",
                    "tonnage": row[7].strip() if len(row) > 7 else "",
                    "grt": row[8].strip() if len(row) > 8 else "",
                    "vessel_flag": row[9].strip() if len(row) > 9 else "",
                    "vessel_owner": row[10].strip() if len(row) > 10 else "",
                    "remarks": row[11].strip() if len(row) > 11 else "",
                }
            )
        return entries

    def _parse_alt_csv(self, text: str) -> dict[str, list[str]]:
        """Parse OFAC ALT CSV — alternate names keyed by ent_num."""
        alt_map: dict[str, list[str]] = {}
        reader = csv.reader(io.StringIO(text))
        for row in reader:
            if len(row) < 4:
                continue
            ent_num = row[0].strip()
            alt_name = row[3].strip()
            if ent_num and alt_name:
                alt_map.setdefault(ent_num, []).append(alt_name)
        return alt_map

    def _parse_add_csv(self, text: str) -> dict[str, list[str]]:
        """Parse OFAC ADD CSV — addresses keyed by ent_num."""
        add_map: dict[str, list[str]] = {}
        reader = csv.reader(io.StringIO(text))
        for row in reader:
            if len(row) < 6:
                continue
            ent_num = row[0].strip()
            parts = [p.strip() for p in row[2:6] if p.strip()]
            address = ", ".join(parts)
            if ent_num and address:
                add_map.setdefault(ent_num, []).append(address)
        return add_map

    async def search(
        self,
        query: str,
        entity_type: str = "any",
    ) -> list[SanctionEntry]:
        """Search the OFAC SDN list for entities matching *query*."""
        cache_params = {"q": query, "entity_type": entity_type}
        cached = get_cached(_CACHE_NS_OFAC, action="search", **cache_params)
        if cached is not None:
            return [SanctionEntry.model_validate(e) for e in cached]

        await self._ensure_loaded()
        assert self._sdn_entries is not None

        query_lower = query.lower()
        # Tokenize using word characters to avoid punctuation mismatches like
        # "VEKSELBERG," vs "Vekselberg".
        query_tokens = set(re.findall(r"[a-z0-9]+", query_lower))
        results: list[SanctionEntry] = []

        type_filter = ""
        if entity_type and entity_type != "any":
            type_map = {
                "person": "individual",
                "company": "entity",
                "organization": "entity",
                "vessel": "vessel",
                "aircraft": "aircraft",
            }
            type_filter = type_map.get(entity_type.lower(), "")

        for row in self._sdn_entries:
            if type_filter and row["type"].strip('"').lower() != type_filter:
                continue

            name = row["name"]
            ent_num = row["ent_num"]
            name_lower = name.lower()

            # Check main name
            score = self._match_score(query_lower, query_tokens, name_lower)

            # Check alternate names
            alt_names = (self._alt_names or {}).get(ent_num, [])
            for alt in alt_names:
                alt_score = self._match_score(query_lower, query_tokens, alt.lower())
                score = max(score, alt_score)

            if score < 0.3:
                continue

            # Map OFAC type to our type
            ofac_type = row["type"].strip('"').lower()
            etype_map = {
                "individual": "person",
                "entity": "company",
                "vessel": "vessel",
                "aircraft": "aircraft",
            }
            mapped_type = etype_map.get(ofac_type, "unknown")

            # Parse programs
            programs = [p.strip() for p in row["program"].split(";") if p.strip()]

            # Extract identifiers from remarks
            identifiers = self._parse_remarks_identifiers(row["remarks"])

            # Build addresses
            addresses = (self._addresses or {}).get(ent_num, [])

            # Parse designation date from remarks if present
            designation_date = self._extract_date_from_remarks(row["remarks"])

            results.append(
                SanctionEntry(
                    id=f"ofac-{ent_num}",
                    name=name,
                    aliases=alt_names,
                    entity_type=mapped_type,
                    programs=programs,
                    addresses=addresses,
                    identifiers=identifiers,
                    list_source="OFAC SDN",
                    designation_date=designation_date,
                    remarks=row["remarks"] or None,
                    score=round(score, 3),
                )
            )

        # Sort by score descending, limit to top 20
        results.sort(key=lambda e: e.score or 0, reverse=True)
        results = results[:20]

        set_cached(
            [e.model_dump(mode="json") for e in results],
            _CACHE_NS_OFAC,
            ttl=_CACHE_TTL_SEARCH,
            action="search",
            **cache_params,
        )
        return results

    async def get_recent_designations(
        self,
        days: int = 30,
    ) -> list[RecentDesignation]:
        """Return entries with designation dates within *days* of today.

        Since OFAC CSV has limited date info in the remarks field, this is
        a best-effort extraction. We look for date patterns in the remarks
        and the overall file freshness.
        """
        await self._ensure_loaded()
        assert self._sdn_entries is not None

        cutoff = datetime.utcnow() - timedelta(days=days)
        recent: list[RecentDesignation] = []

        for row in self._sdn_entries:
            designation_date = self._extract_date_from_remarks(row["remarks"])
            if designation_date and designation_date >= cutoff:
                ent_num = row["ent_num"]
                ofac_type = row["type"].strip('"').lower()
                etype_map = {
                    "individual": "person",
                    "entity": "company",
                    "vessel": "vessel",
                    "aircraft": "aircraft",
                }
                alt_names = (self._alt_names or {}).get(ent_num, [])
                addresses = (self._addresses or {}).get(ent_num, [])
                programs = [p.strip() for p in row["program"].split(";") if p.strip()]

                entry = SanctionEntry(
                    id=f"ofac-{ent_num}",
                    name=row["name"],
                    aliases=alt_names,
                    entity_type=etype_map.get(ofac_type, "unknown"),
                    programs=programs,
                    addresses=addresses,
                    identifiers=self._parse_remarks_identifiers(row["remarks"]),
                    list_source="OFAC SDN",
                    designation_date=designation_date,
                    remarks=row["remarks"] or None,
                )
                recent.append(
                    RecentDesignation(
                        entry=entry,
                        action_type="designation",
                        effective_date=designation_date,
                    )
                )

        recent.sort(
            key=lambda r: r.effective_date or datetime.min, reverse=True
        )
        return recent

    # --- Helpers ---

    @staticmethod
    def _match_score(
        query_lower: str, query_tokens: set[str], name_lower: str
    ) -> float:
        """Compute a simple fuzzy match score between query and name."""
        # Exact substring match
        if query_lower in name_lower:
            return 1.0
        if name_lower in query_lower:
            return 0.9

        # Token overlap
        name_tokens = set(re.findall(r"[a-z0-9]+", name_lower))
        if not query_tokens:
            return 0.0
        overlap = query_tokens & name_tokens
        if overlap:
            return len(overlap) / len(query_tokens) * 0.8

        # Partial token matching
        partial_matches = sum(
            1
            for qt in query_tokens
            if any(qt in nt or nt in qt for nt in name_tokens)
        )
        if partial_matches:
            return partial_matches / len(query_tokens) * 0.5

        return 0.0

    @staticmethod
    def _parse_remarks_identifiers(remarks: str) -> dict[str, str]:
        """Extract key-value identifiers from OFAC remarks field."""
        identifiers: dict[str, str] = {}
        if not remarks:
            return identifiers

        # Common patterns: "DOB 01 Jan 1970; POB Moscow; Passport 12345"
        id_keys = [
            "DOB",
            "POB",
            "Passport",
            "National ID No.",
            "Tax ID No.",
            "Registration ID",
            "SWIFT/BIC",
            "Website",
            "Email Address",
            "alt. Passport",
            "SSN",
            "Cedula No.",
            "D-U-N-S Number",
        ]
        for key in id_keys:
            if key in remarks:
                # Extract value up to next semicolon or end
                start = remarks.index(key) + len(key)
                # Skip whitespace and optional colon/period
                while start < len(remarks) and remarks[start] in " :.":
                    start += 1
                end = remarks.find(";", start)
                if end == -1:
                    end = len(remarks)
                value = remarks[start:end].strip().rstrip(".")
                if value:
                    identifiers[key] = value
        return identifiers

    @staticmethod
    def _extract_date_from_remarks(remarks: str) -> datetime | None:
        """Try to extract a designation/listing date from remarks."""
        if not remarks:
            return None

        import re

        # Pattern: "Linked To: ... (dd Mon yyyy)" or just dates in remarks
        date_patterns = [
            r"(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})",
            r"(\d{4}-\d{2}-\d{2})",
        ]
        for pattern in date_patterns:
            match = re.search(pattern, remarks, re.IGNORECASE)
            if match:
                date_str = match.group(1)
                for fmt in [
                    "%d %b %Y",
                    "%d %B %Y",
                    "%Y-%m-%d",
                ]:
                    try:
                        return datetime.strptime(date_str, fmt)
                    except ValueError:
                        continue
        return None


# ---------------------------------------------------------------------------
# Composite sanctions client
# ---------------------------------------------------------------------------


class SanctionsClient:
    """Unified client querying the Trade.gov Consolidated Screening List and OFAC SDN.

    OpenSanctions is retained only for get_proximity() which requires entity graph data
    that CSL does not provide. All standard sanctions checks use CSL + OFAC SDN.
    """

    def __init__(self) -> None:
        self.opensanctions = OpenSanctionsClient()
        self.ofac = OFACClient()

    @staticmethod
    def _csl_to_entries(csl_results: list[dict[str, Any]]) -> list[SanctionEntry]:
        """Convert Trade.gov CSL API results to SanctionEntry objects."""
        _type_map = {
            "entity": "company",
            "individual": "person",
            "vessel": "vessel",
            "aircraft": "aircraft",
        }
        entries: list[SanctionEntry] = []
        for hit in csl_results:
            name = (hit.get("name") or "").strip()
            if not name:
                continue

            designation_date: datetime | None = None
            start_date = hit.get("start_date")
            if start_date:
                for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y"):
                    try:
                        designation_date = datetime.strptime(start_date[:10], fmt[:len(start_date[:10])])
                        break
                    except ValueError:
                        continue

            identifiers: dict[str, str] = {}
            for id_obj in hit.get("ids", []):
                id_type = (id_obj.get("type") or "").strip()
                id_num = (id_obj.get("number") or "").strip()
                if id_type and id_num:
                    identifiers[id_type] = id_num

            addresses: list[str] = []
            for addr in hit.get("addresses", []):
                parts = [addr.get("city"), addr.get("state"), addr.get("country")]
                parts = [p for p in parts if p]
                if parts:
                    addresses.append(", ".join(parts))

            entries.append(SanctionEntry(
                id=f"csl-{hit.get('entity_number') or name}",
                name=name,
                aliases=hit.get("alt_names") or [],
                entity_type=_type_map.get((hit.get("type") or "").lower(), "unknown"),
                programs=hit.get("programs") or [],
                addresses=addresses,
                identifiers=identifiers,
                list_source=hit.get("source") or "CSL",
                designation_date=designation_date,
                remarks=hit.get("remarks"),
                score=0.9,
            ))
        return entries

    async def search(
        self,
        query: str,
        entity_type: str = "any",
    ) -> SanctionSearchResult:
        """Search the Trade.gov CSL and OFAC SDN and merge results."""
        csl_task = asyncio.create_task(search_csl(query))
        ofac_task = asyncio.create_task(self.ofac.search(query, entity_type=entity_type))

        csl_raw, ofac_results = await asyncio.gather(
            csl_task, ofac_task, return_exceptions=True
        )

        matches: list[SanctionEntry] = []

        if isinstance(csl_raw, list):
            matches.extend(self._csl_to_entries(csl_raw))
        else:
            logger.warning("CSL search error: %s", csl_raw)

        if isinstance(ofac_results, list):
            matches.extend(ofac_results)
        else:
            logger.warning("OFAC search error: %s", ofac_results)

        # Deduplicate by name (case-insensitive), keeping higher-scored entry
        seen: dict[str, SanctionEntry] = {}
        for entry in matches:
            key = entry.name.lower().strip()
            existing = seen.get(key)
            if existing is None or (entry.score or 0) > (existing.score or 0):
                seen[key] = entry
        deduped = list(seen.values())
        deduped.sort(key=lambda e: e.score or 0, reverse=True)

        return SanctionSearchResult(
            query=query,
            matches=deduped,
            total_matches=len(deduped),
        )

    async def check_status(self, entity_name: str) -> SanctionStatus:
        """Check whether *entity_name* is sanctioned on any list.

        Uses Trade.gov CSL + OFAC SDN. CSL results score 0.9 (government-verified).
        OFAC SDN fuzzy matches require >= 0.85 to reduce false positives.
        """
        result = await self.search(entity_name)

        strong_matches = [m for m in result.matches if (m.score or 0) >= 0.85]

        lists_found: list[str] = []
        designation_dates: list[datetime | None] = []
        all_programs: list[str] = []

        for entry in strong_matches:
            if entry.list_source and entry.list_source not in lists_found:
                lists_found.append(entry.list_source)
            if entry.designation_date:
                designation_dates.append(entry.designation_date)
            for prog in entry.programs:
                if prog not in all_programs:
                    all_programs.append(prog)

        return SanctionStatus(
            entity_name=entity_name,
            is_sanctioned=len(strong_matches) > 0,
            lists_found=lists_found,
            designation_dates=designation_dates,
            programs=all_programs,
            entries=strong_matches,
        )

    async def get_proximity(
        self,
        entity_name: str,
        max_hops: int = 3,
    ) -> ProximityResult:
        """Check degrees of separation from sanctioned entities.

        Uses the OpenSanctions API to walk the entity graph up to *max_hops*
        levels. At each level, checks if any connected entities are sanctioned.
        """
        # First, find the entity in OpenSanctions
        search_results = await self.opensanctions.search_entities(entity_name, limit=3)
        if not search_results:
            return ProximityResult(
                query_entity=entity_name,
                nearest_sanctioned_hop=None,
            )

        root_entry = search_results[0]
        root_node = ProximityNode(
            entity_id=root_entry.id,
            entity_name=root_entry.name,
            entity_type=root_entry.entity_type,
            is_sanctioned=bool(root_entry.programs),
            sanctions_lists=[root_entry.list_source] if root_entry.programs else [],
            hop_distance=0,
        )

        nodes: dict[str, ProximityNode] = {root_entry.id: root_node}
        edges: list[ProximityEdge] = []
        sanctioned_neighbors: list[ProximityNode] = []
        nearest_sanctioned_hop: int | None = None

        if root_node.is_sanctioned:
            nearest_sanctioned_hop = 0
            sanctioned_neighbors.append(root_node)

        # BFS through the graph
        current_frontier = [root_entry.id]

        for hop in range(1, max_hops + 1):
            if not current_frontier:
                break

            next_frontier: list[str] = []
            for entity_id in current_frontier:
                relationships = await self.opensanctions.get_entity_relationships(
                    entity_id
                )
                for rel in relationships:
                    related_id = rel.get("related_id", "")
                    if not related_id or related_id in nodes:
                        continue

                    # Fetch the related entity details
                    related_entry = await self.opensanctions.get_entity(related_id)
                    if related_entry is None:
                        # Create a minimal node from what we know
                        node = ProximityNode(
                            entity_id=related_id,
                            entity_name=rel.get("related_name", related_id),
                            hop_distance=hop,
                        )
                    else:
                        is_sanctioned = bool(related_entry.programs)
                        node = ProximityNode(
                            entity_id=related_id,
                            entity_name=related_entry.name,
                            entity_type=related_entry.entity_type,
                            is_sanctioned=is_sanctioned,
                            sanctions_lists=(
                                [related_entry.list_source]
                                if is_sanctioned
                                else []
                            ),
                            hop_distance=hop,
                        )

                    nodes[related_id] = node
                    next_frontier.append(related_id)

                    edges.append(
                        ProximityEdge(
                            source_id=entity_id,
                            target_id=related_id,
                            relationship_type=rel.get(
                                "relationship_type", "associated"
                            ),
                        )
                    )

                    if node.is_sanctioned:
                        sanctioned_neighbors.append(node)
                        if (
                            nearest_sanctioned_hop is None
                            or hop < nearest_sanctioned_hop
                        ):
                            nearest_sanctioned_hop = hop

            current_frontier = next_frontier

        return ProximityResult(
            query_entity=entity_name,
            nodes=list(nodes.values()),
            edges=edges,
            nearest_sanctioned_hop=nearest_sanctioned_hop,
            sanctioned_neighbors=sanctioned_neighbors,
        )

    async def get_recent_designations(
        self,
        days: int = 30,
    ) -> list[RecentDesignation]:
        """Get recent OFAC designation actions."""
        return await self.ofac.get_recent_designations(days=days)
