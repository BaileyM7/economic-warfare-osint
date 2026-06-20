"""Safety-net tests for the sanctions surfacing/scoring logic.

These lock in the behaviour that commits #16–#18 fixed and that several
api.py endpoints depend on (person-profile, sector-analysis, entity-risk-report):

  * CSL → SanctionEntry mapping (so BIS Entity List hits like SMIC surface)
  * CSL + OFAC aggregation with name-dedup keeping the higher score
  * the >= 0.85 "strong match" threshold in check_status()

All pure or mocked — no network — so they stay fast (<0.1s) and protect the
Stage-3 api.py refactor from silently regressing the surfacing path.
"""

from __future__ import annotations

from src.tools.sanctions import client as sanctions_client
from src.tools.sanctions.client import SanctionsClient
from src.tools.sanctions.models import SanctionEntry

# A representative Trade.gov CSL hit: SMIC on the BIS Entity List. This is the
# exact shape the #16 fix made sector-analysis surface (it was OFAC-SDN-only
# before, so Entity-List entities read as "Clear").
_SMIC_CSL_HIT = {
    "name": "Semiconductor Manufacturing International Corporation",
    "source": "Entity List (EL) - Bureau of Industry and Security",
    "programs": ["Entity List"],
    "start_date": "2020-12-18",
    "alt_names": ["SMIC"],
    "ids": [{"type": "D-U-N-S", "number": "123456789"}],
    "addresses": [{"city": "Shanghai", "country": "China"}],
    "entity_number": "EL-12345",
    "type": "Entity",
}


# --- _csl_to_entries (pure mapping) ---------------------------------------


def test_csl_to_entries_maps_entity_list_hit():
    entries = SanctionsClient._csl_to_entries([_SMIC_CSL_HIT])
    assert len(entries) == 1
    e = entries[0]
    assert e.name == "Semiconductor Manufacturing International Corporation"
    assert e.entity_type == "company"  # CSL "Entity" → company
    assert "Entity List" in e.programs
    assert e.list_source.startswith("Entity List")
    assert e.aliases == ["SMIC"]
    assert e.identifiers == {"D-U-N-S": "123456789"}
    assert e.addresses == ["Shanghai, China"]
    assert e.score == 0.9  # CSL is government-verified → strong by construction
    assert e.designation_date is not None and e.designation_date.year == 2020


def test_csl_to_entries_skips_blank_names():
    assert SanctionsClient._csl_to_entries([{"name": "  "}]) == []
    assert SanctionsClient._csl_to_entries([{}]) == []


# --- mock helpers ----------------------------------------------------------


def _patch_sources(monkeypatch, csl=None, ofac=None):
    """Stub the two network legs of SanctionsClient.search (CSL + OFAC SDN)."""

    async def fake_csl(query, *args, **kwargs):
        return list(csl or [])

    async def fake_ofac_search(self, query, entity_type="any"):
        return list(ofac or [])

    monkeypatch.setattr(sanctions_client, "search_csl", fake_csl)
    monkeypatch.setattr(sanctions_client.OFACClient, "search", fake_ofac_search)


# --- check_status threshold + aggregation ----------------------------------


async def test_check_status_surfaces_csl_entity_list(monkeypatch):
    """#16/#17 regression guard: a CSL Entity-List hit must read as sanctioned."""
    _patch_sources(monkeypatch, csl=[_SMIC_CSL_HIT], ofac=[])
    status = await SanctionsClient().check_status("SMIC")
    assert status.is_sanctioned is True
    assert any("Entity List" in p for p in status.programs)
    assert status.lists_found  # the CSL source surfaced
    assert status.entries and status.entries[0].score >= 0.85


async def test_check_status_clean_name_not_sanctioned(monkeypatch):
    _patch_sources(monkeypatch, csl=[], ofac=[])
    status = await SanctionsClient().check_status("Totally Clean Co")
    assert status.is_sanctioned is False
    assert status.lists_found == []
    assert status.entries == []


async def test_check_status_weak_ofac_match_below_threshold(monkeypatch):
    """A fuzzy OFAC match under 0.85 must NOT flag the entity as sanctioned."""
    weak = SanctionEntry(id="ofac-1", name="Weakish Match", list_source="OFAC SDN", score=0.6)
    _patch_sources(monkeypatch, csl=[], ofac=[weak])
    status = await SanctionsClient().check_status("Weakish Match")
    assert status.is_sanctioned is False


# --- search() merge + dedup ------------------------------------------------


async def test_search_dedups_by_name_keeping_higher_score(monkeypatch):
    csl_hit = {**_SMIC_CSL_HIT, "name": "Acme Corp", "alt_names": []}  # CSL → score 0.9
    ofac_hit = SanctionEntry(id="ofac-1", name="Acme Corp", list_source="OFAC SDN", score=0.7)
    _patch_sources(monkeypatch, csl=[csl_hit], ofac=[ofac_hit])
    result = await SanctionsClient().search("Acme Corp")
    assert result.total_matches == 1  # same name → deduped
    assert result.matches[0].score == 0.9  # higher (CSL) score wins


async def test_search_merges_distinct_names_sorted_by_score(monkeypatch):
    csl_hit = {**_SMIC_CSL_HIT, "name": "High Score Co", "alt_names": []}
    ofac_hit = SanctionEntry(id="ofac-1", name="Low Score Co", list_source="OFAC SDN", score=0.5)
    _patch_sources(monkeypatch, csl=[csl_hit], ofac=[ofac_hit])
    result = await SanctionsClient().search("co")
    assert result.total_matches == 2
    assert [m.score for m in result.matches] == [0.9, 0.5]  # sorted desc
