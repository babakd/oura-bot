"""
Intervention logging and storage.
"""

import json
from datetime import timedelta

from oura_agent.config import INTERVENTIONS_DIR, logger


def _ensure_interventions_dir():
    """Ensure interventions directory exists."""
    INTERVENTIONS_DIR.mkdir(parents=True, exist_ok=True)


def _migrate_json_to_jsonl(date: str):
    """Migrate legacy .json file to .jsonl format if needed."""
    json_file = INTERVENTIONS_DIR / f"{date}.json"
    jsonl_file = INTERVENTIONS_DIR / f"{date}.jsonl"

    # Only migrate if .json exists and .jsonl doesn't
    if json_file.exists() and not jsonl_file.exists():
        data = load_interventions(date)  # This reads the .json file
        if data.get("entries"):
            with open(jsonl_file, 'w') as f:
                for entry in data["entries"]:
                    f.write(json.dumps(entry) + "\n")
            logger.info(f"Migrated {len(data['entries'])} entries from {json_file} to {jsonl_file}")
        # Remove legacy file after migration
        json_file.unlink()


def load_interventions(date: str) -> dict:
    """
    Load interventions for a given date. Returns full data structure.

    Supports both JSONL (new format) and JSON (legacy format) for backwards compatibility.
    """
    _ensure_interventions_dir()

    # Try JSONL first (new format)
    jsonl_file = INTERVENTIONS_DIR / f"{date}.jsonl"
    if jsonl_file.exists():
        entries = []
        with open(jsonl_file) as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        logger.warning(f"Skipped corrupt line {line_num} in {jsonl_file}: {e}")
        return {"date": date, "entries": entries}

    # Fall back to JSON (legacy format)
    json_file = INTERVENTIONS_DIR / f"{date}.json"
    if json_file.exists():
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
            return data

    return {"date": date, "entries": []}


def save_interventions(date: str, data: dict):
    """
    Save interventions for a given date.

    Writes JSONL format and removes any legacy JSON file.
    """
    _ensure_interventions_dir()

    jsonl_file = INTERVENTIONS_DIR / f"{date}.jsonl"
    json_file = INTERVENTIONS_DIR / f"{date}.json"

    # Write as JSONL
    with open(jsonl_file, 'w') as f:
        for entry in data.get("entries", []):
            f.write(json.dumps(entry) + "\n")

    # Remove legacy JSON file if it exists (migration complete)
    if json_file.exists():
        json_file.unlink()


def load_historical_interventions(days: int = 28) -> dict:
    """Load all interventions from the past N days. Returns {date: {date, entries}}."""
    from oura_agent.utils import now_nyc

    interventions_by_date = {}
    for i in range(days):
        date = (now_nyc() - timedelta(days=i)).strftime("%Y-%m-%d")
        data = load_interventions(date)
        if data.get("entries"):
            interventions_by_date[date] = data
    return interventions_by_date


def save_intervention_raw(raw_text: str, cleaned_text: str = None) -> dict:
    """
    Save an intervention to today's file using atomic append.

    Uses JSONL (one JSON object per line) for append-only writes.
    This avoids read-modify-write race conditions with the daily job.

    Args:
        raw_text: Original user input (kept for audit)
        cleaned_text: Claude-cleaned version (used for display/analysis)
    """
    from oura_agent.utils import now_nyc

    today = now_nyc().strftime("%Y-%m-%d")
    time = now_nyc().strftime("%H:%M")

    _ensure_interventions_dir()

    # Migrate legacy .json to .jsonl if needed (before first write)
    _migrate_json_to_jsonl(today)

    interventions_file = INTERVENTIONS_DIR / f"{today}.jsonl"

    entry = {
        "time": time,
        "raw": raw_text,
        "cleaned": cleaned_text or raw_text,
    }

    # Atomic append - no read required, avoids race conditions
    with open(interventions_file, 'a') as f:
        f.write(json.dumps(entry) + "\n")

    return entry


def get_today_interventions() -> list:
    """Get today's logged interventions."""
    from oura_agent.utils import now_nyc

    today = now_nyc().strftime("%Y-%m-%d")
    data = load_interventions(today)
    return data.get("entries", [])
