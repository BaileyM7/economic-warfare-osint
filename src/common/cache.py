"""Disk-based cache for API responses to respect free-tier rate limits."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from diskcache import Cache

from .config import config

_cache = Cache(config.cache_dir)


def cache_key(namespace: str, **params: Any) -> str:
    """Generate a deterministic cache key from namespace + params."""
    raw = json.dumps(params, sort_keys=True, default=str)
    h = hashlib.sha256(f"{namespace}:{raw}".encode()).hexdigest()[:16]
    return f"{namespace}:{h}"


def get_cached(namespace: str, **params: Any) -> Any | None:
    """Retrieve a cached value, or None if missing/expired."""
    key = cache_key(namespace, **params)
    return _cache.get(key)


def set_cached(value: Any, namespace: str, ttl: int | None = None, **params: Any) -> None:
    """Store a value in cache with TTL."""
    key = cache_key(namespace, **params)
    _cache.set(key, value, expire=ttl or config.cache_ttl_seconds)
