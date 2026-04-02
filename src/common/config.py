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
    opensanctions_api_key: str = field(default_factory=lambda: os.getenv("OPENSANCTIONS_API_KEY", ""))
    trade_gov_api_key: str = field(default_factory=lambda: os.getenv("TRADE_GOV_API_KEY", ""))
    acled_api_key: str = field(default_factory=lambda: os.getenv("ACLED_API_KEY", ""))
    acled_email: str = field(default_factory=lambda: os.getenv("ACLED_EMAIL", ""))
    acled_password: str = field(default_factory=lambda: os.getenv("ACLED_PASSWORD", ""))
    acled_refresh_token: str = field(default_factory=lambda: os.getenv("REFRESH_TOKEN", ""))
    opencorporates_api_key: str = field(default_factory=lambda: os.getenv("OPENCORPORATES_API_KEY", ""))
    datalastic_api_key: str = field(default_factory=lambda: os.getenv("DATALASTIC_API_KEY", ""))
    sayari_client_id: str = field(default_factory=lambda: os.getenv("SAYARI_CLIENT_ID", ""))
    sayari_client_secret: str = field(default_factory=lambda: os.getenv("SAYARI_CLIENT_SECRET", ""))

    # Sayari Graph API (entity resolution, traversal, UBO)
    sayari_client_id: str = field(default_factory=lambda: os.getenv("SAYARI_CLIENT_ID", ""))
    sayari_client_secret: str = field(default_factory=lambda: os.getenv("SAYARI_CLIENT_SECRET", ""))

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
    model: str = field(default_factory=lambda: os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514"))

    def validate(self) -> list[str]:
        """Return list of missing required config values."""
        issues = []
        if not self.anthropic_api_key:
            issues.append("ANTHROPIC_API_KEY is required")
        return issues


# Singleton
config = Config()
