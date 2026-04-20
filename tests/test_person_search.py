"""Tests for src.orchestrator.person_search.

These tests exercise the pure-python pieces of the module — candidate merging,
ranking, network pruning, and risk-factor classification — without making any
network calls. The async fan-out helpers (search_persons, build_person_network)
are network-dependent and not covered here; smoke them via the live curl
checks listed in the plan.
"""

from __future__ import annotations

import pytest

from src.orchestrator.person_search import (
    PersonCandidate,
    PersonNetworkEdge,
    PersonNetworkNode,
    RiskFactor,
    _csl_hits_to_candidates,
    _merge_candidates,
    _normalize_name,
    _officers_to_candidates,
    build_risk_factors,
    prune_l2_nodes,
    rank_candidates,
)
from src.tools.corporate.models import Officer


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Viktor Vekselberg", "viktor vekselberg"),
        ("VIKTOR  F. VEKSELBERG", "viktor f vekselberg"),
        ("  Viktor,  Vekselberg!  ", "viktor vekselberg"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_name(raw, expected):
    assert _normalize_name(raw) == expected


# ---------------------------------------------------------------------------
# CSL hit conversion
# ---------------------------------------------------------------------------


def test_csl_hits_filter_to_persons_only():
    hits = [
        {"name": "VEKSELBERG, Viktor", "type": "Individual", "programs": ["UKRAINE-EO13661"], "addresses": [{"country": "RU"}]},
        {"name": "Renova Group", "type": "Entity", "programs": ["UKRAINE-EO13662"]},  # company; should be dropped
        {"name": "Mystery Person", "type": None, "programs": []},  # missing type — keep
    ]
    candidates = _csl_hits_to_candidates(hits)
    names = {c.name for c in candidates}
    assert "VEKSELBERG, Viktor" in names
    assert "Renova Group" not in names
    assert "Mystery Person" in names
    vk = next(c for c in candidates if c.name == "VEKSELBERG, Viktor")
    assert vk.sanctioned is True
    assert vk.country == "RU"
    assert "UKRAINE-EO13661" in vk.sanction_programs


# ---------------------------------------------------------------------------
# Officer hit conversion + dedupe
# ---------------------------------------------------------------------------


def test_officers_collapse_to_one_candidate_per_person():
    officers = [
        Officer(
            name="Viktor Vekselberg", role="director",
            company_name="Renova Group", company_jurisdiction="ru", company_number="111",
        ),
        Officer(
            name="Viktor Vekselberg", role="director", end_date=None,
            company_name="Renova US Inc", company_jurisdiction="us_de", company_number="222",
        ),
        Officer(name="Jane Smith", role="director", company_name="Acme"),
    ]
    candidates = _officers_to_candidates(officers)
    assert len(candidates) == 2
    vk = next(c for c in candidates if "vekselberg" in c.name.lower())
    # Active company should win as the displayed affiliation
    assert vk.primary_affiliation in {"Renova Group", "Renova US Inc"}
    assert vk.sources == ["opencorporates"]
    assert vk.sanctioned is False


# ---------------------------------------------------------------------------
# Cross-source merge
# ---------------------------------------------------------------------------


def test_merge_unions_sources_and_keeps_sanctions_data():
    csl_only = [
        PersonCandidate(name="Viktor Vekselberg", sources=["opensanctions"],
                        sanctioned=True, sanction_programs=["UKRAINE-EO13661"], country="RU"),
    ]
    oc_only = [
        PersonCandidate(name="VIKTOR VEKSELBERG", sources=["opencorporates"],
                        primary_affiliation="Renova Group"),
    ]
    merged = _merge_candidates(csl_only, oc_only)
    assert len(merged) == 1
    m = merged[0]
    assert set(m.sources) == {"opensanctions", "opencorporates"}
    assert m.sanctioned is True
    assert "UKRAINE-EO13661" in m.sanction_programs
    assert m.primary_affiliation == "Renova Group"
    assert m.country == "RU"


# ---------------------------------------------------------------------------
# Ranking policy (pinned 2026-04-13):
#   1. Sanctioned hits always outrank clean matches, even on spelling.
#   2. Multi-source beats single-source within a tier.
#   3. Exact name match is an intra-tier bonus.
#   4. Tiebreaker: adversary-country first, then country, then name.
# ---------------------------------------------------------------------------


def test_rank_sanctioned_outranks_clean_exact_match():
    """A sanctioned fuzzy-match beats a clean exact-match — hits always rise."""
    cands = [
        PersonCandidate(name="John Smith", sources=["opencorporates"]),  # exact
        PersonCandidate(name="Viktor Vekselberg", sources=["opensanctions"], sanctioned=True),
    ]
    ranked = rank_candidates(cands, query="John Smith")
    assert ranked[0].sanctioned is True


def test_rank_exact_match_boost_is_intra_tier():
    """Exact match wins among equally-sanctioned candidates."""
    cands = [
        PersonCandidate(name="Viktor Vekselberg Jr", sources=["opencorporates"]),
        PersonCandidate(name="Viktor Vekselberg", sources=["opencorporates"]),
    ]
    ranked = rank_candidates(cands, query="Viktor Vekselberg")
    assert ranked[0].name == "Viktor Vekselberg"


def test_rank_multi_source_beats_single_source():
    cands = [
        PersonCandidate(name="A", sources=["opencorporates"]),
        PersonCandidate(name="A", sources=["opencorporates", "opensanctions"]),
    ]
    ranked = rank_candidates(cands, query="x")
    assert ranked[0].sources == ["opencorporates", "opensanctions"]


def test_rank_adversary_country_tiebreaks_equal_score():
    """Two clean candidates, same everything except country: adversary first."""
    cands = [
        PersonCandidate(name="Same Name", sources=["opencorporates"], country="GB"),
        PersonCandidate(name="Same Name", sources=["opencorporates"], country="RU"),
    ]
    ranked = rank_candidates(cands, query="Same Name")
    assert ranked[0].country == "RU"


def test_rank_country_normalization_handles_opencorporates_codes():
    """OpenCorporates uses extended codes like 'us_de' — should normalize to 'US'."""
    cands = [
        PersonCandidate(name="X", sources=["opencorporates"], country="us_de"),
        PersonCandidate(name="X", sources=["opencorporates"], country="ru"),
    ]
    ranked = rank_candidates(cands, query="x")
    assert ranked[0].country == "ru"  # lowercased adversary still wins


# ---------------------------------------------------------------------------
# Network pruning
# ---------------------------------------------------------------------------


def test_prune_passes_through_when_no_l2():
    nodes = [
        PersonNetworkNode(id="p_a", label="A", group="person", depth=0),
        PersonNetworkNode(id="co_x", label="X", group="company", depth=1),
    ]
    edges = [PersonNetworkEdge(**{"from": "p_a", "to": "co_x"})]
    out_nodes, out_edges = prune_l2_nodes(nodes, edges)
    assert out_nodes == nodes
    assert out_edges == edges


def test_prune_caps_l2_per_l1_parent():
    """A single L1 parent with many L2 children should be capped at max_per_l1."""
    nodes = [
        PersonNetworkNode(id="p_central", label="C", group="person", depth=0),
        PersonNetworkNode(id="p_l1", label="L1", group="person", depth=1),
    ]
    for i in range(50):
        nodes.append(
            PersonNetworkNode(id=f"p_l2_{i}", label=f"L2_{i}", group="person", depth=2)
        )
    edges = [PersonNetworkEdge(**{"from": "p_l1", "to": f"p_l2_{i}"}) for i in range(50)]
    pruned_nodes, pruned_edges = prune_l2_nodes(
        nodes, edges, max_per_l1=5, min_shared_companies=1,
    )
    l2_kept = [n for n in pruned_nodes if n.depth == 2]
    assert len(l2_kept) <= 5
    assert all(e.to in {n.id for n in pruned_nodes} for e in pruned_edges)


def test_prune_prefers_clustered_l2s():
    """L2 under 2+ L1 parents survives even when an exclusive L2 is dropped."""
    nodes = [
        PersonNetworkNode(id="p_central", label="C", group="person", depth=0),
        PersonNetworkNode(id="p_l1a", label="L1A", group="person", depth=1),
        PersonNetworkNode(id="p_l1b", label="L1B", group="person", depth=1),
        PersonNetworkNode(id="p_shared", label="Shared", group="person", depth=2),
        PersonNetworkNode(id="p_exclusive", label="Exclusive", group="person", depth=2),
    ]
    edges = [
        PersonNetworkEdge(**{"from": "p_l1a", "to": "p_shared"}),
        PersonNetworkEdge(**{"from": "p_l1b", "to": "p_shared"}),
        PersonNetworkEdge(**{"from": "p_l1a", "to": "p_exclusive"}),
    ]
    pruned_nodes, _ = prune_l2_nodes(
        nodes, edges, max_per_l1=1, min_shared_companies=2,
    )
    ids = {n.id for n in pruned_nodes}
    # Shared appears under 2 parents — survives the min_shared floor.
    assert "p_shared" in ids
    # Exclusive is under 1 parent AND min_shared_companies=2 — dropped.
    assert "p_exclusive" not in ids


# ---------------------------------------------------------------------------
# Risk factor builder
# ---------------------------------------------------------------------------


def test_build_risk_factors_emits_five_factors_in_order():
    factors = build_risk_factors({})
    assert [f.title for f in factors] == [
        "Sanctions Exposure",
        "Corporate Ties",
        "Offshore Exposure",
        "Recent News Events",
        "Political Exposure",
    ]
    assert all(isinstance(f, RiskFactor) for f in factors)


def test_clean_profile_yields_all_none_severity():
    factors = build_risk_factors({
        "is_sanctioned": False,
        "sanction_programs": [],
        "affiliations": [],
        "offshore": [],
        "recent_events": [],
        "pep_hits": [],
    })
    assert all(f.severity == "none" and f.score == 0 for f in factors)


def test_sdn_sanctions_drive_prohibited():
    factors = build_risk_factors({
        "is_sanctioned": True,
        "sanction_programs": ["UKRAINE-EO13661"],   # not a consent-decree label
    })
    sanctions = next(f for f in factors if f.title == "Sanctions Exposure")
    assert sanctions.severity == "prohibited"
    assert sanctions.score == 100


def test_consent_decree_only_steps_down_to_discouraged():
    """Per policy: matches solely on DPL / UVL / consent-decree lists
    step down from prohibited to discouraged."""
    factors = build_risk_factors({
        "is_sanctioned": True,
        "sanction_programs": ["Denied Persons List", "Unverified List"],
    })
    sanctions = next(f for f in factors if f.title == "Sanctions Exposure")
    assert sanctions.severity == "discouraged"
    assert sanctions.score == 70


def test_mixed_program_hits_stay_prohibited():
    """Any SDN-style program in the mix keeps severity at prohibited."""
    factors = build_risk_factors({
        "is_sanctioned": True,
        "sanction_programs": ["Denied Persons List", "UKRAINE-EO13661"],
    })
    sanctions = next(f for f in factors if f.title == "Sanctions Exposure")
    assert sanctions.severity == "prohibited"


def test_corporate_ties_adversary_jurisdiction_escalates():
    factors = build_risk_factors({
        "affiliations": [
            {"company": "Renova Group", "role": "director", "active": True, "nationality": "RU"},
        ],
    })
    corp = next(f for f in factors if f.title == "Corporate Ties")
    assert corp.severity == "expected"
    # Control: same shape but clean jurisdiction should be only "suggested".
    factors_clean = build_risk_factors({
        "affiliations": [
            {"company": "Acme", "role": "director", "active": True, "nationality": "US"},
        ],
    })
    corp_clean = next(f for f in factors_clean if f.title == "Corporate Ties")
    assert corp_clean.severity == "suggested"


def test_offshore_any_hit_is_expected():
    """Per policy: any ICIJ hit is expected, regardless of count."""
    one = build_risk_factors({"offshore": [{"entity": "X"}]})
    two = build_risk_factors({"offshore": [{"entity": "X"}, {"entity": "Y"}]})
    assert next(f for f in one if f.title == "Offshore Exposure").severity == "expected"
    assert next(f for f in two if f.title == "Offshore Exposure").severity == "expected"
    # Score still scales with count as a magnitude signal.
    assert (
        next(f for f in two if f.title == "Offshore Exposure").score
        > next(f for f in one if f.title == "Offshore Exposure").score
    )


def test_news_only_negative_coverage_counts():
    """Per policy: neutral/positive coverage yields severity 'none'."""
    positive = build_risk_factors({"recent_events": [{"title": "X", "tone": 5.0}]})
    neutral = build_risk_factors({"recent_events": [{"title": "X", "tone": 0.0}]})
    slightly_negative = build_risk_factors({"recent_events": [{"title": "X", "tone": -1.0}]})
    for factors in (positive, neutral, slightly_negative):
        news = next(f for f in factors if f.title == "Recent News Events")
        assert news.severity == "none"
        assert news.score == 0


def test_pep_current_position_is_expected():
    """Active political position yields 'expected' severity."""
    factors = build_risk_factors({
        "pep_hits": [{"name": "X", "positions": ["President"], "parties": [], "is_current": True}],
    })
    pep = next(f for f in factors if f.title == "Political Exposure")
    assert pep.severity == "expected"
    assert pep.score == 65
    assert "President" in pep.summary


def test_pep_former_position_is_suggested():
    """Former (no is_current) political position yields 'suggested'."""
    factors = build_risk_factors({
        "pep_hits": [{"name": "X", "positions": ["Minister of Finance"], "parties": [], "is_current": False}],
    })
    pep = next(f for f in factors if f.title == "Political Exposure")
    assert pep.severity == "suggested"
    assert pep.score == 30


def test_pep_party_only_is_suggested():
    """Party membership with no position yields the lower 'suggested' score."""
    factors = build_risk_factors({
        "pep_hits": [{"name": "X", "positions": [], "parties": ["United Russia"], "is_current": False}],
    })
    pep = next(f for f in factors if f.title == "Political Exposure")
    assert pep.severity == "suggested"
    assert pep.score == 20


def test_pep_no_hits_is_none():
    factors = build_risk_factors({"pep_hits": []})
    pep = next(f for f in factors if f.title == "Political Exposure")
    assert pep.severity == "none"
    assert pep.score == 0


# ---------------------------------------------------------------------------
# Alt names in PersonCandidate
# ---------------------------------------------------------------------------


def test_csl_hits_surface_alt_names():
    hits = [
        {
            "name": "VEKSELBERG, Viktor",
            "type": "Individual",
            "programs": ["UKRAINE-EO13661"],
            "addresses": [{"country": "RU"}],
            "alt_names": ["Viktor Felixovich Vekselberg", "Vekselberg Viktor"],
        }
    ]
    candidates = _csl_hits_to_candidates(hits)
    assert len(candidates) == 1
    assert "Viktor Felixovich Vekselberg" in candidates[0].alt_names
    assert "Vekselberg Viktor" in candidates[0].alt_names


def test_merge_unions_alt_names():
    csl_only = [
        PersonCandidate(name="Viktor Vekselberg", sources=["opensanctions"],
                        sanctioned=True, alt_names=["Viktor F. Vekselberg"]),
    ]
    oc_only = [
        PersonCandidate(name="VIKTOR VEKSELBERG", sources=["opencorporates"],
                        alt_names=["Vekselberg V."]),
    ]
    merged = _merge_candidates(csl_only, oc_only)
    assert len(merged) == 1
    assert "Viktor F. Vekselberg" in merged[0].alt_names
    assert "Vekselberg V." in merged[0].alt_names


def test_news_multiple_negative_articles_expected():
    loud = build_risk_factors({"recent_events": [
        {"title": "A", "tone": -5.0},
        {"title": "B", "tone": -4.0},
        {"title": "C", "tone": -3.5},
    ]})
    news = next(f for f in loud if f.title == "Recent News Events")
    assert news.severity == "expected"
    assert news.score == 60
