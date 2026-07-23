"""Deterministic daily insight packet and Telegram-card rendering.

The language model selects and explains from this packet. All displayed
numbers are rendered from the packet itself so a fluent response cannot invent
or subtly alter biometric values.
"""

from __future__ import annotations

import math
import re
import statistics
from datetime import datetime
from typing import Any, Iterable


METRICS: dict[str, dict[str, Any]] = {
    "readiness": {
        "label": "Readiness",
        "unit": "",
        "direction": 1,
        "importance": 1.25,
        "domain": "recovery",
    },
    "sleep_score": {
        "label": "Sleep",
        "unit": "",
        "direction": 1,
        "importance": 1.2,
        "domain": "sleep_quality",
    },
    "hrv": {
        "label": "HRV",
        "unit": " ms",
        "direction": 1,
        "importance": 1.15,
        "domain": "recovery",
    },
    "resting_hr": {
        "label": "Resting HR",
        "unit": " bpm",
        "direction": -1,
        "importance": 1.0,
        "domain": "recovery",
    },
    "deep_sleep_minutes": {
        "label": "Deep sleep",
        "unit": " min",
        "direction": 1,
        "importance": 0.85,
        "domain": "sleep_quality",
    },
    "rem_sleep_minutes": {
        "label": "REM sleep",
        "unit": " min",
        "direction": 1,
        "importance": 0.75,
        "domain": "sleep_quality",
        "decision_metric": False,
    },
    "total_sleep_minutes": {
        "label": "Total sleep",
        "unit": " min",
        "direction": 1,
        "importance": 0.9,
        "domain": "sleep_duration",
    },
    "sleep_efficiency": {
        "label": "Efficiency",
        "unit": "%",
        "direction": 1,
        "importance": 0.7,
        "domain": "sleep_quality",
    },
    "stress_high": {
        "label": "High stress",
        "unit": " min",
        "direction": -1,
        "importance": 0.65,
        "domain": "stress",
    },
    "recovery_high": {
        "label": "Restorative time",
        "unit": " min",
        "direction": 1,
        "importance": 0.65,
        "domain": "recovery",
    },
    "daytime_hr_avg": {
        "label": "Daytime HR",
        "unit": " bpm",
        "direction": -1,
        "importance": 0.6,
        "domain": "stress",
    },
}

CARD_MAX_CHARS = 1200
MIN_PERSONAL_BASELINE_N = 7
SNAPSHOT_GROUPS = (
    ("sleep_score", "readiness"),
    ("total_sleep_minutes", "deep_sleep_minutes", "rem_sleep_minutes"),
    ("hrv", "resting_hr"),
)
CANONICAL_ACTION_DOMAINS = sorted(
    {config["domain"] for config in METRICS.values()}
    | {"data_quality", "no_action"}
)
DECISION_METRIC_KEYS = sorted(
    key
    for key, config in METRICS.items()
    if config.get("decision_metric", True)
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def _round(value: float | None, digits: int = 1) -> float | None:
    return round(value, digits) if value is not None else None


def _chronological(history: Iterable[dict]) -> list[dict]:
    """Return one valid record per date in ascending order."""
    by_date: dict[str, dict] = {}
    for row in history:
        date = str(row.get("date", ""))
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            by_date[date] = row
    return [by_date[date] for date in sorted(by_date)]


def _linear_slope(values: list[float]) -> float | None:
    if len(values) < 3:
        return None
    x_mean = (len(values) - 1) / 2
    y_mean = statistics.mean(values)
    denominator = sum((i - x_mean) ** 2 for i in range(len(values)))
    if denominator == 0:
        return None
    return sum((i - x_mean) * (value - y_mean) for i, value in enumerate(values)) / denominator


def _percentile(value: float, comparison: list[float]) -> float | None:
    if len(comparison) < 3:
        return None
    below = sum(1 for item in comparison if item < value)
    equal = sum(1 for item in comparison if item == value)
    return round(100 * (below + 0.5 * equal) / len(comparison))


def _fmt_value(value: float | int | None, unit: str) -> str:
    if value is None:
        return "not recorded"
    numeric = float(value)
    shown = str(int(numeric)) if numeric.is_integer() else f"{numeric:.1f}"
    return f"{shown}{unit}"


def format_metric_evidence(metric: dict) -> str:
    """Format one factual signal from a computed metric record."""
    current = _fmt_value(metric.get("current"), metric["unit"])
    mean = metric.get("baseline_mean")
    z_score = metric.get("z_score")
    label = metric.get("evidence_label") or metric["label"]
    if mean is None:
        return f"{label} {current}"

    baseline = _fmt_value(mean, metric["unit"])
    if z_score is None:
        return f"{label} {current} vs usual {baseline}"

    sign = "+" if z_score > 0 else ""
    return f"{label} {current} vs usual {baseline} ({sign}{z_score:.1f}σ)"


def _metric_source(
    today: str,
    key: str,
    metric_provenance: dict | None,
) -> tuple[str, str, str]:
    """Return source date, source kind, and a user-facing evidence label prefix."""
    provenance = (metric_provenance or {}).get(key, {})
    if not isinstance(provenance, dict):
        provenance = {}

    source_date = str(provenance.get("source_date") or today)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", source_date):
        source_date = today
    source = _plain_text(provenance.get("source") or "current", 30)

    prefix = ""
    try:
        today_date = datetime.strptime(today, "%Y-%m-%d").date()
        source_day = datetime.strptime(source_date, "%Y-%m-%d").date()
        days_ago = (today_date - source_day).days
        if days_ago == 1:
            prefix = "Yesterday’s "
        elif days_ago != 0:
            prefix = f"{source_date} "
    except ValueError:
        source_date = today

    return source_date, source, prefix


def build_daily_insight_packet(
    today: str,
    current_metrics: dict,
    detailed_sleep: dict,
    historical_metrics: Iterable[dict],
    baselines: dict,
    profile: dict | None = None,
    feedback_summary: dict | None = None,
    fetch_status: dict | None = None,
    generated_at: datetime | None = None,
    metric_provenance: dict | None = None,
) -> dict:
    """Build the stable, JSON-serializable packet used by the daily card."""
    history = _chronological(historical_metrics)

    baseline_metrics = baselines.get("metrics", {})
    metric_rows: dict[str, dict] = {}
    candidates: list[dict] = []

    for key, config in METRICS.items():
        current = _number(current_metrics.get(key))
        if current is None:
            continue

        source_date, source, evidence_prefix = _metric_source(
            today,
            key,
            metric_provenance,
        )
        baseline = baseline_metrics.get(key, {})
        baseline_values = [_number(value) for value in baseline.get("values", [])]
        baseline_values = [value for value in baseline_values if value is not None]
        baseline_n = len(baseline_values)
        personal_baseline_ready = baseline_n >= MIN_PERSONAL_BASELINE_N
        # Defaults are population priors used only to initialize storage. They
        # must never be described as the user's "usual" range. Statistical
        # comparisons begin only after a minimum personal history exists.
        mean = (
            _number(baseline.get("mean"))
            if personal_baseline_ready
            else None
        )
        std = (
            _number(baseline.get("std"))
            if personal_baseline_ready
            else None
        )

        prior_values = []
        for row in history:
            # The current observation is passed separately and is authoritative.
            # Excluding its source date prevents yesterday's already-persisted
            # activity from appearing as both "previous" and "current".
            if row["date"] >= source_date:
                continue
            value = _number(row.get("summary", {}).get(key))
            if value is not None:
                prior_values.append(value)

        z_score = None
        if mean is not None and std is not None and std > 0:
            z_score = (current - mean) / std

        recent = (prior_values + [current])[-7:]
        slope = _linear_slope(recent)
        previous = prior_values[-1] if prior_values else None
        delta = current - previous if previous is not None else None
        direction_adjusted_z = z_score * config["direction"] if z_score is not None else None

        if direction_adjusted_z is None or abs(direction_adjusted_z) < 1:
            polarity = "typical"
        elif direction_adjusted_z > 0:
            polarity = "positive"
        else:
            polarity = "caution"

        row = {
            "key": key,
            "label": config["label"],
            "evidence_label": evidence_prefix + config["label"],
            "unit": config["unit"],
            "domain": config["domain"],
            "direction": config["direction"],
            "decision_metric": config.get("decision_metric", True),
            "source_date": source_date,
            "source": source,
            "current": _round(current),
            "previous": _round(previous),
            "delta_from_previous": _round(delta),
            "baseline_mean": _round(mean),
            "baseline_std": _round(std),
            "baseline_n": baseline_n,
            "personal_baseline_ready": personal_baseline_ready,
            "z_score": _round(z_score, 2),
            "direction_adjusted_z": _round(direction_adjusted_z, 2),
            "personal_percentile": _percentile(current, prior_values[-60:]),
            "recent_7_average": _round(statistics.mean(recent) if recent else None),
            "recent_7_count": len(recent),
            "recent_7_slope_per_day": _round(slope, 2),
            "polarity": polarity,
        }
        row["evidence"] = format_metric_evidence(row)
        metric_rows[key] = row

        anomaly = abs(z_score) if z_score is not None else 0
        trend_strength = 0
        if slope is not None and std is not None and std > 0:
            trend_strength = min(abs(slope) / std, 1.5)
        if row["decision_metric"]:
            candidates.append(
                {
                    "metric_key": key,
                    "domain": config["domain"],
                    "polarity": polarity,
                    "source_date": source_date,
                    "source": source,
                    "priority": round(
                        config["importance"] * (anomaly + 0.35 * trend_strength),
                        3,
                    ),
                    "evidence": row["evidence"],
                }
            )

    candidates.sort(key=lambda item: item["priority"], reverse=True)
    concerning = [
        row for row in metric_rows.values()
        if row.get("decision_metric", True)
        and row.get("direction_adjusted_z") is not None
        and row["direction_adjusted_z"] <= -1
    ]
    strong = [
        row for row in metric_rows.values()
        if row.get("decision_metric", True)
        and row.get("direction_adjusted_z") is not None
        and row["direction_adjusted_z"] >= 1
    ]

    sleep_recorded = current_metrics.get("sleep_recorded") is not False
    status_items = list((fetch_status or {}).items())
    required_failures = [
        status for _, status in status_items
        if status.get("required") and not status.get("ok")
    ]
    required_empty = [
        endpoint for endpoint, status in status_items
        if (
            status.get("required")
            and status.get("ok") is True
            and status.get("count") == 0
        )
    ]
    optional_failures = [
        status for _, status in status_items
        if not status.get("required") and not status.get("ok")
    ]
    stale_sources = [status for _, status in status_items if status.get("stale")]
    personal_baseline_count = sum(
        1 for row in metric_rows.values() if row["personal_baseline_ready"]
    )
    if required_failures:
        quality = "failed"
    elif (
        not sleep_recorded
        or required_empty
        or optional_failures
        or stale_sources
    ):
        quality = "partial"
    else:
        quality = "complete"

    if quality == "failed":
        state = "data_unavailable"
    elif not sleep_recorded:
        state = "sleep_missing"
    elif metric_rows and personal_baseline_count == 0:
        state = "baseline_building"
    elif any(row["direction_adjusted_z"] <= -1.5 for row in concerning):
        state = "caution"
    elif strong and not concerning:
        state = "strong"
    else:
        state = "typical"

    suppressed_domains = set((feedback_summary or {}).get("not_for_me_domains", []))
    for candidate in candidates:
        candidate["previously_rejected"] = candidate["domain"] in suppressed_domains

    generated = generated_at or datetime.now().astimezone()
    return {
        "schema_version": 1,
        "date": today,
        "state": state,
        "no_action_recommended": state in {
            "typical",
            "strong",
            "baseline_building",
        },
        "metrics": metric_rows,
        "ranked_candidates": candidates[:5],
        "freshness": {
            "generated_at": generated.isoformat(),
            "sleep_session_ended_at": detailed_sleep.get("bedtime_end"),
            "data_quality": quality,
            "sleep_recorded": sleep_recorded,
            "required_empty_endpoints": required_empty,
            "has_stale_sources": bool(stale_sources),
            "endpoints": fetch_status or {},
        },
        "baseline": {
            "minimum_personal_samples": MIN_PERSONAL_BASELINE_N,
            "ready_metric_count": personal_baseline_count,
        },
        "personal_context": profile or {},
        "feedback_context": feedback_summary or {},
    }


def default_card_from_packet(packet: dict) -> dict:
    """Produce a safe useful card when model generation is unavailable."""
    state = packet.get("state")
    candidates = packet.get("ranked_candidates", [])
    caution_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("polarity") == "caution"
    ]
    strong_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("polarity") == "positive"
    ]
    top = (
        caution_candidates[0]
        if state == "caution" and caution_candidates
        else (candidates[0] if candidates else None)
    )

    if state == "data_unavailable":
        return {
            "headline": "Oura data is unavailable",
            "observation": "Required Oura data could not be verified, so I am not drawing a recovery conclusion.",
            "decision": "Keep your existing plan and try /regen-brief after Oura is reachable.",
            "action_domain": "data_quality",
            "evidence_keys": [],
            "confidence": "low",
            "no_action": False,
            "review_after": "after the next successful sync",
            "expected_outcome": "a complete, evidence-based card",
        }

    if state == "sleep_missing":
        return {
            "headline": "Sleep data is not ready",
            "observation": "A complete sleep session was not available, so I am not inferring recovery from the gap.",
            "decision": "Sync Oura and try /regen-brief later. Until then, use how you feel and your existing plan.",
            "action_domain": "data_quality",
            "evidence_keys": [],
            "confidence": "low",
            "no_action": False,
            "review_after": "after the next sync",
            "expected_outcome": "a complete, evidence-based card",
        }

    if state == "baseline_building":
        evidence_keys = [
            item["metric_key"]
            for item in candidates[:2]
            if item["metric_key"] in packet.get("metrics", {})
        ]
        return {
            "headline": "Building your personal baseline",
            "observation": (
                "Today’s signals are recorded, but there is not enough personal "
                "history yet to call them unusual."
            ),
            "decision": "Follow your normal plan.",
            "action_domain": "no_action",
            "evidence_keys": evidence_keys,
            "confidence": "low",
            "no_action": True,
            "review_after": "after more personal observations",
            "expected_outcome": "a more reliable personal comparison",
        }

    if state == "caution" and top:
        label = METRICS.get(top["metric_key"], {}).get("label", "Recovery")
        supporting = caution_candidates[:2] or [top]
        return {
            "headline": f"{label} is the main caveat",
            "observation": (
                "The strongest caution signal is outside your typical range. "
                "Treat it as a signal, not a diagnosis."
            ),
            "decision": "Keep the day flexible and let energy, soreness, and your planned activity decide the intensity.",
            "action_domain": top["domain"],
            "evidence_keys": [item["metric_key"] for item in supporting],
            "confidence": "medium",
            "no_action": False,
            "review_after": "tomorrow",
            "expected_outcome": "avoid overreacting while checking whether the signal persists",
        }

    if state == "strong" and strong_candidates:
        label = METRICS.get(
            strong_candidates[0]["metric_key"], {}
        ).get("label", "Recovery")
        return {
            "headline": f"{label} is notably strong",
            "observation": (
                "At least one verified signal is above your personal range, "
                "with no meaningful caution signal present. That supports the "
                "normal plan without requiring extra intensity."
            ),
            "decision": "Follow your normal plan.",
            "action_domain": "no_action",
            "evidence_keys": [
                item["metric_key"] for item in strong_candidates[:2]
            ],
            "confidence": "medium",
            "no_action": True,
            "review_after": "tomorrow",
            "expected_outcome": "maintain the routine without manufacturing an intervention",
        }

    evidence_keys = [item["metric_key"] for item in candidates[:2]]
    return {
        "headline": "Nothing unusual today",
        "observation": (
            "Your main recovery and sleep signals are close to your personal "
            "range, with no verified signal strong enough to justify changing "
            "the plan."
        ),
        "decision": "Follow your normal plan.",
        "action_domain": "no_action",
        "evidence_keys": evidence_keys,
        "confidence": "medium" if evidence_keys else "low",
        "no_action": True,
        "review_after": "tomorrow",
        "expected_outcome": "maintain the routine without manufacturing an intervention",
    }


def _plain_text(value: Any, limit: int) -> str:
    text = re.sub(r"[*_`#\[\]]", "", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    shortened = text[: max(1, limit - 1)].rsplit(" ", 1)[0]
    return (shortened or text[: max(1, limit - 1)]) + "…"


def validate_model_card(card: dict, packet: dict) -> None:
    """Reject model selections that cannot be grounded deterministically."""
    if not isinstance(card, dict):
        raise ValueError("daily card must be an object")

    raw_keys = card.get("evidence_keys")
    if not isinstance(raw_keys, list) or any(
        not isinstance(key, str) for key in raw_keys
    ):
        raise ValueError("evidence_keys must be a list of strings")

    packet_metrics = packet.get("metrics", {})
    allowed_metrics = {
        key: metric
        for key, metric in packet_metrics.items()
        if metric.get("decision_metric", True)
    }
    if any(key not in allowed_metrics for key in raw_keys):
        raise ValueError("daily card contains an unknown or display-only evidence key")

    no_action = card.get("no_action")
    if not isinstance(no_action, bool):
        raise ValueError("no_action must be boolean")
    if packet.get("no_action_recommended") and not no_action:
        raise ValueError("an ordinary day cannot be converted into an action day")
    if not no_action and not raw_keys:
        raise ValueError("an action card requires validated evidence")
    if (
        packet.get("state") == "caution"
        and not no_action
        and allowed_metrics[raw_keys[0]].get("polarity") != "caution"
    ):
        raise ValueError("a caution action must use caution evidence first")

    expected_domain = (
        "no_action"
        if no_action
        else allowed_metrics[raw_keys[0]].get("domain")
    )
    if card.get("action_domain") != expected_domain:
        raise ValueError("action_domain is inconsistent with primary evidence")


def normalize_card(card: dict, packet: dict) -> dict:
    """Validate model-selected fields and fill safe deterministic defaults."""
    fallback = default_card_from_packet(packet)
    if (
        packet.get("state") in {"data_unavailable", "sleep_missing"}
        or packet.get("freshness", {}).get("data_quality") == "failed"
    ):
        card = fallback

    allowed_keys = {
        key
        for key, metric in packet.get("metrics", {}).items()
        if metric.get("decision_metric", True)
    }
    raw_evidence_keys = card.get("evidence_keys", [])
    invalid_evidence = (
        not isinstance(raw_evidence_keys, list)
        or any(
            not isinstance(key, str) or key not in allowed_keys
            for key in raw_evidence_keys
        )
    )
    evidence_keys = (
        raw_evidence_keys[:2]
        if not invalid_evidence
        else fallback["evidence_keys"]
    )

    requested_no_action = card.get("no_action", fallback["no_action"])
    no_action = (
        requested_no_action
        if isinstance(requested_no_action, bool)
        else fallback["no_action"]
    )
    if packet.get("no_action_recommended"):
        no_action = True

    if (
        invalid_evidence
        or (not no_action and not evidence_keys)
        or (
            packet.get("state") == "caution"
            and not no_action
            and evidence_keys
            and packet.get("metrics", {})
            .get(evidence_keys[0], {})
            .get("polarity") != "caution"
        )
    ):
        card = fallback
        evidence_keys = fallback["evidence_keys"]
        no_action = fallback["no_action"]

    expected_domain = (
        "no_action"
        if no_action
        else (
            packet.get("metrics", {}).get(evidence_keys[0], {}).get("domain")
            if evidence_keys
            else fallback["action_domain"]
        )
    )
    if card.get("action_domain") != expected_domain:
        card = fallback
        evidence_keys = fallback["evidence_keys"]
        no_action = fallback["no_action"]
        expected_domain = fallback["action_domain"]

    confidence = card.get("confidence")
    if confidence not in {"low", "medium", "high"}:
        confidence = fallback["confidence"]
    if (
        packet.get("freshness", {}).get("data_quality") == "partial"
        and confidence == "high"
    ):
        confidence = "medium"

    normalized = {
        "headline": _plain_text(card.get("headline") or fallback["headline"], 72),
        "observation": _plain_text(card.get("observation") or fallback["observation"], 360),
        "decision": _plain_text(card.get("decision") or fallback["decision"], 190),
        "action_domain": expected_domain,
        "evidence_keys": evidence_keys or fallback["evidence_keys"],
        "confidence": confidence,
        "no_action": no_action,
        "review_after": _plain_text(card.get("review_after") or fallback["review_after"], 60),
        "expected_outcome": _plain_text(
            card.get("expected_outcome") or fallback["expected_outcome"], 160
        ),
    }
    if normalized["no_action"]:
        normalized["decision"] = "Follow your normal plan."
        normalized["action_domain"] = "no_action"
        normalized["expected_outcome"] = (
            "maintain the routine without manufacturing an intervention"
        )
    if not normalized["decision"]:
        normalized["decision"] = "Follow your normal plan."
        normalized["no_action"] = True
        normalized["action_domain"] = "no_action"
    if (
        packet.get("state") == "typical"
        and packet.get("freshness", {}).get("data_quality") == "partial"
    ):
        normalized["headline"] = "No concern in the available data"
    normalized["display_evidence_keys"] = display_evidence_keys(
        normalized,
        packet,
    )
    return normalized


def display_evidence_keys(card: dict, packet: dict) -> list[str]:
    """Return every metric key whose value the renderer will show."""
    if packet.get("state") in {"data_unavailable", "sleep_missing"}:
        return []

    metrics = packet.get("metrics", {})
    keys: list[str] = []
    for group in SNAPSHOT_GROUPS:
        for key in group:
            if key in metrics and key not in keys:
                keys.append(key)
    if packet.get("state") != "baseline_building":
        for key in card.get("evidence_keys", []):
            if key in metrics and key not in keys:
                keys.append(key)
    return keys


def _compact_number(value: Any) -> str | None:
    numeric = _number(value)
    if numeric is None:
        return None
    if numeric.is_integer():
        return str(int(numeric))
    return f"{numeric:.1f}"


def _duration_label(value: Any, suffix: str) -> str | None:
    numeric = _number(value)
    if numeric is None:
        return None
    if numeric >= 60 and suffix in {"total", "per night"}:
        rounded = int(round(numeric))
        hours, minutes = divmod(rounded, 60)
        return f"{hours}h {minutes}m {suffix}"
    shown = _compact_number(numeric)
    return f"{shown}m {suffix}" if shown is not None else None


def _snapshot_fact(key: str, metric: dict) -> str | None:
    """Format one compact, deterministic morning snapshot fact."""
    value = metric.get("current")
    if key == "total_sleep_minutes":
        return _duration_label(value, "total")
    if key == "deep_sleep_minutes":
        return _duration_label(value, "deep")
    if key == "rem_sleep_minutes":
        return _duration_label(value, "REM")

    shown = _compact_number(value)
    if shown is None:
        return None
    if key == "sleep_score":
        return f"Sleep {shown}"
    if key == "readiness":
        return f"Readiness {shown}"
    if key == "hrv":
        return f"HRV {shown} ms"
    if key == "resting_hr":
        return f"Resting HR {shown} bpm"
    return None


def _snapshot_lines(packet: dict) -> list[str]:
    if packet.get("state") in {"data_unavailable", "sleep_missing"}:
        return []

    metrics = packet.get("metrics", {})
    lines = []
    for group in SNAPSHOT_GROUPS:
        facts = [
            fact
            for key in group
            if key in metrics
            for fact in [_snapshot_fact(key, metrics[key])]
            if fact is not None
        ]
        if facts:
            lines.append(" · ".join(facts))
    return lines


def _recent_context(card: dict, packet: dict) -> str | None:
    """Describe one recent average without implying consecutive calendar days."""
    metrics = packet.get("metrics", {})
    for key in card.get("evidence_keys", []):
        metric = metrics.get(key, {})
        count = metric.get("recent_7_count")
        average = metric.get("recent_7_average")
        if not isinstance(count, int) or count < 3 or _number(average) is None:
            continue

        current = metric.get("current")
        if key == "total_sleep_minutes":
            average_label = _duration_label(average, "per night")
            current_label = _duration_label(current, "today")
        else:
            average_label = _fmt_value(average, metric.get("unit", ""))
            current_label = _fmt_value(current, metric.get("unit", ""))

        current_number = _number(current)
        average_number = _number(average)
        std = _number(metric.get("baseline_std"))
        tolerance = (
            0.5 * std
            if std is not None and std > 0
            else max(abs(average_number or 0) * 0.05, 1)
        )
        in_line = (
            current_number is not None
            and average_number is not None
            and abs(current_number - average_number) <= tolerance
        )
        if in_line:
            detail = (
                f"is in line with its {average_label} average across "
                f"{count} recent observations"
            )
        else:
            detail = (
                f"is {current_label} today versus a recent average of "
                f"{average_label} across {count} observations"
            )
        return f"*Recent context:* {metric.get('label', key)} {detail}."
    return None


_ENDPOINT_LABELS = {
    "daily_sleep": "sleep score",
    "daily_readiness": "readiness",
    "daily_activity": "activity",
    "sleep": "sleep detail",
    "daily_stress": "stress",
    "heartrate": "daytime HR",
    "workout": "workouts",
}


def _freshness_issues(packet: dict) -> list[str]:
    endpoints = packet.get("freshness", {}).get("endpoints", {})
    if not isinstance(endpoints, dict):
        return []

    issues = []
    for endpoint, status in endpoints.items():
        if not isinstance(status, dict):
            continue
        label = _ENDPOINT_LABELS.get(endpoint, endpoint.replace("_", " "))
        if status.get("stale"):
            issue = f"{label} stale"
        elif (
            status.get("required")
            and status.get("ok") is True
            and status.get("count") == 0
        ):
            issue = f"{label} not ready"
        elif status.get("ok") is False:
            issue = f"{label} unavailable"
        else:
            continue
        if issue not in issues:
            issues.append(issue)
    return issues


def _activity_freshness_label(packet: dict) -> str | None:
    source_dates = sorted(
        {
            metric.get("source_date")
            for metric in packet.get("metrics", {}).values()
            if (
                isinstance(metric, dict)
                and metric.get("source") == "activity"
                and re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}",
                    str(metric.get("source_date", "")),
                )
            )
        }
    )
    if not source_dates:
        return None

    today = packet.get("date")
    if len(source_dates) == 1 and re.fullmatch(
        r"\d{4}-\d{2}-\d{2}",
        str(today or ""),
    ):
        source_date = source_dates[0]
        try:
            source_day = datetime.strptime(source_date, "%Y-%m-%d").date()
            current_day = datetime.strptime(today, "%Y-%m-%d").date()
            age = (current_day - source_day).days
            if age == 0:
                return "today’s activity included"
            if age == 1:
                return "yesterday’s activity included"
        except ValueError:
            pass
        return f"{source_date} activity included"
    return "dated activity included"


def _freshness_label(packet: dict) -> str:
    freshness = packet.get("freshness", {})
    ended = freshness.get("sleep_session_ended_at")
    quality = freshness.get("data_quality", "unknown")
    parts = []
    if ended:
        try:
            parsed = datetime.fromisoformat(ended.replace("Z", "+00:00"))
            time_label = parsed.strftime("%-I:%M %p")
            parts.append(f"Sleep through {time_label}")
        except (TypeError, ValueError):
            pass
    has_sleep_cutoff = bool(parts)
    if not has_sleep_cutoff:
        if quality == "failed":
            parts.append("Data unavailable")
        elif quality != "complete":
            parts.append("Partial data")
        else:
            parts.append("Data complete")

    activity_label = _activity_freshness_label(packet)
    if activity_label:
        parts.append(activity_label)

    if has_sleep_cutoff and quality == "failed":
        parts.append("data unavailable")
    elif has_sleep_cutoff and quality != "complete":
        parts.append("partial data")
    return " · ".join(parts)


def render_daily_card(card: dict, packet: dict, max_chars: int = CARD_MAX_CHARS) -> str:
    """Render one adaptive legacy-Markdown Telegram message under the product cap."""
    card = normalize_card(card, packet)
    icon = {
        "data_unavailable": "↻",
        "sleep_missing": "↻",
        "baseline_building": "◌",
        "caution": "△",
        "strong": "↗",
        "typical": "○",
    }.get(packet.get("state"), "○")

    metrics = packet.get("metrics", {})
    evidence = []
    for key in card["evidence_keys"]:
        if key in metrics:
            evidence.append(metrics[key]["evidence"])
    if packet.get("state") == "baseline_building":
        evidence = []
    snapshots = _snapshot_lines(packet)
    recent = (
        None
        if packet.get("state") == "baseline_building"
        else _recent_context(card, packet)
    )
    data_issues = _freshness_issues(packet)
    data_note = " · ".join(data_issues[:2]) if data_issues else None
    show_recheck = (
        not card["no_action"]
        or packet.get("state") == "baseline_building"
    )
    recheck = card.get("review_after") if show_recheck else None
    if recheck:
        recheck = recheck.rstrip(".!?")
        recheck = recheck[0].upper() + recheck[1:]
        if packet.get("state") == "caution":
            recheck = f"{recheck} — see whether the signal persists"

    footer = (
        f"_{card['confidence'].capitalize()} confidence · "
        f"{_freshness_label(packet)}_"
    )
    context_label = (
        "Data status"
        if packet.get("state") in {"data_unavailable", "sleep_missing"}
        else "What stands out"
    )
    def compose(
        *,
        headline: str,
        observation: str,
        decision: str,
        snapshot_rows: list[str],
        data_note_line: str | None,
        evidence_rows: list[str],
        recent_line: str | None,
        recheck_line: str | None,
    ) -> str:
        lines = [f"*{icon} {headline}*"]
        if snapshot_rows:
            lines.extend(["", "*Morning snapshot*", *snapshot_rows])
        if data_note_line:
            lines.extend(
                ["", f"*Data note:* {data_note_line[0].upper() + data_note_line[1:]}."]
            )
        lines.extend(["", f"*{context_label}:* {observation}"])
        if evidence_rows:
            lines.extend(f"• {item}" for item in evidence_rows)
        if recent_line:
            lines.extend(["", recent_line])
        lines.extend(["", f"*Today:* {decision}"])
        if recheck_line:
            lines.extend(["", f"*Recheck:* {recheck_line}."])
        lines.extend(["", footer])
        return "\n".join(lines).strip()

    rendered = compose(
        headline=card["headline"],
        observation=card["observation"],
        decision=card["decision"],
        snapshot_rows=snapshots,
        data_note_line=data_note,
        evidence_rows=evidence,
        recent_line=recent,
        recheck_line=recheck,
    )
    if len(rendered) <= max_chars:
        return rendered

    # Remove optional context before shortening the core interpretation. Keep
    # all deterministic facts at the production cap so the ledger's displayed
    # evidence list remains an exact audit of what the user saw.
    rendered = compose(
        headline=card["headline"],
        observation=card["observation"],
        decision=card["decision"],
        snapshot_rows=snapshots,
        data_note_line=data_note,
        evidence_rows=evidence,
        recent_line=None,
        recheck_line=recheck,
    )
    if len(rendered) <= max_chars:
        return rendered

    rendered = compose(
        headline=_plain_text(card["headline"], 64),
        observation=_plain_text(card["observation"], 240),
        decision=_plain_text(card["decision"], 160),
        snapshot_rows=snapshots,
        data_note_line=data_note,
        evidence_rows=evidence,
        recent_line=None,
        recheck_line=recheck,
    )
    if len(rendered) <= max_chars:
        return rendered

    # Defensive final compaction preserves the decision, primary comparison,
    # essential snapshot, and freshness.
    rendered = compose(
        headline=_plain_text(card["headline"], 56),
        observation=_plain_text(card["observation"], 180),
        decision=_plain_text(card["decision"], 150),
        snapshot_rows=snapshots[:1],
        data_note_line=data_note,
        evidence_rows=evidence[:1],
        recent_line=None,
        recheck_line=None,
    )
    if len(rendered) > max_chars:
        raise ValueError(f"Daily card exceeds {max_chars} characters after compaction")
    return rendered
