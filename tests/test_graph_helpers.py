"""Safety-net for the graph-visualization upgrade (issue #28).

Pins the *current* vis.js node/edge shape produced by the shared graph helpers
and the /api/entity-graph endpoint BEFORE the viz upgrade adds optional
size/risk/weight fields. The new fields must be strictly additive: a default
`node()` call (no new args) must keep emitting exactly the historical keys, so
the four routers that share this factory (entity, person, sector, vessel) and
the frontend `GraphNode`/`GraphEdge` types don't silently drift.

When the upgrade lands, the back-compat tests stay green; the additive tests
assert the new opt-in fields appear only when requested.
"""

from __future__ import annotations

from src.common.graph_helpers import ENTITY_COLORS, node

# Historical key set emitted by node() with no optional args. Frozen on purpose.
_BASE_KEYS = {"id", "label", "title", "group", "color"}


def test_node_default_shape_is_unchanged():
    """A plain node() call must emit exactly the historical keys/values."""
    n = node("acme", "Acme Corp", "company", "US")
    assert set(n.keys()) == _BASE_KEYS
    assert n["id"] == "acme"
    assert n["label"] == "Acme Corp"
    assert n["title"] == "Acme Corp\ncompany · US"
    assert n["group"] == "company"
    assert n["color"] == ENTITY_COLORS["company"]


def test_node_without_country_omits_country_suffix():
    n = node("acme", "Acme Corp", "company")
    assert n["title"] == "Acme Corp\ncompany"
    assert "sayariId" not in n


def test_node_unknown_type_falls_back_to_grey():
    n = node("x", "Mystery", "alien")
    assert n["color"] == "#808080"


def test_node_sayari_id_only_present_when_supplied():
    n = node("acme", "Acme Corp", "company", "US", sayari_id="ent123")
    assert n["sayariId"] == "ent123"


# --- Additive viz fields (issue #28): only present when opted into ---


def test_node_value_and_risk_are_opt_in():
    plain = node("acme", "Acme Corp", "company")
    assert "value" not in plain and "riskLevel" not in plain

    sized = node("acme", "Acme Corp", "company", value=7, risk="HIGH")
    assert sized["value"] == 7
    assert sized["riskLevel"] == "HIGH"
    # base shape preserved alongside the new hints
    assert _BASE_KEYS <= set(sized.keys())


def test_node_value_zero_is_emitted():
    # value is an int hint; 0 is a legitimate (isolated) node, not "absent".
    n = node("x", "Lonely", "company", value=0)
    assert n["value"] == 0


def test_graph_summary_counts_types_and_risk():
    from src.routers.entity import _graph_summary

    nodes = [
        {"group": "company", "label": "Acme", "title": "Acme\ncompany"},
        {"group": "company", "label": "Beta", "title": "Beta\ncompany", "riskLevel": "HIGH"},
        {"group": "sanctions_list", "label": "OFAC X", "title": "OFAC X"},
        {"group": "person", "label": "Jane", "color": "#F85149", "riskLevel": "HIGH"},
    ]
    s = _graph_summary(nodes, edges=[])
    assert s["by_type"] == {"company": 2, "sanctions_list": 1, "person": 1}
    # one sanctions_list node + one red-colored node
    assert s["sanctioned_count"] == 2
    assert s["high_risk_count"] == 2
    assert "Beta" in s["high_risk_entities"]
