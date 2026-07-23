"""Claude request helpers with explicit Fable refusal fallback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import anthropic

from oura_agent.config import (
    CLAUDE_FABLE_MODEL,
    CLAUDE_FALLBACK_MODEL,
    CLAUDE_MODEL,
    logger,
)


@dataclass(frozen=True)
class ModelCall:
    response: Any
    model: str
    fallback_used: bool
    refusal_seen: bool


def is_fable_model(model: str) -> bool:
    return isinstance(model, str) and (
        model == CLAUDE_FABLE_MODEL or model.startswith("claude-fable-")
    )


def is_fable_access_error(model: str, exc: Exception) -> bool:
    """Return whether a Fable workspace/model access error may use Opus."""
    return (
        is_fable_model(model)
        and isinstance(exc, anthropic.APIStatusError)
        and getattr(exc, "status_code", 0) in {400, 403, 404}
    )


def _served_model(response: Any, requested: str) -> str:
    value = getattr(response, "model", None)
    return value if isinstance(value, str) and value else requested


def create_message_with_fallback(
    client: anthropic.Anthropic,
    request: dict,
    model: str | None = None,
) -> ModelCall:
    """Create a message and retry Fable refusals on Opus 4.8.

    Fable returns safety refusals as HTTP 200 responses. Access can also fail
    with a 4xx when mandatory retention is not enabled for the workspace; that
    setup failure is safe to serve with the configured fallback.
    """
    primary = model or CLAUDE_MODEL
    try:
        response = client.messages.create(model=primary, **request)
    except anthropic.APIStatusError as exc:
        if not is_fable_access_error(primary, exc):
            raise
        logger.warning(
            "Fable unavailable (HTTP %s); serving with %s",
            getattr(exc, "status_code", "unknown"),
            CLAUDE_FALLBACK_MODEL,
        )
        response = client.messages.create(model=CLAUDE_FALLBACK_MODEL, **request)
        return ModelCall(
            response=response,
            model=_served_model(response, CLAUDE_FALLBACK_MODEL),
            fallback_used=True,
            refusal_seen=False,
        )

    if getattr(response, "stop_reason", None) != "refusal":
        return ModelCall(
            response=response,
            model=_served_model(response, primary),
            fallback_used=False,
            refusal_seen=False,
        )

    if not is_fable_model(primary) or primary == CLAUDE_FALLBACK_MODEL:
        return ModelCall(
            response=response,
            model=_served_model(response, primary),
            fallback_used=False,
            refusal_seen=True,
        )

    logger.warning("Fable returned stop_reason=refusal; retrying on %s", CLAUDE_FALLBACK_MODEL)
    fallback = client.messages.create(model=CLAUDE_FALLBACK_MODEL, **request)
    return ModelCall(
        response=fallback,
        model=_served_model(fallback, CLAUDE_FALLBACK_MODEL),
        fallback_used=True,
        refusal_seen=True,
    )


def response_text(response: Any) -> str:
    parts = []
    for block in getattr(response, "content", []):
        block_type = getattr(block, "type", None)
        value = getattr(block, "text", "")
        # Older SDK-shaped mocks did not include a type field. Real non-text
        # blocks do have a string type and are ignored.
        if (
            block_type == "text"
            or not isinstance(block_type, str)
        ) and isinstance(value, str) and value.strip():
            parts.append(value)
    return "\n".join(parts).strip()
