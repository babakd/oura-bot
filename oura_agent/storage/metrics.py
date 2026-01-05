"""
Metrics and briefs file storage.
"""

import json
from datetime import timedelta

from oura_agent.config import METRICS_DIR, BRIEFS_DIR


def _ensure_metrics_dir():
    """Ensure metrics directory exists."""
    METRICS_DIR.mkdir(parents=True, exist_ok=True)


def _ensure_briefs_dir():
    """Ensure briefs directory exists."""
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)


def load_historical_metrics(days: int = None) -> list:
    """Load extracted metrics from the past N days, or all if days=None.

    Args:
        days: Number of days to load. If None, loads all available metrics.
    """
    from oura_agent.utils import now_nyc

    _ensure_metrics_dir()

    if days is None:
        # Load all available metrics files
        metrics_history = []
        for metrics_file in sorted(METRICS_DIR.glob("*.json"), reverse=True):
            try:
                with open(metrics_file) as f:
                    data = json.load(f)
                    date = metrics_file.stem  # YYYY-MM-DD from filename
                    metrics_history.append({"date": date, **data})
            except (json.JSONDecodeError, IOError):
                continue
        return metrics_history

    # Load specific number of days
    metrics_history = []
    for i in range(days):
        date = (now_nyc() - timedelta(days=i)).strftime("%Y-%m-%d")
        metrics_file = METRICS_DIR / f"{date}.json"
        if metrics_file.exists():
            with open(metrics_file) as f:
                data = json.load(f)
                metrics_history.append({"date": date, **data})
    return metrics_history


def save_daily_metrics(
    date: str,
    metrics: dict = None,
    detailed_sleep: dict = None,
    detailed_workouts: list = None,
    merge: bool = False
):
    """Save extracted metrics for a day.

    Args:
        date: Date string (YYYY-MM-DD)
        metrics: Summary metrics dict
        detailed_sleep: Detailed sleep data dict
        detailed_workouts: List of workout dicts
        merge: If True, merge with existing file instead of overwriting
    """
    _ensure_metrics_dir()

    metrics_file = METRICS_DIR / f"{date}.json"

    # Load existing data if merging
    existing_data = {}
    if merge and metrics_file.exists():
        try:
            with open(metrics_file) as f:
                existing_data = json.load(f)
        except (json.JSONDecodeError, IOError):
            existing_data = {}

    # Build new data, merging with existing if requested
    data = {"date": date}

    # Handle summary metrics
    if metrics is not None:
        if merge and "summary" in existing_data:
            merged_summary = existing_data.get("summary", {}).copy()
            merged_summary.update(metrics)
            data["summary"] = merged_summary
        else:
            data["summary"] = metrics
    elif "summary" in existing_data:
        data["summary"] = existing_data["summary"]
    else:
        data["summary"] = {}

    # Handle detailed sleep
    if detailed_sleep is not None:
        data["detailed_sleep"] = detailed_sleep
    elif "detailed_sleep" in existing_data:
        data["detailed_sleep"] = existing_data["detailed_sleep"]
    else:
        data["detailed_sleep"] = {}

    # Handle detailed workouts
    if detailed_workouts is not None:
        data["detailed_workouts"] = detailed_workouts
    elif "detailed_workouts" in existing_data:
        data["detailed_workouts"] = existing_data["detailed_workouts"]
    else:
        data["detailed_workouts"] = []

    with open(metrics_file, 'w') as f:
        json.dump(data, f, indent=2)


def load_recent_briefs(days: int = 3) -> list:
    """Load recent briefs for context."""
    from oura_agent.utils import now_nyc

    _ensure_briefs_dir()

    briefs = []
    for i in range(1, days + 1):
        date = (now_nyc() - timedelta(days=i)).strftime("%Y-%m-%d")
        brief_file = BRIEFS_DIR / f"{date}.md"
        if brief_file.exists():
            with open(brief_file) as f:
                briefs.append({"date": date, "content": f.read()})
    return briefs
