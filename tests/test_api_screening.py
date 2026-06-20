"""Unit tests for the OFAC false-positive filter used by sector-analysis and
entity-risk-report. `_ofac_hit_matches_company_label` requires a significant
(>=4-char) token from the company name to appear as a WHOLE token in the OFAC
row's name/aliases — this is what stops "intel" matching "intelligence" and
short ticker tokens matching unrelated designations.

NOTE: this helper currently lives in src/api.py. Stage 3 moves it to
src/common/screening_helpers.py — update the import below in that same change.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.common.screening_helpers import ofac_hit_matches_company_label as matches


def _row(name: str, aliases: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(name=name, aliases=aliases or [])


def test_exact_name_token_matches():
    assert matches("SMIC", _row("SMIC")) is True
    assert matches("TSMC Holdings", _row("TSMC")) is True


def test_matches_via_alias():
    entry = _row("Semiconductor Manufacturing International Corp", aliases=["SMIC"])
    assert matches("SMIC", entry) is True


def test_substring_false_positive_is_filtered():
    # "intel" must NOT match "intelligence" (whole-token match required)
    assert matches("Intel", _row("Central Intelligence Agency")) is False
    # "apple" must NOT match "pineapple"
    assert matches("Apple Inc", _row("Pineapple Trading Co")) is False


def test_blank_company_name_is_false():
    assert matches("", _row("Anything")) is False
    assert matches("   ", _row("Anything")) is False


def test_short_only_tokens_fall_back_to_all_tokens():
    # No >=4-char token, so the 2-3 char tokens are used as-is.
    assert matches("BP", _row("BP PLC")) is True
    assert matches("BP", _row("Unrelated Company")) is False
