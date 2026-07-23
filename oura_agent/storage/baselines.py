"""
Baseline metrics management.
"""

import json
from copy import deepcopy
from numbers import Real
import statistics
import uuid

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

    persisted = None
    candidates = [
        BASELINES_FILE,
        BASELINES_FILE.with_suffix(BASELINES_FILE.suffix + ".backup"),
    ]
    for index, path in enumerate(candidates):
        if not path.exists():
            continue
        try:
            with open(path) as handle:
                value = json.load(handle)
            if isinstance(value, dict):
                persisted = value
                if index:
                    logger.warning("Loaded last-known-good baseline backup")
                break
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Could not load baselines from %s: %s", path, exc)

    if persisted is None:
        return defaults

    # Merge: ensure all default metrics exist in persisted baselines
    if "metrics" not in persisted:
        persisted["metrics"] = {}

    for metric, default_data in defaults["metrics"].items():
        if metric not in persisted["metrics"]:
            logger.info(f"Adding new baseline metric: {metric}")
            persisted["metrics"][metric] = default_data

    return persisted


def _write_json_atomic(path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with open(temporary, "x") as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
    temporary.replace(path)


def save_baselines(baselines: dict) -> None:
    """Atomically save baselines and retain the previous valid generation."""
    backup = BASELINES_FILE.with_suffix(BASELINES_FILE.suffix + ".backup")
    if BASELINES_FILE.exists():
        try:
            with open(BASELINES_FILE) as handle:
                previous = json.load(handle)
            if isinstance(previous, dict):
                _write_json_atomic(backup, previous)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "Existing baselines are invalid; preserving prior backup: %s",
                exc,
            )
    _write_json_atomic(BASELINES_FILE, baselines)


def _is_numeric(value) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool)


def _migrate_metric_observations(metric_data: dict, dates: list) -> tuple:
    """Migrate a legacy values-only metric without inventing sparse dates.

    Legacy files stored one shared dates array but independently skipped missing
    metric values.  A value can only be assigned to a date safely when the
    lengths match.  Otherwise the old values remain explicitly undated: they
    still contribute to the baseline until aged out, but correcting one date
    can never delete a different date's value.
    """
    existing_observations = metric_data.get("observations")
    if isinstance(existing_observations, dict):
        observations = {
            str(observation_date): value
            for observation_date, value in existing_observations.items()
            if _is_numeric(value)
        }
        undated = [
            value
            for value in metric_data.get("undated_values", [])
            if _is_numeric(value)
        ]
        return observations, undated

    values = [
        value for value in metric_data.get("values", []) if _is_numeric(value)
    ]
    if len(values) == len(dates):
        return dict(zip(dates, values)), []
    return {}, values


def _recalculate_metric(metric_data: dict, observations: dict, undated: list, window: int) -> None:
    """Refresh compatibility values and statistics from safe observations."""
    ordered_observations = [
        observations[observation_date]
        for observation_date in sorted(observations)
    ]
    remaining_undated = max(0, window - len(ordered_observations))
    undated = undated[-remaining_undated:] if remaining_undated else []
    values = undated + ordered_observations

    metric_data["observations"] = {
        observation_date: observations[observation_date]
        for observation_date in sorted(observations)
    }
    if undated:
        metric_data["undated_values"] = undated
    else:
        metric_data.pop("undated_values", None)
    metric_data["values"] = values

    if len(values) >= 2:
        metric_data["mean"] = round(statistics.mean(values), 1)
        metric_data["std"] = round(statistics.stdev(values), 1)
    elif len(values) == 1:
        metric_data["mean"] = values[0]
        metric_data["std"] = 0
    else:
        # Do not keep reporting an observation that has rolled out of the
        # window merely because its old aggregate was persisted.
        metric_data["mean"] = None
        metric_data["std"] = None


def update_baselines(
    baselines: dict,
    new_metrics: dict,
    date: str,
    window: int = BASELINE_WINDOW_DAYS,
) -> dict:
    """Update rolling baselines with per-metric dated observations.

    The returned object retains the legacy ``values`` arrays for readers, while
    ``observations`` becomes the correction-safe source of truth.
    """
    from oura_agent.utils import now_nyc

    window = max(1, int(window))
    updated = deepcopy(baselines)
    updated.setdefault("metrics", {})

    # Keep all persisted/default metric definitions, but normalize chronology.
    dates_seen = sorted(set(updated.get("dates", [])))
    recognized_values = {
        metric: value
        for metric, value in new_metrics.items()
        if metric in updated["metrics"] and _is_numeric(value)
    }
    if recognized_values:
        dates_seen = sorted(set(dates_seen + [date]))
    dates_seen = dates_seen[-window:]
    retained_dates = set(dates_seen)

    for metric, metric_data in updated["metrics"].items():
        observations, undated = _migrate_metric_observations(
            metric_data,
            sorted(set(updated.get("dates", []))),
        )

        # Remove observations that have rolled out of the calendar window.
        observations = {
            observation_date: value
            for observation_date, value in observations.items()
            if observation_date in retained_dates
        }

        # None/missing values are deliberately a no-op. A partial regeneration
        # must not erase the last known good observation.
        if metric in recognized_values and date in retained_dates:
            observations[date] = recognized_values[metric]

        _recalculate_metric(metric_data, observations, undated, window)

    updated["schema_version"] = 2
    updated["window_days"] = window
    updated["dates"] = dates_seen
    updated["last_updated"] = now_nyc().isoformat()
    updated["data_points"] = len(
        updated["metrics"].get("sleep_score", {}).get("values", [])
    )
    return updated
