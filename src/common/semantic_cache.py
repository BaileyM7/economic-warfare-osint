"""Semantic query matching for the analysis pre-warm cache (Phase 6).

Today a warmed analysis only replays on an EXACT query-string match, and the
"Did you mean…?" suggester is Jaccard token overlap. This module upgrades both to
semantic matching so a *reworded* demo question can replay (≈10s) instead of
paying a ~4.5-min cold run — WITHOUT ever silently answering a different question.

**This is a resolver, not a store.** It maps an incoming query to a *canonical
warmed query string*; the payload stays in the diskcache the orchestrator already
uses, and the existing exact-key replay runs unchanged. So a Redis outage costs
fuzzy matching, not the pre-warm feature.

The safety problem, measured on the real API (voyage-large-2, see
scripts/calibrate_semantic_cache.py):

    genuine paraphrase of a demo query                   -> 0.956 – 0.991
    ENTITY SWAP (same question, different company):
        Fujian Jinhua -> SMIC                            -> 0.961
        DJI -> Autel                                     -> 0.960
        Nuctech -> Hikvision                             -> 0.897
    entirely unrelated question                          -> 0.735

The classes **overlap**: two entity swaps (0.961, 0.960) score HIGHER than the
weakest genuine paraphrase (0.956). So there is *no* distance threshold that
admits real paraphrases without also admitting entity swaps — a threshold-only
auto-replay would return Fujian Jinhua's assessment for a question about SMIC.
Auto-replay therefore requires low distance AND an identical *entity signature*;
if the signatures differ it is demoted to a suggestion regardless of cosine. It
is also opt-in (``EMISSARY_SEMANTIC_REPLAY``, default off) — for an intel tool,
silently answering a near-miss is a liability, so suggest-only is the honest
default.

Degrades to the old Jaccard behaviour when embeddings are unavailable, and the
lexical path can NEVER return the auto-replay band.
"""

from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

# Similarity (1 - cosine distance) bands. Calibrated against the measured spread
# above; see scripts/calibrate_semantic_cache.py to re-derive.
AUTO_REPLAY_MIN_SIMILARITY = 0.93  # above the 0.897 entity-swap, below 0.960 paraphrase
SUGGEST_MIN_SIMILARITY = 0.60  # matches the old Jaccard 0.6 "Did you mean" feel

# Auto-replay is OFF unless explicitly enabled (demo service only). A suggestion
# is confirmed by a human, so the suggest upgrade is always on; auto-replay
# answers without confirmation, so it is not.
REPLAY_ENABLED = os.getenv("EMISSARY_SEMANTIC_REPLAY", "0").lower() in ("1", "true", "yes")

Band = Literal["exact", "auto_replay", "suggest", "none"]

# Sentence-starters / question words that are capitalized but carry no entity
# identity — excluded from the entity signature so "What…" vs "How…" don't count.
_SIGNATURE_STOP = frozenset(
    "what who how why when where which map show tell give find is are the a an of "
    "to in on for and or if we our do does their its it this that with from into "
    "about as at by would could should".split()
)


@dataclass(frozen=True)
class QueryMatch:
    """The result of resolving an incoming query against the warmed set."""

    query: str | None  # the canonical warmed query (original casing), or None
    similarity: float
    band: Band
    backend: Literal["vector", "lexical"]
    entity_signature_match: bool = False


def _normalize(q: str) -> str:
    return " ".join((q or "").lower().split())


def entity_signature(query: str) -> frozenset[str]:
    """The set of identity-bearing tokens in a query (lowercased).

    Proper nouns, acronyms, and years — the things that make "…owns Nuctech…" a
    DIFFERENT question from "…owns Hikvision…" even at 0.897 cosine. Deterministic
    and cheap; this is the gate that a vector score cannot be trusted to enforce.
    """
    sig: set[str] = set()
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9]*", query or ""):
        is_acronym = tok.isupper() and len(tok) >= 2  # DJI, SMIC, UMC
        is_proper = tok[0].isupper() and tok.lower() not in _SIGNATURE_STOP
        if is_acronym or is_proper:
            sig.add(tok.lower())
    for year in re.findall(r"\b(?:19|20)\d{2}\b", query or ""):
        sig.add(year)
    return frozenset(sig)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def is_semantic_available() -> bool:
    try:
        from src.common.embeddings import embeddings_enabled

        return embeddings_enabled()
    except Exception:  # noqa: BLE001
        return False


async def match_query(query: str, warmed_queries: list[str]) -> QueryMatch:
    """Resolve ``query`` against the warmed set into a banded match.

    Bands, in order:
      * ``exact``       — normalized string already warmed (caller instant-replays).
      * ``auto_replay`` — semantically ~identical AND same entity signature AND
                          replay enabled. The ONLY band that answers without a human.
      * ``suggest``     — similar enough to offer as "Did you mean…?" (confirmed).
      * ``none``        — nothing close enough.

    Never raises; falls back to Jaccard when embeddings are unavailable, and the
    lexical path can only ever return ``suggest`` (never ``auto_replay``).
    """
    qn = _normalize(query)
    candidates = [c for c in warmed_queries if c and c.strip()]

    # Exact match — the existing replay path already handles this instantly.
    for c in candidates:
        if _normalize(c) == qn:
            return QueryMatch(
                query=c,
                similarity=1.0,
                band="exact",
                backend="lexical",
                entity_signature_match=True,
            )

    non_exact = [c for c in candidates if _normalize(c) != qn]
    if not non_exact:
        return QueryMatch(query=None, similarity=0.0, band="none", backend="lexical")

    if is_semantic_available():
        try:
            return await _match_semantic(query, qn, non_exact)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Semantic query match failed (using lexical): %s", exc)

    return _match_lexical(query, non_exact)


async def _match_semantic(query: str, qn: str, candidates: list[str]) -> QueryMatch:
    from src.common.embeddings import embed, embed_many

    q_vec = await embed(query)
    cand_vecs = await embed_many(candidates)
    if q_vec is None or cand_vecs is None:
        return _match_lexical(query, candidates)

    best_i, best_sim = -1, -1.0
    for i, cv in enumerate(cand_vecs):
        sim = _cosine(q_vec, cv)
        if sim > best_sim:
            best_i, best_sim = i, sim

    best = candidates[best_i]
    sig_match = entity_signature(query) == entity_signature(best)
    band = _band(best_sim, sig_match, backend="vector")
    return QueryMatch(
        query=best,
        similarity=round(best_sim, 4),
        band=band,
        backend="vector",
        entity_signature_match=sig_match,
    )


def _match_lexical(query: str, candidates: list[str]) -> QueryMatch:
    """Jaccard fallback — today's behaviour. Can NEVER return auto_replay."""
    from src.common.similarity import jaccard_similarity

    best, best_sim = None, 0.0
    for c in candidates:
        s = jaccard_similarity(query, c)
        if s > best_sim:
            best, best_sim = c, s

    if best is not None and best_sim >= SUGGEST_MIN_SIMILARITY:
        # Lexical is suggest-at-most, by construction: it must not auto-answer.
        return QueryMatch(
            query=best,
            similarity=round(best_sim, 4),
            band="suggest",
            backend="lexical",
            entity_signature_match=False,
        )
    return QueryMatch(query=None, similarity=round(best_sim, 4), band="none", backend="lexical")


def _band(similarity: float, sig_match: bool, backend: str) -> Band:
    if (
        backend == "vector"
        and similarity >= AUTO_REPLAY_MIN_SIMILARITY
        and sig_match
        and REPLAY_ENABLED
    ):
        return "auto_replay"
    if similarity >= SUGGEST_MIN_SIMILARITY:
        return "suggest"
    return "none"
