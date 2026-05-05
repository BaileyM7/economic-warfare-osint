"""Input-validation E2E tests.

Verifies the Phase 2 hardening: oversized payloads, malformed IDs, and
control-character injection are rejected at the Pydantic boundary
(HTTP 422) before they can reach the LLM or DB layer. Also confirms
SQL injection attempts are stored as data, never executed.

These are pure validation tests — no external network, no cassettes.
"""

from __future__ import annotations


def test_coa_create_rejects_empty_name(app_client, auth_headers):
    resp = app_client.post(
        "/api/coa",
        json={"name": "", "description": "x"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_coa_create_rejects_oversized_name(app_client, auth_headers):
    resp = app_client.post(
        "/api/coa",
        json={"name": "a" * 201, "description": "x"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_coa_generate_rejects_oversized_objective(app_client, auth_headers):
    resp = app_client.post(
        "/api/coa/generate",
        json={"objective": "x" * 1001},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_coa_generate_rejects_oversized_analysis_data(app_client, auth_headers):
    # 60KB serialized — exceeds the 50KB JSON budget validator.
    payload = {"analysis_data": {"blob": "y" * 60_000}, "objective": "ok"}
    resp = app_client.post("/api/coa/generate", json=payload, headers=auth_headers)
    assert resp.status_code == 422
    assert "50KB" in resp.text or "exceeds" in resp.text.lower()


def test_coa_create_rejects_invalid_confidence(app_client, auth_headers):
    resp = app_client.post(
        "/api/coa",
        json={"name": "Test", "confidence": 1.5},  # > 1.0
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_briefing_generate_rejects_sql_injection_shaped_id(app_client, auth_headers):
    """coa_id pattern allows only [a-zA-Z0-9_-]. Quotes / semicolons / spaces
    must be rejected by Pydantic before any DB lookup."""
    resp = app_client.post(
        "/api/briefing/generate",
        json={"coa_id": "abc'; DROP TABLE coas; --"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_briefing_create_rejects_oversized_content(app_client, auth_headers):
    resp = app_client.post(
        "/api/briefing",
        json={"title": "T", "content_markdown": "z" * 50_001},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_sql_injection_in_entity_name_is_stored_as_data(app_client, auth_headers):
    """Confirms parameterized queries treat dangerous strings as values, not SQL.

    The Pydantic layer accepts the string (it's within length); the DB layer
    must store it verbatim without executing the SQL fragment.
    """
    payload = {"name": "Acme'; DROP TABLE coas; --", "description": "test"}
    resp = app_client.post("/api/coa", json=payload, headers=auth_headers)
    # Pydantic doesn't reject this (no dangerous-pattern rule on `name`),
    # but it must round-trip safely through the DB.
    assert resp.status_code in (200, 201)
    coa_id = resp.json()["id"]

    # Round-trip: read it back, name must equal what we sent.
    get_resp = app_client.get(f"/api/coa/{coa_id}", headers=auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == payload["name"]

    # And the coas table still exists — `DROP TABLE` was not executed.
    list_resp = app_client.get("/api/coa", headers=auth_headers)
    assert list_resp.status_code == 200


def test_unauthenticated_request_rejected(app_client):
    """Verifies require_auth still gates the endpoint — no token, no service."""
    resp = app_client.post("/api/coa", json={"name": "Test"})
    assert resp.status_code == 401
