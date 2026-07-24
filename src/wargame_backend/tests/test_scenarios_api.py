"""Tests for the Scenarios API (POST, GET list, GET single)."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from wargame_backend.app.db.models import Scenario


# ---------------------------------------------------------------------------
# POST /api/scenarios
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_scenario_returns_201(client: AsyncClient) -> None:
    """Happy path: valid body returns HTTP 201 with the created scenario."""
    payload = {
        "title": "China–Taiwan Blockade 2027",
        "description": "China initiates a quarantine blockade.",
        "country_ids": ["CHN", "TWN", "USA"],
        "initial_conditions": {
            "posture_overrides": {"CHN": "aggressive"},
            "seed_events": [],
        },
    }
    response = await client.post("/api/scenarios", json=payload)
    assert response.status_code == 201, response.text

    body = response.json()
    assert body["error"] is None
    data = body["data"]
    assert data["title"] == "China–Taiwan Blockade 2027"
    assert data["status"] == "ready"
    assert set(data["country_ids"]) == {"CHN", "TWN", "USA"}
    assert uuid.UUID(data["id"])  # valid UUID


@pytest.mark.asyncio
async def test_create_scenario_normalises_iso3(client: AsyncClient) -> None:
    """ISO-3 codes should be uppercased and deduplicated."""
    payload = {
        "title": "Normalisation Test",
        "country_ids": ["chn", "twn", "CHN"],  # lowercase + duplicate
    }
    response = await client.post("/api/scenarios", json=payload)
    assert response.status_code == 201

    data = response.json()["data"]
    assert data["country_ids"] == ["CHN", "TWN"]  # deduped + uppercased


@pytest.mark.asyncio
async def test_create_scenario_requires_title(client: AsyncClient) -> None:
    """Missing title should return 422."""
    payload = {"country_ids": ["CHN", "USA"]}
    response = await client.post("/api/scenarios", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_scenario_requires_two_countries(client: AsyncClient) -> None:
    """Scenarios with fewer than 2 countries should return 422."""
    payload = {"title": "One Country", "country_ids": ["CHN"]}
    response = await client.post("/api/scenarios", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_scenario_empty_country_ids(client: AsyncClient) -> None:
    """Empty country_ids list should return 422."""
    payload = {"title": "Empty", "country_ids": []}
    response = await client.post("/api/scenarios", json=payload)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/scenarios
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_scenarios_returns_paginated_response(
    client: AsyncClient, scenario: Scenario
) -> None:
    """List endpoint returns items + pagination metadata."""
    response = await client.get("/api/scenarios")
    assert response.status_code == 200

    body = response.json()
    assert body["error"] is None
    data = body["data"]
    assert "items" in data
    assert "total" in data
    assert "limit" in data
    assert "offset" in data
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_list_scenarios_filter_by_status(client: AsyncClient, scenario: Scenario) -> None:
    """Status filter should return only matching scenarios."""
    response = await client.get("/api/scenarios?status=ready")
    assert response.status_code == 200

    items = response.json()["data"]["items"]
    for item in items:
        assert item["status"] == "ready"


@pytest.mark.asyncio
async def test_list_scenarios_invalid_status_returns_400(client: AsyncClient) -> None:
    """An invalid status value should return HTTP 400."""
    response = await client.get("/api/scenarios?status=invalid_status")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_list_scenarios_pagination(client: AsyncClient) -> None:
    """Limit and offset parameters should be respected."""
    # Create 3 scenarios
    for i in range(3):
        await client.post(
            "/api/scenarios",
            json={"title": f"Paging Test {i}", "country_ids": ["CHN", "USA"]},
        )

    page1 = (await client.get("/api/scenarios?limit=2&offset=0")).json()["data"]
    page2 = (await client.get("/api/scenarios?limit=2&offset=2")).json()["data"]

    assert len(page1["items"]) <= 2
    assert page1["limit"] == 2
    assert page2["offset"] == 2


# ---------------------------------------------------------------------------
# GET /api/scenarios/{id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_scenario_by_id(client: AsyncClient, scenario: Scenario) -> None:
    """Fetching a scenario by valid UUID returns the full object."""
    response = await client.get(f"/api/scenarios/{scenario.id}")
    assert response.status_code == 200

    data = response.json()["data"]
    assert data["id"] == str(scenario.id)
    assert data["title"] == scenario.title


@pytest.mark.asyncio
async def test_get_scenario_not_found(client: AsyncClient) -> None:
    """A non-existent UUID should return HTTP 404."""
    missing = uuid.uuid4()
    response = await client.get(f"/api/scenarios/{missing}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_scenario_invalid_uuid(client: AsyncClient) -> None:
    """A malformed UUID path parameter should return 422."""
    response = await client.get("/api/scenarios/not-a-uuid")
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/scenarios/extract-events
# ---------------------------------------------------------------------------


class _FakeSettings:
    anthropic_api_key = "sk-ant-test"
    extract_model = "claude-haiku-4-5"


class _FakeBlock:
    type = "tool_use"

    def __init__(self, tool_input: dict) -> None:
        self.input = tool_input


class _FakeResponse:
    def __init__(self, tool_input: dict) -> None:
        self.content = [_FakeBlock(tool_input)]


class _FakeAnthropicClient:
    def __init__(self, tool_input: dict) -> None:
        self._tool_input = tool_input
        self.messages = self

    async def create(self, **kwargs) -> _FakeResponse:
        return _FakeResponse(self._tool_input)


@pytest.mark.asyncio
async def test_extract_events_no_key_returns_fallback(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without an Anthropic key the endpoint degrades to the empty stub."""
    from wargame_backend.app.api import scenarios as scenarios_mod

    class _NoKeySettings:
        anthropic_api_key = ""
        extract_model = "claude-haiku-4-5"

    monkeypatch.setattr(scenarios_mod, "get_settings", lambda: _NoKeySettings())

    response = await client.post(
        "/api/scenarios/extract-events",
        json={"description": "China blockades Taiwan.", "country_ids": []},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["is_stub"] is True
    assert data["source"] == "fallback"
    assert data["seed_events"] == []


@pytest.mark.asyncio
async def test_extract_events_llm_success(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A well-formed tool_use payload maps onto the response contract."""
    from wargame_backend.app.api import scenarios as scenarios_mod

    tool_input = {
        "seed_events": [
            {
                "actor_country": "CHN",
                "target_country": "TWN",
                "domain": "kinetic_limited",
                "action_type": "naval_blockade",
                "rationale": "China initiates a quarantine blockade of the strait.",
                "payload": {"location": "Taiwan Strait"},
                "escalation_rung": 3,
            },
            {
                # Unsupported actor — must be dropped, flipping source to partial.
                "actor_country": "GBR",
                "domain": "diplomatic",
                "action_type": "statement",
                "rationale": "UK issues a statement.",
            },
        ],
        "selected_countries": [
            {"iso3": "CHN", "relevance_score": 1.0, "rationale": "Aggressor."},
            {"iso3": "TWN", "relevance_score": 0.95, "rationale": "Primary target."},
        ],
        "posture_overrides": {"CHN": "aggressive", "GBR": "neutral", "TWN": "not-a-posture"},
    }

    monkeypatch.setattr(scenarios_mod, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(
        scenarios_mod, "_anthropic_client", lambda: _FakeAnthropicClient(tool_input)
    )

    response = await client.post(
        "/api/scenarios/extract-events",
        json={"description": "China blockades Taiwan.", "country_ids": ["CHN", "TWN"]},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["is_stub"] is False
    assert data["source"] == "partial_fallback"  # the GBR event was dropped
    assert len(data["seed_events"]) == 1
    assert data["seed_events"][0]["actor_country"] == "CHN"
    assert [c["iso3"] for c in data["selected_countries"]] == ["CHN", "TWN"]
    assert data["posture_overrides"] == {"CHN": "aggressive"}


@pytest.mark.asyncio
async def test_extract_events_llm_error_returns_fallback(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any LLM failure degrades to the fallback rather than a 500."""
    from wargame_backend.app.api import scenarios as scenarios_mod

    class _ExplodingClient:
        def __init__(self) -> None:
            self.messages = self

        async def create(self, **kwargs):
            raise RuntimeError("api down")

    monkeypatch.setattr(scenarios_mod, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(scenarios_mod, "_anthropic_client", lambda: _ExplodingClient())

    response = await client.post(
        "/api/scenarios/extract-events",
        json={"description": "China blockades Taiwan.", "country_ids": []},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["is_stub"] is True
    assert data["source"] == "fallback"
