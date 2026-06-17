"""Tests for OpenSanctionsClient._parse_search_results / _parse_entity.

The bug these tests guard against: the OpenSanctions ``/search/default``
endpoint doesn't return a per-result ``score`` field. Previous code used
``result.get("score")`` which yielded ``None``; downstream filters then
treated those entries as score ``0`` and dropped sanctioned subjects
(notably Roman Abramovich) as "no match".

We derive a score from the entity's ``topics`` property instead.
"""

from __future__ import annotations

from src.tools.sanctions.client import OpenSanctionsClient


_ABRAMOVICH = {
    "id": "Q171428",
    "caption": "Roman Abramovich",
    "schema": "Person",
    "properties": {
        "name": ["Roman Abramovich"],
        "topics": [
            "role.pep",
            "sanction",
            "role.pol",
            "poi",
            "role.oligarch",
            "corp.disqual",
        ],
        "programId": [
            "AU-RUSSIA",
            "CA-SEMA",
            "NZ-RSA2022",
            "SECO-UKRAINE",
            "EU-UKR",
            "UA-SA1644",
            "GB-RUS",
        ],
    },
    "datasets": [
        "au_dfat_sanctions",
        "gb_fcdo_sanctions",
        "eu_fsf",
        "ca_dfatd_sema_sanctions",
    ],
}

_LINKED_RELATIVE = {
    "id": "Q4791679",
    "caption": "Arkadiy Abramovich",
    "schema": "Person",
    "properties": {
        "name": ["Arkadiy Abramovich"],
        "topics": ["sanction.linked", "role.rca"],
    },
    "datasets": ["wikidata", "wd_curated"],
}

_PEP_ONLY = {
    "id": "Q-pep",
    "caption": "Some Politician",
    "schema": "Person",
    "properties": {
        "name": ["Some Politician"],
        "topics": ["role.pep", "role.pol"],
    },
    "datasets": ["wikidata"],
}


def test_abramovich_gets_high_score_from_sanction_topic():
    """The bug: this entity used to land with score=None and get filtered out."""
    client = OpenSanctionsClient()
    entries = client._parse_search_results({"results": [_ABRAMOVICH]})
    assert len(entries) == 1
    entry = entries[0]
    assert entry.name == "Roman Abramovich"
    assert entry.score == 0.9  # passes the 0.6 sanctions threshold
    # Programs surface the UK/EU/etc. list codes for display.
    assert "GB-RUS" in entry.programs
    assert "EU-UKR" in entry.programs


def test_sanction_linked_gets_mid_score():
    """Relatives of sanctioned individuals shouldn't auto-flag as sanctioned."""
    client = OpenSanctionsClient()
    entries = client._parse_search_results({"results": [_LINKED_RELATIVE]})
    assert len(entries) == 1
    # 0.5 sits below the 0.6 person-profile threshold but preserves the
    # entry for graph enrichment / proximity walks.
    assert entries[0].score == 0.5


def test_pep_only_does_not_count_as_sanctioned():
    """A PEP without a sanction designation shouldn't pass the score floor."""
    client = OpenSanctionsClient()
    entries = client._parse_search_results({"results": [_PEP_ONLY]})
    assert len(entries) == 1
    assert entries[0].score == 0.3  # below the 0.6 threshold


def test_explicit_api_score_overrides_topic_inference():
    """If OS ever starts returning a per-hit score, honour it."""
    result_with_score = dict(_ABRAMOVICH, score=0.42)
    client = OpenSanctionsClient()
    entries = client._parse_search_results({"results": [result_with_score]})
    assert entries[0].score == 0.42


def test_programs_pulled_from_programId_first():
    """Don't fall through to topics when machine-readable programId exists."""
    client = OpenSanctionsClient()
    entries = client._parse_search_results({"results": [_ABRAMOVICH]})
    # Should be the programId codes, not the topic strings.
    assert "GB-RUS" in entries[0].programs
    assert "role.pep" not in entries[0].programs


def test_programs_fall_back_to_datasets_when_no_programId():
    """Entities without programId still show source list names."""
    no_program_id = {
        "id": "x",
        "caption": "Someone",
        "schema": "Person",
        "properties": {"name": ["Someone"], "topics": ["sanction"]},
        "datasets": ["gb_fcdo_sanctions", "eu_fsf"],
    }
    client = OpenSanctionsClient()
    entries = client._parse_search_results({"results": [no_program_id]})
    assert "gb_fcdo_sanctions" in entries[0].programs
