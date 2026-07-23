"""
Intervention logging and storage.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import timedelta
from pathlib import Path

from oura_agent.config import INTERVENTIONS_DIR, logger


CLEAR_SNAPSHOTS_DIRNAME = ".clear_snapshots"
EVENTS_DIRNAME = ".events"
STATE_DIRNAME = ".state"
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _ensure_interventions_dir():
    """Ensure interventions directory exists."""
    INTERVENTIONS_DIR.mkdir(parents=True, exist_ok=True)


def _clear_snapshots_dir() -> Path:
    directory = INTERVENTIONS_DIR / CLEAR_SNAPSHOTS_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _events_root() -> Path:
    directory = INTERVENTIONS_DIR / EVENTS_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _events_dir(date: str) -> Path:
    directory = _events_root() / _validate_date(date)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _state_path(date: str) -> Path:
    directory = INTERVENTIONS_DIR / STATE_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{_validate_date(date)}.json"


def _load_state(date: str) -> dict:
    path = _state_path(date)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _event_ids_for_date(date: str) -> set[str]:
    """Return IDs for immutable events already committed for a date."""
    event_ids = set()
    for path in _events_dir(date).glob("*.json"):
        try:
            with open(path) as handle:
                entry = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        event_id = entry.get("_event_id") if isinstance(entry, dict) else None
        if isinstance(event_id, str) and event_id:
            event_ids.add(event_id)
    return event_ids


def _validate_date(date: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""):
        raise ValueError("date must use YYYY-MM-DD format")
    return date


def _snapshot_path(snapshot_id: str) -> Path:
    if not snapshot_id or not _SAFE_ID_RE.fullmatch(snapshot_id):
        raise ValueError("invalid snapshot_id")
    return _clear_snapshots_dir() / f"{snapshot_id}.json"


def _write_json_atomic(path: Path, payload: dict):
    """Write JSON via same-directory replace so snapshots are never partial."""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with open(temporary, "w") as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
    temporary.replace(path)


def _write_active_entries(date: str, entries: list):
    """Write canonical active JSONL without deleting any legacy source file."""
    _ensure_interventions_dir()
    jsonl_file = INTERVENTIONS_DIR / f"{date}.jsonl"
    temporary = jsonl_file.with_name(
        f".{jsonl_file.name}.{uuid.uuid4().hex}.tmp"
    )
    with open(temporary, "w") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")
        handle.flush()
    temporary.replace(jsonl_file)


def _migrate_json_to_jsonl(date: str):
    """Copy legacy JSON into the canonical JSONL format without deleting it."""
    json_file = INTERVENTIONS_DIR / f"{date}.json"
    jsonl_file = INTERVENTIONS_DIR / f"{date}.jsonl"

    # Only migrate if .json exists and .jsonl doesn't
    if json_file.exists() and not jsonl_file.exists():
        data = load_interventions(date)  # This reads the .json file
        if data.get("entries"):
            _write_active_entries(date, data["entries"])
            logger.info(f"Migrated {len(data['entries'])} entries from {json_file} to {jsonl_file}")
        # The JSONL file shadows this legacy source on future reads. Keep the
        # original as a recoverable migration backup.


def load_interventions(date: str) -> dict:
    """
    Load interventions for a given date. Returns full data structure.

    Supports both JSONL (new format) and JSON (legacy format) for backwards compatibility.
    """
    _ensure_interventions_dir()

    date = _validate_date(date)
    entries = []

    # Read the canonical/legacy base snapshot first.
    jsonl_file = INTERVENTIONS_DIR / f"{date}.jsonl"
    if jsonl_file.exists():
        with open(jsonl_file) as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        logger.warning(f"Skipped corrupt line {line_num} in {jsonl_file}: {e}")

    # Fall back to JSON only when no canonical JSONL exists.
    json_file = INTERVENTIONS_DIR / f"{date}.json"
    if not jsonl_file.exists() and json_file.exists():
        with open(json_file) as f:
            data = json.load(f)
            # Migrate old format if needed
            if "interventions" in data and "entries" not in data:
                data["entries"] = [
                    {"time": e.get("timestamp", "").split("T")[1][:5] if "T" in e.get("timestamp", "") else "",
                     "raw": f"{e.get('name', '')} ({e.get('details', '')})" if e.get('details') else e.get('name', ''),
                     "cleaned": f"{e.get('name', '')} ({e.get('details', '')})" if e.get('details') else e.get('name', '')}
                    for e in data["interventions"]
                ]
                del data["interventions"]
            entries.extend(data.get("entries", []))

    # New writes are immutable per-entry files. A clear marker hides entries
    # created before the most recent soft clear without deleting their files.
    state = _load_state(date)
    hidden_ids_present = "hidden_event_ids" in state
    hidden_event_ids = {
        event_id
        for event_id in state.get("hidden_event_ids", [])
        if isinstance(event_id, str)
    }
    active_after = str(state.get("active_after") or "")
    for path in sorted(_events_dir(date).glob("*.json")):
        try:
            with open(path) as handle:
                entry = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"Skipped invalid intervention event {path}: {exc}")
            continue
        if not isinstance(entry, dict):
            continue
        event_id = str(entry.get("_event_id") or "")
        if event_id and event_id in hidden_event_ids:
            continue
        created_at = str(entry.get("_created_at") or "")
        # Compatibility for state files written by the first soft-clear
        # implementation. New state uses exact event IDs, which remains correct
        # even when a fixed test clock or coarse timestamp gives two writes the
        # same value.
        if not hidden_ids_present and active_after and created_at <= active_after:
            continue
        entries.append(entry)

    # A restored base snapshot may contain an event that also remains visible
    # in the immutable event directory. Exact deduplication keeps undo
    # idempotent while preserving every distinct user action.
    unique_entries = []
    seen = set()
    for entry in entries:
        marker = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        if marker in seen:
            continue
        seen.add(marker)
        unique_entries.append(entry)
    return {"date": date, "entries": unique_entries}


def save_interventions(date: str, data: dict):
    """
    Save interventions for a given date.

    Writes JSONL format without deleting a legacy recovery source.
    """
    _ensure_interventions_dir()

    _write_active_entries(date, data.get("entries", []))
    from oura_agent.utils import now_nyc

    _write_json_atomic(
        _state_path(date),
        {
            "version": 2,
            "active_after": now_nyc().isoformat(),
            "hidden_event_ids": sorted(_event_ids_for_date(date)),
            "reason": "explicit_save",
        },
    )


def load_historical_interventions(days: int = None) -> dict:
    """Load all interventions from the past N days, or all if days=None.

    Args:
        days: Number of days to load. If None, loads all available interventions.

    Returns:
        dict: {date: {date, entries}} mapping
    """
    from oura_agent.utils import now_nyc

    _ensure_interventions_dir()

    if days is None:
        # Load all available legacy and immutable intervention dates.
        interventions_by_date = {}
        dates = {
            path.stem for path in INTERVENTIONS_DIR.glob("*.jsonl")
        } | {
            path.stem for path in INTERVENTIONS_DIR.glob("*.json")
        } | {
            path.name
            for path in _events_root().iterdir()
            if path.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.name)
        }
        for date in sorted(dates):
            data = load_interventions(date)
            if data.get("entries"):
                interventions_by_date[date] = data
        return interventions_by_date

    # Load specific number of days
    interventions_by_date = {}
    for i in range(days):
        date = (now_nyc() - timedelta(days=i)).strftime("%Y-%m-%d")
        data = load_interventions(date)
        if data.get("entries"):
            interventions_by_date[date] = data
    return interventions_by_date


def save_intervention_raw(
    raw_text: str,
    cleaned_text: str = None,
    source_update_id: str | int | None = None,
) -> dict:
    """
    Save an intervention as an immutable one-file event.

    Separate Modal containers never modify the same file, avoiding Volume
    last-write-wins data loss during concurrent chat and webhook activity.

    Args:
        raw_text: Original user input (kept for audit)
        cleaned_text: Claude-cleaned version (used for display/analysis)
    """
    from oura_agent.utils import now_nyc

    today = now_nyc().strftime("%Y-%m-%d")
    logged_at = now_nyc()
    display_time = logged_at.strftime("%H:%M")

    _ensure_interventions_dir()

    normalized_update_id = (
        str(source_update_id) if source_update_id is not None else None
    )
    if normalized_update_id is not None:
        for existing_path in _events_dir(today).glob("*.json"):
            try:
                existing = json.loads(existing_path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if (
                isinstance(existing, dict)
                and existing.get("_source_update_id") == normalized_update_id
            ):
                return existing

    # Migrate legacy .json to .jsonl if needed (before first write)
    _migrate_json_to_jsonl(today)

    entry = {
        "time": display_time,
        "raw": raw_text,
        "cleaned": cleaned_text or raw_text,
        "_created_at": logged_at.isoformat(),
        "_event_id": uuid.uuid4().hex,
    }
    if normalized_update_id is not None:
        entry["_source_update_id"] = normalized_update_id

    path = _events_dir(today) / (
        f"{time.time_ns():020d}-{entry['_event_id']}.json"
    )
    _write_json_atomic(path, entry)

    return entry


def list_clear_snapshots(
    date: str = None,
    include_restored: bool = True,
) -> list[dict]:
    """List recoverable intervention-clear snapshots, newest first."""
    if date is not None:
        _validate_date(date)

    snapshots = []
    for path in _clear_snapshots_dir().glob("*.json"):
        try:
            with open(path) as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            logger.warning(f"Skipped invalid intervention snapshot: {path}")
            continue

        if date is not None and payload.get("date") != date:
            continue
        if not include_restored and payload.get("restored_at"):
            continue
        payload = payload.copy()
        payload["path"] = str(path)
        snapshots.append(payload)

    snapshots.sort(
        key=lambda item: (
            item.get("cleared_at", ""),
            item.get("snapshot_id", ""),
        ),
        reverse=True,
    )
    return snapshots


def soft_clear_interventions(
    date: str = None,
    source_update_id: str | int | None = None,
) -> dict:
    """Clear a day's active interventions only after saving an undo snapshot.

    The active JSONL file is replaced with an empty file. No intervention file
    or snapshot is unlinked, so `undo_clear_interventions` can recover the data.
    """
    from oura_agent.utils import now_nyc

    target_date = _validate_date(
        date or now_nyc().strftime("%Y-%m-%d")
    )
    normalized_update_id = (
        str(source_update_id) if source_update_id is not None else None
    )
    if normalized_update_id is not None:
        for previous in list_clear_snapshots(
            date=target_date,
            include_restored=True,
        ):
            if previous.get("source_update_id") == normalized_update_id:
                return {
                    "status": "cleared",
                    "date": target_date,
                    "snapshot_id": previous["snapshot_id"],
                    "cleared_count": len(previous.get("entries", [])),
                }

    current = load_interventions(target_date)
    entries = current.get("entries", [])
    if not entries:
        return {
            "status": "empty",
            "date": target_date,
            "snapshot_id": None,
            "cleared_count": 0,
        }

    cleared_at = now_nyc().isoformat()
    snapshot_id = (
        f"{target_date}-"
        f"{now_nyc().strftime('%Y%m%dT%H%M%S%f')}-"
        f"{uuid.uuid4().hex[:12]}"
    )
    snapshot = {
        "version": 1,
        "snapshot_id": snapshot_id,
        "date": target_date,
        "cleared_at": cleared_at,
        "restored_at": None,
        "source_update_id": normalized_update_id,
        "entries": entries,
        "source_files": [
            path.name
            for path in (
                INTERVENTIONS_DIR / f"{target_date}.jsonl",
                INTERVENTIONS_DIR / f"{target_date}.json",
            )
            if path.exists()
        ],
    }
    _write_json_atomic(_snapshot_path(snapshot_id), snapshot)

    # Empty the base snapshot first, then hide exactly the immutable events
    # included in this clear. A concurrently committed event that was not in
    # the snapshot remains visible, which favors preserving user data over
    # silently clearing an action that raced with the command.
    state = _load_state(target_date)
    hidden_event_ids = {
        event_id
        for event_id in state.get("hidden_event_ids", [])
        if isinstance(event_id, str)
    }
    hidden_event_ids.update(
        str(entry["_event_id"])
        for entry in entries
        if isinstance(entry, dict) and entry.get("_event_id")
    )
    _write_active_entries(target_date, [])
    _write_json_atomic(
        _state_path(target_date),
        {
            "version": 2,
            "active_after": cleared_at,
            "hidden_event_ids": sorted(hidden_event_ids),
            "reason": "soft_clear",
            "snapshot_id": snapshot_id,
        },
    )
    logger.info(
        f"Soft-cleared {len(entries)} intervention(s) for {target_date}; "
        f"snapshot={snapshot_id}"
    )
    return {
        "status": "cleared",
        "date": target_date,
        "snapshot_id": snapshot_id,
        "cleared_count": len(entries),
    }


def restore_interventions_snapshot(
    snapshot_id: str,
    merge: bool = True,
    source_update_id: str | int | None = None,
) -> dict:
    """Restore a clear snapshot, preserving entries logged after the clear.

    With the default `merge=True`, snapshot entries are placed before current
    entries and exact duplicates are removed. This makes repeated undo requests
    idempotent and avoids discarding interventions logged after `/clear`.
    """
    from oura_agent.utils import now_nyc

    path = _snapshot_path(snapshot_id)
    if not path.exists():
        return {
            "status": "not_found",
            "snapshot_id": snapshot_id,
            "restored_count": 0,
        }

    try:
        with open(path) as handle:
            snapshot = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"Could not restore intervention snapshot {snapshot_id}: {exc}")
        return {
            "status": "invalid",
            "snapshot_id": snapshot_id,
            "restored_count": 0,
        }

    target_date = _validate_date(snapshot.get("date", ""))
    snapshot_entries = snapshot.get("entries", [])
    if not isinstance(snapshot_entries, list):
        return {
            "status": "invalid",
            "snapshot_id": snapshot_id,
            "restored_count": 0,
        }

    normalized_update_id = (
        str(source_update_id) if source_update_id is not None else None
    )
    if (
        normalized_update_id is not None
        and snapshot.get("restore_update_id") == normalized_update_id
    ):
        return {
            "status": "restored",
            "date": target_date,
            "snapshot_id": snapshot_id,
            "restored_count": len(snapshot_entries),
            "active_count": len(
                load_interventions(target_date).get("entries", [])
            ),
        }

    current_entries = (
        load_interventions(target_date).get("entries", [])
        if merge
        else []
    )
    combined = []
    seen = set()
    for entry in [*snapshot_entries, *current_entries]:
        marker = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        if marker in seen:
            continue
        seen.add(marker)
        combined.append(entry)

    _write_active_entries(target_date, combined)
    snapshot["restored_at"] = now_nyc().isoformat()
    snapshot["last_restore_merged"] = bool(merge)
    snapshot["restore_update_id"] = normalized_update_id
    _write_json_atomic(path, snapshot)
    logger.info(
        f"Restored intervention snapshot {snapshot_id} for {target_date}"
    )
    return {
        "status": "restored",
        "date": target_date,
        "snapshot_id": snapshot_id,
        "restored_count": len(snapshot_entries),
        "active_count": len(combined),
    }


def undo_clear_interventions(
    date: str = None,
    source_update_id: str | int | None = None,
) -> dict:
    """Restore the newest not-yet-restored clear snapshot for a date."""
    from oura_agent.utils import now_nyc

    target_date = _validate_date(
        date or now_nyc().strftime("%Y-%m-%d")
    )
    normalized_update_id = (
        str(source_update_id) if source_update_id is not None else None
    )
    if normalized_update_id is not None:
        for previous in list_clear_snapshots(
            date=target_date,
            include_restored=True,
        ):
            if previous.get("restore_update_id") == normalized_update_id:
                return {
                    "status": "restored",
                    "date": target_date,
                    "snapshot_id": previous["snapshot_id"],
                    "restored_count": len(previous.get("entries", [])),
                    "active_count": len(
                        load_interventions(target_date).get("entries", [])
                    ),
                }

    snapshots = list_clear_snapshots(
        date=target_date,
        include_restored=False,
    )
    if not snapshots:
        return {
            "status": "no_snapshot",
            "date": target_date,
            "snapshot_id": None,
            "restored_count": 0,
        }
    return restore_interventions_snapshot(
        snapshots[0]["snapshot_id"],
        merge=True,
        source_update_id=normalized_update_id,
    )


# Readable aliases for integrations that prefer verb-first naming.
clear_interventions_soft = soft_clear_interventions
restore_interventions = restore_interventions_snapshot


def get_today_interventions() -> list:
    """Get today's logged interventions."""
    from oura_agent.utils import now_nyc

    today = now_nyc().strftime("%Y-%m-%d")
    data = load_interventions(today)
    return data.get("entries", [])
