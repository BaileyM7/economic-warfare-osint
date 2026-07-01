"""Graph-integration Phase 1 (#34): the orchestrator ("Ask Anything") path must
build a populated entity_graph from its tool results.

Root cause this guards: `_synthesize` previously constructed `ImpactAssessment`
without setting `entity_graph`, so the deep-analysis view had an empty graph.
`build_graph_from_results` is now called on the raw tool results; this pins that
it extracts entities + relationships from the real orchestrator result shape
(`{step: {"results": {tool: {"data": ...}}}}`).
"""

from __future__ import annotations

from src.fusion.graph_builder import build_graph_from_results


def _tool_results():
    return {
        "step_1": {
            "description": "Resolve German chemical firms + sanctions status",
            "results": {
                "search_entity": {
                    "data": {
                        "companies": [
                            {
                                "name": "BASF SE",
                                "entity_type": "company",
                                "country": "DE",
                                "parent": "BASF Group",
                            },
                            {"company_name": "Covestro AG", "country": "DE"},
                        ]
                    }
                },
                "check_sanctions_status": {
                    "data": {
                        "matches": [
                            {"name": "Rosatom", "programs": ["RUSSIA-EO14024"], "country": "RU"},
                        ]
                    }
                },
            },
        }
    }


def test_build_graph_extracts_entities_and_relationships():
    g = build_graph_from_results(_tool_results())
    names = {e.name for e in g.entities}
    assert "BASF SE" in names
    assert "Covestro AG" in names
    assert "Rosatom" in names
    # parent → subsidiary_of edge, sanctions programs → listed_on edge
    rel_types = {r.relationship_type for r in g.relationships}
    assert "subsidiary_of" in rel_types
    assert "listed_on" in rel_types


def test_build_graph_empty_results_is_empty_graph():
    g = build_graph_from_results({})
    assert g.entities == []
    assert g.relationships == []


def test_orchestrator_synthesize_wires_entity_graph():
    """The synthesize path passes tool_results through build_graph_from_results.

    We don't run the full (LLM) pipeline here — just assert the wiring exists so a
    future refactor can't silently drop it again.
    """
    import inspect

    from src.orchestrator import main

    src = inspect.getsource(main.Orchestrator._synthesize)
    assert "build_graph_from_results(tool_results)" in src
    assert "entity_graph=entity_graph" in src
