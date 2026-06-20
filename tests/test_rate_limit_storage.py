"""The rate limiter must fail OPEN when REDIS_URL is unreachable.

slowapi's middleware path mishandles a storage ConnectionError (it assumes a
RateLimitExceeded and reads `.detail`), turning a Redis blip into a 500 on every
request. _storage_uri() probes Redis at startup and falls back to in-memory so
the app stays available — this also stops a developer's prod-shaped .env from
500-ing local runs.
"""

from __future__ import annotations

from src.common.rate_limit import _storage_uri


def test_unreachable_redis_falls_back_to_memory(monkeypatch):
    # Port 6399 has nothing listening → connection refused → fall back.
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399")
    assert _storage_uri() == "memory://"


def test_blank_redis_url_is_memory(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "")
    assert _storage_uri() == "memory://"
