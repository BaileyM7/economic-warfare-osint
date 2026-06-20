"""Shared sanctions-screening helpers used by multiple API endpoints.

Pure functions — no app state. Extracted from src/api.py so that both the
sector-analysis and entity-risk-report endpoints (and their tests) can share
the OFAC false-positive filter. Behaviour is unchanged from the original
inline definition.
"""

from __future__ import annotations

import re
from typing import Any


def ofac_hit_matches_company_label(company_name: str, entry: Any) -> bool:
    """Require a significant token from *company_name* to appear as a whole token in the OFAC row.

    This filters substring false positives (e.g. "intel" matching "intelligence",
    "samsung" matching "SAMSUN", short ticker tokens matching unrelated words).
    """
    tokens = [t for t in re.findall(r"[a-z0-9]+", company_name.lower()) if len(t) >= 2]
    if not tokens:
        return False
    significant = [t for t in tokens if len(t) >= 4] or tokens
    rows: list[str] = [getattr(entry, "name", "") or ""]
    aliases = getattr(entry, "aliases", None) or []
    rows.extend(str(a) for a in aliases if a)
    for text in rows:
        row_tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
        for t in significant:
            if t in row_tokens:
                return True
    return False
