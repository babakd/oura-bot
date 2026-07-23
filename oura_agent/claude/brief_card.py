"""Model selection for the decision-first daily card."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import anthropic

from oura_agent.claude.models import create_message_with_fallback, response_text
from oura_agent.config import CLAUDE_MODEL, logger
from oura_agent.insights import (
    CANONICAL_ACTION_DOMAINS,
    METRICS,
    default_card_from_packet,
    normalize_card,
    render_daily_card,
    validate_model_card,
)
from oura_agent.prompts import load_prompt


CARD_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "maxLength": 72},
        "observation": {"type": "string", "maxLength": 260},
        "decision": {"type": "string", "maxLength": 190},
        "action_domain": {
            "type": "string",
            "enum": CANONICAL_ACTION_DOMAINS,
        },
        "evidence_keys": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": sorted(METRICS),
            },
            "maxItems": 2,
        },
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "no_action": {"type": "boolean"},
        "review_after": {"type": "string", "maxLength": 60},
        "expected_outcome": {"type": "string", "maxLength": 160},
    },
    "required": [
        "headline",
        "observation",
        "decision",
        "action_domain",
        "evidence_keys",
        "confidence",
        "no_action",
        "review_after",
        "expected_outcome",
    ],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class GeneratedDailyCard:
    text: str
    card: dict
    model: str
    stop_reason: str
    fallback_used: bool
    deterministic_fallback: bool


def _safe_fallback(packet: dict, stop_reason: str) -> GeneratedDailyCard:
    card = normalize_card(default_card_from_packet(packet), packet)
    return GeneratedDailyCard(
        text=render_daily_card(card, packet),
        card=card,
        model="deterministic",
        stop_reason=stop_reason,
        fallback_used=False,
        deterministic_fallback=True,
    )


def _remove_ungrounded_numbers(card: dict, packet: dict) -> dict:
    """Keep all numeric claims in deterministic evidence lines only."""
    fallback = default_card_from_packet(packet)
    for field in ("headline", "observation", "decision", "expected_outcome"):
        if re.search(r"\d", str(card.get(field, ""))):
            logger.warning("Discarded numeric model text in daily-card field %s", field)
            card[field] = fallback[field]
    return card


def generate_daily_card(api_key: str, packet: dict) -> GeneratedDailyCard:
    """Select one grounded insight and render it as a compact daily card."""
    if (
        packet.get("state") in {
            "data_unavailable",
            "sleep_missing",
            "baseline_building",
        }
        or packet.get("freshness", {}).get("data_quality") == "failed"
    ):
        logger.info("Serving deterministic data-quality card for incomplete required data")
        return _safe_fallback(packet, "data_quality_guardrail")

    try:
        prompt = load_prompt("daily_card")
    except FileNotFoundError:
        logger.error("daily_card.md prompt not found; using deterministic card")
        return _safe_fallback(packet, "missing_prompt")

    client = anthropic.Anthropic(api_key=api_key)
    request = {
        "max_tokens": 4096,
        "system": [
            {
                "type": "text",
                "text": prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [
            {
                "role": "user",
                "content": (
                    "Select today's one useful card from this deterministic packet. "
                    "When no_action is true, action_domain must be no_action. "
                    "Otherwise action_domain must exactly match the domain of the "
                    "first evidence key. "
                    "Return only the requested JSON.\n\n"
                    + json.dumps(packet, separators=(",", ":"))
                ),
            }
        ],
        "output_config": {
            "effort": "medium",
            "format": {"type": "json_schema", "schema": CARD_SCHEMA},
        },
    }

    try:
        call = create_message_with_fallback(client, request, model=CLAUDE_MODEL)
    except anthropic.APIError as exc:
        logger.error("Daily-card model call failed: %s", exc)
        return _safe_fallback(packet, "api_error")

    response = call.response
    if getattr(response, "stop_reason", None) == "refusal":
        logger.error("Daily-card request was refused by all configured models")
        return _safe_fallback(packet, "refusal")

    raw = response_text(response)
    if not raw:
        logger.error("Daily-card model returned no text")
        return _safe_fallback(packet, "empty_output")

    try:
        selected = json.loads(raw)
        if not isinstance(selected, dict):
            raise ValueError("structured output was not an object")
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("Daily-card structured output invalid: %s", exc)
        return _safe_fallback(packet, "invalid_output")

    selected = _remove_ungrounded_numbers(selected, packet)
    try:
        validate_model_card(selected, packet)
    except ValueError as exc:
        logger.error("Daily-card selection failed deterministic validation: %s", exc)
        return _safe_fallback(packet, "invalid_selection")

    card = normalize_card(selected, packet)
    return GeneratedDailyCard(
        text=render_daily_card(card, packet),
        card=card,
        model=call.model,
        stop_reason=getattr(response, "stop_reason", "end_turn") or "end_turn",
        fallback_used=call.fallback_used,
        deterministic_fallback=False,
    )
