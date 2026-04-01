"""Shared async HTTP client with retry and rate-limit awareness."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _sanitise_json(text: str) -> dict[str, Any]:
    """Parse JSON, stripping invalid control characters if the first parse fails.

    Returns an empty dict when the payload is not valid JSON at all
    (e.g. an HTML error page or truly empty body).
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        cleaned = _CONTROL_CHAR_RE.sub("", text).strip()
        if not cleaned:
            return {}
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            raise


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
                text = resp.text.strip()
                if not text:
                    return {}
                return _sanitise_json(text)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429:
                retry_after = int(exc.response.headers.get("Retry-After", 2 ** (attempt + 1)))
                last_exc = exc
                if attempt < retries:
                    await asyncio.sleep(min(retry_after, 30))
                    continue
            if status < 500:
                raise
            last_exc = exc
            if attempt < retries:
                await asyncio.sleep(2 ** attempt)
        except httpx.TransportError as exc:
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
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                raise
            last_exc = exc
            if attempt < retries:
                await asyncio.sleep(2 ** attempt)
        except httpx.TransportError as exc:
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
