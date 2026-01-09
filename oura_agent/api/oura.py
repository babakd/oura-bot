"""
Oura API client with retry logic.
"""

import requests
from datetime import datetime, timedelta
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from oura_agent.config import OURA_API_BASE, NYC_TZ, logger


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.RequestException, requests.Timeout)),
    reraise=True
)
def fetch_oura_data(token: str, endpoint: str, start_date: str, end_date: str = None) -> dict:
    """Fetch data from Oura API with automatic retry on transient failures."""
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
    """
    data = {}

    # Use context_date for activity/stress/workout if provided, otherwise use date
    activity_date = context_date if context_date else date

    # Calculate date range for sleep endpoint
    target = datetime.strptime(date, "%Y-%m-%d")
    day_before = (target - timedelta(days=1)).strftime("%Y-%m-%d")
    day_after = (target + timedelta(days=1)).strftime("%Y-%m-%d")

    # Wake-date endpoints - fetch for the target date (sleep/readiness)
    for endpoint in ["daily_sleep", "daily_readiness"]:
        try:
            result = fetch_oura_data(token, endpoint, date, date)
            data[endpoint] = result.get("data", [])
        except Exception as e:
            logger.warning(f"Failed to fetch {endpoint}: {e}")
            data[endpoint] = []

    # Calendar-day endpoints - fetch for activity_date (complete day data)
    for endpoint in ["daily_activity", "daily_stress"]:
        try:
            result = fetch_oura_data(token, endpoint, activity_date, activity_date)
            data[endpoint] = result.get("data", [])
        except Exception as e:
            logger.warning(f"Failed to fetch {endpoint}: {e}")
            data[endpoint] = []

    # Workouts - fetch for activity_date (complete day data)
    try:
        activity_end = (datetime.strptime(activity_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        result = fetch_oura_data(token, "workout", activity_date, activity_end)
        data["workouts"] = result.get("data", [])
    except Exception as e:
        logger.warning(f"Failed to fetch workouts: {e}")
        data["workouts"] = []

    # Sleep endpoint: fetch sessions that ended on target date
    # Only use type: "long_sleep" sessions (main sleep, 3+ hours)
    # This filters out short fragments (type: "sleep", "rest", "late_nap") from incomplete recordings
    try:
        result = fetch_oura_data(token, "sleep", day_before, day_after)
        sleep_sessions = result.get("data", [])
        for session in reversed(sleep_sessions):
            bedtime_end = session.get("bedtime_end", "")
            session_type = session.get("type", "")
            if date in bedtime_end and session_type == "long_sleep":
                data["sleep"] = [session]
                break
        if "sleep" not in data:
            data["sleep"] = []  # No valid main sleep found
    except Exception as e:
        logger.warning(f"Failed to fetch sleep: {e}")
        data["sleep"] = []

    return data


def get_oura_sleep_data(token: str, wake_date: str) -> dict:
    """
    Fetch sleep and readiness data for a given wake date.

    Args:
        token: Oura API access token
        wake_date: The date you woke up (YYYY-MM-DD)

    Returns:
        Dict with keys: daily_sleep, daily_readiness, sleep (detailed)
    """
    data = {}

    # Calculate date range for sleep endpoint
    target = datetime.strptime(wake_date, "%Y-%m-%d")
    day_before = (target - timedelta(days=1)).strftime("%Y-%m-%d")
    day_after = (target + timedelta(days=1)).strftime("%Y-%m-%d")

    # Wake-date endpoints
    for endpoint in ["daily_sleep", "daily_readiness"]:
        try:
            result = fetch_oura_data(token, endpoint, wake_date, wake_date)
            data[endpoint] = result.get("data", [])
        except Exception as e:
            logger.warning(f"Failed to fetch {endpoint}: {e}")
            data[endpoint] = []

    # Sleep endpoint: fetch sessions that ended on wake_date
    # Only use type: "long_sleep" sessions (main sleep, 3+ hours)
    # This filters out short fragments (type: "sleep", "rest", "late_nap") from incomplete recordings
    try:
        result = fetch_oura_data(token, "sleep", day_before, day_after)
        sleep_sessions = result.get("data", [])
        for session in reversed(sleep_sessions):
            bedtime_end = session.get("bedtime_end", "")
            session_type = session.get("type", "")
            if wake_date in bedtime_end and session_type == "long_sleep":
                data["sleep"] = [session]
                break
        if "sleep" not in data:
            data["sleep"] = []  # No valid main sleep found
    except Exception as e:
        logger.warning(f"Failed to fetch sleep: {e}")
        data["sleep"] = []

    return data


def get_oura_activity_data(token: str, activity_date: str) -> dict:
    """
    Fetch activity, stress, workouts, and heart rate for a calendar date.

    Args:
        token: Oura API access token
        activity_date: The calendar date (YYYY-MM-DD)

    Returns:
        Dict with keys: daily_activity, daily_stress, workouts, daytime_hr
    """
    data = {}

    # Calendar-day endpoints
    for endpoint in ["daily_activity", "daily_stress"]:
        try:
            result = fetch_oura_data(token, endpoint, activity_date, activity_date)
            data[endpoint] = result.get("data", [])
        except Exception as e:
            logger.warning(f"Failed to fetch {endpoint}: {e}")
            data[endpoint] = []

    # Workouts - Oura API end_date is EXCLUSIVE
    try:
        activity_end = (datetime.strptime(activity_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        result = fetch_oura_data(token, "workout", activity_date, activity_end)
        data["workouts"] = result.get("data", [])
    except Exception as e:
        logger.warning(f"Failed to fetch workouts: {e}")
        data["workouts"] = []

    # Daytime heart rate
    data["daytime_hr"] = get_oura_heartrate(token, activity_date)

    return data


def get_oura_heartrate(token: str, date: str) -> list:
    """
    Fetch daytime heart rate data for a date.

    The heartrate endpoint returns 5-minute interval readings throughout the day.
    We filter to non-sleep readings to get daytime HR.

    Args:
        token: Oura API access token
        date: Date in YYYY-MM-DD format (NYC local time)

    Returns:
        List of HR readings with bpm, source, and timestamp
    """
    # Query full day using NYC timezone datetime range
    date_obj = datetime.strptime(date, "%Y-%m-%d")
    date_nyc = date_obj.replace(tzinfo=NYC_TZ)
    start_dt = date_nyc.isoformat()
    end_dt = (date_nyc + timedelta(days=1) - timedelta(seconds=1)).isoformat()

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
        logger.warning(f"Failed to fetch heartrate: {e}")
        return []
