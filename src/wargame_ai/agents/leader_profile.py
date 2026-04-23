"""Structured leader-profile parsing for country personas.

Persona files at ``src/shared/seeds/personas/{ISO3}.md`` may carry a YAML
frontmatter block declaring the country's current leader and an OCEAN /
Big-Five personality vector.  This module parses that frontmatter into a
typed :class:`LeaderProfile` so the country-agent prompt can inject a
stable, structured personality block alongside the free-form persona prose.

Files without a frontmatter block degrade gracefully: ``parse_persona_file``
returns ``(None, original_text)`` and the prompt template falls back to a
"(no structured leader profile)" placeholder.  This keeps existing personas
loading unchanged while we backfill OCEAN data.
"""

from __future__ import annotations

import re
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

OceanDimension = Literal[
    "openness",
    "conscientiousness",
    "extraversion",
    "agreeableness",
    "neuroticism",
]

_OCEAN_DIMENSIONS: tuple[OceanDimension, ...] = (
    "openness",
    "conscientiousness",
    "extraversion",
    "agreeableness",
    "neuroticism",
)

# Matches a leading YAML frontmatter block delimited by --- on its own line.
# The closing fence may be followed by a newline OR end-of-file so callers
# can author a frontmatter-only file without a body.
_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<yaml>.*?)\n---\s*(?:\n|\Z)",
    re.DOTALL,
)


class OceanScores(BaseModel):
    """Big-Five personality scores on a 0-100 scale."""

    model_config = ConfigDict(extra="forbid")

    openness: int = Field(ge=0, le=100)
    conscientiousness: int = Field(ge=0, le=100)
    extraversion: int = Field(ge=0, le=100)
    agreeableness: int = Field(ge=0, le=100)
    neuroticism: int = Field(ge=0, le=100)

    def as_dict(self) -> dict[OceanDimension, int]:
        return {dim: getattr(self, dim) for dim in _OCEAN_DIMENSIONS}


class LeaderProfile(BaseModel):
    """Structured leader profile parsed from a persona file's YAML frontmatter."""

    model_config = ConfigDict(extra="forbid")

    leader: str = Field(min_length=1)
    ocean: OceanScores
    # Per-dimension one-sentence gloss: what THIS score means for THIS leader.
    # Keys must be a subset of the five OCEAN dimensions.  Empty dict is allowed
    # for partial authoring; the prompt block will substitute a generic phrase.
    ocean_descriptors: dict[OceanDimension, str] = Field(default_factory=dict)


class LeaderProfileError(ValueError):
    """Raised when frontmatter is malformed or scores are invalid."""


def parse_persona_file(text: str) -> tuple[LeaderProfile | None, str]:
    """Split a persona file into ``(LeaderProfile | None, body_markdown)``.

    Returns ``(None, text)`` when no frontmatter block is present, so the
    caller can keep using the full text as the legacy ``persona`` field.

    Raises :class:`LeaderProfileError` when frontmatter exists but is malformed
    (invalid YAML, missing required fields, scores out of range).  We fail
    loudly here rather than silently dropping to None — a typo in scores
    should not silently strip the leader profile from the prompt.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None, text

    yaml_text = match.group("yaml")
    body = text[match.end():]

    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise LeaderProfileError(f"invalid YAML in persona frontmatter: {exc}") from exc

    if not isinstance(data, dict):
        raise LeaderProfileError(
            f"persona frontmatter must be a YAML mapping, got {type(data).__name__}"
        )

    try:
        profile = LeaderProfile.model_validate(data)
    except ValidationError as exc:
        raise LeaderProfileError(
            f"persona frontmatter failed validation: {exc}"
        ) from exc

    return profile, body


def render_leader_profile_block(profile: LeaderProfile | None) -> str:
    """Render a LeaderProfile as a markdown table for prompt injection.

    Returns the placeholder string when ``profile`` is None so the prompt
    template never carries an unsubstituted token.
    """
    if profile is None:
        return "(no structured leader profile)"

    rows = []
    for dim in _OCEAN_DIMENSIONS:
        score = getattr(profile.ocean, dim)
        descriptor = profile.ocean_descriptors.get(dim, "").strip()
        if not descriptor:
            descriptor = _generic_descriptor(dim, score)
        rows.append(f"| {dim.capitalize()} | {score} | {descriptor} |")

    table = "\n".join(rows)
    return (
        f"**Leader:** {profile.leader}\n\n"
        f"| Trait | Score (0-100) | What this means for {profile.leader.split()[-1]} |\n"
        f"|---|---|---|\n"
        f"{table}"
    )


def _generic_descriptor(dimension: OceanDimension, score: int) -> str:
    """Fallback gloss when the persona file omits a per-dimension descriptor."""
    pole = "high" if score >= 60 else ("low" if score <= 40 else "moderate")
    generics: dict[OceanDimension, dict[str, str]] = {
        "openness": {
            "high": "Receptive to novel frameworks and unconventional moves.",
            "moderate": "Balances novel ideas against established doctrine.",
            "low": "Trusts established doctrine; suspicious of improvisation.",
        },
        "conscientiousness": {
            "high": "Methodical, long planning horizons, low tolerance for sloppiness.",
            "moderate": "Plans deliberately but tolerates some improvisation.",
            "low": "Operates on instinct and short cycles; light on follow-through.",
        },
        "extraversion": {
            "high": "Public, demonstrative; signals through visible action.",
            "moderate": "Mixes public signaling with quiet back-channel work.",
            "low": "Prefers written channels and back-rooms; rarely freelances publicly.",
        },
        "agreeableness": {
            "high": "Coalition-minded; values long-running cooperative relationships.",
            "moderate": "Cooperative when interests align; transactional otherwise.",
            "low": "Treats relationships as instrumental; punishes slights coldly.",
        },
        "neuroticism": {
            "high": "Reactive under pressure; volatility increases with perceived threat.",
            "moderate": "Calm under steady pressure; reactive when red lines are touched.",
            "low": "Steady under pressure; absorbs setbacks without visible escalation.",
        },
    }
    return generics[dimension][pole]
