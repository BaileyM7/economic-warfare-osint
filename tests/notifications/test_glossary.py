"""Tests for the email glossary / acronym-expansion helper."""

from __future__ import annotations

from src.notifications.glossary import GLOSSARY, expand_acronyms


def test_glossary_has_starter_entries():
    """Confirm the starter set we documented is actually present."""
    starter_terms = {"OFAC", "SDN", "IRGC", "OPEC+", "OPEC", "Brent", "WTI", "mbpd", "GDELT"}
    missing = starter_terms - set(GLOSSARY.keys())
    assert not missing, f"Glossary missing starter entries: {missing}"


def test_glossary_values_are_concise():
    """Each expansion should be a short phrase, not a paragraph."""
    for term, expansion in GLOSSARY.items():
        assert len(expansion) <= 60, f"{term}: expansion too long ({len(expansion)} chars)"
        assert "\n" not in expansion, f"{term}: expansion contains newline"


def test_expand_acronyms_first_mention_only():
    """OFAC appears twice — only the first gets expanded."""
    text = "OFAC said X. OFAC also said Y."
    result = expand_acronyms(text)
    # First mention expanded
    assert "OFAC (U.S. Treasury's sanctions office) said X" in result
    # Second mention NOT expanded — there should be exactly one "(U.S. Treasury"
    assert result.count("(U.S. Treasury's sanctions office)") == 1


def test_expand_acronyms_multiple_terms_in_one_text():
    text = "New OFAC SDN designation citing IRGC ties."
    result = expand_acronyms(text)
    assert "OFAC (U.S. Treasury's sanctions office)" in result
    assert "SDN (sanctions list designation)" in result
    assert "IRGC (Iran's Revolutionary Guard)" in result


def test_expand_acronyms_respects_word_boundaries():
    """'OFACES' should NOT match 'OFAC' (would be wrong word)."""
    # Using a synthetic non-real word to demonstrate the boundary check.
    text = "The OFACES of the situation are unclear, but OFAC is involved."
    result = expand_acronyms(text)
    # OFAC should expand (whole word), OFACES should not
    assert "OFACES of" in result
    assert "OFAC (U.S. Treasury's sanctions office) is involved" in result


def test_expand_acronyms_handles_keys_with_special_chars():
    """OPEC+ ends in a non-word char; the regex must still anchor it cleanly."""
    text = "OPEC+ announced a cut. OPEC alone did not comment."
    result = expand_acronyms(text)
    # OPEC+ matched first (sorted longest-first), so OPEC alone gets expanded too
    assert "OPEC+ (major oil producers plus Russia)" in result
    assert "OPEC (Organization of Petroleum Exporting Countries) alone" in result


def test_expand_acronyms_handles_end_of_string():
    """Term at the very end of the text should still match (end-of-string boundary)."""
    text = "The new designation came from OFAC"
    result = expand_acronyms(text)
    assert "OFAC (U.S. Treasury's sanctions office)" in result


def test_expand_acronyms_empty_input():
    assert expand_acronyms("") == ""
    assert expand_acronyms(None) is None  # type: ignore[arg-type]


def test_expand_acronyms_no_known_terms():
    """Text with no glossary terms is returned unchanged."""
    text = "The meeting concluded at 3pm."
    assert expand_acronyms(text) == text


def test_expand_acronyms_is_per_block_not_global():
    """Each call gets its own 'seen' set — expanding twice expands twice."""
    text = "OFAC moved."
    first = expand_acronyms(text)
    second = expand_acronyms(text)
    # Both calls should produce the same expanded output (no shared state)
    assert first == second
    assert "(U.S. Treasury's sanctions office)" in first


def test_expand_acronyms_in_rendered_digest():
    """End-to-end: a card synthesis containing 'OFAC' renders with the expansion."""
    from datetime import datetime, timezone

    from src.notifications.email_digest import WeekData, render_digest

    wd = WeekData(
        username="alice",
        week_iso="2026-W21",
        week_start=datetime(2026, 5, 18, tzinfo=timezone.utc),
        week_end=datetime(2026, 5, 24, tzinfo=timezone.utc),
        top_cards=[
            {
                "severity": "high",
                "entity": "COSCO",
                "source": "OFAC",
                "fetched_at": "2026-05-18T10:00:00Z",
                "synthesis": "New OFAC SDN designation announced.",
            }
        ],
    )
    html, text = render_digest(wd)
    # The card synthesis text should have inline expansions in both variants.
    # HTML autoescapes the apostrophe ('s → &#39;s) — that's correct, we WANT
    # autoescape on user-derived content. Plain text preserves the apostrophe.
    assert "OFAC (U.S. Treasury&#39;s sanctions office)" in html
    assert "OFAC (U.S. Treasury's sanctions office)" in text
    assert "SDN (sanctions list designation)" in html
    assert "SDN (sanctions list designation)" in text
