"""Small append-only operational ledger and single-day run lock."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from oura_agent.config import RUNS_DIR, logger
from oura_agent.utils import now_nyc


UPDATES_FILE = RUNS_DIR / "telegram_updates.jsonl"
RUN_LEDGER_FILE = RUNS_DIR / "runs.jsonl"


def _ensure_dir() -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def _event_dir(kind: str) -> Path:
    directory = RUNS_DIR / kind
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _append_event(kind: str, value: dict) -> None:
    """Write an immutable operational event to its own Volume file."""
    _ensure_dir()
    stamp = str(value.get("at") or value.get("processed_at") or now_nyc().isoformat())
    stamp = stamp.replace(":", "").replace("+", "_")
    path = _event_dir(kind) / f"{stamp}-{uuid.uuid4().hex}.json"
    temporary = path.with_suffix(".json.tmp")
    with open(temporary, "x") as handle:
        json.dump(value, handle, separators=(",", ":"))
        handle.flush()
    temporary.replace(path)


def record_run_event(run_id: str, event: str, **details) -> None:
    _append_event(
        "run_events",
        {
            "run_id": run_id,
            "event": event,
            "at": now_nyc().isoformat(),
            **details,
        },
    )


def _recent_update_ids(limit: int = 500) -> set[str]:
    _ensure_dir()
    values = set()
    if UPDATES_FILE.exists():
        lines = UPDATES_FILE.read_text().splitlines()[-limit:]
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("update_id") is not None:
                values.add(str(event["update_id"]))

    event_paths = sorted(
        _event_dir("telegram_updates").glob("*.json"),
        reverse=True,
    )[:limit]
    for path in event_paths:
        try:
            event = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if event.get("update_id") is not None:
            values.add(str(event["update_id"]))
    return values


def claim_update(
    update_id: str | int | None,
    coordination: Any = None,
    stale_after_minutes: int = 6,
) -> str | None:
    """Atomically claim a Telegram update and return its ownership token.

    Completed updates stay deduplicated. A handler that fails marks its claim
    failed, allowing one subsequent Telegram retry to recover it.
    """
    if update_id is None:
        return "untracked"
    normalized = str(update_id)
    claim_id = uuid.uuid4().hex
    event = {
        "update_id": normalized,
        "claim_id": claim_id,
        "state": "claimed",
        "claimed_at": now_nyc().isoformat(),
    }
    if coordination is not None:
        if coordination.put(
            f"telegram-update:{normalized}",
            event,
            skip_if_exists=True,
        ):
            return claim_id

        key = f"telegram-update:{normalized}"
        existing = coordination.get(key)
        if not isinstance(existing, dict):
            return None
        recoverable = existing.get("state") == "failed"
        if existing.get("state") == "claimed":
            try:
                claimed_at = datetime.fromisoformat(existing["claimed_at"])
                recoverable = (
                    now_nyc() - claimed_at
                    > timedelta(minutes=stale_after_minutes)
                )
            except (TypeError, ValueError, KeyError):
                recoverable = False
        if not recoverable:
            return None

        recovery_key = (
            f"telegram-update-retry:{normalized}:"
            f"{existing.get('claim_id', 'unknown')}"
        )
        if not coordination.put(
            recovery_key,
            {"new_claim_id": claim_id, "at": now_nyc().isoformat()},
            skip_if_exists=True,
        ):
            return None
        if coordination.get(key) != existing:
            return None
        coordination.pop(key, None)
        if coordination.put(key, event, skip_if_exists=True):
            return claim_id
        return None
    elif normalized in _recent_update_ids():
        return None
    return claim_id


def complete_update(
    update_id: str | int | None,
    claim_id: str,
    coordination: Any = None,
) -> bool:
    """Mark a claimed update complete only after its side effect is durable."""
    if update_id is None:
        return True
    normalized = str(update_id)
    event = {
        "update_id": normalized,
        "claim_id": claim_id,
        "state": "completed",
        "processed_at": now_nyc().isoformat(),
    }
    if coordination is not None:
        key = f"telegram-update:{normalized}"
        existing = coordination.get(key)
        if not isinstance(existing, dict) or existing.get("claim_id") != claim_id:
            return False
        coordination.put(key, event)
    _append_event("telegram_updates", event)
    return True


def fail_update(
    update_id: str | int | None,
    claim_id: str,
    coordination: Any = None,
) -> bool:
    """Make a failed claim recoverable by one later Telegram retry."""
    if update_id is None:
        return True
    if coordination is None:
        return True
    normalized = str(update_id)
    key = f"telegram-update:{normalized}"
    existing = coordination.get(key)
    if not isinstance(existing, dict) or existing.get("claim_id") != claim_id:
        return False
    coordination.put(
        key,
        {
            **existing,
            "state": "failed",
            "failed_at": now_nyc().isoformat(),
        },
    )
    return True


def mark_update_processed(
    update_id: str | int | None,
    coordination: Any = None,
) -> bool:
    """Backward-compatible one-step claim+complete helper."""
    claim_id = claim_update(update_id, coordination=coordination)
    if claim_id is None:
        return False
    return complete_update(update_id, claim_id, coordination=coordination)


def _lock_path(date: str) -> Path:
    return RUNS_DIR / f"morning-{date}.lock"


def acquire_daily_lock(
    date: str,
    run_id: str,
    stale_after_minutes: int = 20,
    coordination: Any = None,
) -> bool:
    """Acquire a daily lock locally or through a distributed Modal Dict."""
    _ensure_dir()
    payload = {"run_id": run_id, "acquired_at": now_nyc().isoformat()}

    if coordination is not None:
        key = f"morning-lock:{date}"
        if coordination.put(key, payload, skip_if_exists=True):
            return True

        existing = coordination.get(key)
        if not isinstance(existing, dict):
            return False
        try:
            acquired_at = datetime.fromisoformat(existing["acquired_at"])
        except (TypeError, ValueError, KeyError):
            return False
        if now_nyc() - acquired_at <= timedelta(minutes=stale_after_minutes):
            return False

        # Only one contender may recover this exact stale owner. The function
        # timeout is well below the stale threshold, so the former owner can no
        # longer be executing when this branch is reached.
        recovery_key = f"morning-lock-recovery:{date}:{existing.get('run_id', 'unknown')}"
        if not coordination.put(
            recovery_key,
            {"recovered_at": now_nyc().isoformat(), "new_run_id": run_id},
            skip_if_exists=True,
        ):
            return False
        latest = coordination.get(key)
        if latest != existing:
            return False
        coordination.pop(key, None)
        return bool(coordination.put(key, payload, skip_if_exists=True))

    path = _lock_path(date)
    if path.exists():
        try:
            payload = json.loads(path.read_text())
            acquired_at = datetime.fromisoformat(payload["acquired_at"])
            if now_nyc() - acquired_at <= timedelta(minutes=stale_after_minutes):
                return False
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            # A malformed lock is safer to treat as active briefly based on mtime.
            age_seconds = now_nyc().timestamp() - path.stat().st_mtime
            if age_seconds <= stale_after_minutes * 60:
                return False
        logger.warning("Replacing stale morning-brief lock for %s", date)
        path.unlink(missing_ok=True)

    serialized = json.dumps(payload, separators=(",", ":"))
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w") as handle:
        handle.write(serialized)
    return True


def release_daily_lock(date: str, run_id: str, coordination: Any = None) -> None:
    if coordination is not None:
        key = f"morning-lock:{date}"
        existing = coordination.get(key)
        if isinstance(existing, dict) and existing.get("run_id") == run_id:
            coordination.pop(key, None)
        return

    path = _lock_path(date)
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    if payload.get("run_id") == run_id:
        path.unlink(missing_ok=True)
