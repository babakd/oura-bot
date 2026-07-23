#!/usr/bin/env python3
"""
Local backfill script - downloads Oura data to local ./data folder.
This data is gitignored and won't be uploaded to GitHub.

Usage:
    python scripts/local_backfill.py --days 270
"""

import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import requests

# ============================================================================
# LOCAL CONFIGURATION (override Modal paths)
# ============================================================================

NYC_TZ = ZoneInfo("America/New_York")

# Use local ./data directory instead of /data (Modal volume)
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
BRIEFS_DIR = DATA_DIR / "briefs"
RAW_DIR = DATA_DIR / "raw"
METRICS_DIR = DATA_DIR / "metrics"
INTERVENTIONS_DIR = DATA_DIR / "interventions"
CONVERSATIONS_DIR = DATA_DIR / "conversations"
BASELINES_FILE = DATA_DIR / "baselines.json"

OURA_API_BASE = "https://api.ouraring.com/v2/usercollection"
BASELINE_WINDOW_DAYS = 60


def load_env():
    """Load .env file."""
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        print(f"Error: .env file not found at {env_file}")
        sys.exit(1)

    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def now_nyc() -> datetime:
    """Current time in NYC timezone."""
    return datetime.now(NYC_TZ)


def ensure_directories():
    """Create data directories if they don't exist."""
    for d in [DATA_DIR, BRIEFS_DIR, RAW_DIR, METRICS_DIR, INTERVENTIONS_DIR, CONVERSATIONS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def fetch_oura_data(token: str, endpoint: str, start_date: str, end_date: str) -> dict:
    """Fetch data from Oura API."""
    url = f"{OURA_API_BASE}/{endpoint}"
    params = {"start_date": start_date, "end_date": end_date}

    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def get_oura_heartrate(token: str, date: str) -> list:
    """Get heart rate data for a specific date (daytime only)."""
    start_dt = f"{date}T06:00:00"
    end_dt = f"{date}T22:00:00"

    url = f"{OURA_API_BASE}/heartrate"
    params = {"start_datetime": start_dt, "end_datetime": end_dt}

    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30
    )
    response.raise_for_status()

    data = response.json()
    readings = data.get("data", [])

    # Filter out sleep readings
    return [r for r in readings if r.get("source") != "sleep"]


def extract_metrics(oura_data: dict) -> dict:
    """Extract metrics from Oura API response."""
    metrics = {
        "sleep_score": None,
        "hrv": None,
        "deep_sleep_minutes": None,
        "light_sleep_minutes": None,
        "rem_sleep_minutes": None,
        "total_sleep_minutes": None,
        "sleep_efficiency": None,
        "latency_minutes": None,
        "resting_hr": None,
        "daytime_hr_avg": None,
        "readiness": None,
        "stress_high": None,
        "recovery_high": None,
        "day_summary": None,  # "restored", "normal", "stressed"
        "workout_minutes": None,
        "workout_calories": None,
        # New metrics: SpO2, breathing, resilience, VO2 max
        "spo2_avg": None,
        "breathing_rate_avg": None,
        "breathing_disturbance_index": None,  # 0-100 scale (lower is better)
        "resilience_level": None,
        "resilience_contributors_sleep_recovery": None,
        "resilience_contributors_daytime_recovery": None,
        "resilience_contributors_stress": None,
        "vo2_max": None,
        "sleep_time_recommendation": None,
    }

    # Daily sleep
    if oura_data.get("daily_sleep"):
        ds = oura_data["daily_sleep"][0]
        metrics["sleep_score"] = ds.get("score")

    # Detailed sleep (includes breathing rate)
    if oura_data.get("sleep"):
        sleep = oura_data["sleep"][0]
        metrics["hrv"] = sleep.get("average_hrv")
        metrics["deep_sleep_minutes"] = sleep.get("deep_sleep_duration", 0) // 60 if sleep.get("deep_sleep_duration") else None
        metrics["light_sleep_minutes"] = sleep.get("light_sleep_duration", 0) // 60 if sleep.get("light_sleep_duration") else None
        metrics["rem_sleep_minutes"] = sleep.get("rem_sleep_duration", 0) // 60 if sleep.get("rem_sleep_duration") else None
        metrics["total_sleep_minutes"] = sleep.get("total_sleep_duration", 0) // 60 if sleep.get("total_sleep_duration") else None
        metrics["sleep_efficiency"] = sleep.get("efficiency")
        metrics["latency_minutes"] = sleep.get("latency", 0) // 60 if sleep.get("latency") else None
        metrics["resting_hr"] = sleep.get("lowest_heart_rate")
        # Breathing rate from sleep data
        metrics["breathing_rate_avg"] = sleep.get("average_breath")

    # Readiness
    if oura_data.get("daily_readiness"):
        dr = oura_data["daily_readiness"][0]
        metrics["readiness"] = dr.get("score")

    # Stress
    if oura_data.get("daily_stress"):
        stress = oura_data["daily_stress"][0]
        metrics["stress_high"] = stress.get("stress_high")
        metrics["recovery_high"] = stress.get("recovery_high")
        metrics["day_summary"] = stress.get("day_summary")  # "restored", "normal", "stressed"

    # Daytime HR
    if oura_data.get("daytime_hr"):
        readings = oura_data["daytime_hr"]
        if readings:
            bpms = [r["bpm"] for r in readings if r.get("bpm")]
            if bpms:
                metrics["daytime_hr_avg"] = round(sum(bpms) / len(bpms), 1)

    # Workouts
    if oura_data.get("workouts"):
        total_mins = 0
        total_cals = 0
        for w in oura_data["workouts"]:
            if w.get("start_datetime") and w.get("end_datetime"):
                start = datetime.fromisoformat(w["start_datetime"].replace("Z", "+00:00"))
                end = datetime.fromisoformat(w["end_datetime"].replace("Z", "+00:00"))
                total_mins += int((end - start).total_seconds() / 60)
            total_cals += w.get("calories", 0) or 0
        metrics["workout_minutes"] = total_mins
        metrics["workout_calories"] = total_cals

    # SpO2 (blood oxygen)
    if oura_data.get("daily_spo2"):
        spo2 = oura_data["daily_spo2"][0]
        metrics["spo2_avg"] = spo2.get("spo2_percentage", {}).get("average") if isinstance(spo2.get("spo2_percentage"), dict) else spo2.get("spo2_percentage")
        # Breathing Disturbance Index (0-100, lower is better - derived from SpO2 drops)
        metrics["breathing_disturbance_index"] = spo2.get("breathing_disturbance_index")

    # Resilience
    if oura_data.get("daily_resilience"):
        res = oura_data["daily_resilience"][0]
        metrics["resilience_level"] = res.get("level")  # e.g., "limited", "adequate", "solid", "strong", "exceptional"
        contributors = res.get("contributors", {})
        metrics["resilience_contributors_sleep_recovery"] = contributors.get("sleep_recovery")
        metrics["resilience_contributors_daytime_recovery"] = contributors.get("daytime_recovery")
        metrics["resilience_contributors_stress"] = contributors.get("stress")

    # VO2 max
    if oura_data.get("vo2_max"):
        vo2 = oura_data["vo2_max"][0]
        metrics["vo2_max"] = vo2.get("vo2_max")

    # Sleep time recommendation
    if oura_data.get("sleep_time"):
        st = oura_data["sleep_time"][0]
        # Recommendation is typically a status like "on_target", "early", "late"
        metrics["sleep_time_recommendation"] = st.get("recommendation")

    return metrics


def save_daily_metrics(date: str, metrics: dict, detailed_sleep: dict = None, detailed_workouts: list = None):
    """Save extracted metrics to file."""
    metrics_file = METRICS_DIR / f"{date}.json"

    data = {"date": date, "metrics": metrics}
    if detailed_sleep:
        data["detailed_sleep"] = detailed_sleep
    if detailed_workouts:
        data["detailed_workouts"] = detailed_workouts

    with open(metrics_file, 'w') as f:
        json.dump(data, f, indent=2)


def extract_detailed_sleep(oura_data: dict) -> dict:
    """Extract detailed sleep data for storage."""
    if not oura_data.get("sleep"):
        return {}

    sleep = oura_data["sleep"][0]
    return {
        "bedtime_start": sleep.get("bedtime_start"),
        "bedtime_end": sleep.get("bedtime_end"),
        "average_hrv": sleep.get("average_hrv"),
        "lowest_heart_rate": sleep.get("lowest_heart_rate"),
        "efficiency": sleep.get("efficiency"),
        "sleep_phase_5_min": sleep.get("sleep_phase_5_min"),
        "hr_5_min": sleep.get("heart_rate", {}).get("items") if isinstance(sleep.get("heart_rate"), dict) else None,
        "hrv_5_min": sleep.get("hrv", {}).get("items") if isinstance(sleep.get("hrv"), dict) else None,
        # Breathing data
        "average_breath": sleep.get("average_breath"),
        "readiness_score_delta": sleep.get("readiness_score_delta"),
    }


def extract_detailed_workouts(oura_data: dict) -> list:
    """Extract detailed workout data for storage."""
    if not oura_data.get("workouts"):
        return []

    return [
        {
            "activity": w.get("activity"),
            "start": w.get("start_datetime"),
            "end": w.get("end_datetime"),
            "calories": w.get("calories"),
            "intensity": w.get("intensity"),
        }
        for w in oura_data["workouts"]
    ]


def backfill(days: int):
    """Backfill historical Oura data."""
    load_env()

    oura_token = os.environ.get("OURA_ACCESS_TOKEN")
    if not oura_token:
        print("Error: OURA_ACCESS_TOKEN not found in environment")
        sys.exit(1)

    today = now_nyc()
    print(f"Starting local backfill for {days} days of history...")
    print(f"Data will be saved to: {DATA_DIR}")

    ensure_directories()

    start_date = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    end_date = (today + timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"Fetching data from {start_date} to {end_date} (exclusive)")

    all_daily_sleep = {}
    all_daily_readiness = {}
    all_sleep = {}
    all_daily_stress = {}
    all_workouts = {}
    all_daytime_hr = {}
    # New data stores
    all_daily_spo2 = {}
    all_daily_resilience = {}
    all_vo2_max = {}
    all_sleep_time = {}

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

    # Fetch sleep (detailed) - need to go back one more day due to date convention
    print("3. Fetching detailed sleep...")
    sleep_start = (today - timedelta(days=days+1)).strftime("%Y-%m-%d")
    try:
        result = fetch_oura_data(oura_token, "sleep", sleep_start, end_date)
        for item in result.get("data", []):
            bedtime_end = item.get("bedtime_end", "")
            if bedtime_end:
                wake_date = bedtime_end.split("T")[0]
                # Keep the longest sleep session if multiple
                if wake_date not in all_sleep or bedtime_end > all_sleep[wake_date].get("bedtime_end", ""):
                    all_sleep[wake_date] = item
        print(f"   Got {len(all_sleep)} days")
    except Exception as e:
        print(f"   Error: {e}")

    # Fetch daily_stress
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

    # Fetch daytime heart rate (this is slow - one request per day)
    print("6. Fetching daytime heart rate (this may take a while)...")
    hr_days_to_fetch = min(days, 28)  # Limit HR fetching to 28 days
    for i in range(hr_days_to_fetch):
        date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            readings = get_oura_heartrate(oura_token, date)
            if readings:
                all_daytime_hr[date] = readings
        except Exception:
            pass
        if (i + 1) % 7 == 0:
            print(f"   Processed {i+1}/{hr_days_to_fetch} days...")
    print(f"   Got heart rate data for {len(all_daytime_hr)} days")

    # Fetch daily_spo2 (blood oxygen)
    print("7. Fetching daily_spo2 (blood oxygen)...")
    try:
        result = fetch_oura_data(oura_token, "daily_spo2", start_date, end_date)
        for item in result.get("data", []):
            day = item.get("day")
            if day:
                all_daily_spo2[day] = item
        print(f"   Got {len(all_daily_spo2)} days")
    except Exception as e:
        print(f"   Error (SpO2 requires Gen3+ ring): {e}")

    # Fetch daily_resilience
    print("8. Fetching daily_resilience...")
    try:
        result = fetch_oura_data(oura_token, "daily_resilience", start_date, end_date)
        for item in result.get("data", []):
            day = item.get("day")
            if day:
                all_daily_resilience[day] = item
        print(f"   Got {len(all_daily_resilience)} days")
    except Exception as e:
        print(f"   Error (resilience may not be available): {e}")

    # Fetch vo2_max
    print("9. Fetching vo2_max...")
    try:
        result = fetch_oura_data(oura_token, "vo2_max", start_date, end_date)
        for item in result.get("data", []):
            day = item.get("day")
            if day:
                all_vo2_max[day] = item
        print(f"   Got {len(all_vo2_max)} days")
    except Exception as e:
        print(f"   Error (VO2 max may not be available): {e}")

    # Fetch sleep_time (recommendations)
    print("10. Fetching sleep_time...")
    try:
        result = fetch_oura_data(oura_token, "sleep_time", start_date, end_date)
        for item in result.get("data", []):
            day = item.get("day")
            if day:
                all_sleep_time[day] = item
        print(f"   Got {len(all_sleep_time)} days")
    except Exception as e:
        print(f"   Error (sleep_time may not be available): {e}")

    # Process each day
    print("\n11. Processing daily metrics...")
    all_metrics = {}

    for i in range(days):
        date = (today - timedelta(days=i)).strftime("%Y-%m-%d")

        oura_data = {
            "daily_sleep": [all_daily_sleep[date]] if date in all_daily_sleep else [],
            "daily_readiness": [all_daily_readiness[date]] if date in all_daily_readiness else [],
            "sleep": [all_sleep[date]] if date in all_sleep else [],
            "daily_stress": [all_daily_stress[date]] if date in all_daily_stress else [],
            "workouts": all_workouts.get(date, []),
            "daytime_hr": all_daytime_hr.get(date, []),
            # New data
            "daily_spo2": [all_daily_spo2[date]] if date in all_daily_spo2 else [],
            "daily_resilience": [all_daily_resilience[date]] if date in all_daily_resilience else [],
            "vo2_max": [all_vo2_max[date]] if date in all_vo2_max else [],
            "sleep_time": [all_sleep_time[date]] if date in all_sleep_time else [],
        }

        metrics = extract_metrics(oura_data)

        if metrics and any(v is not None for v in metrics.values()):
            all_metrics[date] = metrics

            detailed_sleep_data = extract_detailed_sleep(oura_data)
            detailed_workouts_data = extract_detailed_workouts(oura_data)
            save_daily_metrics(date, metrics, detailed_sleep_data, detailed_workouts_data)

    print(f"   Extracted metrics for {len(all_metrics)} days")

    # Build baselines from last 60 days
    print("\n12. Building baselines (from last 60 days)...")

    sorted_dates = sorted(all_metrics.keys())

    baselines = {
        "last_updated": now_nyc().isoformat(),
        "dates": [],
        "data_points": 0,
        "window_days": BASELINE_WINDOW_DAYS,
        "metrics": {
            "sleep_score": {"mean": 0, "std": 0, "values": []},
            "hrv": {"mean": 0, "std": 0, "values": []},
            "deep_sleep_minutes": {"mean": 0, "std": 0, "values": []},
            "light_sleep_minutes": {"mean": 0, "std": 0, "values": []},
            "rem_sleep_minutes": {"mean": 0, "std": 0, "values": []},
            "sleep_efficiency": {"mean": 0, "std": 0, "values": []},
            "latency_minutes": {"mean": 0, "std": 0, "values": []},
            "total_sleep_minutes": {"mean": 0, "std": 0, "values": []},
            "resting_hr": {"mean": 0, "std": 0, "values": []},
            "daytime_hr_avg": {"mean": 0, "std": 0, "values": []},
            "readiness": {"mean": 0, "std": 0, "values": []},
            "stress_high": {"mean": 0, "std": 0, "values": []},
            "recovery_high": {"mean": 0, "std": 0, "values": []},
            "workout_minutes": {"mean": 0, "std": 0, "values": []},
            "workout_calories": {"mean": 0, "std": 0, "values": []},
            # New numeric metrics for baselines
            "spo2_avg": {"mean": 0, "std": 0, "values": []},
            "breathing_rate_avg": {"mean": 0, "std": 0, "values": []},
            "breathing_disturbance_index": {"mean": 0, "std": 0, "values": []},
            "vo2_max": {"mean": 0, "std": 0, "values": []},
            "resilience_contributors_sleep_recovery": {"mean": 0, "std": 0, "values": []},
            "resilience_contributors_daytime_recovery": {"mean": 0, "std": 0, "values": []},
            "resilience_contributors_stress": {"mean": 0, "std": 0, "values": []},
        }
    }

    for date in sorted_dates:
        metrics = all_metrics[date]
        baselines["dates"].append(date)
        baselines["dates"] = baselines["dates"][-BASELINE_WINDOW_DAYS:]

        for metric, value in metrics.items():
            if metric in baselines["metrics"] and value is not None:
                values = baselines["metrics"][metric]["values"]
                values.append(value)
                values = values[-BASELINE_WINDOW_DAYS:]
                baselines["metrics"][metric]["values"] = values

    for metric, data in baselines["metrics"].items():
        values = data["values"]
        if len(values) >= 2:
            data["mean"] = round(statistics.mean(values), 1)
            data["std"] = round(statistics.stdev(values), 1)
        elif len(values) == 1:
            data["mean"] = values[0]
            data["std"] = 0

    baselines["data_points"] = len(baselines["dates"])

    with open(BASELINES_FILE, 'w') as f:
        json.dump(baselines, f, indent=2)

    print(f"\n13. Baselines saved with {baselines['data_points']} data points")
    print("\nBaseline summary:")
    for metric, data in baselines["metrics"].items():
        if data["values"]:
            print(f"   {metric}: {data['mean']:.1f} +/- {data['std']:.1f} (n={len(data['values'])})")

    print(f"\nBackfill complete! Data saved to {DATA_DIR}")
    print(f"Total days with data: {len(all_metrics)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill Oura data locally")
    parser.add_argument("--days", type=int, default=270, help="Number of days to backfill (default: 270 = ~9 months)")
    args = parser.parse_args()

    backfill(args.days)
