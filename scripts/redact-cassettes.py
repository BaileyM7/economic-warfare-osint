#!/usr/bin/env python3
"""Redact secrets from VCR cassettes before they get committed.

Mirrors the pattern in example_e2e.md (Polly.js redact-secrets script).
Loads every value from .env, walks tests/cassettes/**/*.yaml, and replaces
each secret value with the literal string "<redacted>".

Run manually:
    python scripts/redact-cassettes.py

Run as pre-commit hook (auto-wired via .pre-commit-config.yaml):
    runs on every commit that touches tests/cassettes/

Exit codes:
    0  — clean (or nothing to do)
    1  — at least one cassette was rewritten; re-stage and commit again
    2  — at least one secret was found AND we couldn't redact it
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CASSETTE_GLOB = "tests/cassettes/**/*.yaml"
ENV_FILES = [".env", ".env.local"]

# Values shorter than this are skipped — common short strings ("test", "1",
# the literal word "true") would mass-replace legitimate cassette content.
MIN_SECRET_LEN = 12

# Hard-skip these env vars even if their value is long enough — they're
# not secrets and substituting them would wreck cassette correctness.
NEVER_REDACT_KEYS = frozenset(
    {
        "APP_ENV",
        "EMISSARY_DEMO_USERNAME",
        "CORS_ORIGINS",
        "PYTHON_VERSION",
        "NODE_VERSION",
    }
)


def _load_env_values() -> set[str]:
    """Return the set of secret VALUES from any .env file in repo root.

    Only values longer than MIN_SECRET_LEN are returned; key names listed
    in NEVER_REDACT_KEYS are skipped.
    """
    values: set[str] = set()
    for fname in ENV_FILES:
        path = REPO_ROOT / fname
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            key = key.strip()
            value = raw.strip().strip('"').strip("'")
            if not value or len(value) < MIN_SECRET_LEN:
                continue
            if key in NEVER_REDACT_KEYS:
                continue
            values.add(value)
    return values


def _redact_file(path: Path, secrets: set[str]) -> bool:
    """Replace any occurrence of any secret in `secrets` with "<redacted>".

    Returns True if the file was modified.
    """
    original = path.read_text(encoding="utf-8")
    redacted = original
    for secret in secrets:
        if secret in redacted:
            redacted = redacted.replace(secret, "<redacted>")
    if redacted != original:
        path.write_text(redacted, encoding="utf-8")
        return True
    return False


def main() -> int:
    secrets = _load_env_values()
    if not secrets:
        print("redact-cassettes: no .env values to redact (clean)")
        return 0

    cassettes = list(REPO_ROOT.glob(CASSETTE_GLOB))
    if not cassettes:
        print("redact-cassettes: no cassettes to scan")
        return 0

    modified: list[Path] = []
    for path in cassettes:
        if _redact_file(path, secrets):
            modified.append(path)

    if not modified:
        print(f"redact-cassettes: scanned {len(cassettes)} cassettes, nothing to redact")
        return 0

    print(f"redact-cassettes: REDACTED {len(modified)} cassette(s):")
    for p in modified:
        print(f"  {p.relative_to(REPO_ROOT)}")
    print("\nRe-stage and commit again so the redacted version is committed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
