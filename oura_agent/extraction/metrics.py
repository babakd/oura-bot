"""
Metrics extraction from Oura API responses.
"""

from datetime import datetime


def _workout_duration_minutes(start_dt: str, end_dt: str) -> int:
    """Calculate workout duration in minutes from ISO datetime strings."""
    if not start_dt or not end_dt:
        return 0
    try:
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

    # Daytime heart rate
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


def extract_sleep_metrics(oura_data: dict) -> dict:
    """Extract sleep-related metrics from Oura API response."""
    metrics = {}

    # Daily sleep
    if oura_data.get("daily_sleep"):
        sleep = oura_data["daily_sleep"][0]
        metrics["sleep_score"] = sleep.get("score")

    # Detailed sleep
    if oura_data.get("sleep"):
        sleep_detail = oura_data["sleep"][0]
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
        metrics["resting_hr"] = sleep_detail.get("lowest_heart_rate")

    # Readiness
    if oura_data.get("daily_readiness"):
        readiness = oura_data["daily_readiness"][0]
        metrics["readiness"] = readiness.get("score")
        metrics["temperature_deviation"] = readiness.get("temperature_deviation")

    return metrics


def extract_activity_metrics(oura_data: dict) -> dict:
    """Extract activity-related metrics from Oura API response."""
    metrics = {}

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

    # Workouts
    if oura_data.get("workouts"):
        workouts = oura_data["workouts"]
        metrics["workout_count"] = len(workouts)
        metrics["workout_calories"] = sum(w.get("calories", 0) or 0 for w in workouts)
        metrics["workout_minutes"] = sum(
            _workout_duration_minutes(w.get("start_datetime"), w.get("end_datetime"))
            for w in workouts
        )
        metrics["workout_activities"] = [w.get("activity") for w in workouts if w.get("activity")]

    # Daytime heart rate
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
            "label": workout.get("label"),
            "intensity": workout.get("intensity"),
            "start_time": workout.get("start_datetime"),
            "end_time": workout.get("end_datetime"),
            "duration_minutes": _workout_duration_minutes(
                workout.get("start_datetime"),
                workout.get("end_datetime")
            ),
            "calories": workout.get("calories"),
            "distance_meters": workout.get("distance"),
            "source": workout.get("source"),
        }
        detailed_workouts.append(detailed)

    return detailed_workouts
