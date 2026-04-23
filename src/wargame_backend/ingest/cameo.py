"""CAMEO event-code to Domain mapping for GDELT normalization.

CAMEO (Conflict and Mediation Event Observations) codes are used by GDELT to
classify events. This module provides a lookup table from CAMEO numeric code
ranges to the project's Domain enum.

Mapping rationale
-----------------
- 010-099  Verbal cooperation / conflict, diplomatic statements → diplomatic
- 100-119  Diplomatic interactions, consultations              → diplomatic
- 120-129  Yield (concede, retreat)                           → diplomatic
- 130-139  Threaten, display force                            → diplomatic  (explicit threat)
- 140-149  Protest, demand                                    → diplomatic
- 150-159  Force (exhibit / show)                             → kinetic_limited (posturing)
- 160-169  Reduce relations, sanction, ban, expel             → economic
- 170-179  Coerce (arrest, torture, seize)                    → kinetic_limited
- 180-199  Assault, fight, use unconventional mass violence   → kinetic_limited by default;
           upgraded to kinetic_general when Goldstein ≤ -8.0
- 200+     Mass destruction / WMD use                         → kinetic_general

CAMEO sub-codes that imply information / cyber operations:
- 013, 014, 015  Media appeal / propaganda → info
- 1711-1713      Cyber-related (unofficial GDELT extension codes) → cyber

Note: GDELT event codes are stored as strings like "014" or "1713". We convert
to int for range comparison; individual overrides take priority over ranges.
"""

from __future__ import annotations

from wargame_shared.schemas.sim_event import Domain  # reuse canonical enum

# ---------------------------------------------------------------------------
# Individual-code overrides (take priority over range table below)
# ---------------------------------------------------------------------------
_CODE_OVERRIDES: dict[int, Domain] = {
    # Information / media operations
    13: Domain.info,
    14: Domain.info,
    15: Domain.info,
    # Cyber / electronic warfare (unofficial GDELT extensions)
    1711: Domain.cyber,
    1712: Domain.cyber,
    1713: Domain.cyber,
}

# ---------------------------------------------------------------------------
# Range table — list of (lo_inclusive, hi_inclusive, domain) sorted by lo
# ---------------------------------------------------------------------------
_RANGE_TABLE: list[tuple[int, int, Domain]] = [
    (10,  99,  Domain.diplomatic),
    (100, 139, Domain.diplomatic),
    (140, 149, Domain.diplomatic),
    (150, 159, Domain.kinetic_limited),   # force display / posturing
    (160, 169, Domain.economic),          # sanctions, bans, expulsions
    (170, 179, Domain.kinetic_limited),   # coercive arrests / seizure
    (180, 199, Domain.kinetic_limited),   # assault / fight (may upgrade below)
    (200, 999, Domain.kinetic_general),
]


def cameo_to_domain(
    event_code: str,
    goldstein_scale: float | None = None,
) -> Domain:
    """Map a CAMEO event code string to a project Domain enum value.

    Parameters
    ----------
    event_code:
        GDELT EventCode string, e.g. ``"014"``, ``"1713"``, ``"193"``.
    goldstein_scale:
        Goldstein conflict/cooperation score (-10 to +10). Used to escalate
        codes 180-199 from ``kinetic_limited`` to ``kinetic_general`` when
        the value is ≤ -8.0 (most destructive end of the scale).

    Returns
    -------
    Domain
        Best-fit domain classification.  Falls back to ``Domain.diplomatic``
        when the code is unrecognised or unparseable.
    """
    try:
        code_int = int(event_code)
    except (TypeError, ValueError):
        return Domain.diplomatic

    # Individual overrides first
    if code_int in _CODE_OVERRIDES:
        return _CODE_OVERRIDES[code_int]

    # Range scan
    for lo, hi, domain in _RANGE_TABLE:
        if lo <= code_int <= hi:
            # Escalate severe assault/fight codes to kinetic_general
            if (
                domain is Domain.kinetic_limited
                and 180 <= code_int <= 199
                and goldstein_scale is not None
                and goldstein_scale <= -8.0
            ):
                return Domain.kinetic_general
            return domain

    return Domain.diplomatic
