"""Shared Anthropic LLM client singleton — avoids circular imports between api.py and routers."""

from __future__ import annotations

import anthropic
from src.common.config import config

_anthropic_client: anthropic.AsyncAnthropic | None = None

# The Anthropic SDK retries 429/408/5xx (including 529 "Overloaded") with
# exponential backoff. We bump this above the default of 2 so transient
# capacity spikes don't surface as hard errors for the user.
_MAX_RETRIES = 4


def get_anthropic_client() -> anthropic.AsyncAnthropic | None:
    global _anthropic_client
    if _anthropic_client is None and config.anthropic_api_key:
        _anthropic_client = anthropic.AsyncAnthropic(
            api_key=config.anthropic_api_key,
            max_retries=_MAX_RETRIES,
        )
    return _anthropic_client
