"""Tests for team-wide collection priorities (issue #32).

Covers the admin CRUD surface (reads = any analyst, writes = admin only), the
matching/boost seam, and the no-op guarantee for the risk-feed integration: with
no priorities configured, feed ordering is identical to before this feature.
"""

from __future__ import annotations

import pytest

from src.common import priorities as pr


@pytest.fixture
def admin_headers(app_client, monkeypatch) -> dict[str, str]:
    """Promote 'tester' to admin and return bearer headers (mirrors notifications tests)."""
    monkeypatch.setenv("EMISSARY_ADMIN_USERS", "tester:testpass")
    resp = app_client.post("/api/auth/login", json={"username": "tester", "password": "testpass"})
    resp.raise_for_status()
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# --- Seam / matching --------------------------------------------------------


def test_text_boost_is_zero_without_priorities(app_client):
    # Fresh DB, no priorities → boost is always 0 (the no-op guarantee).
    assert pr.text_boost("Huawei sanctioned in China semiconductors") == 0.0


def test_upsert_and_text_boost_matches_case_insensitively(app_client):
    pr.upsert_priority("company", "Huawei", weight=5.0)
    pr.upsert_priority("country", "CN", weight=2.0)
    pmap = pr.priority_map()
    # company key present in text → its (higher) weight wins
    assert pr.text_boost("OFAC adds HUAWEI affiliate to SDN", pmap) == 5.0
    # only the country key present
    assert pr.text_boost("Entity based in cn region", pmap) == 2.0
    # nothing matches
    assert pr.text_boost("Unrelated headline", pmap) == 0.0


def test_upsert_is_idempotent_on_level_key(app_client):
    _, created1 = pr.upsert_priority("sector", "semiconductors", weight=1.0)
    _, created2 = pr.upsert_priority("sector", "semiconductors", weight=9.0)
    assert created1 is True and created2 is False
    items = pr.list_priorities(level="sector")
    assert len(items) == 1 and items[0]["weight"] == 9.0


# --- API: auth matrix -------------------------------------------------------


def test_read_allowed_for_any_analyst(app_client, auth_headers):
    r = app_client.get("/api/priorities", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"priorities": [], "count": 0}


def test_write_forbidden_for_non_admin(app_client, auth_headers):
    r = app_client.post(
        "/api/priorities",
        json={"level": "company", "key": "Huawei", "weight": 3.0},
        headers=auth_headers,
    )
    assert r.status_code == 403


def test_admin_can_set_and_delete_priority(app_client, admin_headers):
    r = app_client.post(
        "/api/priorities",
        json={"level": "country", "key": "CN", "weight": 4.0, "label": "China"},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True
    pid = body["priority"]["id"]

    # visible to a normal analyst (team-wide)
    listing = app_client.get("/api/priorities", headers=admin_headers).json()
    assert listing["count"] == 1

    r = app_client.delete(f"/api/priorities/{pid}", headers=admin_headers)
    assert r.status_code == 200
    assert app_client.get("/api/priorities", headers=admin_headers).json()["count"] == 0


def test_invalid_level_rejected(app_client, admin_headers):
    r = app_client.post(
        "/api/priorities",
        json={"level": "galaxy", "key": "x"},
        headers=admin_headers,
    )
    assert r.status_code == 400


# --- Risk-feed integration --------------------------------------------------


def test_sort_items_is_noop_without_priorities(app_client):
    from src.routers.risk_feed import _sort_items

    items = [
        {"id": "a", "severity": "low", "category": "markets", "fetched_at": "2026-01-01"},
        {
            "id": "b",
            "severity": "high",
            "category": "company_sanctions",
            "fetched_at": "2026-01-02",
        },
    ]
    # high severity sorts first, exactly as before #32
    assert [i["id"] for i in _sort_items(items)] == ["b", "a"]


def test_sort_items_floats_prioritized_entity_to_top(app_client):
    from src.routers.risk_feed import _sort_items

    pr.upsert_priority("company", "Huawei", weight=10.0)
    items = [
        {
            "id": "a",
            "severity": "high",
            "category": "x",
            "headline": "OFAC update",
            "entity": "Acme",
        },
        {
            "id": "b",
            "severity": "low",
            "category": "x",
            "headline": "Huawei affiliate added",
            "entity": "Huawei Tech",
        },
    ]
    out = _sort_items(items)
    # Despite lower severity, the prioritized Huawei item leads, and is badged.
    assert out[0]["id"] == "b"
    assert out[0]["priority_weight"] == 10.0
