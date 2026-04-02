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
    SayariRelationship,
    SayariResolveResult,
    SayariTraversalResult,
    SayariUBOOwner,
    SayariUBOResult,
)

logger = logging.getLogger(__name__)

_BASE = "https://api.sayari.com"
_TOKEN_URL = f"{_BASE}/oauth/token"


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


_client: SayariClient | None = None


def get_sayari_client() -> SayariClient:
    """Singleton accessor for the Sayari client."""
    global _client
    if _client is None:
        _client = SayariClient()
    return _client
