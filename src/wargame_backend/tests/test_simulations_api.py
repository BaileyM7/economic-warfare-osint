"""Tests for the Simulations API (POST create, GET status, POST abort).

All tests use the NullSimRunner injected via the conftest client fixture.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from wargame_backend.app.db.models import Scenario, Simulation


# ---------------------------------------------------------------------------
# POST /api/simulations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_simulation_returns_202(
    client: AsyncClient, scenario: Scenario
) -> None:
    """Happy path: creates a simulation for an existing scenario → HTTP 202."""
    payload = {"scenario_id": str(scenario.id)}
    response = await client.post("/api/simulations", json=payload)
    assert response.status_code == 202, response.text

    body = response.json()
    assert body["error"] is None
    data = body["data"]
    assert uuid.UUID(data["id"])
    assert data["scenario_id"] == str(scenario.id)
    assert data["status"] == "pending"
    assert data["ws_url"] == f"/ws/simulations/{data['id']}"


@pytest.mark.asyncio
async def test_create_simulation_default_max_turns(
    client: AsyncClient, scenario: Scenario
) -> None:
    """max_turns defaults to 20 when not supplied."""
    response = await client.post(
        "/api/simulations", json={"scenario_id": str(scenario.id)}
    )
    assert response.status_code == 202
    assert response.json()["data"]["max_turns"] == 20


@pytest.mark.asyncio
async def test_create_simulation_custom_max_turns(
    client: AsyncClient, scenario: Scenario
) -> None:
    """Custom max_turns is persisted."""
    response = await client.post(
        "/api/simulations",
        json={"scenario_id": str(scenario.id), "max_turns": 5},
    )
    assert response.status_code == 202
    assert response.json()["data"]["max_turns"] == 5


@pytest.mark.asyncio
async def test_create_simulation_scenario_not_found(client: AsyncClient) -> None:
    """Non-existent scenario_id → 404."""
    response = await client.post(
        "/api/simulations", json={"scenario_id": str(uuid.uuid4())}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_simulation_invalid_scenario_id(client: AsyncClient) -> None:
    """Malformed UUID → 422."""
    response = await client.post(
        "/api/simulations", json={"scenario_id": "not-a-uuid"}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_simulation_conflict_on_active(
    client: AsyncClient, scenario: Scenario
) -> None:
    """Second simulation for the same scenario → 409 while first is active."""
    first = await client.post(
        "/api/simulations", json={"scenario_id": str(scenario.id)}
    )
    assert first.status_code == 202

    second = await client.post(
        "/api/simulations", json={"scenario_id": str(scenario.id)}
    )
    assert second.status_code == 409


# ---------------------------------------------------------------------------
# GET /api/simulations/{id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_simulation(client: AsyncClient, scenario: Scenario) -> None:
    """GET returns the simulation state."""
    create_resp = await client.post(
        "/api/simulations", json={"scenario_id": str(scenario.id)}
    )
    sim_id = create_resp.json()["data"]["id"]

    get_resp = await client.get(f"/api/simulations/{sim_id}")
    assert get_resp.status_code == 200

    data = get_resp.json()["data"]
    assert data["id"] == sim_id
    assert data["scenario_id"] == str(scenario.id)


@pytest.mark.asyncio
async def test_get_simulation_not_found(client: AsyncClient) -> None:
    """Non-existent simulation → 404."""
    response = await client.get(f"/api/simulations/{uuid.uuid4()}")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/simulations/{id}/abort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_abort_simulation(client: AsyncClient, scenario: Scenario) -> None:
    """Aborting a pending simulation sets status to aborted."""
    create_resp = await client.post(
        "/api/simulations", json={"scenario_id": str(scenario.id)}
    )
    assert create_resp.status_code == 202
    sim_id = create_resp.json()["data"]["id"]

    abort_resp = await client.post(f"/api/simulations/{sim_id}/abort")
    assert abort_resp.status_code == 200
    assert abort_resp.json()["data"]["status"] == "aborted"


@pytest.mark.asyncio
async def test_abort_simulation_not_found(client: AsyncClient) -> None:
    """Aborting a non-existent simulation → 404."""
    response = await client.post(f"/api/simulations/{uuid.uuid4()}/abort")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_abort_completed_simulation_returns_409(
    client: AsyncClient, scenario: Scenario, db
) -> None:
    """Aborting an already-completed simulation → 409."""
    from sqlalchemy import select as sa_select

    from wargame_backend.app.db.models import SimulationStatus

    # Create sim then manually mark it completed
    create_resp = await client.post(
        "/api/simulations", json={"scenario_id": str(scenario.id)}
    )
    sim_id = create_resp.json()["data"]["id"]

    # Force status to completed in DB
    result = await db.execute(
        sa_select(Simulation).where(Simulation.id == uuid.UUID(sim_id))
    )
    sim = result.scalar_one()
    sim.status = SimulationStatus.completed
    await db.commit()

    abort_resp = await client.post(f"/api/simulations/{sim_id}/abort")
    assert abort_resp.status_code == 409
