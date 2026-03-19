"""Shared async HTTP client with retry and rate-limit awareness."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx


async def fetch_json(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    retries: int = 2,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """GET a URL and return parsed JSON, with simple retry on transient errors."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < retries:
                await asyncio.sleep(2 ** attempt)
    raise last_exc  # type: ignore[misc]


async def post_json(
    url: str,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    retries: int = 2,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """POST JSON to a URL and return parsed JSON, with simple retry on transient errors."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.post(url, json=json_body, params=params, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < retries:
                await asyncio.sleep(2 ** attempt)
    raise last_exc  # type: ignore[misc]


async def fetch_text(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> str:
    """GET a URL and return raw text."""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        return resp.text
