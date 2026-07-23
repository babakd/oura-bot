"""
Conversation history storage.
"""

import json
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from oura_agent.config import CONVERSATIONS_DIR, CONVERSATION_WINDOW_DAYS, logger


def _ensure_conversations_dir():
    """Ensure conversations directory exists."""
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)


def _events_dir() -> Path:
    directory = CONVERSATIONS_DIR / "events"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _all_messages() -> list:
    """Read the legacy transcript plus immutable per-message event files."""
    records = []
    conv_file = CONVERSATIONS_DIR / "history.jsonl"
    if conv_file.exists():
        with open(conv_file) as handle:
            for index, line in enumerate(handle):
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                records.append(
                    (
                        str(message.get("timestamp", "")),
                        f"legacy-{index:012d}",
                        message,
                    )
                )

    for path in _events_dir().glob("*.json"):
        try:
            with open(path) as handle:
                message = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(message, dict):
            records.append(
                (str(message.get("timestamp", "")), path.name, message)
            )

    records.sort(key=lambda item: (item[0], item[1]))
    return [message for _, _, message in records]


def load_conversation_history(
    limit: int = 20,
    today_only: bool = False,
    days_back: int = None,
) -> list:
    """Load recent conversation messages.

    Args:
        limit: Maximum messages to return (most recent).
        today_only: If True, only return messages from today (legacy flag).
        days_back: If set, only return messages within the last N days
            (relative to now). Takes precedence over today_only when both set.
    """
    from oura_agent.utils import now_nyc

    _ensure_conversations_dir()

    now = now_nyc()
    today_str = now.strftime("%Y-%m-%d")
    cutoff = None
    if days_back is not None:
        cutoff = now - timedelta(days=days_back)

    messages = []
    for msg in _all_messages():
        ts_str = msg.get("timestamp", "")
        if cutoff is not None:
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts < cutoff:
                    continue
            except (ValueError, TypeError):
                continue
        elif today_only and ts_str[:10] != today_str:
            continue
        messages.append(msg)

    return messages[-limit:]


def save_conversation_message(role: str, content: str):
    """Persist a message without concurrently modifying a shared Volume file."""
    from oura_agent.utils import now_nyc

    _ensure_conversations_dir()

    entry = {
        "timestamp": now_nyc().isoformat(),
        "role": role,  # "user" or "assistant"
        "content": content
    }

    path = _events_dir() / f"{time.time_ns():020d}-{uuid.uuid4().hex}.json"
    temporary = path.with_suffix(".json.tmp")
    with open(temporary, "x") as handle:
        json.dump(entry, handle, separators=(",", ":"))
        handle.flush()
    temporary.replace(path)


def prune_conversation_history():
    """Remove messages older than CONVERSATION_WINDOW_DAYS (365 days)."""
    from oura_agent.utils import now_nyc

    cutoff = now_nyc() - timedelta(days=CONVERSATION_WINDOW_DAYS)
    conv_file = CONVERSATIONS_DIR / "history.jsonl"
    kept_messages = []

    # No new code writes the legacy file, so compacting it cannot race an
    # append. Keep it for backwards compatibility with existing deployments.
    if conv_file.exists():
        with open(conv_file) as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                    ts = datetime.fromisoformat(msg["timestamp"])
                    if ts >= cutoff:
                        kept_messages.append(msg)
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

        temporary = conv_file.with_suffix(".jsonl.tmp")
        with open(temporary, "w") as handle:
            for msg in kept_messages:
                handle.write(json.dumps(msg) + "\n")
        temporary.replace(conv_file)

    # Immutable event files can be pruned independently; a concurrent new
    # message has a current timestamp and cannot match this cutoff.
    for path in _events_dir().glob("*.json"):
        try:
            message = json.loads(path.read_text())
            timestamp = datetime.fromisoformat(message["timestamp"])
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            continue
        if timestamp < cutoff:
            path.unlink()
