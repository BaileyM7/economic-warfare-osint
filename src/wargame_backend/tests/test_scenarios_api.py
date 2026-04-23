"""Tests for the Scenarios API (POST, GET list, GET single)."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
async def test_list_scenarios_filter_by_status(
    client: AsyncClient, scenario: Scenario
) -> None:
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
