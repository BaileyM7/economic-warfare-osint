"""vis.js graph-node helpers shared by the entity / person / vessel / sector
endpoints. Pure functions — no app state. Extracted from api.py (Phase 2
Stage 3) with behaviour unchanged.
"""

from __future__ import annotations

import re
from typing import Any

ENTITY_COLORS: dict[str, str] = {
    "company": "#58a6ff",
    "person": "#a371f7",
    "government": "#DC143C",
    "vessel": "#3fb950",
    "sanctions_list": "#F85149",
    "theme": "#F0883E",
    "sector": "#f0883e",
}


def truncate(s: str, n: int = 28) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def node(
    nid: str,
    name: str,
    entity_type: str,
    country: str | None = None,
    sayari_id: str | None = None,
    value: int | None = None,
    risk: str | None = None,
) -> dict[str, Any]:
    """Build a vis.js node dict.

    The base shape (id/label/title/group/color, plus optional sayariId) is frozen
    by tests/test_graph_helpers.py. ``value`` and ``risk`` are **opt-in** viz hints
    for the graph upgrade (issue #28) and are only emitted when supplied, so the
    four routers that share this factory and the frontend GraphNode type stay
    back-compatible:

      * ``value`` — relative importance, drives node *size* via vis-network's
        ``nodes.scaling`` (e.g. graph degree / centrality).
      * ``risk`` — "HIGH" | "MEDIUM" | "LOW"; surfaced as ``riskLevel`` so the
        frontend can ring/tint the node without changing its category ``color``.
    """
    title = f"{name}\n{entity_type}" + (f" · {country}" if country else "")
    out: dict[str, Any] = {
        "id": nid,
        "label": truncate(name),
        "title": title,
        "group": entity_type,
        "color": ENTITY_COLORS.get(entity_type, "#808080"),
    }
    if sayari_id:
        out["sayariId"] = sayari_id
    if value is not None:
        out["value"] = value
    if risk:
        out["riskLevel"] = risk
    return out


_LEI_20 = re.compile(r"[A-Z0-9]{20}")


def canonical_lei(ref: str | None) -> str:
    """Extract a 20-character LEI from a bare code or JSON:API href-style id."""
    if not ref or not isinstance(ref, str):
        return ""
    compact = ref.strip().upper().replace("-", "").replace(" ", "")
    m = _LEI_20.search(compact)
    return m.group(0) if m else ""


def lei_resolve_node_id(lei_map: dict[str, str], ref: str | None) -> str | None:
    """Map a parent/child reference from API payloads to our graph node id."""
    if ref is None or ref == "":
        return None
    raw = str(ref).strip()
    cand = canonical_lei(raw)
    for key in (raw, cand):
        if key and key in lei_map:
            return lei_map[key]
    return None
