"""Unit tests for :mod:`ai.agents.leader_profile`."""

from __future__ import annotations

import pytest

from wargame_ai.agents.leader_profile import (
    LeaderProfile,
    LeaderProfileError,
    OceanScores,
    parse_persona_file,
    render_leader_profile_block,
)


class TestParsePersonaFile:
    def test_well_formed_frontmatter_parses(self) -> None:
        text = (
            "---\n"
            "leader: Xi Jinping\n"
            "ocean:\n"
            "  openness: 35\n"
            "  conscientiousness: 80\n"
            "  extraversion: 30\n"
            "  agreeableness: 25\n"
            "  neuroticism: 55\n"
            "ocean_descriptors:\n"
            "  openness: Suspicious of novel frameworks; trusts Party doctrine.\n"
            "  conscientiousness: Long planning horizons; lets situations ripen.\n"
            "---\n"
            "# People's Republic of China (CHN)\n"
            "\n"
            "## Head of Government\n"
        )
        profile, body = parse_persona_file(text)

        assert profile is not None
        assert profile.leader == "Xi Jinping"
        assert profile.ocean.openness == 35
        assert profile.ocean.conscientiousness == 80
        assert profile.ocean.neuroticism == 55
        assert "Suspicious of novel frameworks" in profile.ocean_descriptors["openness"]
        # Body strips the frontmatter and its trailing newline
        assert body.startswith("# People's Republic of China")
        assert "---" not in body.split("\n")[0]

    def test_no_frontmatter_returns_none_and_original_text(self) -> None:
        text = "# People's Republic of China (CHN)\n\nNo frontmatter here.\n"
        profile, body = parse_persona_file(text)
        assert profile is None
        assert body == text

    def test_out_of_range_score_raises(self) -> None:
        text = (
            "---\n"
            "leader: Test Leader\n"
            "ocean:\n"
            "  openness: 150\n"
            "  conscientiousness: 50\n"
            "  extraversion: 50\n"
            "  agreeableness: 50\n"
            "  neuroticism: 50\n"
            "---\n"
            "body\n"
        )
        with pytest.raises(LeaderProfileError):
            parse_persona_file(text)

    def test_missing_required_dimension_raises(self) -> None:
        text = (
            "---\n"
            "leader: Test Leader\n"
            "ocean:\n"
            "  openness: 50\n"
            "  conscientiousness: 50\n"
            "  extraversion: 50\n"
            "  agreeableness: 50\n"
            # neuroticism omitted
            "---\n"
            "body\n"
        )
        with pytest.raises(LeaderProfileError):
            parse_persona_file(text)

    def test_invalid_yaml_raises(self) -> None:
        text = "---\nleader: [unterminated\n---\nbody\n"
        with pytest.raises(LeaderProfileError):
            parse_persona_file(text)


class TestRenderLeaderProfileBlock:
    def test_none_returns_placeholder(self) -> None:
        assert render_leader_profile_block(None) == "(no structured leader profile)"

    def test_renders_table_with_authored_descriptors(self) -> None:
        profile = LeaderProfile(
            leader="Xi Jinping",
            ocean=OceanScores(
                openness=35,
                conscientiousness=80,
                extraversion=30,
                agreeableness=25,
                neuroticism=55,
            ),
            ocean_descriptors={
                "openness": "Suspicious of novel frameworks.",
                "conscientiousness": "Lets situations ripen.",
                "extraversion": "Prefers back-channels.",
                "agreeableness": "Punishes slights coldly.",
                "neuroticism": "Volatile when legitimacy is touched.",
            },
        )
        block = render_leader_profile_block(profile)
        assert "Xi Jinping" in block
        assert "| Openness | 35 |" in block
        assert "Suspicious of novel frameworks." in block
        assert "Lets situations ripen." in block
        # Author-provided descriptor used verbatim
        assert "Volatile when legitimacy is touched." in block

    def test_falls_back_to_generic_when_descriptor_missing(self) -> None:
        profile = LeaderProfile(
            leader="Test Leader",
            ocean=OceanScores(
                openness=20,
                conscientiousness=50,
                extraversion=85,
                agreeableness=50,
                neuroticism=50,
            ),
        )
        block = render_leader_profile_block(profile)
        # Low openness → "Trusts established doctrine" generic
        assert "Trusts established doctrine" in block
        # High extraversion → "Public, demonstrative" generic
        assert "Public, demonstrative" in block
