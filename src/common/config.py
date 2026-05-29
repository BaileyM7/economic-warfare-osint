"""Configuration loading from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
_project_root = Path(__file__).resolve().parent.parent.parent
load_dotenv(_project_root / ".env")


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

    # BuildWorkforce AI sector intelligence
    buildworkforce_api_key: str = field(
        default_factory=lambda: os.getenv("BUILDWORKFORCE_API_KEY", "")
    )
    buildworkforce_team_id: str = field(
        default_factory=lambda: os.getenv(
            "BUILDWORKFORCE_TEAM_ID", "56487d92-a610-4875-8263-07a4d4afb6eb"
        )
    )

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

    def validate(self) -> list[str]:
        """Return list of missing required config values."""
        issues = []
        if not self.anthropic_api_key:
            issues.append("ANTHROPIC_API_KEY is required")
        return issues


# Singleton
config = Config()
