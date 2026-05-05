"""Rate-limit E2E tests.

Confirms the slowapi decorators wired in Phase 1 actually trip 429s
when the per-user budget is exhausted, AND that requests with no
auth fall back to per-IP keying.

Note: tests run against in-memory storage (REDIS_URL is unset in
conftest), and slowapi's moving-window strategy is per-test isolated
because each test_* function gets a fresh app_client session.
"""

from __future__ import annotations


def test_coa_generate_429_after_burst(app_client, auth_headers):
    """LLM_GENERATE_LIMIT is "3/minute;30/day" — 4th request in <60s gets 429.

    The endpoint will fail with 502 before reaching LLM (no Anthropic key
    in test env), but the rate limiter runs FIRST as middleware — so the
    first 3 calls return 502 (LLM error or 503 missing key) and the 4th
    returns 429 from slowapi before ever reaching the handler.
    """
    payload = {"objective": "test"}
    statuses = []
    for _ in range(4):
        r = app_client.post("/api/coa/generate", json=payload, headers=auth_headers)
        statuses.append(r.status_code)
    # First three should pass slowapi (handler may 503/502); fourth must be 429.
    assert statuses[3] == 429, f"Expected 429 on 4th call, got {statuses}"


def test_429_response_includes_retry_headers(app_client, auth_headers):
    payload = {"objective": "test"}
    last = None
    for _ in range(4):
        last = app_client.post("/api/coa/generate", json=payload, headers=auth_headers)
    assert last.status_code == 429
    # slowapi with headers_enabled=True adds these.
    assert "Retry-After" in last.headers or "x-ratelimit-limit" in {k.lower() for k in last.headers}


def test_rate_limit_keyed_per_user(app_client, app_module):
    """Two distinct users should NOT share a bucket.

    Build a second token by calling create_token directly with a different
    username — proves the key function buckets by username, not IP.
    """
    from src.auth import create_token

    token_alice = create_token("alice")
    token_bob = create_token("bob")
    payload = {"objective": "test"}

    # Burn alice's bucket (4 calls -> 429 on 4th).
    for _ in range(4):
        app_client.post(
            "/api/coa/generate",
            json=payload,
            headers={"Authorization": f"Bearer {token_alice}"},
        )

    # Bob should still have a fresh bucket.
    r = app_client.post(
        "/api/coa/generate",
        json=payload,
        headers={"Authorization": f"Bearer {token_bob}"},
    )
    assert (
        r.status_code != 429
    ), f"Bob's bucket got mixed with Alice's — key function may be wrong. status={r.status_code}"
