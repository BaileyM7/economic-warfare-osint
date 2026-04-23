"""Application configuration loaded from environment variables.

All settings have sensible defaults for local development.
In production, set the corresponding env vars (see .env.example).
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration object.  Populated from env vars at startup."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # Database                                                             #
    # ------------------------------------------------------------------ #
    database_url: str = Field(
        default="postgresql+asyncpg://swarm:swarm@localhost:5432/swarm",
        alias="DATABASE_URL",
        description="Async-compatible PostgreSQL DSN (asyncpg driver).",
    )
    db_echo: bool = Field(
        default=False,
        alias="DB_ECHO",
        description="Log all SQL statements (noisy — dev only).",
    )

    # ------------------------------------------------------------------ #
    # Redis                                                                #
    # ------------------------------------------------------------------ #
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        alias="REDIS_URL",
        description="Redis connection URL for PubSub and caching.",
    )

    # ------------------------------------------------------------------ #
    # AI / LLM                                                             #
    # ------------------------------------------------------------------ #
    anthropic_api_key: str = Field(
        default="",
        alias="ANTHROPIC_API_KEY",
        description="Anthropic API key for Claude agents.",
    )
    voyage_api_key: str = Field(
        default="",
        alias="VOYAGE_API_KEY",
        description="Voyage AI key for generating 1536-dim text embeddings.",
    )
    agent_model: str = Field(
        default="claude-sonnet-4-6",
        alias="AGENT_MODEL",
        description="LLM used for country agent decisions.",
    )
    arbiter_model: str = Field(
        default="claude-opus-4-6",
        alias="ARBITER_MODEL",
        description="LLM used for arbiter conflict adjudication.",
    )
    embedding_model: str = Field(
        default="voyage-3",
        alias="EMBEDDING_MODEL",
        description="Voyage AI model for generating agent-memory embeddings.",
    )
    embedding_dims: int = Field(
        default=1536,
        alias="EMBEDDING_DIMS",
        description="Dimensionality of the embedding vector.  Must match vector column.",
    )

    # ------------------------------------------------------------------ #
    # App / environment                                                    #
    # ------------------------------------------------------------------ #
    environment: Literal["development", "staging", "production"] = Field(
        default="development",
        alias="APP_ENV",
        description="Deployment environment: development, staging, or production.",
    )
    log_level: str = Field(
        default="INFO",
        alias="LOG_LEVEL",
        description="Structlog log level.",
    )
    cors_origins: list[str] = Field(
        default=["http://localhost:3000"],
        alias="CORS_ORIGINS",
        description=(
            "Allowed CORS origins.  In env, supply as a CSV string, e.g. "
            "'http://localhost:3000,https://app.example.com'."
        ),
    )

    # ------------------------------------------------------------------ #
    # Simulation limits                                                    #
    # ------------------------------------------------------------------ #
    max_turns: int = Field(
        default=20,
        alias="MAX_TURNS",
        description="Default maximum number of turns per simulation.",
    )
    max_concurrent_sims: int = Field(
        default=4,
        alias="MAX_CONCURRENT_SIMS",
        description="Maximum number of simultaneously running simulations.",
    )

    # ------------------------------------------------------------------ #
    # Sim runner implementation                                            #
    # ------------------------------------------------------------------ #
    agent_runner_impl: str = Field(
        default="null",
        alias="AGENT_RUNNER_IMPL",
        description=(
            "Which SimRunner implementation to use.  "
            "'null' = NullSimRunner (stub, no AI); "
            "'langgraph' = real LangGraph engine (Phase 4)."
        ),
    )

    # ------------------------------------------------------------------ #
    # Validators                                                           #
    # ------------------------------------------------------------------ #

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_csv(cls, v: object) -> list[str]:
        """Accept a CSV string or a list; always return a list."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return list(v)  # type: ignore[arg-type]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached Settings singleton."""
    return Settings()
