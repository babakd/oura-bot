"""
Oura Daily Optimization Agent
Runs daily via Modal cron, analyzes Oura data, sends brief to Telegram.
Uses Claude Opus 4.5 for analysis.
"""

import modal
import os
import json
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

# Timezone for all timestamps
NYC_TZ = ZoneInfo("America/New_York")


def now_nyc() -> datetime:
    """Get current time in NYC timezone."""
    return datetime.now(NYC_TZ)

# ============================================================================
# MODAL CONFIGURATION
# ============================================================================

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "anthropic>=0.40.0",
        "requests>=2.28.0",
        "fastapi>=0.100.0",
    )
)

app = modal.App("oura-agent", image=image)

# Persistent encrypted volume for health data
volume = modal.Volume.from_name("oura-health-data", create_if_missing=True)

# ============================================================================
# CONSTANTS
# ============================================================================

DATA_DIR = Path("/data")
BRIEFS_DIR = DATA_DIR / "briefs"
RAW_DIR = DATA_DIR / "raw"
METRICS_DIR = DATA_DIR / "metrics"  # Extracted daily metrics
INTERVENTIONS_DIR = DATA_DIR / "interventions"
BASELINES_FILE = DATA_DIR / "baselines.json"

OURA_API_BASE = "https://api.ouraring.com/v2/usercollection"

# Claude Opus 4.5 model ID
CLAUDE_MODEL = "claude-opus-4-5-20251101"

# Data retention windows
RAW_WINDOW_DAYS = 28      # Keep detailed raw data for 28 days
BASELINE_WINDOW_DAYS = 60  # Baselines computed from 60 days before raw window

# ============================================================================
# SYSTEM PROMPT
# ============================================================================

SYSTEM_PROMPT = """You are a personal health optimization agent. Analyze Oura Ring biometric data and generate actionable daily recommendations.

## Communication Style
- Be direct and data-driven. Skip pleasantries.
- Use specific numbers, not vague trends.
- Include confidence levels when making predictions.
- Flag concerning patterns proactively.

## Analysis Approach

You should make dynamic, context-aware decisions rather than applying rigid thresholds. Use your judgment based on the individual's patterns and context.

### Example Guardrails (Reference, Not Absolutes)

These are suggestions to help calibrate your thinking, but always consider context:

| Metric | Suggested Concern Level | Contextual Notes |
|--------|------------------------|------------------|
| Readiness <60 | Likely recovery day | But 62 after days of 80+ differs from 62 after days of 55 |
| HRV >1.5σ below baseline for 3+ days | Potential overtraining | Consider trend direction, not just deviation |
| Deep sleep <45 min | Worth investigating | Varies by individual - learn optimal range |
| Temperature >0.5°C deviation | Could indicate illness | Also affected by alcohol, late eating, exercise |
| Sleep efficiency <80% | Suboptimal | Correlate with next-day readiness |
| RHR >2σ above baseline | Stress/illness indicator | Context matters |
| Stress high >180 min | High stress day | Correlate with next night's sleep quality |
| Recovery high <60 min | Low recovery | May indicate overtraining or inadequate rest |
| Daytime HR >10 bpm above baseline | Elevated | Could indicate stress, illness, or dehydration |

### Decision Principles

1. **Learn the individual**: Build mental model of the user's patterns
2. **Context over cutoffs**: Same value means different things depending on recent history
3. **Explain reasoning**: Don't just say "readiness is low" - explain contributing factors
4. **Correlate interventions**: Look for patterns between logged interventions and outcomes
5. **State uncertainty**: Be honest about confidence levels
6. **Proactive flagging**: If something looks off, mention it even without hitting a threshold

### Workout Intensity Guidance

Don't use rigid readiness-to-intensity mapping. Consider:
- Previous days' training load (use workout_minutes and workout_calories from history)
- Accumulated fatigue (multi-day trend)
- Any scheduled events
- Recovery debt from recent poor sleep
- Yesterday's stress/recovery balance

## Output Format

Always structure briefs exactly like this (use plain text, NO markdown tables - they don't render in Telegram):

*TL;DR*
• [Most critical insight]
• [Second insight]
• [Primary action item]

*METRICS*
✅/⚠️/🔴 *Sleep Score*: X (baseline X ± X, Δ +/-X)
✅/⚠️/🔴 *HRV*: X ms (baseline X ± X, Δ +/-X)
✅/⚠️/🔴 *Deep Sleep*: X min (baseline X ± X, Δ +/-X)
✅/⚠️/🔴 *Readiness*: X (baseline X ± X, Δ +/-X)
✅/⚠️/🔴 *RHR*: X bpm (baseline X ± X, Δ +/-X)

*RECOMMENDATIONS*
1. Workout Intensity: [1-10] — [reasoning based on data and context]
2. Cognitive Load: [High/Medium/Low] — [reasoning]
3. Recovery Protocols: [specific actions if needed]

*PATTERNS & INSIGHTS*
[Multi-day trends, intervention correlations, notable observations]

*ALERTS*
[Only if genuinely concerning - explain why it matters]
"""

EVENING_SYSTEM_PROMPT = """You are a personal health optimization agent. Generate evening recommendations to optimize tonight's sleep based on today's data.

## Communication Style
- Be direct and actionable. Focus on what to do NOW.
- Prioritize interventions that can still impact tonight's sleep.
- Reference specific data points when making recommendations.

## Analysis Focus

At 7 PM, you're looking at:
- Today's activity and stress levels (complete or near-complete data)
- Workouts done today (recovery implications for sleep)
- Interventions logged today (what has/hasn't been done)
- Recent sleep patterns (to identify what helps)

### Key Considerations

| Factor | Impact on Sleep | Suggested Action Window |
|--------|-----------------|------------------------|
| High stress day (>120 min) | Likely elevated cortisol | Start wind-down earlier, consider magnesium |
| Intense workout today | Body needs recovery | Avoid late eating, ensure hydration |
| Low activity day | May have excess energy | Light movement before dinner |
| Recent poor sleep streak | Accumulated debt | Prioritize early bedtime |
| Late caffeine (after 2 PM) | Delayed sleep onset | Note for tomorrow |

### Intervention Timing Guidelines

Evening interventions should be timed appropriately:
- Magnesium: 1-2 hours before bed
- Screen reduction: Start 1 hour before bed
- Room cooling: 30 min before bed
- Last meal: 2-3 hours before bed
- Light exercise: Not within 2 hours of bed

## Output Format

Structure evening briefs exactly like this (plain text, NO markdown tables):

*TL;DR*
• [Today's key observation affecting sleep]
• [Primary recommendation for tonight]

*TODAY'S SUMMARY*
Activity: [steps/movement level vs baseline]
Stress: [high/recovery minutes, day summary]
Workouts: [what was done, if any]
Interventions logged: [list or "none yet"]

*TONIGHT'S RECOMMENDATIONS*
1. [Most impactful action] — [why, based on today's data]
2. [Secondary action] — [reasoning]
3. [Optional: timing recommendation] — [e.g., "aim for bed by 10:30 PM"]

*PATTERNS TO NOTE*
[Any correlations from recent data - e.g., "Last 3 days with evening magnesium showed +8% deep sleep"]

*ALERTS*
[Only if something today suggests poor sleep risk - explain why]
"""

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def ensure_directories():
    """Create data directories if they don't exist."""
    for dir_path in [BRIEFS_DIR, RAW_DIR, METRICS_DIR, INTERVENTIONS_DIR]:
        dir_path.mkdir(parents=True, exist_ok=True)


def fetch_oura_data(token: str, endpoint: str, start_date: str, end_date: str = None) -> dict:
    """Fetch data from Oura API."""
    url = f"{OURA_API_BASE}/{endpoint}"
    params = {"start_date": start_date}
    if end_date:
        params["end_date"] = end_date

    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def get_oura_daily_data(token: str, date: str, context_date: str = None) -> dict:
    """
    Fetch all relevant Oura data for a given date.

    Note on Oura date conventions:
    - daily_sleep, daily_readiness: 'day' = the morning you woke up (wake-date)
    - daily_activity, daily_stress, workout: 'day' = calendar day (context-date)
    - sleep (detailed): 'day' = the night sleep started

    Args:
        token: Oura API access token
        date: The wake-date (date you woke up) for sleep/readiness data
        context_date: The calendar date for activity/stress/workout (defaults to date if not specified)
                     For morning briefs, this should typically be yesterday (complete day data)

    So for a brief on Jan 1 at 10 AM, we want:
    - daily_sleep for Jan 1 (reflects Dec 31 night → Jan 1 morning)
    - daily_readiness for Jan 1
    - sleep session that ended on Jan 1 (started Dec 31)
    - daily_activity for Dec 31 (yesterday - complete day)
    - daily_stress for Dec 31 (yesterday - complete day)
    - workouts for Dec 31 (yesterday - complete day)
    """
    data = {}

    # Use context_date for activity/stress/workout if provided, otherwise use date
    activity_date = context_date if context_date else date

    # Calculate date range for sleep endpoint
    # We need day_before AND day_after because:
    # - If user goes to bed before midnight on Dec 31, day = Dec 31
    # - If user goes to bed after midnight (e.g., 3am on Jan 1), day = Jan 1
    # Oura API end_date is EXCLUSIVE, so we query [day_before, day_after) to get both
    target = datetime.strptime(date, "%Y-%m-%d")
    day_before = (target - timedelta(days=1)).strftime("%Y-%m-%d")
    day_after = (target + timedelta(days=1)).strftime("%Y-%m-%d")

    # Wake-date endpoints - fetch for the target date (sleep/readiness)
    for endpoint in ["daily_sleep", "daily_readiness"]:
        try:
            result = fetch_oura_data(token, endpoint, date, date)
            data[endpoint] = result.get("data", [])
        except Exception as e:
            print(f"Warning: Failed to fetch {endpoint}: {e}")
            data[endpoint] = []

    # Calendar-day endpoints - fetch for activity_date (complete day data)
    for endpoint in ["daily_activity", "daily_stress"]:
        try:
            result = fetch_oura_data(token, endpoint, activity_date, activity_date)
            data[endpoint] = result.get("data", [])
        except Exception as e:
            print(f"Warning: Failed to fetch {endpoint}: {e}")
            data[endpoint] = []

    # Workouts - fetch for activity_date (complete day data)
    try:
        result = fetch_oura_data(token, "workout", activity_date, activity_date)
        data["workouts"] = result.get("data", [])
    except Exception as e:
        print(f"Warning: Failed to fetch workouts: {e}")
        data["workouts"] = []

    # Sleep endpoint: fetch sessions that ended on target date
    # Query [day_before, day_after) to capture sessions where day = day_before OR day = date
    try:
        result = fetch_oura_data(token, "sleep", day_before, day_after)
        sleep_sessions = result.get("data", [])
        # Find the sleep session that ended on our target date
        for session in reversed(sleep_sessions):  # Most recent first
            bedtime_end = session.get("bedtime_end", "")
            if date in bedtime_end:
                data["sleep"] = [session]
                break
        if "sleep" not in data:
            # Fallback: use most recent session
            data["sleep"] = sleep_sessions[-1:] if sleep_sessions else []
    except Exception as e:
        print(f"Warning: Failed to fetch sleep: {e}")
        data["sleep"] = []

    return data


def get_oura_heartrate(token: str, date: str) -> list:
    """
    Fetch daytime heart rate data for a date.

    The heartrate endpoint returns 5-minute interval readings throughout the day.
    We filter to non-sleep readings to get daytime HR.

    Args:
        token: Oura API access token
        date: Date in YYYY-MM-DD format

    Returns:
        List of HR readings with bpm, source, and timestamp
    """
    # Query full day using datetime range
    start_dt = f"{date}T00:00:00+00:00"
    end_dt = f"{date}T23:59:59+00:00"

    url = f"{OURA_API_BASE}/heartrate"
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params={"start_datetime": start_dt, "end_datetime": end_dt},
            timeout=30
        )
        response.raise_for_status()

        all_readings = response.json().get("data", [])
        # Filter to non-sleep readings for daytime HR
        return [r for r in all_readings if r.get("source") != "sleep"]
    except Exception as e:
        print(f"Warning: Failed to fetch heartrate: {e}")
        return []


def _workout_duration_minutes(start_dt: str, end_dt: str) -> int:
    """Calculate workout duration in minutes from ISO datetime strings."""
    if not start_dt or not end_dt:
        return 0
    try:
        # Parse ISO format timestamps
        start = datetime.fromisoformat(start_dt.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_dt.replace("Z", "+00:00"))
        return int((end - start).total_seconds() / 60)
    except (ValueError, TypeError):
        return 0


def extract_metrics(oura_data: dict) -> dict:
    """Extract summary metrics from Oura API response (for daily tracking)."""
    metrics = {}

    # Daily sleep
    if oura_data.get("daily_sleep"):
        sleep = oura_data["daily_sleep"][0]
        metrics["sleep_score"] = sleep.get("score")

    # Detailed sleep (for deep sleep, efficiency, HRV)
    if oura_data.get("sleep"):
        sleep_detail = oura_data["sleep"][0]
        # Duration fields are in seconds, convert to minutes
        deep_sleep_sec = sleep_detail.get("deep_sleep_duration", 0)
        light_sleep_sec = sleep_detail.get("light_sleep_duration", 0)
        rem_sleep_sec = sleep_detail.get("rem_sleep_duration", 0)
        total_sleep_sec = sleep_detail.get("total_sleep_duration", 0)

        metrics["deep_sleep_minutes"] = deep_sleep_sec // 60 if deep_sleep_sec else None
        metrics["light_sleep_minutes"] = light_sleep_sec // 60 if light_sleep_sec else None
        metrics["rem_sleep_minutes"] = rem_sleep_sec // 60 if rem_sleep_sec else None
        metrics["total_sleep_minutes"] = total_sleep_sec // 60 if total_sleep_sec else None
        metrics["sleep_efficiency"] = sleep_detail.get("efficiency")
        metrics["hrv"] = sleep_detail.get("average_hrv")
        metrics["avg_hr"] = sleep_detail.get("average_heart_rate")
        metrics["avg_breath"] = sleep_detail.get("average_breath")
        metrics["latency_minutes"] = sleep_detail.get("latency", 0) // 60 if sleep_detail.get("latency") else None
        metrics["restless_periods"] = sleep_detail.get("restless_periods")

        # Resting HR: use lowest_heart_rate if available
        metrics["resting_hr"] = sleep_detail.get("lowest_heart_rate")

    # Readiness
    if oura_data.get("daily_readiness"):
        readiness = oura_data["daily_readiness"][0]
        metrics["readiness"] = readiness.get("score")
        metrics["temperature_deviation"] = readiness.get("temperature_deviation")

    # Activity
    if oura_data.get("daily_activity"):
        activity = oura_data["daily_activity"][0]
        metrics["activity_score"] = activity.get("score")
        metrics["steps"] = activity.get("steps")

    # Daily stress (API returns seconds, convert to minutes)
    if oura_data.get("daily_stress"):
        stress = oura_data["daily_stress"][0]
        stress_sec = stress.get("stress_high")
        recovery_sec = stress.get("recovery_high")
        metrics["stress_high"] = round(stress_sec / 60) if stress_sec else None
        metrics["recovery_high"] = round(recovery_sec / 60) if recovery_sec else None
        metrics["stress_day_summary"] = stress.get("day_summary")

    # Workouts (aggregate if multiple)
    if oura_data.get("workouts"):
        workouts = oura_data["workouts"]
        metrics["workout_count"] = len(workouts)
        metrics["workout_calories"] = sum(w.get("calories", 0) or 0 for w in workouts)
        metrics["workout_minutes"] = sum(
            _workout_duration_minutes(w.get("start_datetime"), w.get("end_datetime"))
            for w in workouts
        )
        metrics["workout_activities"] = [w.get("activity") for w in workouts if w.get("activity")]

    # Daytime heart rate (passed separately via daytime_hr key)
    if oura_data.get("daytime_hr"):
        readings = oura_data["daytime_hr"]
        if readings:
            bpms = [r["bpm"] for r in readings if r.get("bpm")]
            if bpms:
                metrics["daytime_hr_avg"] = round(sum(bpms) / len(bpms), 1)
                metrics["daytime_hr_min"] = min(bpms)
                metrics["daytime_hr_max"] = max(bpms)
                metrics["daytime_hr_samples"] = len(bpms)

    return metrics


def extract_detailed_sleep(oura_data: dict) -> dict:
    """Extract detailed sleep data for last night (time series + contributors)."""
    if not oura_data.get("sleep"):
        return {}

    sleep = oura_data["sleep"][0]

    detailed = {
        "bedtime_start": sleep.get("bedtime_start"),
        "bedtime_end": sleep.get("bedtime_end"),
        "time_in_bed_minutes": sleep.get("time_in_bed", 0) // 60,
        "total_sleep_minutes": sleep.get("total_sleep_duration", 0) // 60,
        "awake_minutes": sleep.get("awake_time", 0) // 60,
        "latency_minutes": sleep.get("latency", 0) // 60,

        # Sleep stages
        "deep_sleep_minutes": sleep.get("deep_sleep_duration", 0) // 60,
        "light_sleep_minutes": sleep.get("light_sleep_duration", 0) // 60,
        "rem_sleep_minutes": sleep.get("rem_sleep_duration", 0) // 60,

        # Quality metrics
        "efficiency": sleep.get("efficiency"),
        "restless_periods": sleep.get("restless_periods"),

        # Vitals
        "average_hr": sleep.get("average_heart_rate"),
        "lowest_hr": sleep.get("lowest_heart_rate"),
        "average_hrv": sleep.get("average_hrv"),
        "average_breath": sleep.get("average_breath"),
    }

    # HR time series - extract key stats
    hr_data = sleep.get("heart_rate", {}).get("items", [])
    hr_values = [x for x in hr_data if x is not None]
    if hr_values:
        detailed["hr_min"] = min(hr_values)
        detailed["hr_max"] = max(hr_values)
        detailed["hr_range"] = max(hr_values) - min(hr_values)
        # First third vs last third (sleep quality indicator)
        third = len(hr_values) // 3
        if third > 0:
            detailed["hr_first_third_avg"] = round(sum(hr_values[:third]) / third, 1)
            detailed["hr_last_third_avg"] = round(sum(hr_values[-third:]) / third, 1)

    # HRV time series - extract key stats
    hrv_data = sleep.get("hrv", {}).get("items", [])
    hrv_values = [x for x in hrv_data if x is not None]
    if hrv_values:
        detailed["hrv_min"] = min(hrv_values)
        detailed["hrv_max"] = max(hrv_values)
        detailed["hrv_range"] = max(hrv_values) - min(hrv_values)
        # First third vs last third
        third = len(hrv_values) // 3
        if third > 0:
            detailed["hrv_first_third_avg"] = round(sum(hrv_values[:third]) / third, 1)
            detailed["hrv_last_third_avg"] = round(sum(hrv_values[-third:]) / third, 1)

    # Sleep phases - decode and summarize
    sleep_phases = sleep.get("sleep_phase_5_min", "")
    if sleep_phases:
        phase_counts = {"deep": 0, "light": 0, "rem": 0, "awake": 0}
        for phase in sleep_phases:
            if phase == "1":
                phase_counts["deep"] += 1
            elif phase == "2":
                phase_counts["light"] += 1
            elif phase == "3":
                phase_counts["rem"] += 1
            elif phase == "4":
                phase_counts["awake"] += 1

        total_phases = sum(phase_counts.values())
        if total_phases > 0:
            detailed["deep_sleep_pct"] = round(100 * phase_counts["deep"] / total_phases, 1)
            detailed["light_sleep_pct"] = round(100 * phase_counts["light"] / total_phases, 1)
            detailed["rem_sleep_pct"] = round(100 * phase_counts["rem"] / total_phases, 1)
            detailed["awake_pct"] = round(100 * phase_counts["awake"] / total_phases, 1)

        # Sleep architecture: count transitions
        transitions = sum(1 for i in range(1, len(sleep_phases)) if sleep_phases[i] != sleep_phases[i-1])
        detailed["phase_transitions"] = transitions

    # Readiness contributors (if embedded in sleep data)
    if sleep.get("readiness"):
        readiness = sleep["readiness"]
        detailed["readiness_score"] = readiness.get("score")
        detailed["temperature_deviation"] = readiness.get("temperature_deviation")
        detailed["temperature_trend"] = readiness.get("temperature_trend_deviation")

        contributors = readiness.get("contributors", {})
        detailed["contributor_activity_balance"] = contributors.get("activity_balance")
        detailed["contributor_body_temperature"] = contributors.get("body_temperature")
        detailed["contributor_hrv_balance"] = contributors.get("hrv_balance")
        detailed["contributor_previous_day_activity"] = contributors.get("previous_day_activity")
        detailed["contributor_previous_night"] = contributors.get("previous_night")
        detailed["contributor_recovery_index"] = contributors.get("recovery_index")
        detailed["contributor_resting_heart_rate"] = contributors.get("resting_heart_rate")
        detailed["contributor_sleep_balance"] = contributors.get("sleep_balance")

    return detailed


def extract_detailed_workouts(oura_data: dict) -> list:
    """Extract detailed workout data for yesterday (all sessions with full context)."""
    if not oura_data.get("workouts"):
        return []

    detailed_workouts = []
    for workout in oura_data["workouts"]:
        detailed = {
            "activity": workout.get("activity"),
            "label": workout.get("label"),  # User's custom name if set
            "intensity": workout.get("intensity"),
            "start_time": workout.get("start_datetime"),
            "end_time": workout.get("end_datetime"),
            "duration_minutes": _workout_duration_minutes(
                workout.get("start_datetime"),
                workout.get("end_datetime")
            ),
            "calories": workout.get("calories"),
            "distance_meters": workout.get("distance"),
            "source": workout.get("source"),  # manual, auto-detected, etc.
        }
        detailed_workouts.append(detailed)

    return detailed_workouts


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
    # (new metrics get default values, existing metrics keep their data)
    if "metrics" not in persisted:
        persisted["metrics"] = {}

    for metric, default_data in defaults["metrics"].items():
        if metric not in persisted["metrics"]:
            print(f"Adding new baseline metric: {metric}")
            persisted["metrics"][metric] = default_data

    return persisted


def update_baselines(baselines: dict, new_metrics: dict, date: str, window: int = BASELINE_WINDOW_DAYS) -> dict:
    """
    Update rolling baselines with new data.

    If the date already exists, replaces the old values (allows corrections).
    """
    import statistics

    # Track which dates we have data for
    dates_seen = baselines.get("dates", [])

    # If date exists, remove old values before adding new ones (allows corrections)
    if date in dates_seen:
        date_index = dates_seen.index(date)
        print(f"Replacing baseline data for {date} (index {date_index})")

        # Remove old value at this index for each metric
        for metric in baselines["metrics"]:
            values = baselines["metrics"][metric].get("values", [])
            if len(values) > date_index:
                values.pop(date_index)
                baselines["metrics"][metric]["values"] = values

        dates_seen.remove(date)

    # Add this date to our tracking
    dates_seen.append(date)
    dates_seen = dates_seen[-window:]  # Keep only last N dates
    baselines["dates"] = dates_seen

    for metric, value in new_metrics.items():
        if metric in baselines["metrics"] and value is not None:
            values = baselines["metrics"][metric].get("values", [])
            values.append(value)
            # Keep only last N days
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


def load_interventions(date: str) -> dict:
    """
    Load interventions for a given date. Returns full data structure.

    Supports both JSONL (new format) and JSON (legacy format) for backwards compatibility.
    """
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
                        print(f"Warning: Skipped corrupt line {line_num} in {jsonl_file}: {e}")
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
    interventions_by_date = {}
    for i in range(days):
        date = (now_nyc() - timedelta(days=i)).strftime("%Y-%m-%d")
        data = load_interventions(date)
        if data.get("entries"):
            interventions_by_date[date] = data
    return interventions_by_date


def load_historical_metrics(days: int = 28) -> list:
    """Load extracted metrics from the past N days."""
    metrics_history = []
    for i in range(days):
        date = (now_nyc() - timedelta(days=i)).strftime("%Y-%m-%d")
        metrics_file = METRICS_DIR / f"{date}.json"
        if metrics_file.exists():
            with open(metrics_file) as f:
                data = json.load(f)
                metrics_history.append({"date": date, **data})
    return metrics_history


def save_daily_metrics(date: str, metrics: dict, detailed_sleep: dict, detailed_workouts: list = None):
    """Save extracted metrics for a day."""
    metrics_file = METRICS_DIR / f"{date}.json"
    data = {
        "date": date,
        "summary": metrics,
        "detailed_sleep": detailed_sleep,
        "detailed_workouts": detailed_workouts or []
    }
    with open(metrics_file, 'w') as f:
        json.dump(data, f, indent=2)


def load_recent_briefs(days: int = 3) -> list:
    """Load recent briefs for context."""
    briefs = []
    for i in range(1, days + 1):
        date = (now_nyc() - timedelta(days=i)).strftime("%Y-%m-%d")
        brief_file = BRIEFS_DIR / f"{date}.md"
        if brief_file.exists():
            with open(brief_file) as f:
                briefs.append({"date": date, "content": f.read()})
    return briefs


def prune_old_data():
    """Remove data older than retention windows and aggregate into baselines."""
    cutoff_date = now_nyc() - timedelta(days=RAW_WINDOW_DAYS)
    cutoff_str = cutoff_date.strftime("%Y-%m-%d")

    pruned_count = 0

    # Prune raw data
    for raw_file in RAW_DIR.glob("*.json"):
        file_date = raw_file.stem  # filename without extension
        if file_date < cutoff_str:
            raw_file.unlink()
            pruned_count += 1

    # Prune metrics (but aggregate into baselines first - done separately)
    for metrics_file in METRICS_DIR.glob("*.json"):
        file_date = metrics_file.stem
        if file_date < cutoff_str:
            metrics_file.unlink()

    # Prune briefs
    for brief_file in BRIEFS_DIR.glob("*.md"):
        file_date = brief_file.stem
        if file_date < cutoff_str:
            brief_file.unlink()

    # Prune interventions (both JSONL and legacy JSON)
    for intervention_file in INTERVENTIONS_DIR.glob("*.json*"):
        file_date = intervention_file.stem
        if file_date < cutoff_str:
            intervention_file.unlink()

    if pruned_count > 0:
        print(f"Pruned {pruned_count} files older than {cutoff_str}")


def send_telegram(message: str, bot_token: str, chat_id: str) -> bool:
    """Send message to Telegram. Returns success status."""
    try:
        # Telegram has 4096 char limit
        chunks = [message[i:i+4000] for i in range(0, len(message), 4000)]

        for chunk in chunks:
            # Try Markdown first, fall back to plain text if parsing fails
            response = requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True
                },
                timeout=30
            )

            # If Markdown parsing fails, retry without parse_mode
            if not response.ok and "can't parse entities" in response.text:
                print("Markdown parsing failed, sending as plain text...")
                response = requests.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": chunk,
                        "disable_web_page_preview": True
                    },
                    timeout=30
                )

            if not response.ok:
                print(f"Telegram API error: {response.status_code} - {response.text}")
                return False

        return True

    except Exception as e:
        print(f"Telegram send error: {e}")
        return False


def generate_brief_with_claude(
    api_key: str,
    today: str,
    metrics: dict,
    detailed_sleep: dict,
    detailed_workouts: list,
    baselines: dict,
    historical_metrics: list,
    historical_interventions: dict,
    recent_briefs: list
) -> str:
    """Use Claude Opus 4.5 to generate the morning brief with verbose context."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    # Build comprehensive context for Claude
    user_prompt = f"""Generate my morning optimization brief for {today}.

═══════════════════════════════════════════════════════════════════════════════
LAST NIGHT'S DETAILED SLEEP DATA
═══════════════════════════════════════════════════════════════════════════════

```json
{json.dumps(detailed_sleep, indent=2)}
```

Key observations from the data:
- Bedtime: {detailed_sleep.get('bedtime_start', 'N/A')} → {detailed_sleep.get('bedtime_end', 'N/A')}
- Time in bed: {detailed_sleep.get('time_in_bed_minutes', 'N/A')} min, actual sleep: {detailed_sleep.get('total_sleep_minutes', 'N/A')} min
- Sleep stages: Deep {detailed_sleep.get('deep_sleep_pct', 'N/A')}%, Light {detailed_sleep.get('light_sleep_pct', 'N/A')}%, REM {detailed_sleep.get('rem_sleep_pct', 'N/A')}%
- HR trend: first third avg {detailed_sleep.get('hr_first_third_avg', 'N/A')} → last third avg {detailed_sleep.get('hr_last_third_avg', 'N/A')} bpm
- HRV trend: first third avg {detailed_sleep.get('hrv_first_third_avg', 'N/A')} → last third avg {detailed_sleep.get('hrv_last_third_avg', 'N/A')} ms
- Phase transitions (sleep fragmentation indicator): {detailed_sleep.get('phase_transitions', 'N/A')}

═══════════════════════════════════════════════════════════════════════════════
YESTERDAY'S WORKOUTS
═══════════════════════════════════════════════════════════════════════════════

"""

    if detailed_workouts:
        user_prompt += f"```json\n{json.dumps(detailed_workouts, indent=2)}\n```\n\n"
        total_mins = sum(w.get('duration_minutes', 0) for w in detailed_workouts)
        total_cals = sum(w.get('calories', 0) or 0 for w in detailed_workouts)
        activities = [w.get('activity') for w in detailed_workouts if w.get('activity')]
        user_prompt += f"Summary: {len(detailed_workouts)} workout(s), {total_mins} total minutes, {total_cals} calories\n"
        user_prompt += f"Activities: {', '.join(activities)}\n"
        for i, w in enumerate(detailed_workouts, 1):
            intensity = w.get('intensity', 'unknown')
            label = f" ({w.get('label')})" if w.get('label') else ""
            user_prompt += f"  {i}. {w.get('activity')}{label}: {w.get('duration_minutes')}min, {intensity} intensity, {w.get('calories') or 0} cal\n"
    else:
        user_prompt += "No workouts recorded yesterday.\n"

    user_prompt += """
═══════════════════════════════════════════════════════════════════════════════
TODAY'S SUMMARY METRICS
═══════════════════════════════════════════════════════════════════════════════

```json
{json.dumps(metrics, indent=2)}
```

═══════════════════════════════════════════════════════════════════════════════
BASELINES (rolling 60-day averages, updated daily)
═══════════════════════════════════════════════════════════════════════════════

```json
{json.dumps(baselines.get('metrics', {}), indent=2)}
```
Data points in baseline: {baselines.get('data_points', 0)}
Dates covered: {baselines.get('dates', [])}

═══════════════════════════════════════════════════════════════════════════════
HISTORICAL METRICS (last 28 days)
═══════════════════════════════════════════════════════════════════════════════

"""

    if historical_metrics:
        for day_data in historical_metrics[:28]:  # Ensure max 28 days
            date = day_data.get('date', 'unknown')
            summary = day_data.get('summary', {})
            # Core metrics
            line = f"\n{date}: Sleep={summary.get('sleep_score', '-')}, Readiness={summary.get('readiness', '-')}, HRV={summary.get('hrv', '-')}, Deep={summary.get('deep_sleep_minutes', '-')}min, RHR={summary.get('resting_hr', '-')}"
            # Stress/recovery if available
            if summary.get('stress_high') is not None:
                line += f", Stress={summary.get('stress_high')}min, Recovery={summary.get('recovery_high', '-')}min"
            # Workout if available
            if summary.get('workout_minutes'):
                line += f", Workout={summary.get('workout_minutes')}min/{summary.get('workout_calories', '-')}cal"
            # Daytime HR if available
            if summary.get('daytime_hr_avg'):
                line += f", DayHR={summary.get('daytime_hr_avg')}bpm"
            user_prompt += line
    else:
        user_prompt += "No historical data available yet (building baseline)."

    user_prompt += """

═══════════════════════════════════════════════════════════════════════════════
INTERVENTIONS (last 28 days)
═══════════════════════════════════════════════════════════════════════════════

"""

    if historical_interventions:
        for date, data in sorted(historical_interventions.items(), reverse=True):
            entries = data.get("entries", [])
            for e in entries:
                # Use cleaned version for analysis, fall back to raw for old entries
                display = e.get("cleaned", e.get("raw", "unknown"))
                user_prompt += f"{date}: {display}\n"
    else:
        user_prompt += "No interventions logged yet."

    user_prompt += """

═══════════════════════════════════════════════════════════════════════════════
RECENT BRIEFS (for continuity)
═══════════════════════════════════════════════════════════════════════════════

"""

    for brief in recent_briefs[:3]:
        user_prompt += f"\n### {brief['date']}\n{brief['content']}\n"

    if not recent_briefs:
        user_prompt += "No previous briefs available."

    user_prompt += """

═══════════════════════════════════════════════════════════════════════════════
YOUR TASK
═══════════════════════════════════════════════════════════════════════════════

1. Analyze last night's detailed sleep data - look at HR/HRV trends, sleep architecture, timing
2. Compare today's metrics against 60-day baselines (calculate z-scores where possible)
3. Look for patterns in the 28-day historical data
4. Correlate any interventions with outcomes (e.g., did alcohol correlate with poor sleep?)
5. Generate the brief in the exact format specified in your instructions

Be specific with numbers. Use status emojis: ✅ (normal), ⚠️ (notable deviation), 🔴 (significant concern).
"""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2500,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": user_prompt}
        ]
    )

    return response.content[0].text


def generate_evening_brief_with_claude(
    api_key: str,
    today: str,
    metrics: dict,
    detailed_workouts: list,
    baselines: dict,
    historical_metrics: list,
    today_interventions: list,
    recent_briefs: list
) -> str:
    """Use Claude Opus 4.5 to generate the evening brief with today's context."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    # Build context focused on today's activity and tonight's sleep preparation
    user_prompt = f"""Generate my evening optimization brief for {today}.

═══════════════════════════════════════════════════════════════════════════════
TODAY'S ACTIVITY & STRESS DATA
═══════════════════════════════════════════════════════════════════════════════

```json
{json.dumps(metrics, indent=2)}
```

Key observations:
- Steps: {metrics.get('steps', 'N/A')}
- Activity score: {metrics.get('activity_score', 'N/A')}
- Stress high: {metrics.get('stress_high', 'N/A')} min
- Recovery high: {metrics.get('recovery_high', 'N/A')} min
- Stress summary: {metrics.get('stress_day_summary', 'N/A')}

═══════════════════════════════════════════════════════════════════════════════
TODAY'S WORKOUTS
═══════════════════════════════════════════════════════════════════════════════

"""

    if detailed_workouts:
        user_prompt += f"```json\n{json.dumps(detailed_workouts, indent=2)}\n```\n\n"
        total_mins = sum(w.get('duration_minutes', 0) for w in detailed_workouts)
        total_cals = sum(w.get('calories', 0) or 0 for w in detailed_workouts)
        activities = [w.get('activity') for w in detailed_workouts if w.get('activity')]
        user_prompt += f"Summary: {len(detailed_workouts)} workout(s), {total_mins} total minutes, {total_cals} calories\n"
        user_prompt += f"Activities: {', '.join(activities)}\n"
    else:
        user_prompt += "No workouts recorded today.\n"

    user_prompt += """
═══════════════════════════════════════════════════════════════════════════════
TODAY'S INTERVENTIONS (logged so far)
═══════════════════════════════════════════════════════════════════════════════

"""

    if today_interventions:
        for entry in today_interventions:
            time = entry.get('time', '??:??')
            cleaned = entry.get('cleaned', entry.get('raw', 'unknown'))
            user_prompt += f"- {time}: {cleaned}\n"
    else:
        user_prompt += "No interventions logged today yet.\n"

    user_prompt += f"""
═══════════════════════════════════════════════════════════════════════════════
BASELINES (rolling 60-day averages)
═══════════════════════════════════════════════════════════════════════════════

```json
{json.dumps(baselines.get('metrics', {}), indent=2)}
```

═══════════════════════════════════════════════════════════════════════════════
RECENT SLEEP PATTERNS (last 7 days)
═══════════════════════════════════════════════════════════════════════════════

"""

    # Include last 7 days of sleep data for pattern analysis
    if historical_metrics:
        for day_data in historical_metrics[:7]:
            date = day_data.get('date', 'unknown')
            summary = day_data.get('summary', {})
            line = f"{date}: Sleep={summary.get('sleep_score', '-')}, HRV={summary.get('hrv', '-')}, Deep={summary.get('deep_sleep_minutes', '-')}min"
            if summary.get('stress_high') is not None:
                line += f", Stress={summary.get('stress_high')}min"
            user_prompt += line + "\n"
    else:
        user_prompt += "No historical data available yet.\n"

    user_prompt += """
═══════════════════════════════════════════════════════════════════════════════
RECENT BRIEFS (for context)
═══════════════════════════════════════════════════════════════════════════════

"""

    if recent_briefs:
        for brief in recent_briefs[:2]:  # Last 2 briefs for context
            user_prompt += f"--- {brief.get('date', 'unknown')} ---\n{brief.get('content', '')[:500]}...\n\n"
    else:
        user_prompt += "No recent briefs available.\n"

    user_prompt += """
Based on today's data, generate evening recommendations to optimize tonight's sleep.
Consider what interventions would be most helpful given today's activity and stress levels.
"""

    response = client.messages.create(
        model="claude-opus-4-5-20251101",
        max_tokens=1500,
        system=EVENING_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": user_prompt}
        ]
    )

    return response.content[0].text


# ============================================================================
# MAIN AGENT FUNCTION
# ============================================================================

@app.function(
    secrets=[
        modal.Secret.from_name("anthropic"),
        modal.Secret.from_name("oura"),
        modal.Secret.from_name("telegram"),
    ],
    volumes={"/data": volume},
    timeout=300,
    schedule=modal.Cron("0 15 * * *"),  # 15:00 UTC = 10:00 AM EST
)
def morning_brief():
    """
    Main agent function. Runs daily to:
    1. Fetch Oura data
    2. Analyze against baselines
    3. Generate recommendations with Claude Opus 4.5
    4. Send brief to Telegram
    """
    today = now_nyc().strftime("%Y-%m-%d")

    oura_token = os.environ.get("OURA_ACCESS_TOKEN")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    print(f"Starting morning brief for {today}")

    try:
        ensure_directories()

        # Calculate dates:
        # - today: wake-date for sleep/readiness (data for the night that just ended)
        # - yesterday: context-date for activity/stress/workout/HR (complete day data)
        yesterday = (now_nyc() - timedelta(days=1)).strftime("%Y-%m-%d")

        # Fetch Oura data: sleep/readiness for today, activity/stress/workout for yesterday
        print(f"Fetching Oura data (wake-date={today}, context-date={yesterday})...")
        oura_data = get_oura_daily_data(oura_token, today, context_date=yesterday)

        # Fetch daytime HR for yesterday (complete day - at 10 AM today's HR is incomplete)
        oura_data["daytime_hr"] = get_oura_heartrate(oura_token, yesterday)
        print(f"Fetched {len(oura_data.get('daytime_hr', []))} daytime HR readings for {yesterday}")

        # Save raw data
        raw_file = RAW_DIR / f"{today}.json"
        with open(raw_file, 'w') as f:
            json.dump(oura_data, f, indent=2)
        print(f"Saved raw data to {raw_file}")

        # Extract summary metrics
        metrics = extract_metrics(oura_data)
        print(f"Extracted metrics: {metrics}")

        if not metrics:
            raise ValueError("No metrics extracted from Oura data")

        # Extract detailed sleep data for last night
        detailed_sleep = extract_detailed_sleep(oura_data)
        print(f"Extracted detailed sleep data: {len(detailed_sleep)} fields")

        # Extract detailed workout data for yesterday
        detailed_workouts = extract_detailed_workouts(oura_data)
        print(f"Extracted detailed workout data: {len(detailed_workouts)} workout(s)")

        # Save daily metrics (for historical tracking)
        save_daily_metrics(today, metrics, detailed_sleep, detailed_workouts)

        # Load baselines (60-day aggregates)
        baselines = load_baselines()

        # Load historical context (28 days)
        historical_metrics = load_historical_metrics(RAW_WINDOW_DAYS)
        historical_interventions = load_historical_interventions(RAW_WINDOW_DAYS)
        recent_briefs = load_recent_briefs(3)

        print(f"Loaded context: {len(historical_metrics)} days of metrics, {len(historical_interventions)} days with interventions")

        # Generate brief with Claude (verbose context)
        print("Generating brief with Claude Opus 4.5...")
        brief_content = generate_brief_with_claude(
            anthropic_key,
            today,
            metrics,
            detailed_sleep,
            detailed_workouts,
            baselines,
            historical_metrics,
            historical_interventions,
            recent_briefs
        )

        # Save brief
        brief_file = BRIEFS_DIR / f"{today}.md"
        with open(brief_file, 'w') as f:
            f.write(brief_content)
        print(f"Saved brief to {brief_file}")

        # Update baselines with new data (deduped by date)
        baselines = update_baselines(baselines, metrics, today)
        with open(BASELINES_FILE, 'w') as f:
            json.dump(baselines, f, indent=2)
        print("Updated baselines")

        # Prune old data (older than 28 days)
        prune_old_data()

        # Commit volume changes
        volume.commit()

        # Send to Telegram
        telegram_message = f"*Morning Brief — {today}*\n\n{brief_content}"

        if send_telegram(telegram_message, bot_token, chat_id):
            print("Brief sent to Telegram")
        else:
            print("Warning: Failed to send to Telegram, but brief saved")

        return {"status": "success", "date": today, "metrics": metrics}

    except Exception as e:
        error_msg = f"Morning brief failed: {str(e)}"
        print(f"Error: {error_msg}")

        # Try to send error notification
        if bot_token and chat_id:
            send_telegram(f"*Oura Agent Error*\n\n`{error_msg}`", bot_token, chat_id)

        raise


@app.function(
    secrets=[
        modal.Secret.from_name("anthropic"),
        modal.Secret.from_name("oura"),
        modal.Secret.from_name("telegram"),
    ],
    volumes={"/data": volume},
    timeout=300,
    # DISABLED: Evening brief not useful until Oura syncs day's data reliably
    # schedule=modal.Cron("0 0 * * *"),  # 00:00 UTC = 7:00 PM EST
)
def evening_brief():
    """
    Evening brief function. Runs daily at 7 PM EST to:
    1. Analyze today's activity, stress, and workouts
    2. Review interventions logged today
    3. Generate sleep preparation recommendations with Claude
    4. Send brief to Telegram
    """
    today = now_nyc().strftime("%Y-%m-%d")

    oura_token = os.environ.get("OURA_ACCESS_TOKEN")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    print(f"Starting evening brief for {today}")

    try:
        ensure_directories()
        volume.reload()

        # Fetch today's activity, stress, and workout data
        print(f"Fetching Oura data for today ({today})...")

        # Get today's activity and stress (may be partial but mostly complete by 7 PM)
        oura_data = {}
        try:
            activity_data = fetch_oura_data(oura_token, "daily_activity", today, today)
            oura_data["daily_activity"] = activity_data.get("data", [])
        except Exception as e:
            print(f"Warning: Could not fetch activity data: {e}")
            oura_data["daily_activity"] = []

        try:
            stress_data = fetch_oura_data(oura_token, "daily_stress", today, today)
            oura_data["daily_stress"] = stress_data.get("data", [])
        except Exception as e:
            print(f"Warning: Could not fetch stress data: {e}")
            oura_data["daily_stress"] = []

        try:
            workout_data = fetch_oura_data(oura_token, "workout", today, today)
            oura_data["workouts"] = workout_data.get("data", [])
        except Exception as e:
            print(f"Warning: Could not fetch workout data: {e}")
            oura_data["workouts"] = []

        # Extract metrics from today's data
        metrics = {}
        if oura_data.get("daily_activity"):
            activity = oura_data["daily_activity"][0]
            metrics["activity_score"] = activity.get("score")
            metrics["steps"] = activity.get("steps")

        if oura_data.get("daily_stress"):
            stress = oura_data["daily_stress"][0]
            stress_sec = stress.get("stress_high")
            recovery_sec = stress.get("recovery_high")
            metrics["stress_high"] = round(stress_sec / 60) if stress_sec else None
            metrics["recovery_high"] = round(recovery_sec / 60) if recovery_sec else None
            metrics["stress_day_summary"] = stress.get("day_summary")

        # Extract detailed workouts
        detailed_workouts = extract_detailed_workouts(oura_data)
        print(f"Extracted {len(detailed_workouts)} workout(s) for today")

        # Load today's interventions
        today_interventions = []
        interventions_file = INTERVENTIONS_DIR / f"{today}.jsonl"
        if interventions_file.exists():
            with open(interventions_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            today_interventions.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        print(f"Loaded {len(today_interventions)} intervention(s) for today")

        # Load baselines and historical context
        baselines = load_baselines()
        historical_metrics = load_historical_metrics(7)  # Last 7 days for sleep patterns
        recent_briefs = load_recent_briefs(3)

        print(f"Loaded context: {len(historical_metrics)} days of metrics")

        # Generate evening brief with Claude
        print("Generating evening brief with Claude Opus 4.5...")
        brief_content = generate_evening_brief_with_claude(
            anthropic_key,
            today,
            metrics,
            detailed_workouts,
            baselines,
            historical_metrics,
            today_interventions,
            recent_briefs
        )

        # Save brief with -evening suffix
        brief_file = BRIEFS_DIR / f"{today}-evening.md"
        with open(brief_file, 'w') as f:
            f.write(brief_content)
        print(f"Saved evening brief to {brief_file}")

        # Commit volume changes
        volume.commit()

        # Send to Telegram
        telegram_message = f"*Evening Brief — {today}*\n\n{brief_content}"

        if send_telegram(telegram_message, bot_token, chat_id):
            print("Evening brief sent to Telegram")
        else:
            print("Warning: Failed to send to Telegram, but brief saved")

        return {"status": "success", "date": today, "type": "evening", "metrics": metrics}

    except Exception as e:
        error_msg = f"Evening brief failed: {str(e)}"
        print(f"Error: {error_msg}")

        # Try to send error notification
        if bot_token and chat_id:
            send_telegram(f"*Oura Agent Error*\n\n`{error_msg}`", bot_token, chat_id)

        raise


# ============================================================================
# MANUAL TRIGGERS & UTILITIES
# ============================================================================

@app.function(
    secrets=[
        modal.Secret.from_name("anthropic"),
        modal.Secret.from_name("oura"),
        modal.Secret.from_name("telegram"),
    ],
    volumes={"/data": volume},
    timeout=300,
)
def run_now():
    """Manual trigger for testing."""
    return morning_brief.local()


@app.function(
    secrets=[modal.Secret.from_name("telegram"), modal.Secret.from_name("anthropic")],
    volumes={"/data": volume},
)
def log_intervention(raw_text: str):
    """
    Log an intervention for correlation tracking.

    Usage:
        modal run modal_agent.py::log_intervention --raw-text "took 2 magnesium capsules"
    """
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    cleaned_text = clean_intervention_with_claude(anthropic_key, raw_text)
    entry = save_intervention_raw(raw_text, cleaned_text)
    volume.commit()

    # Confirm via Telegram
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if bot_token and chat_id:
        send_telegram(f"✓ {cleaned_text}", bot_token, chat_id)

    print(f"Logged intervention: {cleaned_text}")
    return entry


@app.function(volumes={"/data": volume})
def reset_baselines():
    """Reset baselines to defaults. Use after polluted data."""
    ensure_directories()

    baselines = get_default_baselines()

    with open(BASELINES_FILE, 'w') as f:
        json.dump(baselines, f, indent=2)

    volume.commit()
    print("Baselines reset to defaults")
    return baselines


@app.function(volumes={"/data": volume})
def clear_today_interventions():
    """Clear today's interventions (for removing test data)."""
    ensure_directories()
    today = now_nyc().strftime("%Y-%m-%d")
    jsonl_file = INTERVENTIONS_DIR / f"{today}.jsonl"
    json_file = INTERVENTIONS_DIR / f"{today}.json"

    cleared = False
    if jsonl_file.exists():
        jsonl_file.unlink()
        cleared = True
    if json_file.exists():
        json_file.unlink()
        cleared = True

    if cleared:
        volume.commit()
        print(f"Cleared interventions for {today}")
    else:
        print(f"No interventions file for {today}")


@app.function(secrets=[modal.Secret.from_name("oura")])
def debug_workouts(date: str = None, days_back: int = 7):
    """Debug: Check Oura API for workouts. Fetches a range to ensure we catch all workouts."""
    import os
    from datetime import timedelta

    if date is None:
        date = now_nyc().strftime("%Y-%m-%d")

    token = os.environ.get("OURA_ACCESS_TOKEN")

    # Calculate start date (days_back days ago)
    end_dt = now_nyc()
    start_dt = end_dt - timedelta(days=days_back)
    start_date = start_dt.strftime("%Y-%m-%d")
    end_date = end_dt.strftime("%Y-%m-%d")

    print(f"Fetching workouts from {start_date} to {end_date} from Oura API...")

    url = f"{OURA_API_BASE}/workout"
    params = {"start_date": start_date, "end_date": end_date}
    print(f"URL: {url}")
    print(f"Params: {params}")

    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30
    )
    print(f"Response status: {response.status_code}")
    response.raise_for_status()
    data = response.json()

    workouts = data.get("data", [])
    print(f"Found {len(workouts)} workout(s) in range")

    for w in workouts:
        print(f"  - {w.get('day')}: {w.get('activity')}, {w.get('start_datetime')} to {w.get('end_datetime')}")
        print(f"    calories={w.get('calories')}, intensity={w.get('intensity')}, source={w.get('source')}")

    return data


@app.function(volumes={"/data": volume})
def view_history(days: int = 7):
    """View recent briefs and baselines."""
    ensure_directories()

    result = {"baselines": None, "recent_briefs": []}

    if BASELINES_FILE.exists():
        with open(BASELINES_FILE) as f:
            result["baselines"] = json.load(f)
        print("\nCurrent Baselines:")
        for metric, values in result["baselines"].get("metrics", {}).items():
            print(f"  {metric}: {values['mean']:.1f} +/- {values['std']:.1f}")

    print(f"\nRecent Briefs (last {days} days):")
    briefs = sorted(BRIEFS_DIR.glob("*.md"), reverse=True)[:days]
    for brief in briefs:
        print(f"  - {brief.name}")
        result["recent_briefs"].append(str(brief))

    return result


@app.function(
    secrets=[modal.Secret.from_name("oura")],
    volumes={"/data": volume},
    timeout=600,  # 10 minutes for backfill
)
def backfill_history(days: int = 90):
    """
    Backfill historical Oura data to bootstrap baselines.

    Pulls N days of historical data, extracts metrics, and populates:
    - Baselines (60-day rolling averages)
    - Metrics files for last 28 days

    Usage:
        modal run modal_agent.py::backfill_history --days 90
    """
    import statistics

    oura_token = os.environ.get("OURA_ACCESS_TOKEN")
    today = now_nyc()

    print(f"Starting backfill for {days} days of history...")
    ensure_directories()

    # Calculate date range
    start_date = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")

    print(f"Fetching data from {start_date} to {end_date}")

    # Fetch all data in batches (Oura API allows date ranges)
    all_daily_sleep = {}
    all_daily_readiness = {}
    all_sleep = {}

    # Fetch daily_sleep
    print("\n1. Fetching daily_sleep...")
    try:
        result = fetch_oura_data(oura_token, "daily_sleep", start_date, end_date)
        for item in result.get("data", []):
            day = item.get("day")
            if day:
                all_daily_sleep[day] = item
        print(f"   Got {len(all_daily_sleep)} days")
    except Exception as e:
        print(f"   Error: {e}")

    # Fetch daily_readiness
    print("2. Fetching daily_readiness...")
    try:
        result = fetch_oura_data(oura_token, "daily_readiness", start_date, end_date)
        for item in result.get("data", []):
            day = item.get("day")
            if day:
                all_daily_readiness[day] = item
        print(f"   Got {len(all_daily_readiness)} days")
    except Exception as e:
        print(f"   Error: {e}")

    # Fetch sleep (detailed) - need to handle the date convention
    # Sleep 'day' = date sleep started, so we query one day earlier
    print("3. Fetching detailed sleep...")
    sleep_start = (today - timedelta(days=days+1)).strftime("%Y-%m-%d")
    try:
        result = fetch_oura_data(oura_token, "sleep", sleep_start, end_date)
        for item in result.get("data", []):
            # Index by bedtime_end date (the morning you woke up)
            bedtime_end = item.get("bedtime_end", "")
            if bedtime_end:
                wake_date = bedtime_end.split("T")[0]
                # Keep the most recent session for each wake date
                if wake_date not in all_sleep or bedtime_end > all_sleep[wake_date].get("bedtime_end", ""):
                    all_sleep[wake_date] = item
        print(f"   Got {len(all_sleep)} days")
    except Exception as e:
        print(f"   Error: {e}")

    # Fetch daily_stress
    all_daily_stress = {}
    print("4. Fetching daily_stress...")
    try:
        result = fetch_oura_data(oura_token, "daily_stress", start_date, end_date)
        for item in result.get("data", []):
            day = item.get("day")
            if day:
                all_daily_stress[day] = item
        print(f"   Got {len(all_daily_stress)} days")
    except Exception as e:
        print(f"   Error (stress may not be available): {e}")

    # Fetch workouts
    all_workouts = {}
    print("5. Fetching workouts...")
    try:
        result = fetch_oura_data(oura_token, "workout", start_date, end_date)
        for item in result.get("data", []):
            day = item.get("day")
            if day:
                if day not in all_workouts:
                    all_workouts[day] = []
                all_workouts[day].append(item)
        print(f"   Got workouts for {len(all_workouts)} days")
    except Exception as e:
        print(f"   Error: {e}")

    # Fetch daytime heart rate (per-day queries, can be slow)
    all_daytime_hr = {}
    print("6. Fetching daytime heart rate (this may take a while)...")
    hr_success_count = 0
    for i in range(min(days, RAW_WINDOW_DAYS)):  # Only fetch HR for recent days to save time
        date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            readings = get_oura_heartrate(oura_token, date)
            if readings:
                all_daytime_hr[date] = readings
                hr_success_count += 1
        except Exception:
            pass  # Silent fail for individual days
        if i > 0 and i % 7 == 0:
            print(f"   Processed {i}/{min(days, RAW_WINDOW_DAYS)} days...")
    print(f"   Got heart rate data for {hr_success_count} days")

    # Process each day and extract metrics
    print("\n7. Processing daily metrics...")
    all_metrics = {}

    for i in range(days):
        date = (today - timedelta(days=i)).strftime("%Y-%m-%d")

        # Build oura_data dict for this day
        oura_data = {
            "daily_sleep": [all_daily_sleep[date]] if date in all_daily_sleep else [],
            "daily_readiness": [all_daily_readiness[date]] if date in all_daily_readiness else [],
            "sleep": [all_sleep[date]] if date in all_sleep else [],
            "daily_stress": [all_daily_stress[date]] if date in all_daily_stress else [],
            "workouts": all_workouts.get(date, []),
            "daytime_hr": all_daytime_hr.get(date, []),
        }

        # Extract metrics
        metrics = extract_metrics(oura_data)

        if metrics and any(v is not None for v in metrics.values()):
            all_metrics[date] = metrics

            # Save to metrics file if within 28-day window
            if i < RAW_WINDOW_DAYS:
                detailed_sleep = extract_detailed_sleep(oura_data)
                detailed_workouts = extract_detailed_workouts(oura_data)
                save_daily_metrics(date, metrics, detailed_sleep, detailed_workouts)

    print(f"   Extracted metrics for {len(all_metrics)} days")

    # Build baselines from all historical data
    print("\n8. Building baselines...")

    # Sort dates oldest first
    sorted_dates = sorted(all_metrics.keys())

    # Initialize baselines
    baselines = {
        "last_updated": now_nyc().isoformat(),
        "dates": [],
        "data_points": 0,
        "window_days": BASELINE_WINDOW_DAYS,
        "metrics": {
            # Sleep metrics
            "sleep_score": {"mean": 0, "std": 0, "values": []},
            "hrv": {"mean": 0, "std": 0, "values": []},
            "deep_sleep_minutes": {"mean": 0, "std": 0, "values": []},
            "light_sleep_minutes": {"mean": 0, "std": 0, "values": []},
            "rem_sleep_minutes": {"mean": 0, "std": 0, "values": []},
            "sleep_efficiency": {"mean": 0, "std": 0, "values": []},
            "latency_minutes": {"mean": 0, "std": 0, "values": []},
            "total_sleep_minutes": {"mean": 0, "std": 0, "values": []},
            # Vitals
            "resting_hr": {"mean": 0, "std": 0, "values": []},
            "daytime_hr_avg": {"mean": 0, "std": 0, "values": []},
            # Recovery
            "readiness": {"mean": 0, "std": 0, "values": []},
            "stress_high": {"mean": 0, "std": 0, "values": []},
            "recovery_high": {"mean": 0, "std": 0, "values": []},
            # Activity
            "workout_minutes": {"mean": 0, "std": 0, "values": []},
            "workout_calories": {"mean": 0, "std": 0, "values": []},
        }
    }

    # Add each day's metrics to baselines
    for date in sorted_dates:
        metrics = all_metrics[date]

        # Track date
        baselines["dates"].append(date)
        baselines["dates"] = baselines["dates"][-BASELINE_WINDOW_DAYS:]

        # Add values
        for metric, value in metrics.items():
            if metric in baselines["metrics"] and value is not None:
                values = baselines["metrics"][metric]["values"]
                values.append(value)
                values = values[-BASELINE_WINDOW_DAYS:]
                baselines["metrics"][metric]["values"] = values

    # Calculate mean and std for each metric
    for metric, data in baselines["metrics"].items():
        values = data["values"]
        if len(values) >= 2:
            data["mean"] = round(statistics.mean(values), 1)
            data["std"] = round(statistics.stdev(values), 1)
        elif len(values) == 1:
            data["mean"] = values[0]
            data["std"] = 0

    baselines["data_points"] = len(baselines["dates"])

    # Save baselines
    with open(BASELINES_FILE, 'w') as f:
        json.dump(baselines, f, indent=2)

    print(f"\n9. Baselines saved with {baselines['data_points']} data points")
    print("\nBaseline summary:")
    for metric, data in baselines["metrics"].items():
        if data["values"]:
            print(f"   {metric}: {data['mean']:.1f} ± {data['std']:.1f} (n={len(data['values'])})")

    volume.commit()

    print("\nBackfill complete!")
    return {
        "days_processed": len(all_metrics),
        "baseline_data_points": baselines["data_points"],
        "metrics_files_saved": min(len(all_metrics), RAW_WINDOW_DAYS)
    }


# ============================================================================
# TELEGRAM BOT WEBHOOK
# ============================================================================

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
            print(f"Migrated {len(data['entries'])} entries from {json_file} to {jsonl_file}")
        # Remove legacy file after migration
        json_file.unlink()


def clean_intervention_with_claude(api_key: str, raw_text: str) -> str:
    """Use Claude to clean/normalize intervention text."""
    import anthropic

    prompt = f"""Clean and normalize this health intervention log entry. Fix typos, remove filler words, standardize format. Keep it brief (under 10 words ideally).

Input: "{raw_text}"

Output only the cleaned text, nothing else."""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=50,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text.strip().strip('"')
    except Exception as e:
        print(f"Error cleaning intervention: {e}")
        return raw_text  # Fall back to raw if cleaning fails


def save_intervention_raw(raw_text: str, cleaned_text: str = None) -> dict:
    """
    Save an intervention to today's file using atomic append.

    Uses JSONL (one JSON object per line) for append-only writes.
    This avoids read-modify-write race conditions with the daily job.

    Args:
        raw_text: Original user input (kept for audit)
        cleaned_text: Claude-cleaned version (used for display/analysis)
    """
    today = now_nyc().strftime("%Y-%m-%d")
    time = now_nyc().strftime("%H:%M")

    ensure_directories()

    # Reload volume to see latest commits from other containers
    # This only works when running inside Modal, skip for local testing
    try:
        volume.reload()
    except RuntimeError:
        pass  # Running locally, not in Modal

    # Migrate legacy .json to .jsonl if needed (before first write)
    _migrate_json_to_jsonl(today)

    interventions_file = INTERVENTIONS_DIR / f"{today}.jsonl"

    entry = {
        "time": time,
        "raw": raw_text,
        "cleaned": cleaned_text or raw_text,  # Fall back to raw if no cleaned version
    }

    # Atomic append - no read required, avoids race conditions
    with open(interventions_file, 'a') as f:
        f.write(json.dumps(entry) + "\n")

    return entry


def get_today_interventions() -> list:
    """Get today's logged interventions."""
    today = now_nyc().strftime("%Y-%m-%d")
    data = load_interventions(today)
    return data.get("entries", [])


def format_intervention_response(api_key: str, just_logged: str) -> str:
    """Use Claude to generate a natural acknowledgment with today's summary."""
    import anthropic

    entries = get_today_interventions()

    if not entries:
        return "Logged."

    # Build list of today's entries
    entry_lines = []
    for e in entries:
        time = e.get("time", "")
        raw = e.get("raw", e.get("name", "unknown"))
        entry_lines.append(f"- {time}: {raw}")

    prompt = f"""Acknowledge this intervention was logged. Then summarize today's interventions naturally in 1-2 sentences.

Just logged: "{just_logged}"

Today's entries:
{chr(10).join(entry_lines)}

Keep response under 3 lines. No emojis. Be concise."""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text.strip()
    except Exception as e:
        # Fallback if Claude fails
        return f"Logged. ({len(entries)} today)"


def get_latest_brief() -> str:
    """Get the most recent morning brief."""
    ensure_directories()
    # Only return morning briefs (exclude -evening suffix)
    briefs = [b for b in BRIEFS_DIR.glob("*.md") if "-evening" not in b.name]
    if briefs:
        # Sort by modification time, most recent first
        briefs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        latest = briefs[0]
        with open(latest) as f:
            return f.read()
    return "No briefs available yet."


from fastapi import Request
from fastapi.responses import JSONResponse


@app.function(
    secrets=[
        modal.Secret.from_name("telegram"),
        modal.Secret.from_name("anthropic"),
    ],
    volumes={"/data": volume},
)
@modal.fastapi_endpoint(method="POST")
async def telegram_webhook(request: Request):
    """
    Telegram webhook endpoint for receiving messages.

    Commands:
        /log <intervention> [details] - Log an intervention
        /status - Show today's interventions
        /brief - Show latest brief
        /help - Show available commands

    Natural language:
        Any other message is cleaned by Claude and logged.
        Examples: "just had 2 neuro-mag capsules", "20 min sauna"
    """
    # Validate webhook secret (Telegram sends this in header when configured)
    webhook_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    if webhook_secret:
        received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if received_secret != webhook_secret:
            print(f"Webhook auth failed: invalid secret token")
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")

    # Parse the incoming update
    body = await request.json()
    message = body.get("message", {})
    text = message.get("text", "")
    sender_chat_id = str(message.get("chat", {}).get("id", ""))

    # Only respond to the configured chat
    if sender_chat_id != chat_id:
        return {"ok": True}

    # Ignore empty messages
    if not text.strip():
        return {"ok": True}

    response_text = None

    if text.startswith("/log"):
        # Extract everything after /log
        raw_text = text[4:].strip()
        if raw_text:
            cleaned_text = clean_intervention_with_claude(anthropic_key, raw_text)
            save_intervention_raw(raw_text, cleaned_text)
            volume.commit()
            response_text = format_intervention_response(anthropic_key, cleaned_text)
        else:
            response_text = "Usage: /log <intervention>\nExamples:\n  /log magnesium 400mg\n  /log sauna 20min\n  /log 2 drinks of wine"

    elif text.startswith("/status"):
        entries = get_today_interventions()
        if entries:
            lines = ["Today's interventions:"]
            for e in entries:
                # Show cleaned version, fall back to raw for old entries
                display_text = e.get("cleaned", e.get("raw", e.get("name", "unknown")))
                lines.append(f"  • {display_text}")
            response_text = "\n".join(lines)
        else:
            response_text = "No interventions logged today."

    elif text.startswith("/brief"):
        response_text = get_latest_brief()

    elif text.startswith("/clear"):
        today = now_nyc().strftime("%Y-%m-%d")
        jsonl_file = INTERVENTIONS_DIR / f"{today}.jsonl"
        json_file = INTERVENTIONS_DIR / f"{today}.json"
        cleared = False
        # Clear both formats if they exist
        if jsonl_file.exists():
            jsonl_file.unlink()
            cleared = True
        if json_file.exists():
            json_file.unlink()
            cleared = True
        if cleared:
            volume.commit()
            response_text = f"Cleared interventions for {today}"
        else:
            response_text = f"No interventions to clear for {today}"

    elif text.startswith("/help"):
        response_text = """Commands:
/status - Today's interventions
/brief - Latest morning brief
/clear - Clear today's interventions
/help - Show this

Or just type naturally:
  "2 neuro-mag capsules"
  "20 min sauna"
  "glass of wine with dinner" """

    elif text.startswith("/"):
        # Unknown command
        response_text = "Unknown command. Try /help"

    else:
        # Natural language - clean and store
        cleaned_text = clean_intervention_with_claude(anthropic_key, text)
        save_intervention_raw(text, cleaned_text)
        volume.commit()
        response_text = format_intervention_response(anthropic_key, cleaned_text)

    # Send response if we have one
    if response_text:
        send_telegram(response_text, bot_token, chat_id)

    return {"ok": True}


@app.local_entrypoint()
def main():
    """CLI entrypoint for manual runs."""
    print("Triggering morning brief...")
    result = run_now.remote()
    print(f"Result: {result}")
