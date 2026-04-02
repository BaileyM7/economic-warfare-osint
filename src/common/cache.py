"""Disk-based cache for API responses to respect free-tier rate limits."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from diskcache import Cache

from .config import config

logger = logging.getLogger(__name__)

_cache = Cache(config.cache_dir)

# Sentinel stored in cache to distinguish "API returned empty" from "not cached yet".
# Using a plain None return from get_cached for both cases caused error states to be
# re-fetched on every request even after successful empty responses.
_EMPTY_SENTINEL = "__empty__"


def cache_key(namespace: str, **params: Any) -> str:
    """Generate a deterministic cache key from namespace + params."""
    raw = json.dumps(params, sort_keys=True, default=str)
    # Use full 32-char hex to eliminate birthday-paradox collision risk
    h = hashlib.sha256(f"{namespace}:{raw}".encode()).hexdigest()[:32]
    return f"{namespace}:{h}"


def get_cached(namespace: str, **params: Any) -> Any | None:
    """Retrieve a cached value, or None if missing/expired.

    Returns None for both cache-miss and cached-empty-list, so callers
    treat both the same way (re-fetch or return empty as appropriate).
    The empty sentinel is purely internal bookkeeping.
    """
    key = cache_key(namespace, **params)
    value = _cache.get(key)
    if value is _EMPTY_SENTINEL or value == _EMPTY_SENTINEL:
        return []  # cached empty result — return empty list, not None (avoids re-fetch)
    return value


def set_cached(value: Any, namespace: str, ttl: int | None = None, **params: Any) -> None:
    """Store a value in cache with TTL.

    Never caches error dicts — only caches successful (possibly empty) responses.
    Empty lists are stored with a short TTL to allow retry after transient failures.
    """
    # Do not cache error payloads — they would suppress retries for the full TTL
    if isinstance(value, dict) and "error" in value:
        logger.debug("cache: skipping error payload for namespace=%s", namespace)
        return

    key = cache_key(namespace, **params)
    effective_ttl = ttl or config.cache_ttl_seconds

    if isinstance(value, list) and len(value) == 0:
        # Cache empty list with a short TTL (5 min) to allow retry after transient failures
        _cache.set(key, _EMPTY_SENTINEL, expire=min(effective_ttl, 300))
    else:
        _cache.set(key, value, expire=effective_ttl)
