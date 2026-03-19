"""Basic import and structure tests — verify all tools load without API keys."""

import pytest


def test_common_types_import():
    from src.common.types import (
        AnalystQuery,
        Confidence,
        Entity,
        EntityGraph,
        ImpactAssessment,
        Relationship,
        ScenarioType,
        SourceReference,
        ToolResponse,
    )
    assert Confidence.HIGH.value == "HIGH"
    assert ScenarioType.SANCTION_IMPACT.value == "sanction_impact"


def test_common_config_import():
    from src.common.config import config
    # Should load without error even without .env
    assert isinstance(config.cache_ttl_seconds, int)


def test_tool_response_construction():
    from src.common.types import Confidence, SourceReference, ToolResponse
    resp = ToolResponse(
        data={"test": True},
        confidence=Confidence.HIGH,
        sources=[SourceReference(name="test_source")],
    )
    assert resp.confidence == Confidence.HIGH
    assert resp.sources[0].name == "test_source"
    dumped = resp.model_dump(mode="json")
    assert dumped["data"]["test"] is True


def test_entity_graph_merge():
    from src.common.types import Confidence, Entity, EntityGraph, Relationship
    g1 = EntityGraph()
    g1.add_entity(Entity(id="a", name="Company A", entity_type="company"))
    g2 = EntityGraph()
    g2.add_entity(Entity(id="b", name="Company B", entity_type="company"))
    g2.add_relationship(Relationship(
        source_id="a", target_id="b", relationship_type="subsidiary_of"
    ))
    g1.merge(g2)
    assert len(g1.entities) == 2
    assert len(g1.relationships) == 1


def test_sanctions_models_import():
    from src.tools.sanctions.models import SanctionEntry, SanctionSearchResult


def test_corporate_models_import():
    from src.tools.corporate.models import CompanyRecord, LEIRecord, Officer


def test_market_models_import():
    from src.tools.market.models import ExposureReport, InstitutionalHolder, StockProfile


def test_trade_models_import():
    from src.tools.trade.models import CommodityDependency, TradeFlow, TradePartnerSummary


def test_geopolitical_models_import():
    from src.tools.geopolitical.models import AcledEvent, ConflictSummary, GdeltEvent


def test_orchestrator_prompts():
    from src.orchestrator.prompts import DECOMPOSITION_PROMPT, SYNTHESIS_PROMPT, SYSTEM_PROMPT
    assert "sanctions" in SYSTEM_PROMPT.lower()
    assert "{query}" in DECOMPOSITION_PROMPT


def test_fusion_renderer():
    from src.common.types import AnalystQuery, ImpactAssessment, ScenarioType
    from src.fusion.renderer import render_json, render_markdown

    assessment = ImpactAssessment(
        query=AnalystQuery(raw_query="test query"),
        scenario_type=ScenarioType.SANCTION_IMPACT,
        executive_summary="Test summary",
        findings=[{"category": "Test", "finding": "Test finding", "confidence": "HIGH"}],
    )
    md = render_markdown(assessment)
    assert "Test summary" in md
    assert "Test finding" in md

    js = render_json(assessment)
    assert "test query" in js
