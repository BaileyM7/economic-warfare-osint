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
