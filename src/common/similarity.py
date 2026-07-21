"""Entity similarity / link prediction (issue #30).

The customer's top ask: "identify companies with similar characteristics to this
one." This module ranks the entities in the knowledge store by similarity to a
target, with an **explainable** per-result basis (what drove the score).

Two backends, chosen by ``config.similarity_backend``:

  * ``lexical`` (default) — dependency-free and fully offline. A weighted blend of
    name-token overlap + shared traits (entity type, country, identifiers, notes).
    Deterministic and explainable; always available.
  * ``embedding`` — a local sentence-transformers model (install the ``similarity``
    extra). Better at catching semantic/synonym similarity. If the dependency is
    missing it raises ``EmbeddingsUnavailable`` so the caller can fall back to
    lexical; nothing here ever calls the network beyond the one-time model load.

The lexical token primitives are also the single home for the orchestrator's
"Did you mean…?" query similarity (previously an inline Jaccard), so there is one
place that defines "content-token overlap" for the whole app.
"""

from __future__ import annotations

import math
import re
from functools import lru_cache

# Low-signal tokens dropped before comparing text. Kept deliberately small — it's
# meant to remove structural filler, not domain words.
DEFAULT_STOPWORDS = frozenset(
    "a an the of to in on for and or if we our is are be do does what who how "
    "their its it this that with from into about as at by ltd inc llc plc co corp "
    "company limited holdings group".split()
)


class EmbeddingsUnavailable(RuntimeError):
    """Raised when the embedding backend is requested but unavailable."""


# --- Lexical primitives (shared with the orchestrator query-suggest seam) ----


def tokenize(text: str, stopwords: frozenset[str] = DEFAULT_STOPWORDS) -> set[str]:
    """Content tokens: lowercased alphanumerics, stopwords + 1-char tokens removed."""
    toks = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {t for t in toks if t not in stopwords and len(t) > 1}


def jaccard_similarity(a: str, b: str, stopwords: frozenset[str] = DEFAULT_STOPWORDS) -> float:
    """Jaccard overlap of two strings' content tokens (0.0–1.0). Pure + deterministic."""
    ta, tb = tokenize(a, stopwords), tokenize(b, stopwords)
    if not ta or not tb:
        return 0.0
    union = len(ta | tb)
    return len(ta & tb) / union if union else 0.0


# --- Entity lexical similarity ----------------------------------------------


def entity_text(entity: dict) -> str:
    """A flat text representation of an entity for embedding / token comparison."""
    parts = [
        entity.get("name") or "",
        entity.get("entity_type") or "",
        entity.get("country") or "",
        " ".join(entity.get("aliases") or []),
        " ".join(str(v) for v in (entity.get("identifiers") or {}).values()),
        entity.get("notes") or "",
    ]
    return " ".join(p for p in parts if p).strip()


def _lexical_pair(target: dict, cand: dict) -> tuple[float, dict]:
    """Weighted lexical similarity of two entities + an explainable basis.

    Blend: name/alias token overlap (primary) plus small boosts for shared traits.
    Returns (score in 0..1, basis dict).
    """
    name_sim = jaccard_similarity(
        f"{target.get('name', '')} {' '.join(target.get('aliases') or [])}",
        f"{cand.get('name', '')} {' '.join(cand.get('aliases') or [])}",
    )
    same_type = bool(
        target.get("entity_type") and target.get("entity_type") == cand.get("entity_type")
    )
    same_country = bool(target.get("country") and target.get("country") == cand.get("country"))

    t_ids = set((target.get("identifiers") or {}).values())
    c_ids = set((cand.get("identifiers") or {}).values())
    shared_ids = sorted(t_ids & c_ids)

    notes_sim = jaccard_similarity(target.get("notes") or "", cand.get("notes") or "")

    # Weights chosen so a strong name/alias match dominates, traits nudge ties.
    score = (
        0.60 * name_sim
        + 0.15 * (1.0 if same_type else 0.0)
        + 0.10 * (1.0 if same_country else 0.0)
        + 0.10 * (1.0 if shared_ids else 0.0)
        + 0.05 * notes_sim
    )
    shared_terms = sorted(
        tokenize(f"{target.get('name', '')} {target.get('notes', '')}")
        & tokenize(f"{cand.get('name', '')} {cand.get('notes', '')}")
    )
    basis = {
        "name_overlap": round(name_sim, 3),
        "same_type": same_type,
        "same_country": same_country,
        "shared_identifiers": shared_ids,
        "shared_terms": shared_terms[:8],
    }
    return min(score, 1.0), basis


# --- Embedding backend (optional, lazy) -------------------------------------


@lru_cache(maxsize=1)
def _get_model(model_name: str):
    """Lazily load the sentence-transformers model (cached process-wide)."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # dependency not installed
        raise EmbeddingsUnavailable(
            "sentence-transformers is not installed; run `uv sync --extra similarity` "
            "to enable the embedding backend (or use SIMILARITY_BACKEND=lexical)."
        ) from exc
    return SentenceTransformer(model_name)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _embedding_rank(
    target: dict, candidates: list[dict], model_name: str
) -> list[tuple[float, dict, dict]]:
    model = _get_model(model_name)
    texts = [entity_text(target)] + [entity_text(c) for c in candidates]
    vectors = model.encode(texts, normalize_embeddings=False)
    t_vec = list(map(float, vectors[0]))
    out: list[tuple[float, dict, dict]] = []
    for cand, vec in zip(candidates, vectors[1:]):
        score = max(0.0, _cosine(t_vec, list(map(float, vec))))
        # Pair the semantic score with the lexical basis so results stay explainable.
        _, basis = _lexical_pair(target, cand)
        out.append((score, cand, basis))
    return out


# --- Public API -------------------------------------------------------------


def rank_similar(
    target: dict,
    candidates: list[dict],
    top_k: int = 5,
    backend: str = "lexical",
) -> tuple[list[dict], str]:
    """Rank ``candidates`` by similarity to ``target``.

    Returns ``(results, backend_used)`` where each result is
    ``{"entity": <candidate>, "score": float, "basis": {...}}`` sorted desc.
    Falls back to lexical (recording it in ``backend_used``) if the embedding
    backend is requested but unavailable.
    """
    # Never compare the target to itself.
    cands = [c for c in candidates if c.get("entity_id") != target.get("entity_id")]
    backend_used = backend

    scored: list[tuple[float, dict, dict]]
    if backend == "embedding":
        try:
            from src.common.config import config

            scored = _embedding_rank(target, cands, config.similarity_model)
        except EmbeddingsUnavailable:
            backend_used = "lexical"
            scored = [(s, c, b) for c in cands for s, b in [_lexical_pair(target, c)]]
    else:
        backend_used = "lexical"
        scored = [(s, c, b) for c in cands for s, b in [_lexical_pair(target, c)]]

    scored.sort(key=lambda x: x[0], reverse=True)
    results = [
        {"entity": c, "score": round(s, 4), "basis": b} for s, c, b in scored[: max(0, top_k)]
    ]
    return results, backend_used


# --- Hybrid backend (Redis recalls, Python re-ranks) -------------------------

# Ranking is the SEMANTIC score alone. Lexical is kept for EXPLANATION only.
#
# This is measured, not assumed. Real voyage-large-2, target "Fujian Jinhua" (a
# DRAM fab), against the saved graph:
#
#     entity                  semantic   lexical   0.7*sem + 0.3*lex
#     SMIC (foundry)             0.910     0.250        0.712
#     Jinhua Group Holdings      0.872     0.400        0.730  <-- wins the blend
#     (a real-estate developer)
#
# Semantic alone ranks these correctly (SMIC first). Blending in even 30% lexical
# flips it back to the wrong answer — the "jinhua" token collision reasserts
# itself and the real-estate firm outranks the foundry again, which is the exact
# bug this index exists to fix. The semantic gap here is only 0.038 (everything in
# this domain sits in a compressed 0.8–0.95 band), so ANY meaningful lexical
# weight can flip it.
#
# The deeper reason: **name-token overlap is a false signal for entity
# similarity.** Companies sharing a name token are frequently unrelated, while
# genuinely related ones (e.g. "Rosatom" / "Rosatom Overseas") score high
# semantically anyway. So name overlap adds false positives without adding true
# ones. Traits that CAN'T collide by coincidence (a shared LEI) are a real signal
# and are surfaced in the basis — promoting them into the score is a separate
# change that should be made with its own measurements, not guessed at here.
SEMANTIC_WEIGHT = 1.0
LEXICAL_WEIGHT = 0.0

# Above this, a match is "mostly meaning". If it is ALSO lexically empty, the
# result gets an explicit warning — see _explain.
_SEMANTIC_ONLY_FLOOR = 0.55


def _explain(basis: dict, semantic: float, lexical: float) -> str:
    """One line an analyst can read (and defend) about why this matched.

    A bare cosine is not a rationale. This is deliberately blunt when the evidence
    is thin: a strong semantic score with zero lexical overlap is exactly the case
    where a vector is doing real work AND is most likely to be subtly wrong, so it
    says so rather than letting 0.87 imply confidence.
    """
    parts: list[str] = []
    if basis.get("shared_terms"):
        parts.append("shares " + ", ".join(f"'{t}'" for t in basis["shared_terms"][:3]))
    if basis.get("shared_identifiers"):
        parts.append("same identifier " + ", ".join(basis["shared_identifiers"][:2]))
    if basis.get("same_type"):
        parts.append("same type")
    if basis.get("same_country"):
        parts.append("same country")

    lead = f"Semantic match ({semantic:.2f})"
    if not parts:
        if semantic >= _SEMANTIC_ONLY_FLOOR:
            return f"{lead} only — no lexical overlap or shared traits. Verify before citing."
        return f"{lead}; weak on every other signal. Low confidence."
    detail = "; ".join(parts)
    if lexical == 0.0:
        return f"{lead} with no name overlap; {detail}. Verify before citing."
    return f"{lead}, lexical {lexical:.2f}; {detail}."


async def rank_similar_indexed(
    target: dict,
    *,
    top_k: int = 5,
    entity_type: str | None = None,
    country: str | None = None,
) -> tuple[list[dict], str]:
    """Rank saved entities by similarity to ``target`` using the vector index.

    Returns ``(results, backend_used)`` in the SAME shape as `rank_similar`, with
    ``backend_used`` in {"hybrid", "lexical"}.

    Redis recalls a wide-ish candidate set (vector KNN + BM25 + tag filters), then
    we re-rank *those* with the existing `_lexical_pair` — which is what attaches
    the explainable `basis`. That is the cheap version of what the old `embedding`
    backend did: it re-embedded every candidate on every request (O(N) network),
    whereas here the vectors are already in the index and `_lexical_pair` only runs
    on ~20 rows.

    Falls back to `rank_similar(..., "lexical")` whenever the index or embeddings
    are unavailable, so the caller always gets a real answer.
    """
    from src.common import knowledge_store as ks
    from src.common.vector_index import DEFAULT_RECALL, search_entity_ids

    def _lexical_fallback() -> tuple[list[dict], str]:
        return rank_similar(target, ks.list_entities(), top_k=top_k, backend="lexical")

    text = entity_text(target)
    if not text.strip():
        return _lexical_fallback()

    try:
        hits = await search_entity_ids(
            text,
            entity_type=entity_type,
            country=country,
            top_k=max(top_k * 4, DEFAULT_RECALL),
        )
    except Exception:  # noqa: BLE001
        hits = None

    if hits is None:  # no Redis 8 / no embeddings / query failed
        return _lexical_fallback()

    scores = {eid: s for eid, s in hits}
    candidates = ks.get_entities_by_ids(list(scores.keys()))

    out: list[dict] = []
    for cand in candidates:
        if cand.get("entity_id") == target.get("entity_id"):
            continue  # never compare the target to itself
        semantic = scores.get(cand["entity_id"], 0.0)
        lexical, basis = _lexical_pair(target, cand)
        # Every existing basis key survives — the frontend renders them, and a
        # score without an explanation is a regression for an intel product.
        basis["semantic_score"] = round(semantic, 3)
        basis["lexical_score"] = round(lexical, 3)
        basis["why"] = _explain(basis, semantic, lexical)
        out.append(
            {
                "entity": cand,
                "score": round(SEMANTIC_WEIGHT * semantic + LEXICAL_WEIGHT * lexical, 4),
                "basis": basis,
            }
        )

    out.sort(key=lambda r: r["score"], reverse=True)
    return out[: max(0, top_k)], "hybrid"
