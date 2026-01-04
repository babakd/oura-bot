"""
Conversation history storage.
"""

import json
from datetime import datetime, timedelta

from oura_agent.config import CONVERSATIONS_DIR, RAW_WINDOW_DAYS, logger


def _ensure_conversations_dir():
    """Ensure conversations directory exists."""
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)


def load_conversation_history(limit: int = 20) -> list:
    """Load recent conversation messages."""
    _ensure_conversations_dir()

    conv_file = CONVERSATIONS_DIR / "history.jsonl"
    if not conv_file.exists():
        return []

    messages = []
    with open(conv_file) as f:
        for line in f:
            if line.strip():
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    # Return most recent N messages
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
    """Remove messages older than 28 days."""
    from oura_agent.utils import now_nyc

    conv_file = CONVERSATIONS_DIR / "history.jsonl"
    if not conv_file.exists():
        return

    cutoff = now_nyc() - timedelta(days=RAW_WINDOW_DAYS)
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

    # Rewrite file with only kept messages
    with open(conv_file, "w") as f:
        for msg in kept_messages:
            f.write(json.dumps(msg) + "\n")
