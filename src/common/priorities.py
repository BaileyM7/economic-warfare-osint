"""Team-wide collection priorities (issue #32).

The team declares what to prioritize at the **country**, **sector**, or **company**
level; each priority carries a weight. This module is the single seam shared by the
admin CRUD router (`src/routers/priorities.py`) and the risk-feed ranking
(`src/routers/risk_feed.py`), so the matching logic lives in exactly one place.

Priorities are **global / shared** (not per-user). The risk-feed integration is a
pure *boost*: when no priorities are set the boost is always 0, so feed ordering is
byte-for-byte unchanged from before this feature existed.
"""

from __future__ import annotations

from src.db import _new_id, _now, get_db, row_to_priority

LEVELS = ("country", "sector", "company")


# --- CRUD -------------------------------------------------------------------


def list_priorities(level: str | None = None) -> list[dict]:
    conn = get_db()
    try:
        if level:
            rows = conn.execute(
                "SELECT * FROM priorities WHERE level = ? ORDER BY weight DESC, key", (level,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM priorities ORDER BY level, weight DESC, key"
            ).fetchall()
    finally:
        conn.close()
    return [row_to_priority(r) for r in rows]


def upsert_priority(
    level: str,
    key: str,
    weight: float = 1.0,
    label: str = "",
    notes: str = "",
    created_by: str | None = None,
) -> tuple[dict, bool]:
    """Insert or update a priority, deduped on (level, key)."""
    now = _now()
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM priorities WHERE level = ? AND key = ?", (level, key)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE priorities SET weight = ?, label = ?, notes = ?, updated_at = ? "
                "WHERE id = ?",
                (weight, label, notes, now, existing["id"]),
            )
            pid = existing["id"]
            created = False
        else:
            pid = _new_id()
            conn.execute(
                "INSERT INTO priorities (id, level, key, weight, label, notes, created_by, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pid, level, key, weight, label, notes, created_by, now, now),
            )
            created = True
        conn.commit()
        row = conn.execute("SELECT * FROM priorities WHERE id = ?", (pid,)).fetchone()
    finally:
        conn.close()
    return row_to_priority(row), created


def delete_priority(priority_id: str) -> int:
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM priorities WHERE id = ?", (priority_id,))
        deleted = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return deleted


# --- Matching / ranking -----------------------------------------------------


def priority_map() -> dict[str, dict[str, float]]:
    """All priorities as ``{level: {key_lowercased: weight}}`` for fast matching."""
    out: dict[str, dict[str, float]] = {lvl: {} for lvl in LEVELS}
    for p in list_priorities():
        out.setdefault(p["level"], {})[p["key"].lower()] = p["weight"]
    return out


def text_boost(text: str, pmap: dict[str, dict[str, float]] | None = None) -> float:
    """Largest priority weight whose key appears (case-insensitively) in ``text``.

    Returns 0.0 when nothing matches or no priorities are set — making the
    risk-feed integration a no-op until the team configures priorities.
    """
    if pmap is None:
        pmap = priority_map()
    if not text:
        return 0.0
    low = text.lower()
    best = 0.0
    for level_map in pmap.values():
        for key, weight in level_map.items():
            if key and key in low and weight > best:
                best = weight
    return best
