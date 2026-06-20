"""Guardrails for the sanctions-impact risk-narrative prompt.

These lock in the two fixes from the Alibaba heat-check:
  1. The provider's country field (e.g. Finnhub returns "HK" for BABA) is the
     primary LISTING region, not the legal domicile — the prompt must tell the
     model not to assert a domicile from it (Alibaba is Cayman-incorporated /
     China-HQ'd, NOT "Hong Kong domiciled").
  2. The pre-event decline is the comparables' AVERAGE move, not the target's
     own price history — the prompt must say so, so the model can't claim it's
     "already baked into the share price".
"""

from __future__ import annotations

from src.routers.sanctions_impact import _build_narrative_prompt


def _compact() -> dict:
    return {
        "name": "Alibaba Group Holding Ltd",
        "ticker": "BABA",
        "sector": "Retail",
        "listing_region": "HK",
        "is_sanctioned": False,
        "sanction_programs": [],
        "comparables_avg_pre_event_decline_pct": -45.9,
        "day_30_post_pct": -6.2,
        "day_90_post_pct": 4.5,
        "max_drawdown_pct": -8.7,
    }


def test_prompt_uses_listing_region_not_country_and_guards_domicile():
    p = _build_narrative_prompt(_compact(), comp_count=5).lower()
    assert "listing_region" in p
    assert "do not assert a legal domicile" in p
    # the field must NOT be presented to the model as a plain "country"/"domicile" value
    assert '"country"' not in p


def test_prompt_labels_pre_event_as_comparables_average():
    p = _build_narrative_prompt(_compact(), comp_count=5).lower()
    assert "comparables_avg_pre_event_decline_pct" in p
    assert "average" in p
    assert "not this company's own actual price history" in p


def test_prompt_still_includes_core_instructions():
    # don't regress the original intent: sanctions status, trajectory, friendly fire
    p = _build_narrative_prompt(_compact(), comp_count=5).lower()
    assert "sanctions status" in p
    assert "friendly fire" in p
    assert "sector etf benchmark" in p
