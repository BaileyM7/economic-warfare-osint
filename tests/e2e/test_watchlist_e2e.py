"""Watchlist CRUD E2E tests — no external network, no cassettes.

Exercises create/list/update/delete on /api/watchlist plus the
MAX_ACTIVE_PER_USER cap enforcement.
"""

from __future__ import annotations


def _create(client, headers, **overrides):
    payload = {
        "label": "Lockheed Martin",
        "query": "LMT",
        "entity_kind": "ticker",
        "category": "markets",
    }
    payload.update(overrides)
    return client.post("/api/watchlist", json=payload, headers=headers)


def test_watchlist_create_then_list(app_client, auth_headers):
    create = _create(app_client, auth_headers)
    assert create.status_code == 200
    item_id = create.json()["id"]

    listing = app_client.get("/api/watchlist", headers=auth_headers)
    assert listing.status_code == 200
    body = listing.json()
    assert any(it["id"] == item_id for it in body["items"])
    assert "markets" in body["grouped"]


def test_watchlist_create_rejects_invalid_category(app_client, auth_headers):
    resp = _create(app_client, auth_headers, category="not_a_real_category")
    assert resp.status_code == 422


def test_watchlist_create_rejects_invalid_entity_kind(app_client, auth_headers):
    resp = _create(app_client, auth_headers, entity_kind="not_a_kind")
    assert resp.status_code == 422


def test_watchlist_update_label(app_client, auth_headers):
    create = _create(app_client, auth_headers)
    item_id = create.json()["id"]

    upd = app_client.patch(
        f"/api/watchlist/{item_id}",
        json={"label": "Lockheed Martin Corp"},
        headers=auth_headers,
    )
    assert upd.status_code == 200
    assert upd.json()["label"] == "Lockheed Martin Corp"


def test_watchlist_delete_removes_item(app_client, auth_headers):
    create = _create(app_client, auth_headers)
    item_id = create.json()["id"]

    delete = app_client.delete(f"/api/watchlist/{item_id}", headers=auth_headers)
    assert delete.status_code in (200, 204)

    # Implementation is a hard delete — the row is gone from listings.
    listing = app_client.get("/api/watchlist", headers=auth_headers).json()
    assert not any(i["id"] == item_id for i in listing["items"])


def test_watchlist_max_active_cap(app_client, auth_headers):
    """Creating more than MAX_ACTIVE_PER_USER (10) active items must 4xx."""
    from src.routers.watchlist import MAX_ACTIVE_PER_USER

    for i in range(MAX_ACTIVE_PER_USER):
        r = _create(app_client, auth_headers, label=f"Item {i}", query=f"SYM{i}")
        assert r.status_code == 200, f"failed to create item {i}: {r.text}"

    # The (N+1)th must be rejected.
    over = _create(app_client, auth_headers, label="Overflow", query="OVER")
    assert over.status_code in (400, 409, 422)
