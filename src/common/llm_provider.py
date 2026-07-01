"""Pluggable text-generation LLM provider (issue #33).

The single seam for "give me a text completion", so the engine can run against
either the Anthropic API or a local OpenAI-compatible endpoint (Ollama / llama.cpp
/ vLLM) without every call site knowing which. This is what enables a local /
air-gapped deployment with a smaller model.

Selection is driven by ``config.llm_provider``:

  * ``anthropic`` (default) — wraps ``anthropic.AsyncAnthropic``. Behaviour for the
    default deployment is unchanged. ``.raw_client`` exposes the underlying client
    for the few call sites that need Anthropic-specific features (streaming,
    tool-use) the generic ``complete()`` doesn't cover.
  * ``openai`` (a.k.a. ``local`` / ``openai_compatible``) — wraps
    ``openai.AsyncOpenAI`` pointed at ``LLM_BASE_URL`` with ``LLM_MODEL``. The
    ``openai`` package is an optional dependency (``uv sync --extra local``); it's
    imported lazily so the default install stays lean.

``get_text_provider()`` returns ``None`` when the selected provider isn't usable
(e.g. anthropic with no key) so callers can degrade gracefully — exactly the
contract the existing generators already rely on.
"""

from __future__ import annotations

import logging

from src.common.config import config

logger = logging.getLogger(__name__)

_OPENAI_ALIASES = {"openai", "openai_compatible", "local"}


class AnthropicProvider:
    """Text provider backed by the Anthropic Messages API."""

    name = "anthropic"
    is_anthropic = True

    def __init__(self, api_key: str, default_model: str, decompose_model: str) -> None:
        import anthropic

        # max_retries mirrors src/llm.py's client (transient 429/5xx resilience).
        self.raw_client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=4)
        self.default_model = default_model
        self.decompose_model = decompose_model

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict],
        max_tokens: int = 1000,
        model: str | None = None,
    ) -> str:
        kwargs: dict = {
            "model": model or self.default_model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        resp = await self.raw_client.messages.create(**kwargs)
        return resp.content[0].text


class OpenAICompatibleProvider:
    """Text provider backed by any OpenAI-compatible endpoint (Ollama, llama.cpp…)."""

    name = "openai"
    is_anthropic = False
    raw_client = None  # no Anthropic client; streaming/tool callers must guard on is_anthropic

    def __init__(
        self, base_url: str, api_key: str, default_model: str, decompose_model: str
    ) -> None:
        self.base_url = base_url
        # Many local servers ignore the key but the SDK requires a non-empty string.
        self.api_key = api_key or "local"
        self.default_model = default_model
        self.decompose_model = decompose_model or default_model
        self._client = None

    def _client_or_load(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:
                raise RuntimeError(
                    "LLM_PROVIDER=openai needs the `openai` package; run `uv sync --extra local`."
                ) from exc
            self._client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)
        return self._client

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict],
        max_tokens: int = 1000,
        model: str | None = None,
    ) -> str:
        client = self._client_or_load()
        # OpenAI carries the system prompt as a leading system message (if any).
        oai_messages = ([{"role": "system", "content": system}] if system else []) + list(messages)
        resp = await client.chat.completions.create(
            model=model or self.default_model,
            max_tokens=max_tokens,
            messages=oai_messages,
        )
        return resp.choices[0].message.content or ""


_provider: AnthropicProvider | OpenAICompatibleProvider | None = None
_provider_built = False


def get_text_provider():
    """Return the configured text provider, or ``None`` if it isn't usable.

    Cached after the first call. Returns ``None`` (rather than raising) when the
    selected provider lacks required config, so callers degrade to "no LLM".
    """
    global _provider, _provider_built
    if _provider_built:
        return _provider

    _provider_built = True
    provider = config.llm_provider
    if provider == "anthropic":
        if config.anthropic_api_key:
            _provider = AnthropicProvider(
                config.anthropic_api_key, config.model, config.decompose_model
            )
    elif provider in _OPENAI_ALIASES:
        if config.llm_base_url and config.llm_model:
            _provider = OpenAICompatibleProvider(
                config.llm_base_url,
                config.llm_api_key,
                config.llm_model,
                config.llm_decompose_model,
            )
        else:
            logger.warning("LLM_PROVIDER=openai but LLM_BASE_URL/LLM_MODEL not set")
    else:
        logger.warning("Unknown LLM_PROVIDER %r; no provider available", provider)
    return _provider


def reset_provider_cache() -> None:
    """Test hook — clear the cached provider so env changes take effect."""
    global _provider, _provider_built
    _provider = None
    _provider_built = False
