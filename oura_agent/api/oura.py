"""
Oura API client with retry logic and explicit failure types.

Day-keyed collections are always queried with a widened D..D+1 range and then
filtered to D. This remains correct whether an endpoint currently treats
``end_date`` as inclusive or exclusive.
"""

from datetime import datetime, timedelta
from typing import Optional

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from oura_agent.config import OURA_API_BASE, NYC_TZ, logger


class OuraAPIError(RuntimeError):
    """Base class for failures while talking to the Oura API."""

    error_type = "api"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        endpoint: str,
        status_code: Optional[int] = None,
        retry_after: Optional[str] = None,
    ):
        super().__init__(message)
        self.endpoint = endpoint
        self.status_code = status_code
        self.retry_after = retry_after


class OuraAuthenticationError(OuraAPIError):
    """The bearer token is missing, expired, malformed, or revoked."""

    error_type = "authentication"


class OuraAccessError(OuraAPIError):
    """The token lacks permission or the Oura account cannot expose the data."""

    error_type = "access"


class OuraRateLimitError(OuraAPIError):
    """Oura rejected the request because a rate limit was exceeded."""

    error_type = "rate_limit"
    retryable = True


class OuraServerError(OuraAPIError):
    """Oura returned a transient 5xx response."""

    error_type = "server"
    retryable = True


class OuraNetworkError(OuraAPIError):
    """The request could not reach Oura or timed out."""

    error_type = "network"
    retryable = True


class OuraResponseError(OuraAPIError):
    """Oura returned an unexpected status or malformed response."""

    error_type = "response"


def _response_error_message(endpoint: str, status_code: int, body: str) -> str:
    """Build a bounded error message without including credentials."""
    body = (body or "").strip().replace("\n", " ")
    detail = f": {body[:200]}" if body else ""
    return f"Oura {endpoint} returned HTTP {status_code}{detail}"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(
        (OuraNetworkError, OuraRateLimitError, OuraServerError)
    ),
    reraise=True
)
def fetch_oura_data(token: str, endpoint: str, start_date: str, end_date: str = None) -> dict:
    """Fetch data from Oura, retrying only failures that may be transient."""
    if not token:
        raise OuraAuthenticationError(
            "Oura access token is missing",
            endpoint=endpoint,
        )

    url = f"{OURA_API_BASE}/{endpoint}"
    params = {"start_date": start_date}
    if end_date:
        params["end_date"] = end_date

    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=30
        )
    except requests.RequestException as exc:
        raise OuraNetworkError(
            f"Could not reach Oura {endpoint}: {exc}",
            endpoint=endpoint,
        ) from exc

    status_code = response.status_code
    message = _response_error_message(endpoint, status_code, response.text)
    if status_code == 401:
        raise OuraAuthenticationError(
            message,
            endpoint=endpoint,
            status_code=status_code,
        )
    if status_code == 403:
        raise OuraAccessError(
            message,
            endpoint=endpoint,
            status_code=status_code,
        )
    if status_code == 429:
        raise OuraRateLimitError(
            message,
            endpoint=endpoint,
            status_code=status_code,
            retry_after=response.headers.get("Retry-After"),
        )
    if 500 <= status_code <= 599:
        raise OuraServerError(
            message,
            endpoint=endpoint,
            status_code=status_code,
        )
    if not 200 <= status_code <= 299:
        raise OuraResponseError(
            message,
            endpoint=endpoint,
            status_code=status_code,
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise OuraResponseError(
            f"Oura {endpoint} returned invalid JSON",
            endpoint=endpoint,
            status_code=status_code,
        ) from exc

    if not isinstance(payload, dict):
        raise OuraResponseError(
            f"Oura {endpoint} returned an unexpected response shape",
            endpoint=endpoint,
            status_code=status_code,
        )
    return payload


def _success_status(items: list) -> dict:
    return {"ok": True, "count": len(items)}


def _failure_status(exc: OuraAPIError) -> dict:
    return {
        "ok": False,
        "error_type": exc.error_type,
        "status_code": exc.status_code,
        "retryable": exc.retryable,
    }


def _items_for_day(items: list, target_day: str) -> list:
    """Keep only documents whose canonical calendar/wake day is requested."""
    return [
        item
        for item in items
        if isinstance(item, dict) and item.get("day") == target_day
    ]


def _fetch_optional_collection(
    token: str,
    endpoint: str,
    start_date: str,
    end_date: str,
    data: dict,
    output_key: Optional[str] = None,
    target_day: Optional[str] = None,
) -> None:
    """Fetch optional context while retaining whether an empty list is real."""
    output_key = output_key or endpoint
    try:
        result = fetch_oura_data(token, endpoint, start_date, end_date)
        items = result.get("data", [])
        if target_day is not None:
            items = _items_for_day(items, target_day)
        data[output_key] = items
        data["_fetch_status"][endpoint] = _success_status(items)
    except OuraAPIError as exc:
        logger.warning(f"Failed to fetch optional {endpoint}: {exc}")
        data[output_key] = []
        data["_fetch_status"][endpoint] = _failure_status(exc)


def _select_long_sleep(sleep_sessions: list, wake_date: str) -> list:
    """Select the main sleep ending on ``wake_date`` from an API collection."""
    for session in reversed(sleep_sessions):
        bedtime_end = session.get("bedtime_end", "")
        if wake_date in bedtime_end and session.get("type") == "long_sleep":
            return [session]
    return []


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
    data = {"_fetch_status": {}}

    # Use context_date for activity/stress/workout if provided, otherwise use date
    activity_date = context_date if context_date else date

    # Calculate date range for sleep endpoint
    target = datetime.strptime(date, "%Y-%m-%d")
    day_before = (target - timedelta(days=1)).strftime("%Y-%m-%d")
    day_after = (target + timedelta(days=1)).strftime("%Y-%m-%d")

    # Widen every day-keyed collection to D..D+1, then exact-filter its rows.
    # This is robust to inconsistent/undocumented provider boundary behavior.
    # Required failures propagate so callers never mistake an auth/network
    # outage for missing biometric data.
    for endpoint in ["daily_sleep", "daily_readiness"]:
        result = fetch_oura_data(token, endpoint, date, day_after)
        items = _items_for_day(result.get("data", []), date)
        data[endpoint] = items
        data["_fetch_status"][endpoint] = _success_status(items)

    # Optional calendar-day context uses the same widened range and exact-row
    # filtering as the required day-keyed collections above.
    activity_end = (
        datetime.strptime(activity_date, "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%d")
    _fetch_optional_collection(
        token,
        "daily_activity",
        activity_date,
        activity_end,
        data,
        target_day=activity_date,
    )
    _fetch_optional_collection(
        token,
        "daily_stress",
        activity_date,
        activity_end,
        data,
        target_day=activity_date,
    )

    _fetch_optional_collection(
        token,
        "workout",
        activity_date,
        activity_end,
        data,
        output_key="workouts",
        target_day=activity_date,
    )

    # Detailed sleep is required. A successful empty collection remains a
    # legitimate "not recorded/not synced" result; request failures propagate.
    result = fetch_oura_data(token, "sleep", day_before, day_after)
    sleep_sessions = result.get("data", [])
    data["sleep"] = _select_long_sleep(sleep_sessions, date)
    data["_fetch_status"]["sleep"] = _success_status(sleep_sessions)

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
    data = {"_fetch_status": {}}

    # Calculate date range for sleep endpoint
    target = datetime.strptime(wake_date, "%Y-%m-%d")
    day_before = (target - timedelta(days=1)).strftime("%Y-%m-%d")
    day_after = (target + timedelta(days=1)).strftime("%Y-%m-%d")

    # Widen D..D+1 and exact-filter so provider range semantics cannot turn a
    # valid day into a misleading empty collection.
    for endpoint in ["daily_sleep", "daily_readiness"]:
        result = fetch_oura_data(token, endpoint, wake_date, day_after)
        items = _items_for_day(result.get("data", []), wake_date)
        data[endpoint] = items
        data["_fetch_status"][endpoint] = _success_status(items)

    # Detailed sleep is required and therefore also propagates request errors.
    result = fetch_oura_data(token, "sleep", day_before, day_after)
    sleep_sessions = result.get("data", [])
    data["sleep"] = _select_long_sleep(sleep_sessions, wake_date)
    data["_fetch_status"]["sleep"] = _success_status(sleep_sessions)

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
    data = {"_fetch_status": {}}

    activity_end = (
        datetime.strptime(activity_date, "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%d")

    # Widen every collection to D..D+1 and retain only rows keyed to D.
    _fetch_optional_collection(
        token,
        "daily_activity",
        activity_date,
        activity_end,
        data,
        target_day=activity_date,
    )
    _fetch_optional_collection(
        token,
        "daily_stress",
        activity_date,
        activity_end,
        data,
        target_day=activity_date,
    )
    _fetch_optional_collection(
        token,
        "workout",
        activity_date,
        activity_end,
        data,
        output_key="workouts",
        target_day=activity_date,
    )

    # Daytime heart rate is useful context but not required for a sleep brief.
    try:
        readings = get_oura_heartrate(token, activity_date)
        data["daytime_hr"] = readings
        data["_fetch_status"]["heartrate"] = _success_status(readings)
    except OuraAPIError as exc:
        logger.warning(f"Failed to fetch optional heartrate: {exc}")
        data["daytime_hr"] = []
        data["_fetch_status"]["heartrate"] = _failure_status(exc)

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

    endpoint = "heartrate"
    if not token:
        raise OuraAuthenticationError(
            "Oura access token is missing",
            endpoint=endpoint,
        )

    url = f"{OURA_API_BASE}/{endpoint}"
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params={"start_datetime": start_dt, "end_datetime": end_dt},
            timeout=30
        )
    except requests.RequestException as exc:
        raise OuraNetworkError(
            f"Could not reach Oura heartrate: {exc}",
            endpoint=endpoint,
        ) from exc

    status_code = response.status_code
    message = _response_error_message(endpoint, status_code, response.text)
    if status_code == 401:
        raise OuraAuthenticationError(
            message,
            endpoint=endpoint,
            status_code=status_code,
        )
    if status_code == 403:
        raise OuraAccessError(
            message,
            endpoint=endpoint,
            status_code=status_code,
        )
    if status_code == 429:
        raise OuraRateLimitError(
            message,
            endpoint=endpoint,
            status_code=status_code,
            retry_after=response.headers.get("Retry-After"),
        )
    if 500 <= status_code <= 599:
        raise OuraServerError(
            message,
            endpoint=endpoint,
            status_code=status_code,
        )
    if not 200 <= status_code <= 299:
        raise OuraResponseError(
            message,
            endpoint=endpoint,
            status_code=status_code,
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise OuraResponseError(
            "Oura heartrate returned invalid JSON",
            endpoint=endpoint,
            status_code=status_code,
        ) from exc
    if not isinstance(payload, dict):
        raise OuraResponseError(
            "Oura heartrate returned an unexpected response shape",
            endpoint=endpoint,
            status_code=status_code,
        )

    all_readings = payload.get("data", [])
    # Filter to non-sleep readings for daytime HR
    return [r for r in all_readings if r.get("source") != "sleep"]
