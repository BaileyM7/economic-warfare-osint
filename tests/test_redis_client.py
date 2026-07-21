"""The Redis seam must degrade, never raise.

Three states matter, and all three must be non-fatal:
  1. No REDIS_URL          -> get_redis() is None, no capabilities.
  2. REDIS_URL unreachable -> same, plus a warning (never an exception).
  3. Reachable but no FT.* -> `search` is False, so every semantic feature takes
                              its lexical fallback. This is the Valkey case, i.e.
                              exactly what Render's managed `keyvalue` service is,
                              and the failure we most need to be loud about.

These are the guardrails for the whole Redis migration: if `capabilities().search`
is ever allowed to be assumed True, we ship a demo that breaks on the real
infrastructure.
"""

from __future__ import annotations

import pytest

from src.common import redis_client


@pytest.fixture(autouse=True)
def _reset():
    """Each test re-probes; the module caches its client for the process."""
    redis_client.reset_for_tests()
    yield
    redis_client.reset_for_tests()


def _set_url(monkeypatch, url: str) -> None:
    """Point the config singleton's redis_url at `url` (it's read at import)."""
    monkeypatch.setattr(redis_client.config, "redis_url", url, raising=False)


def _free_port() -> int:
    """A port with nothing on it, so 'unreachable' is guaranteed, not assumed."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_no_redis_url_is_not_an_error(monkeypatch):
    _set_url(monkeypatch, "")
    assert redis_client.get_redis() is None

    caps = redis_client.capabilities()
    assert caps.ping is False
    assert caps.search is False
    assert caps.json is False
    assert caps.reason  # explains itself rather than being silently empty


def test_unreachable_redis_degrades_instead_of_raising(monkeypatch):
    # 6399 is the repo's agreed "nothing listens here" port (see also
    # tests/test_rate_limit_storage.py). Assert that rather than assume it: a
    # stray container bound there would otherwise turn this into a silent
    # false-pass, testing nothing at all.
    port = _free_port()
    _set_url(monkeypatch, f"redis://127.0.0.1:{port}")

    assert redis_client.get_redis() is None  # must not raise
    caps = redis_client.capabilities()
    assert caps.ping is False
    assert caps.search is False


def test_capabilities_are_probed_once(monkeypatch):
    _set_url(monkeypatch, "")
    first = redis_client.capabilities()
    second = redis_client.capabilities()
    assert first is second  # cached for the process lifetime


def test_redacted_url_hides_the_password(monkeypatch):
    _set_url(monkeypatch, "redis://:sup3rs3cret@red-abc123:6379/0")
    redacted = redis_client._redacted_url()
    assert "sup3rs3cret" not in redacted
    assert "red-abc123:6379" in redacted


def test_compose_redis_url_from_hostport(monkeypatch):
    """A Render private service exposes host/port, not a connectionString."""
    from src.common.config import _compose_redis_url

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("REDIS_HOSTPORT", "emissary-redis:6379")
    monkeypatch.setenv("REDIS_PASSWORD", "pw")
    assert _compose_redis_url() == "redis://:pw@emissary-redis:6379/0"

    # An explicit REDIS_URL always wins (local dev, the existing keyvalue, CI).
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    assert _compose_redis_url() == "redis://localhost:6379/0"


def test_compose_redis_url_empty_when_nothing_set(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_HOSTPORT", raising=False)
    monkeypatch.delenv("REDIS_PASSWORD", raising=False)

    from src.common.config import _compose_redis_url

    assert _compose_redis_url() == ""


def test_valkey_reports_no_search_capability(monkeypatch):
    """A reachable server WITHOUT the Query Engine must report search=False.

    This is the Render Key Value (Valkey) case. If this ever returns True, the
    semantic features would issue FT.* against a server that has no such command
    and blow up at runtime instead of falling back.
    """

    class _FakeValkey:
        def info(self, _section):
            return {"valkey_version": "8.0.1", "redis_version": "7.2.4"}

        def execute_command(self, name, *args):
            raise Exception(f"unknown command '{name}'")

        def delete(self, *_keys):
            return 0

    monkeypatch.setattr(redis_client, "_client", _FakeValkey())
    monkeypatch.setattr(redis_client, "_client_probed", True)

    caps = redis_client.capabilities()
    assert caps.ping is True
    assert caps.server == "valkey"
    assert caps.search is False
    assert caps.json is False
    assert "Query Engine" in caps.reason


def test_redis8_reports_search_and_json(monkeypatch):
    class _FakeRedis8:
        def info(self, _section):
            return {"redis_version": "8.2.0"}

        def execute_command(self, name, *args):
            return [] if name == "FT._LIST" else "OK"

        def delete(self, *_keys):
            return 1

    monkeypatch.setattr(redis_client, "_client", _FakeRedis8())
    monkeypatch.setattr(redis_client, "_client_probed", True)

    caps = redis_client.capabilities()
    assert caps.ping is True
    assert caps.server == "redis"
    assert caps.version == "8.2.0"
    assert caps.search is True
    assert caps.json is True
    assert caps.reason == ""
