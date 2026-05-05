"""User-input sanitization helpers.

These run AFTER Pydantic validation has accepted the field but BEFORE we
hand it to an LLM or an external HTTP client. The Pydantic layer enforces
shape (max_length, regex pattern); these helpers neutralize content that
slipped through the shape filter but is still risky:

  * `strip_control_chars` — removes ASCII C0 control bytes (0x00-0x1F)
    except tab/newline/CR. Defends against prompt-boundary smuggling
    where an attacker embeds raw \\x00 or \\x1f to confuse the model's
    tokenizer or terminal-rendering of logged prompts.
  * `clamp_for_llm` — caps text length before it lands in a prompt. The
    Pydantic max_length is the hard ceiling; this is a softer cap applied
    at LLM-dispatch time so that even a model with a 200K context can't
    be made to bill us $5 per request.
"""

from __future__ import annotations

# Keep tab (0x09), newline (0x0A), carriage return (0x0D); strip all other
# C0 control characters.
_CONTROL_CHARS = "".join(chr(c) for c in range(0x20) if c not in (0x09, 0x0A, 0x0D))
_CONTROL_TABLE = str.maketrans("", "", _CONTROL_CHARS)


def strip_control_chars(text: str) -> str:
    """Remove ASCII control bytes except \\t, \\n, \\r."""
    if not text:
        return text
    return text.translate(_CONTROL_TABLE)


def clamp_for_llm(text: str, max_chars: int = 10_000) -> str:
    """Truncate `text` to `max_chars`, appending a marker if truncated.

    The marker tells the model the input was cut so it doesn't try to
    complete a half-sentence as if it were the user's full intent.
    Marker length is included in `max_chars` so the returned string is
    never longer than `max_chars`.
    """
    if not text or len(text) <= max_chars:
        return text
    marker = "\n\n[…truncated]"
    return text[: max_chars - len(marker)] + marker


def sanitize_for_llm(text: str, max_chars: int = 10_000) -> str:
    """Convenience: strip control chars + clamp length in one call.

    Apply this immediately before passing user-derived text into an
    Anthropic prompt body.
    """
    return clamp_for_llm(strip_control_chars(text), max_chars=max_chars)
