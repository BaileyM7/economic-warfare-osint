"""Builds entity graphs from tool results for visualization."""

from __future__ import annotations

from typing import Any

import networkx as nx

from src.common.types import Confidence, Entity, EntityGraph, Relationship, SourceReference


def build_graph_from_results(tool_results: dict[str, Any]) -> EntityGraph:
    """Extract entities and relationships from raw tool results into an EntityGraph."""
    graph = EntityGraph()

    for step_key, step_data in tool_results.items():
        if isinstance(step_data, dict) and "results" in step_data:
            for tool_name, result in step_data["results"].items():
                _extract_from_tool_response(graph, tool_name, result)

    return graph


def _extract_from_tool_response(graph: EntityGraph, tool_name: str, result: Any) -> None:
    """Extract entities from a ToolResponse-shaped dict, walking nested structures."""
    if not isinstance(result, dict):
        return

    # Unwrap ToolResponse envelope — data is the payload
    data = result.get("data", result)

    # Recursively collect all dict items that look like entities
    _walk_and_extract(graph, tool_name, data, depth=0)


def _walk_and_extract(
    graph: EntityGraph, tool_name: str, obj: Any, depth: int
) -> None:
    """Recursively walk nested dicts/lists to find entity-like objects."""
    if depth > 6:
        return

    if isinstance(obj, list):
        for item in obj:
            _walk_and_extract(graph, tool_name, item, depth + 1)
        return

    if not isinstance(obj, dict):
        return

    # Try to extract an entity from this dict
    name = (
        obj.get("name")
        or obj.get("entity_name")
        or obj.get("legal_name")
        or obj.get("company_name")
        or obj.get("caption")
    )

    if name and isinstance(name, str) and len(name) > 1:
        entity_id = str(
            obj.get("id") or obj.get("lei") or obj.get("node_id")
            or name.lower().replace(" ", "_").replace(",", "")
        )
        entity_type = _infer_entity_type(obj, tool_name)
        country = obj.get("country") or obj.get("jurisdiction") or obj.get("registered_address_country")

        entity = Entity(
            id=entity_id,
            name=name,
            entity_type=entity_type,
            country=country,
            aliases=obj.get("aliases", []),
        )
        graph.add_entity(entity)

        # Relationship: parent/subsidiary
        parent = obj.get("parent") or obj.get("parent_name") or obj.get("ultimate_parent")
        if parent and isinstance(parent, str):
            parent_id = parent.lower().replace(" ", "_").replace(",", "")
            graph.add_entity(Entity(id=parent_id, name=parent, entity_type="company"))
            graph.add_relationship(Relationship(
                source_id=entity_id,
                target_id=parent_id,
                relationship_type="subsidiary_of",
                confidence=Confidence.MEDIUM,
                sources=[SourceReference(name=tool_name)],
            ))

        # Relationship: sanctions listing
        if obj.get("sanctions") or obj.get("programs") or obj.get("datasets"):
            sanction_node_id = f"sanctions_{entity_id}"
            programs = obj.get("programs") or obj.get("datasets") or ["sanctions"]
            label = ", ".join(programs) if isinstance(programs, list) else str(programs)
            graph.add_entity(Entity(
                id=sanction_node_id, name=label, entity_type="sanctions_list"
            ))
            graph.add_relationship(Relationship(
                source_id=entity_id,
                target_id=sanction_node_id,
                relationship_type="listed_on",
                confidence=Confidence.HIGH,
                sources=[SourceReference(name=tool_name)],
            ))

        # Relationship: officers/holders
        for holder_key in ("holders", "officers", "top_holders"):
            holders = obj.get(holder_key, [])
            if isinstance(holders, list):
                for h in holders[:10]:  # cap to avoid graph explosion
                    if isinstance(h, dict):
                        h_name = h.get("name") or h.get("holder") or h.get("officer_name")
                        if h_name and isinstance(h_name, str):
                            h_id = h_name.lower().replace(" ", "_").replace(",", "")
                            h_type = "person" if h.get("position") or h.get("role") else "company"
                            graph.add_entity(Entity(id=h_id, name=h_name, entity_type=h_type))
                            rel_type = "officer_of" if holder_key == "officers" else "holds_shares_in"
                            graph.add_relationship(Relationship(
                                source_id=h_id,
                                target_id=entity_id,
                                relationship_type=rel_type,
                                confidence=Confidence.MEDIUM,
                                sources=[SourceReference(name=tool_name)],
                            ))

    # Keep walking into nested dicts/lists to find more entities
    for key, val in obj.items():
        if isinstance(val, (dict, list)):
            _walk_and_extract(graph, tool_name, val, depth + 1)


def build_graph_from_assessment(assessment: Any) -> EntityGraph:
    """Extract entities and relationships from the synthesized ImpactAssessment.

    This catches entities that the LLM identified in its synthesis even when
    raw tool data didn't have clean entity-like fields.
    """
    graph = EntityGraph()

    target_id = "target"
    target_name = ""

    # Use the query's target entities or infer from the query text
    if hasattr(assessment, "query"):
        q = assessment.query
        if hasattr(q, "target_entities") and q.target_entities:
            target_name = q.target_entities[0]
        elif hasattr(q, "raw_query"):
            # Try to extract entity name from query
            raw = q.raw_query
            for prefix in ("What happens if we sanction ", "sanction ", "Impact of sanctioning "):
                if raw.lower().startswith(prefix.lower()):
                    target_name = raw[len(prefix):].rstrip("?. ")
                    break
            if not target_name:
                target_name = raw[:60]

    if target_name:
        target_id = target_name.lower().replace(" ", "_").replace(",", "")
        graph.add_entity(Entity(
            id=target_id, name=target_name, entity_type="company", country=None
        ))

    # Extract entities from findings
    for finding in getattr(assessment, "findings", []):
        if not isinstance(finding, dict):
            continue
        category = finding.get("category", "")
        data = finding.get("data", {})
        if not isinstance(data, dict):
            continue

        # LEI / corporate identity
        if data.get("legal_name"):
            lei_id = str(data.get("lei", target_id))
            name = data["legal_name"]
            nid = name.lower().replace(" ", "_").replace(",", "")
            graph.add_entity(Entity(
                id=nid, name=name, entity_type="company",
                country=data.get("country"),
            ))
            if nid != target_id:
                graph.add_relationship(Relationship(
                    source_id=target_id, target_id=nid,
                    relationship_type="also_known_as",
                    confidence=Confidence.MEDIUM,
                ))

        # Sanctions status
        if data.get("current_sanctions") and data["current_sanctions"] != "none_found":
            sid = "sanctions_list"
            graph.add_entity(Entity(id=sid, name="Sanctions List", entity_type="sanctions_list"))
            graph.add_relationship(Relationship(
                source_id=target_id, target_id=sid,
                relationship_type="listed_on",
                confidence=Confidence.HIGH,
            ))

        # Geopolitical themes
        themes = data.get("key_themes", [])
        if isinstance(themes, list):
            for theme in themes[:5]:
                tid = f"theme_{theme}"
                graph.add_entity(Entity(
                    id=tid, name=theme.replace("_", " ").title(),
                    entity_type="theme",
                ))
                graph.add_relationship(Relationship(
                    source_id=target_id, target_id=tid,
                    relationship_type="related_to",
                    confidence=Confidence.MEDIUM,
                ))

        # Sectors
        for key in ("affected_sectors", "top_sectors"):
            sectors = data.get(key, [])
            if isinstance(sectors, list):
                for sector in sectors[:5]:
                    if isinstance(sector, str):
                        sid = f"sector_{sector.lower().replace(' ', '_')}"
                        graph.add_entity(Entity(
                            id=sid, name=sector, entity_type="sector",
                        ))
                        graph.add_relationship(Relationship(
                            source_id=target_id, target_id=sid,
                            relationship_type="affects_sector",
                            confidence=Confidence.MEDIUM,
                        ))

    # Extract from friendly fire
    for ff in getattr(assessment, "friendly_fire", []):
        if not isinstance(ff, dict):
            continue
        ff_name = ff.get("entity", "")
        if ff_name:
            ff_id = ff_name.lower().replace(" ", "_").replace(",", "")
            ff_type = "company"
            if any(w in ff_name.lower() for w in ("government", "allied", "nato", "us ")):
                ff_type = "government"
            graph.add_entity(Entity(
                id=ff_id, name=ff_name, entity_type=ff_type
            ))
            impact = ff.get("estimated_impact", "UNKNOWN")
            graph.add_relationship(Relationship(
                source_id=target_id, target_id=ff_id,
                relationship_type=f"friendly_fire ({impact})",
                confidence=Confidence.MEDIUM,
            ))

    return graph


def _infer_entity_type(item: dict[str, Any], tool_name: str) -> str:
    """Infer entity type from the data shape and tool source."""
    if "ticker" in item or "market_cap" in item:
        return "company"
    if "lei" in item:
        return "company"
    if "vessel" in tool_name.lower() or "imo" in item:
        return "vessel"
    if item.get("entity_type"):
        return item["entity_type"]
    return "company"


def to_networkx(graph: EntityGraph) -> nx.DiGraph:
    """Convert EntityGraph to a NetworkX directed graph for analysis."""
    G = nx.DiGraph()

    for entity in graph.entities:
        G.add_node(
            entity.id,
            label=entity.name,
            entity_type=entity.entity_type,
            country=entity.country,
        )

    for rel in graph.relationships:
        G.add_edge(
            rel.source_id,
            rel.target_id,
            relationship_type=rel.relationship_type,
            confidence=rel.confidence.value,
        )

    return G


def find_paths(graph: EntityGraph, source_name: str, target_name: str) -> list[list[str]]:
    """Find all paths between two entities in the graph."""
    G = to_networkx(graph)

    # Find node IDs by name
    source_id = None
    target_id = None
    for node, data in G.nodes(data=True):
        if data.get("label", "").lower() == source_name.lower():
            source_id = node
        if data.get("label", "").lower() == target_name.lower():
            target_id = node

    if not source_id or not target_id:
        return []

    try:
        paths = list(nx.all_simple_paths(G, source_id, target_id, cutoff=5))
        return paths
    except nx.NetworkXError:
        return []
