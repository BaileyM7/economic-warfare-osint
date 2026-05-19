"""Acronym/jargon glossary for the weekly email digest.

The risk-feed pipeline emits text laden with sanctions/oil-trade/geopolitics
jargon (OFAC, SDN, IRGC, mbpd, ...). That's fine for analysts who live in
this vocabulary daily, but a customer evaluating the demo may not. This
module expands the most-common terms inline on first mention within any
given block of text so the reader can decode without losing flow.

Used as a Jinja filter (`|expand_acronyms`) inside digest.html.j2 and
digest.txt.j2 for the per-card synthesis lines. NOT applied to SMS bodies
(the 160-char budget can't absorb the expansion overhead — anyone getting
an SMS alert is already in the loop).

Per-block, not per-email: each card's synthesis is processed independently,
so "OFAC" gets expanded once per card rather than once per email. That's
deliberate — each card should stand alone to a reader who scans rather
than reads top-to-bottom.
"""

from __future__ import annotations

import re

# Acronym → plain-English expansion. The expansion appears in parentheses
# after the FIRST occurrence in any given block of text; subsequent
# occurrences in the same block are left alone.
#
# Keys are matched case-sensitively on word boundaries (so "OFACE" doesn't
# match "OFAC"). Multi-word keys are matched literally — escape regex chars
# if you add any (the function uses re.escape).
#
# When extending: prefer short, neutral expansions a non-expert recognizes
# at a glance. The goal is to bridge a vocabulary gap, not to teach the
# field. ~30 chars is a good upper bound; longer expansions clutter the email.
#
# Starter set (drafted 2026-05-18). Extend with terms your customer's audience
# will likely encounter — products, regions, regulatory bodies, finance
# benchmarks, etc.
GLOSSARY: dict[str, str] = {
    # Sanctions + regulatory
    "OFAC": "U.S. Treasury's sanctions office",
    "SDN": "sanctions list designation",
    "CSL": "Consolidated Screening List",
    "SEC": "U.S. Securities and Exchange Commission",
    "EDGAR": "SEC corporate filings database",
    "GLEIF": "global legal-entity identifier registry",
    # Geopolitical actors / regions
    "IRGC": "Iran's Revolutionary Guard",
    "OPEC+": "major oil producers plus Russia",
    "OPEC": "Organization of Petroleum Exporting Countries",
    "PLA": "China's People's Liberation Army",
    # Commodities / markets
    "Brent": "the global crude oil benchmark",
    "WTI": "West Texas Intermediate, the U.S. crude benchmark",
    "DXY": "U.S. dollar index",
    "mbpd": "million barrels per day",
    "bpd": "barrels per day",
    "FX": "foreign exchange",
    # Datasets the platform pulls from
    "GDELT": "the global news-events dataset",
    "ACLED": "the conflict-events dataset",
    "AIS": "vessel-tracking transponder data",
    "UBO": "ultimate beneficial owner",
    # TODO(user): add terms your customer's audience will encounter that
    # aren't covered above. Examples worth considering depending on demo focus:
    #   - "FATF" (financial action task force)
    #   - "BIS" (Bureau of Industry and Security, export controls)
    #   - "EU sanctions" / "CAATSA" / specific sanction regime names
    #   - Industry shorthand: "downstream", "midstream", "feedstock"
    #   - Specific entity types: "SOE" (state-owned enterprise)
}


def expand_acronyms(text: str) -> str:
    """Insert plain-English expansions after the first mention of each known term.

    Examples
    --------
    >>> expand_acronyms("New OFAC SDN designation citing IRGC ties.")
    "New OFAC (U.S. Treasury's sanctions office) SDN (sanctions list designation) designation citing IRGC (Iran's Revolutionary Guard) ties."

    >>> expand_acronyms("OFAC said X. OFAC also said Y.")  # second OFAC NOT expanded
    "OFAC (U.S. Treasury's sanctions office) said X. OFAC also said Y."

    >>> expand_acronyms("")
    ""
    """
    if not text or not GLOSSARY:
        return text

    # Build a single alternation regex covering every glossary key.
    # Sort by descending length so multi-char keys (e.g. "OPEC+") match
    # before shorter overlaps (e.g. "OPEC"). Escape special chars.
    keys = sorted(GLOSSARY.keys(), key=len, reverse=True)
    # Word boundary on both sides — but `\b` is char-class based, so for keys
    # ending in non-word chars like "OPEC+", we anchor on lookahead instead.
    pattern_parts = []
    for k in keys:
        escaped = re.escape(k)
        # If key ends in a word char, require word boundary; otherwise just
        # require a non-word char or end-of-string after.
        if k[-1].isalnum() or k[-1] == "_":
            pattern_parts.append(rf"\b{escaped}\b")
        else:
            pattern_parts.append(rf"\b{escaped}(?=\W|$)")
    pattern = re.compile("|".join(pattern_parts))

    seen: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        term = match.group(0)
        if term in seen:
            return term
        seen.add(term)
        # Use the original-case match in the output, look up by exact match
        # against the glossary key.
        expansion = GLOSSARY.get(term)
        if expansion is None:
            return term
        return f"{term} ({expansion})"

    return pattern.sub(_replace, text)
