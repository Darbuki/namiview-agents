"""Anthropic client factory with shared defaults."""

from __future__ import annotations

import os

from anthropic import Anthropic

DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_RETRIES = 3


def build_client(
    api_key: str | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> Anthropic:
    """Construct an Anthropic client. `api_key` falls back to `$ANTHROPIC_API_KEY`."""
    kwargs: dict[str, object] = {
        "timeout": timeout,
        "max_retries": max_retries,
    }
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if key:
        kwargs["api_key"] = key
    return Anthropic(**kwargs)  # type: ignore[arg-type]
