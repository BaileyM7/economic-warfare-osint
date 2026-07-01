"""Tests for the pluggable LLM provider (issue #33).

The default Anthropic path must be unchanged; the OpenAI-compatible path must be
selectable by config and validated. We don't hit any network — only provider
*selection* + config validation are exercised (the local SDK isn't installed in CI).
"""

from __future__ import annotations

import pytest

import src.common.config as config_mod
from src.common import llm_provider as lp


@pytest.fixture
def fresh_config(monkeypatch):
    """Build a fresh Config() from patched env and point the provider seam at it.

    Avoids reloading the config module (which would swap the process-wide
    singleton other modules hold); we just construct an isolated Config and patch
    `llm_provider.config` to it. The provider cache is reset on both sides.
    """

    def _build(**env):
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        cfg = config_mod.Config()  # field default_factory lambdas read os.getenv now
        monkeypatch.setattr(lp, "config", cfg)
        lp.reset_provider_cache()
        return cfg

    yield _build
    lp.reset_provider_cache()


def test_default_provider_is_anthropic_when_key_present(fresh_config, monkeypatch):
    fresh_config(LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="sk-test")
    provider = lp.get_text_provider()
    assert provider is not None
    assert provider.is_anthropic is True
    assert provider.name == "anthropic"


def test_anthropic_provider_is_none_without_key(fresh_config):
    fresh_config(LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="")
    assert lp.get_text_provider() is None


def test_openai_provider_selected_with_base_url_and_model(fresh_config):
    cfg = fresh_config(
        LLM_PROVIDER="openai",
        LLM_BASE_URL="http://localhost:11434/v1",
        LLM_MODEL="llama3.1",
        ANTHROPIC_API_KEY="",
    )
    assert cfg.validate() == []  # base_url + model present → valid
    provider = lp.get_text_provider()
    assert provider is not None
    assert provider.is_anthropic is False
    assert provider.name == "openai"
    assert provider.default_model == "llama3.1"
    # decompose falls back to the main model when unset
    assert provider.decompose_model == "llama3.1"
    # no Anthropic client under a local provider (streaming callers must guard)
    assert provider.raw_client is None


def test_openai_provider_missing_config_fails_validation(fresh_config):
    cfg = fresh_config(LLM_PROVIDER="openai", LLM_BASE_URL="", LLM_MODEL="", ANTHROPIC_API_KEY="")
    issues = cfg.validate()
    assert any("LLM_BASE_URL" in i for i in issues)
    assert any("LLM_MODEL" in i for i in issues)
    # and no usable provider is returned
    assert lp.get_text_provider() is None


def test_unknown_provider_flagged_by_validate(fresh_config):
    cfg = fresh_config(LLM_PROVIDER="banana", ANTHROPIC_API_KEY="sk-test")
    assert any("not recognized" in i for i in cfg.validate())


def test_anthropic_key_not_required_for_local_provider(fresh_config):
    # The whole point of #33: a local deployment needs no Anthropic key.
    cfg = fresh_config(
        LLM_PROVIDER="openai",
        LLM_BASE_URL="http://localhost:11434/v1",
        LLM_MODEL="llama3.1",
        ANTHROPIC_API_KEY="",
    )
    assert "ANTHROPIC_API_KEY is required" not in cfg.validate()
