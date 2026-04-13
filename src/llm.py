"""Shared Anthropic LLM client singleton — avoids circular imports between api.py and routers."""

from __future__ import annotations

import anthropic
from src.common.config import config

_anthropic_client: anthropic.AsyncAnthropic | None = None


def get_anthropic_client() -> anthropic.AsyncAnthropic | None:
    global _anthropic_client
    if _anthropic_client is None and config.anthropic_api_key:
        _anthropic_client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)
    return _anthropic_client
