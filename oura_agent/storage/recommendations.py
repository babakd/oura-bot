"""Append-only daily-card, recommendation, feedback, and delivery ledger."""

from __future__ import annotations

import json
import math
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from oura_agent.config import RECOMMENDATIONS_DIR, logger
from oura_agent.insights import display_evidence_keys as rendered_evidence_keys
from oura_agent.utils import now_nyc


LEDGER_FILE = RECOMMENDATIONS_DIR / "ledger.jsonl"
ALLOWED_FEEDBACK = {
    "accurate",
    "useful",
    "not_for_me",
    "doing_it",
    "skipped",
}
OUTCOME_METRICS = {
    "sleep_score",
    "readiness",
    "hrv",
    "resting_hr",
    "deep_sleep_minutes",
    "rem_sleep_minutes",
    "total_sleep_minutes",
    "sleep_efficiency",
}


def _ensure_dir() -> None:
    RECOMMENDATIONS_DIR.mkdir(parents=True, exist_ok=True)


def _events_dir() -> Path:
    """Return the one-file-per-event directory used by concurrent writers."""
    directory = RECOMMENDATIONS_DIR / "events"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _append(event: dict) -> None:
    """Persist an immutable event without modifying a shared Volume file.

    Modal Volumes use last-write-wins semantics when separate containers
    modify the same file. A unique file per event therefore preserves
    concurrent card, feedback, and delivery writes. ``ledger.jsonl`` remains a
    read-only legacy source for deployments created before this schema.
    """
    _ensure_dir()
    stamp = str(event.get("created_at") or now_nyc().isoformat())
    stamp = stamp.replace(":", "").replace("+", "_")
    path = _events_dir() / f"{stamp}-{uuid.uuid4().hex}.json"
    temporary = path.with_suffix(".json.tmp")
    with open(temporary, "x") as handle:
        json.dump(event, handle, separators=(",", ":"))
        handle.flush()
    temporary.replace(path)


def load_ledger() -> list[dict]:
    """Load legacy and immutable events, skipping corrupt writes safely."""
    _ensure_dir()
    events = []
    if LEDGER_FILE.exists():
        with open(LEDGER_FILE) as handle:
            for line_number, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "Skipped corrupt recommendation ledger line %s: %s",
                        line_number,
                        exc,
                    )

    for path in _events_dir().glob("*.json"):
        try:
            with open(path) as handle:
                event = json.load(handle)
            if isinstance(event, dict):
                events.append(event)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Skipped corrupt recommendation event %s: %s", path, exc)

    # Consumers rely on reverse traversal returning the newest state first.
    events.sort(
        key=lambda event: (
            str(event.get("created_at", "")),
            str(event.get("id") or event.get("card_id") or ""),
        )
    )
    return events


def _card_id(date: str) -> str:
    compact_date = date.replace("-", "")[-6:]
    return f"{compact_date}{uuid.uuid4().hex[:6]}"


def save_daily_card(
    date: str,
    card: dict,
    rendered_text: str,
    packet: dict,
    model: str,
    stop_reason: str | None = None,
    fallback_used: bool = False,
) -> dict:
    """Append a generated daily card and its structured recommendation."""
    primary_evidence_keys = [
        key
        for key in card.get("evidence_keys", [])
        if key in packet.get("metrics", {})
    ]
    display_keys = [
        key
        for key in card.get(
            "display_evidence_keys",
            rendered_evidence_keys(card, packet),
        )
        if key in packet.get("metrics", {})
    ]
    entry = {
        "type": "card",
        "id": _card_id(date),
        "date": date,
        "created_at": now_nyc().isoformat(),
        "headline": card.get("headline"),
        "observation": card.get("observation"),
        "action": card.get("decision"),
        "domain": card.get("action_domain"),
        "reason": [
            packet.get("metrics", {}).get(key, {}).get("evidence")
            for key in primary_evidence_keys
            if packet.get("metrics", {}).get(key, {}).get("evidence")
        ],
        "primary_evidence_keys": primary_evidence_keys,
        "display_evidence_keys": display_keys,
        "display_evidence": [
            {
                "key": key,
                "current": packet["metrics"][key].get("current"),
                "source_date": packet["metrics"][key].get("source_date"),
                "source": packet["metrics"][key].get("source"),
            }
            for key in display_keys
        ],
        "confidence": card.get("confidence"),
        "expected_outcome": card.get("expected_outcome"),
        "review_after": card.get("review_after"),
        "no_action": bool(card.get("no_action")),
        "status": "proposed" if not card.get("no_action") else "no_action",
        "rendered_text": rendered_text,
        "data_quality": packet.get("freshness", {}).get("data_quality"),
        "model": model,
        "stop_reason": stop_reason,
        "fallback_used": fallback_used,
    }
    _append(entry)
    return entry


def get_card(card_id: str) -> dict | None:
    for event in reversed(load_ledger()):
        if event.get("type") == "card" and event.get("id") == card_id:
            return event
    return None


def get_latest_card(date: str | None = None) -> dict | None:
    for event in reversed(load_ledger()):
        if event.get("type") != "card":
            continue
        if date is None or event.get("date") == date:
            return event
    return None


def record_feedback(
    card_id: str,
    feedback: str,
    update_id: str | int | None = None,
) -> dict:
    """Record one idempotent feedback event for an existing card."""
    if feedback not in ALLOWED_FEEDBACK:
        raise ValueError(f"Unsupported feedback: {feedback}")
    card = get_card(card_id)
    if not card:
        raise KeyError(f"Unknown card: {card_id}")

    for event in reversed(load_ledger()):
        if event.get("type") != "feedback":
            continue
        if update_id is not None and str(event.get("update_id")) == str(update_id):
            return event
        if event.get("card_id") == card_id and event.get("feedback") == feedback:
            return event

    event = {
        "type": "feedback",
        "card_id": card_id,
        "date": now_nyc().strftime("%Y-%m-%d"),
        "created_at": now_nyc().isoformat(),
        "feedback": feedback,
        "domain": card.get("domain"),
        "update_id": str(update_id) if update_id is not None else None,
    }
    _append(event)
    return event


def record_delivery(
    card_id: str,
    status: str,
    message_id: int | None = None,
    detail: str | None = None,
) -> dict:
    if status not in {"sent", "failed"}:
        raise ValueError("Delivery status must be sent or failed")
    event = {
        "type": "delivery",
        "card_id": card_id,
        "created_at": now_nyc().isoformat(),
        "status": status,
        "message_id": message_id,
        "detail": detail,
    }
    _append(event)
    return event


def card_was_delivered(card_id: str) -> bool:
    for event in reversed(load_ledger()):
        if event.get("type") == "delivery" and event.get("card_id") == card_id:
            return event.get("status") == "sent"
    return False


def record_next_day_outcome(observed_date: str, metrics: dict) -> dict | None:
    """Link the next available biometric snapshot to the prior delivered card.

    This is an observation, not a causal result. It closes the data loop enough
    for later cards and reviews to see what followed a recommendation while
    keeping adherence, subjective usefulness, and biometrics as separate facts.
    The operation is idempotent for a card/date pair.
    """
    ledger = load_ledger()
    delivered_ids = {
        event.get("card_id")
        for event in ledger
        if event.get("type") == "delivery" and event.get("status") == "sent"
    }
    observed_ids = {
        event.get("card_id")
        for event in ledger
        if event.get("type") == "outcome_observation"
    }
    eligible = [
        event
        for event in ledger
        if (
            event.get("type") == "card"
            and str(event.get("date", "")) < observed_date
            and event.get("id") in delivered_ids
            and event.get("id") not in observed_ids
        )
    ]
    if not eligible:
        return None

    card = max(
        eligible,
        key=lambda event: (
            str(event.get("date", "")),
            str(event.get("created_at", "")),
        ),
    )
    signals = {
        key: value
        for key in sorted(OUTCOME_METRICS)
        if isinstance((value := metrics.get(key)), (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    }
    if not signals:
        return None

    event = {
        "type": "outcome_observation",
        "card_id": card["id"],
        "card_date": card.get("date"),
        "observed_date": observed_date,
        "created_at": now_nyc().isoformat(),
        "domain": card.get("domain"),
        "signals": signals,
        "interpretation": "observed_after_not_attributed",
    }
    _append(event)
    return event


def summarize_feedback(days: int = 90) -> dict:
    """Return compact context that can influence tomorrow's card."""
    cutoff = (now_nyc().date() - timedelta(days=days)).isoformat()
    cards: dict[str, dict] = {}
    feedback_events = []
    outcome_events = []
    for event in load_ledger():
        if event.get("created_at", "")[:10] < cutoff:
            continue
        if event.get("type") == "card":
            cards[event["id"]] = event
        elif event.get("type") == "feedback":
            feedback_events.append(event)
        elif event.get("type") == "outcome_observation":
            outcome_events.append(event)

    counts = {key: 0 for key in ALLOWED_FEEDBACK}
    not_for_me_domains: set[str] = set()
    for event in feedback_events:
        feedback = event.get("feedback")
        if feedback in counts:
            counts[feedback] += 1
        if feedback == "not_for_me" and event.get("domain"):
            not_for_me_domains.add(event["domain"])

    recent_cards = sorted(cards.values(), key=lambda item: item.get("created_at", ""))[-7:]
    return {
        "card_count": len(cards),
        "feedback_counts": counts,
        "not_for_me_domains": sorted(not_for_me_domains),
        "recent_cards": [
            {
                "date": card.get("date"),
                "domain": card.get("domain"),
                "action": card.get("action"),
                "no_action": card.get("no_action"),
            }
            for card in recent_cards
        ],
        "recent_outcomes": [
            {
                "card_id": event.get("card_id"),
                "card_date": event.get("card_date"),
                "observed_date": event.get("observed_date"),
                "domain": event.get("domain"),
                "signals": event.get("signals", {}),
                "interpretation": "observed_after_not_attributed",
            }
            for event in outcome_events[-7:]
        ],
    }


def build_feedback_keyboard(card_id: str) -> dict:
    """Build compact callback data that stays below Telegram's 64-byte cap."""
    return {
        "inline_keyboard": [
            [
                {"text": "✓ Accurate", "callback_data": f"fb:{card_id}:accurate"},
                {"text": "👍 Useful", "callback_data": f"fb:{card_id}:useful"},
            ],
            [
                {"text": "Doing it", "callback_data": f"fb:{card_id}:doing_it"},
                {"text": "Skipped", "callback_data": f"fb:{card_id}:skipped"},
            ],
            [
                {"text": "Not for me", "callback_data": f"fb:{card_id}:not_for_me"},
            ],
        ]
    }
