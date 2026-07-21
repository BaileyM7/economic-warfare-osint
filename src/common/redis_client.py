"""Shared Redis handle + a capability probe.

Everything Redis-backed in Emissary goes through here, and everything here is
**optional**: with no `REDIS_URL` (the default locally and in CI — see
`tests/conftest.py`) `get_redis()` returns ``None`` and callers take their
existing non-Redis path. Redis is a derived index and a bus; SQLite stays the
system of record.

Why the capability probe exists
-------------------------------
The Render `keyvalue` service (`swarm-redis`) runs **Valkey**, which ships **no
modules** — so `FT.*` (the Redis Query Engine: vector KNN, hybrid search) and
`JSON.*` simply do not exist there. Code that assumes them would fail at demo
time with a confusing `ResponseError: unknown command`. Instead we probe once at
startup and let every semantic feature gate on the result:

    if not capabilities().search:
        ...fall back to the lexical path...

That way pointing `REDIS_URL` at a Valkey instance degrades to today's behaviour
loudly (it's on `/api/health`) rather than breaking. Vector/JSON features need a
real Redis 8.

Nothing in this module raises into a request path. An unreachable Redis logs once
and reports itself as absent — the same fail-open posture the rate limiter already
takes (fragility F16).
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

from src.common.config import config

logger = logging.getLogger(__name__)

# Connect/read timeouts are deliberately short: Redis lives on Render's private
# network (sub-ms in practice), so anything slower is a fault, not slowness, and
# we would rather degrade than hold a request open.
_CONNECT_TIMEOUT_SEC = 2
_SOCKET_TIMEOUT_SEC = 2


@dataclass(frozen=True)
class Capabilities:
    """What the Redis behind `REDIS_URL` can actually do."""

    ping: bool = False  # reachable at all
    search: bool = False  # FT.* — the Query Engine (vector + hybrid search)
    json: bool = False  # JSON.* — RedisJSON documents
    server: str = ""  # "redis" | "valkey" | ""
    version: str = ""
    reason: str = ""  # why unavailable, when it is

    def as_dict(self) -> dict:
        return asdict(self)


_client = None  # cached redis.Redis
_client_probed = False
_capabilities: Capabilities | None = None


def get_redis():
    """Return a shared Redis client, or ``None`` when unset/unreachable.

    Cached for the process lifetime. Never raises — callers treat ``None`` as
    "Redis isn't available, use the fallback path".
    """
    global _client, _client_probed
    if _client_probed:
        return _client

    _client_probed = True
    url = (config.redis_url or "").strip()
    if not url:
        return None

    try:
        import redis as _redis

        client = _redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=_CONNECT_TIMEOUT_SEC,
            socket_timeout=_SOCKET_TIMEOUT_SEC,
        )
        client.ping()
        _client = client
    except Exception as exc:  # noqa: BLE001 — any failure means "no Redis"
        logger.warning("REDIS_URL is set but unreachable (%s); Redis features are off.", exc)
        _client = None
    return _client


def capabilities() -> Capabilities:
    """Probe (once) what this Redis supports. Never raises.

    `search` is the gate for every vector/semantic feature; `json` for the
    document-shaped stores. Both are false on Valkey, which is what Render's
    `keyvalue` service runs.
    """
    global _capabilities
    if _capabilities is not None:
        return _capabilities

    client = get_redis()
    if client is None:
        _capabilities = Capabilities(
            reason="REDIS_URL unset or unreachable" if not config.redis_url else "unreachable"
        )
        return _capabilities

    server, version = _server_identity(client)
    has_search = _probe_search(client)
    has_json = _probe_json(client)

    _capabilities = Capabilities(
        ping=True,
        search=has_search,
        json=has_json,
        server=server,
        version=version,
        reason="" if has_search else f"{server or 'server'} has no Query Engine (FT.*)",
    )
    if not has_search:
        logger.warning(
            "Redis at %s has no Query Engine (FT.*) — server=%s version=%s. "
            "Vector/semantic features will use their lexical fallback. "
            "Render Key Value runs Valkey (no modules); a real Redis 8 is required.",
            _redacted_url(),
            server or "unknown",
            version or "unknown",
        )
    return _capabilities


def _server_identity(client) -> tuple[str, str]:
    """(server_name, version) from INFO — distinguishes Valkey from Redis."""
    try:
        info = client.info("server")
    except Exception:  # noqa: BLE001
        return "", ""
    # Valkey reports valkey_version and (for compatibility) redis_version too,
    # so check the Valkey-specific key first.
    if info.get("valkey_version"):
        return "valkey", str(info["valkey_version"])
    if info.get("redis_version"):
        return "redis", str(info["redis_version"])
    return "", ""


def _probe_search(client) -> bool:
    """True when the Query Engine is present. FT._LIST is read-only and cheap."""
    try:
        client.execute_command("FT._LIST")
        return True
    except Exception:  # noqa: BLE001 — unknown command / module absent
        return False


def _probe_json(client) -> bool:
    """True when RedisJSON is present. Writes then drops a throwaway key."""
    probe_key = "emissary:_probe:json"
    try:
        client.execute_command("JSON.SET", probe_key, "$", '{"ok":true}')
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            client.delete(probe_key)
        except Exception:  # noqa: BLE001
            pass


def _redacted_url() -> str:
    """The Redis URL with any password stripped — safe for logs."""
    url = (config.redis_url or "").strip()
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    _, _, hostpart = rest.rpartition("@")
    return f"{scheme}://***@{hostpart}"


def reset_for_tests() -> None:
    """Drop the cached client + probe so a test can re-point REDIS_URL."""
    global _client, _client_probed, _capabilities
    _client = None
    _client_probed = False
    _capabilities = None
