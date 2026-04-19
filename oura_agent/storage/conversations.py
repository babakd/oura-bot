"""
Conversation history storage.
"""

import json
from datetime import datetime, timedelta

from oura_agent.config import CONVERSATIONS_DIR, CONVERSATION_WINDOW_DAYS, logger


def _ensure_conversations_dir():
    """Ensure conversations directory exists."""
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)


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

    conv_file = CONVERSATIONS_DIR / "history.jsonl"
    if not conv_file.exists():
        return []

    now = now_nyc()
    today_str = now.strftime("%Y-%m-%d")
    cutoff = None
    if days_back is not None:
        cutoff = now - timedelta(days=days_back)

    messages = []
    with open(conv_file) as f:
        for line in f:
            if line.strip():
                try:
                    msg = json.loads(line)
                    ts_str = msg.get("timestamp", "")
                    if cutoff is not None:
                        try:
                            ts = datetime.fromisoformat(ts_str)
                            if ts < cutoff:
                                continue
                        except (ValueError, TypeError):
                            continue
                    elif today_only:
                        if ts_str[:10] != today_str:
                            continue
                    messages.append(msg)
                except json.JSONDecodeError:
                    continue

    return messages[-limit:]


def save_conversation_message(role: str, content: str):
    """Append a message to conversation history."""
    from oura_agent.utils import now_nyc

    _ensure_conversations_dir()

    conv_file = CONVERSATIONS_DIR / "history.jsonl"

    entry = {
        "timestamp": now_nyc().isoformat(),
        "role": role,  # "user" or "assistant"
        "content": content
    }

    with open(conv_file, "a") as f:
        f.write(json.dumps(entry) + "\n")


def prune_conversation_history():
    """Remove messages older than CONVERSATION_WINDOW_DAYS (365 days)."""
    from oura_agent.utils import now_nyc

    conv_file = CONVERSATIONS_DIR / "history.jsonl"
    if not conv_file.exists():
        return

    cutoff = now_nyc() - timedelta(days=CONVERSATION_WINDOW_DAYS)
    kept_messages = []

    with open(conv_file) as f:
        for line in f:
            if line.strip():
                try:
                    msg = json.loads(line)
                    ts = datetime.fromisoformat(msg["timestamp"])
                    if ts >= cutoff:
                        kept_messages.append(msg)
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

    with open(conv_file, "w") as f:
        for msg in kept_messages:
            f.write(json.dumps(msg) + "\n")
