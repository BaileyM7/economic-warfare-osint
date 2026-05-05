"""Shared pytest fixtures for the Emissary test suite.

Strategy mirrors example_e2e.md:
  * Boot the real FastAPI app in-process against a throwaway SQLite DB.
  * Default to VCR replay-only mode — no test makes real network calls
    unless explicitly recording (VCR_RECORD_MODE=once).
  * Cassettes live in tests/cassettes/ alongside their test files and
    are scrubbed of secrets by scripts/redact-cassettes.py before commit.

Run: pytest tests/
Record: VCR_RECORD_MODE=once pytest tests/e2e/test_foo.py
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# --- Environment isolation (must run BEFORE any src.* import) ---
# Force in-memory rate-limit storage so tests don't need Redis.
os.environ.pop("REDIS_URL", None)
# Clear Anthropic key so tests never hit the real API. Routes that need a
# client get a fast 503; tests that need real LLM responses use VCR cassettes.
os.environ["ANTHROPIC_API_KEY"] = ""
# Pin auth secret + demo credentials to known values so login fixture works.
os.environ["EMISSARY_AUTH_SECRET"] = "test-secret-do-not-use-in-prod"
os.environ["EMISSARY_DEMO_USERNAME"] = "tester"
os.environ["EMISSARY_DEMO_PASSWORD"] = "testpass"
# CORS — give the test app explicit origins so the env-var check passes.
os.environ.setdefault("CORS_ORIGINS", "http://testserver,http://localhost:5173")

# Redirect the SQLite DB to a temp file. Patch src.db.DB_PATH BEFORE the
# app imports — once the routers cache `get_db` references, it's too late.
_TEST_DB = Path(tempfile.gettempdir()) / "emissary_test.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()

import src.db as _db  # noqa: E402

_db.DB_PATH = _TEST_DB


@pytest.fixture(scope="session")
def app_module():
    """Lazy-import the FastAPI app so env-var patches above take effect first."""
    from src import api as api_module

    api_module.init_db()
    return api_module


@pytest.fixture
def app_client(app_module) -> Iterator:
    """A FastAPI TestClient bound to the real app + throwaway DB.

    Yields fresh per-test so DB mutations don't leak between tests.
    """
    from fastapi.testclient import TestClient

    # Wipe + re-init the DB between tests so each test gets a clean slate.
    if _TEST_DB.exists():
        _TEST_DB.unlink()
    app_module.init_db()

    with TestClient(app_module.app) as client:
        yield client


@pytest.fixture
def auth_token(app_client) -> str:
    """Log in with the demo credentials and return a Bearer token string."""
    resp = app_client.post(
        "/api/auth/login",
        json={"username": "tester", "password": "testpass"},
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


@pytest.fixture
def auth_headers(auth_token: str) -> dict[str, str]:
    """Convenience: pre-built Authorization header for `client.post(..., headers=...)`."""
    return {"Authorization": f"Bearer {auth_token}"}


# --- VCR configuration ---
# Tests can opt into cassette-based recording by adding @pytest.mark.vcr.
# By default we replay only — `none` mode raises if a request has no cassette.
@pytest.fixture(scope="module")
def vcr_config():
    record_mode = os.environ.get("VCR_RECORD_MODE", "none")
    return {
        "record_mode": record_mode,
        # Filter sensitive headers so cassettes stay safe even before
        # scripts/redact-cassettes.py runs.
        "filter_headers": [
            "authorization",
            "x-api-key",
            "anthropic-api-key",
            "cookie",
        ],
        # Scrub query params that might carry credentials.
        "filter_query_parameters": ["api_key", "token", "key"],
        # Match on method+URI+body so identical replays are deterministic.
        "match_on": ["method", "scheme", "host", "port", "path", "query"],
        "decode_compressed_response": True,
    }


@pytest.fixture(scope="module")
def vcr_cassette_dir(request) -> str:
    """Cassette location: tests/cassettes/<test_module_name>/."""
    test_dir = Path(request.module.__file__).parent
    return str(test_dir / "cassettes" / Path(request.module.__file__).stem)
