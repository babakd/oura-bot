"""
Baseline metrics management.
"""

import json
import statistics

from oura_agent.config import BASELINES_FILE, BASELINE_WINDOW_DAYS, logger


def get_default_baselines() -> dict:
    """Return default baselines with population averages."""
    return {
        "last_updated": None,
        "dates": [],
        "data_points": 0,
        "window_days": BASELINE_WINDOW_DAYS,
        "metrics": {
            # Sleep metrics
            "sleep_score": {"mean": 75, "std": 10, "values": []},
            "hrv": {"mean": 45, "std": 10, "values": []},
            "deep_sleep_minutes": {"mean": 70, "std": 15, "values": []},
            "light_sleep_minutes": {"mean": 200, "std": 30, "values": []},
            "rem_sleep_minutes": {"mean": 90, "std": 20, "values": []},
            "sleep_efficiency": {"mean": 85, "std": 5, "values": []},
            "latency_minutes": {"mean": 15, "std": 10, "values": []},
            "total_sleep_minutes": {"mean": 420, "std": 45, "values": []},
            # Vitals
            "resting_hr": {"mean": 55, "std": 5, "values": []},
            "daytime_hr_avg": {"mean": 70, "std": 8, "values": []},
            # Recovery
            "readiness": {"mean": 75, "std": 10, "values": []},
            "stress_high": {"mean": 60, "std": 30, "values": []},
            "recovery_high": {"mean": 120, "std": 45, "values": []},
            # Activity
            "workout_minutes": {"mean": 30, "std": 20, "values": []},
            "workout_calories": {"mean": 200, "std": 150, "values": []},
        }
    }


def load_baselines() -> dict:
    """
    Load existing baselines, merging with defaults to handle schema changes.

    This ensures new metrics added to defaults will be available even in
    existing deployments with persisted baselines.
    """
    defaults = get_default_baselines()

    if not BASELINES_FILE.exists():
        return defaults

    with open(BASELINES_FILE) as f:
        persisted = json.load(f)

    # Merge: ensure all default metrics exist in persisted baselines
    if "metrics" not in persisted:
        persisted["metrics"] = {}

    for metric, default_data in defaults["metrics"].items():
        if metric not in persisted["metrics"]:
            logger.info(f"Adding new baseline metric: {metric}")
            persisted["metrics"][metric] = default_data

    return persisted


def update_baselines(baselines: dict, new_metrics: dict, date: str, window: int = BASELINE_WINDOW_DAYS) -> dict:
    """
    Update rolling baselines with new data.

    If the date already exists, replaces the old values (allows corrections).
    """
    from oura_agent.utils import now_nyc

    # Track which dates we have data for
    dates_seen = baselines.get("dates", [])

    # If date exists, remove old values before adding new ones (allows corrections)
    if date in dates_seen:
        date_index = dates_seen.index(date)
        logger.info(f"Replacing baseline data for {date} (index {date_index})")

        # Remove old value at this index for each metric
        for metric in baselines["metrics"]:
            values = baselines["metrics"][metric].get("values", [])
            if len(values) > date_index:
                values.pop(date_index)
                baselines["metrics"][metric]["values"] = values

        dates_seen.remove(date)

    # Add this date to our tracking
    dates_seen.append(date)
    dates_seen = dates_seen[-window:]
    baselines["dates"] = dates_seen

    for metric, value in new_metrics.items():
        if metric in baselines["metrics"] and value is not None:
            values = baselines["metrics"][metric].get("values", [])
            values.append(value)
            values = values[-window:]
            baselines["metrics"][metric]["values"] = values

            if len(values) >= 2:
                baselines["metrics"][metric]["mean"] = round(statistics.mean(values), 1)
                baselines["metrics"][metric]["std"] = round(statistics.stdev(values), 1)
            elif len(values) == 1:
                baselines["metrics"][metric]["mean"] = values[0]
                baselines["metrics"][metric]["std"] = 0

    baselines["last_updated"] = now_nyc().isoformat()
    baselines["data_points"] = len(baselines["metrics"].get("sleep_score", {}).get("values", []))

    return baselines
