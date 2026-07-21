"""Configuration loading from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
_project_root = Path(__file__).resolve().parent.parent.parent
load_dotenv(_project_root / ".env")


# Output width per Voyage model, verified against the live API. Having this map is
# what stops EMBEDDING_MODEL and EMBEDDING_DIMS drifting into an impossible pair
# (this repo shipped voyage-3 + 1536 for months; voyage-3 emits 1024).
#
# Duplicated in src/wargame_ai/memory/embeddings.py on purpose: the wargame is an
# optional extra (`uv sync --extra wargame`) that this module must not import, and
# it deliberately never imports src.* either. Update both.
_EMBED_MODEL_DIMS: dict[str, int] = {
    "voyage-3": 1024,
    "voyage-3-lite": 512,
    "voyage-3-large": 1024,
    "voyage-large-2": 1536,
    "voyage-code-3": 1024,
    "voyage-finance-2": 1024,
    "voyage-law-2": 1024,
    "voyage-multilingual-2": 1024,
}
# Matches the deployed EMBEDDING_MODEL and the wargame's agent_memory column width.
_DEFAULT_EMBED_MODEL = "voyage-large-2"


def _resolve_embedding_dims() -> int:
    """Vector width for the configured model.

    An explicit ``EMBEDDING_DIMS`` always wins (needed for models newer than this
    map). Otherwise it's derived from ``EMBEDDING_MODEL`` so the two can't silently
    disagree. Either way ``src/common/embeddings.py`` asserts the result against a
    live response before any vector is written.
    """
    explicit = os.getenv("EMBEDDING_DIMS", "").strip()
    if explicit:
        return int(explicit)
    model = os.getenv("EMBEDDING_MODEL", _DEFAULT_EMBED_MODEL).strip()
    return _EMBED_MODEL_DIMS.get(model, _EMBED_MODEL_DIMS[_DEFAULT_EMBED_MODEL])


def _compose_redis_url() -> str:
    """The Redis URL, from `REDIS_URL` or composed from host/port + password.

    A Render **private service** (which is how we get a real Redis 8 with the
    Query Engine — the managed `keyvalue` service is Valkey and has no modules)
    exposes `host`/`port`/`hostport` via `fromService`, NOT a `connectionString`
    the way a managed datastore does. And render.yaml cannot interpolate strings,
    so the URL has to be assembled here rather than in the blueprint.

    `REDIS_URL` still wins when set, so local dev, the existing managed keyvalue,
    and CI (which forces it empty) all keep working unchanged.
    """
    explicit = os.getenv("REDIS_URL", "").strip()
    if explicit:
        return explicit

    hostport = os.getenv("REDIS_HOSTPORT", "").strip()
    if not hostport:
        return ""

    password = os.getenv("REDIS_PASSWORD", "").strip()
    auth = f":{password}@" if password else ""
    return f"redis://{auth}{hostport}/0"


@dataclass
class Config:
    """Central configuration — all values come from env vars."""

    # Required: Claude API
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))

    # Free-tier API keys (some sources need registration)
    fred_api_key: str = field(default_factory=lambda: os.getenv("FRED_API_KEY", ""))
    comtrade_api_key: str = field(default_factory=lambda: os.getenv("COMTRADE_API_KEY", ""))
    opensanctions_api_key: str = field(
        default_factory=lambda: os.getenv("OPENSANCTIONS_API_KEY", "")
    )
    trade_gov_api_key: str = field(default_factory=lambda: os.getenv("TRADE_GOV_API_KEY", ""))
    acled_api_key: str = field(default_factory=lambda: os.getenv("ACLED_API_KEY", ""))
    acled_email: str = field(default_factory=lambda: os.getenv("ACLED_EMAIL", ""))
    acled_password: str = field(default_factory=lambda: os.getenv("ACLED_PASSWORD", ""))
    acled_refresh_token: str = field(default_factory=lambda: os.getenv("REFRESH_TOKEN", ""))
    opencorporates_api_key: str = field(
        default_factory=lambda: os.getenv("OPENCORPORATES_API_KEY", "")
    )
    aisstream_api_key: str = field(default_factory=lambda: os.getenv("AISSTREAM_API_KEY", ""))
    aisstream_sample_seconds: int = field(
        default_factory=lambda: int(os.getenv("AISSTREAM_SAMPLE_SECONDS", "300"))
    )

    # Sayari Graph API (entity resolution, traversal, UBO)
    sayari_client_id: str = field(default_factory=lambda: os.getenv("SAYARI_CLIENT_ID", ""))
    sayari_client_secret: str = field(default_factory=lambda: os.getenv("SAYARI_CLIENT_SECRET", ""))

    # Finnhub — primary equity quote/profile source on cloud deployments where
    # Yahoo Finance's anti-bot WAF blocks yfinance with 401 "Invalid Crumb".
    finnhub_api_key: str = field(default_factory=lambda: os.getenv("FINNHUB_API_KEY", ""))

    # No key needed
    # OFAC, OpenSanctions, GLEIF, ICIJ, GDELT, IMF, World Bank, yfinance, SEC EDGAR

    # Cache settings
    cache_dir: str = field(
        default_factory=lambda: os.getenv("CACHE_DIR", str(_project_root / "data" / "cache"))
    )
    cache_ttl_seconds: int = field(
        default_factory=lambda: int(os.getenv("CACHE_TTL_SECONDS", "3600"))
    )

    # Model settings
    model: str = field(
        default_factory=lambda: os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
    )
    # Decomposition (research-plan) model. Defaults to Haiku — planning is a
    # structured task it handles well at ~4x the speed of Sonnet, cutting the
    # decompose phase from ~45s to ~12s. Synthesis stays on `model` (Sonnet).
    decompose_model: str = field(
        default_factory=lambda: os.getenv("CLAUDE_DECOMPOSE_MODEL", "claude-haiku-4-5-20251001")
    )

    # Semantic similarity backend (issue #30). "lexical" (default) is dependency-
    # free and fully offline — a weighted token/trait overlap. "embedding" uses a
    # local sentence-transformers model (install the `similarity` extra); the
    # endpoint falls back to lexical with a note if the dependency is missing.
    similarity_backend: str = field(
        default_factory=lambda: os.getenv("SIMILARITY_BACKEND", "lexical").strip().lower()
    )
    similarity_model: str = field(
        default_factory=lambda: os.getenv(
            "SIMILARITY_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        )
    )

    # --- Embeddings (src/common/embeddings.py) ---
    # The hosted-embedding seam shared by semantic search / semantic cache / agent
    # memory. Same env-var names the wargame already uses, so there is ONE Voyage
    # convention across the repo.
    #
    # No key => embeddings are OFF and every caller falls back to its lexical path.
    # There is deliberately no stub/hash embedder: fake vectors produce confident,
    # plausible, meaningless neighbours, which in an intel tool is how you ship a
    # wrong assessment. Off and honest beats on and wrong.
    #
    # embedding_dims is DERIVED from the model unless explicitly set, because the
    # two defaulting independently is exactly how this repo ended up shipping a
    # voyage-3 + 1536 pair that cannot exist (voyage-3 emits 1024). It is also
    # asserted against the live response at startup rather than trusted.
    voyage_api_key: str = field(default_factory=lambda: os.getenv("VOYAGE_API_KEY", ""))
    embedding_model: str = field(
        default_factory=lambda: os.getenv("EMBEDDING_MODEL", _DEFAULT_EMBED_MODEL).strip()
    )
    embedding_dims: int = field(default_factory=lambda: _resolve_embedding_dims())

    # LLM provider (issue #33) — local-deployment option. "anthropic" (default)
    # uses the Claude API. "openai" targets any OpenAI-compatible endpoint
    # (Ollama / llama.cpp / vLLM / OpenAI) via base URL + model, so the engine
    # can run against a local/smaller model with no Anthropic key.
    llm_provider: str = field(
        default_factory=lambda: os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
    )
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", ""))
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", ""))
    # Decompose model for the local provider; falls back to llm_model if unset.
    llm_decompose_model: str = field(default_factory=lambda: os.getenv("LLM_DECOMPOSE_MODEL", ""))

    # Orchestrator fan-out controls. A single shared semaphore caps total
    # concurrent tool calls across all parallel steps (also bounds the memory
    # spike that OOM'd the 512MB box). `max_tools` caps total agents per plan so
    # a verbose decomposition can't blow up execute time / truncate synthesis.
    orchestrator_max_concurrency: int = field(
        default_factory=lambda: int(os.getenv("ORCH_MAX_CONCURRENCY", "8"))
    )
    orchestrator_max_tools: int = field(
        default_factory=lambda: int(os.getenv("ORCH_MAX_TOOLS", "24"))
    )

    # --- Notifications (Twilio SMS + SendGrid email) ---
    notifications_enabled: bool = field(
        default_factory=lambda: os.getenv("NOTIFICATIONS_ENABLED", "false").lower() == "true"
    )
    twilio_account_sid: str = field(default_factory=lambda: os.getenv("TWILIO_ACCOUNT_SID", ""))
    twilio_auth_token: str = field(default_factory=lambda: os.getenv("TWILIO_AUTH_TOKEN", ""))
    twilio_from_phone: str = field(default_factory=lambda: os.getenv("TWILIO_FROM_PHONE", ""))
    sendgrid_api_key: str = field(default_factory=lambda: os.getenv("SENDGRID_API_KEY", ""))
    newsletter_from_email: str = field(
        default_factory=lambda: os.getenv("NEWSLETTER_FROM_EMAIL", "noreply@emissary.demo")
    )
    newsletter_from_name: str = field(
        default_factory=lambda: os.getenv("NEWSLETTER_FROM_NAME", "Emissary Weekly Brief")
    )
    sms_daily_cap_per_user: int = field(
        default_factory=lambda: int(os.getenv("SMS_DAILY_CAP_PER_USER", "3"))
    )
    notifications_cron_token: str = field(
        default_factory=lambda: os.getenv("NOTIFICATIONS_CRON_TOKEN", "")
    )
    notifications_allowlist: str = field(
        default_factory=lambda: os.getenv("NOTIFICATIONS_ALLOWLIST", "")
    )
    # Local-dev stub: when true, get_twilio_client() returns a fake client
    # that simulates Twilio responses (including magic-number errors) without
    # making real API calls. Requires NOTIFICATIONS_ENABLED=true to take effect.
    # Logs every "would-send" to data/twilio_stub.jsonl. NEVER set in prod.
    twilio_stub_mode: bool = field(
        default_factory=lambda: os.getenv("TWILIO_STUB_MODE", "false").lower() == "true"
    )
    # Local-dev stub for SendGrid: same idea as TWILIO_STUB_MODE. Returns a
    # fake client that records "would-send" entries to data/sendgrid_stub.jsonl
    # and an in-memory list. Requires NOTIFICATIONS_ENABLED=true. NEVER prod.
    sendgrid_stub_mode: bool = field(
        default_factory=lambda: os.getenv("SENDGRID_STUB_MODE", "false").lower() == "true"
    )
    # Base URL used to construct preferences + unsubscribe links inside
    # outgoing email. Must be reachable by recipients — a broken unsubscribe
    # link is a CAN-SPAM compliance problem, not just bad UX.
    app_base_url: str = field(
        default_factory=lambda: os.getenv("APP_BASE_URL", "https://emissary.onrender.com")
    )

    # --- Environment + app-level settings (previously scattered os.getenv) ---
    # APP_ENV drives prod-only validation below. Local/staging = "development";
    # the Render service should set APP_ENV=production.
    app_env: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    cors_origins: str = field(
        default_factory=lambda: os.getenv(
            "CORS_ORIGINS",
            "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173",
        )
    )
    # Empty = no Redis; every Redis-backed feature then falls back (in-memory rate
    # limiting, disk cache, lexical search). See src/common/redis_client.py — and
    # note that a *reachable* Redis is not necessarily a *capable* one: Valkey has
    # no FT.*/JSON.*, so semantic features gate on redis_client.capabilities().
    redis_url: str = field(default_factory=_compose_redis_url)
    risk_feed_mode: str = field(default_factory=lambda: os.getenv("RISK_FEED_MODE", "auto"))
    emissary_demo_username: str = field(
        default_factory=lambda: os.getenv("EMISSARY_DEMO_USERNAME", "analyst")
    )
    emissary_demo_password: str = field(
        default_factory=lambda: os.getenv("EMISSARY_DEMO_PASSWORD", "demo")
    )
    emissary_admin_users: str = field(default_factory=lambda: os.getenv("EMISSARY_ADMIN_USERS", ""))
    emissary_mock_data: bool = field(
        default_factory=lambda: os.getenv("EMISSARY_MOCK_DATA", "").lower() in ("1", "true", "yes")
    )
    wargame_enabled: bool = field(
        default_factory=lambda: os.getenv("WARGAME_ENABLED", "").lower() in ("1", "true", "yes")
    )
    wargame_debug_errors: bool = field(
        default_factory=lambda: (
            os.getenv("WARGAME_DEBUG_ERRORS", "").lower() in ("1", "true", "yes")
        )
    )

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() == "production"

    def validate(self) -> list[str]:
        """Return a list of configuration problems.

        Always-on: ANTHROPIC_API_KEY is required. In production (APP_ENV=production)
        we additionally fail loudly on the misconfigurations most likely to ship
        silently: a default auth secret, a missing CORS origin, and notifications
        enabled without provider credentials.
        """
        issues: list[str] = []
        # Provider-aware (issue #33): the Anthropic key is only required when the
        # Anthropic provider is selected. For an OpenAI-compatible local provider
        # we instead require a base URL + model so misconfig fails loudly.
        if self.llm_provider == "anthropic":
            if not self.anthropic_api_key:
                issues.append("ANTHROPIC_API_KEY is required")
        elif self.llm_provider in ("openai", "openai_compatible", "local"):
            if not self.llm_base_url:
                issues.append("LLM_BASE_URL is required when LLM_PROVIDER is openai")
            if not self.llm_model:
                issues.append("LLM_MODEL is required when LLM_PROVIDER is openai")
        else:
            issues.append(
                f"LLM_PROVIDER '{self.llm_provider}' is not recognized (use 'anthropic' or 'openai')"
            )

        if self.is_production:
            # EMISSARY_AUTH_SECRET is owned by src/auth.py; read the same env var
            # here purely for the prod gate (a default secret = forgeable tokens).
            if os.getenv("EMISSARY_AUTH_SECRET", "dev-secret-change-me") == "dev-secret-change-me":
                issues.append("EMISSARY_AUTH_SECRET must be overridden in production")
            if not self.cors_origins.strip():
                issues.append("CORS_ORIGINS must be set in production")
            if self.notifications_enabled:
                if not (
                    self.twilio_account_sid and self.twilio_auth_token and self.twilio_from_phone
                ):
                    issues.append("NOTIFICATIONS_ENABLED but Twilio credentials are incomplete")
                if not self.sendgrid_api_key:
                    issues.append("NOTIFICATIONS_ENABLED but SENDGRID_API_KEY is missing")
        return issues


# Singleton
config = Config()
