"""
Shared pytest fixtures for Oura Agent tests.
"""

import json
import pytest
import sys
from pathlib import Path
from datetime import datetime
from unittest.mock import MagicMock

# Add parent directory to path so we can import modal_agent
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def temp_data_dir(tmp_path, monkeypatch):
    """Redirect all data paths to temp directory."""
    import modal_agent

    monkeypatch.setattr(modal_agent, "DATA_DIR", tmp_path)
    monkeypatch.setattr(modal_agent, "BRIEFS_DIR", tmp_path / "briefs")
    monkeypatch.setattr(modal_agent, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(modal_agent, "METRICS_DIR", tmp_path / "metrics")
    monkeypatch.setattr(modal_agent, "INTERVENTIONS_DIR", tmp_path / "interventions")
    monkeypatch.setattr(modal_agent, "BASELINES_FILE", tmp_path / "baselines.json")

    # Create directories
    (tmp_path / "briefs").mkdir()
    (tmp_path / "raw").mkdir()
    (tmp_path / "metrics").mkdir()
    (tmp_path / "interventions").mkdir()

    return tmp_path


@pytest.fixture
def mock_now_nyc(monkeypatch):
    """Mock now_nyc() to return a fixed datetime."""
    import modal_agent
    from zoneinfo import ZoneInfo

    fixed_time = datetime(2026, 1, 15, 10, 30, 0, tzinfo=ZoneInfo("America/New_York"))
    monkeypatch.setattr(modal_agent, "now_nyc", lambda: fixed_time)
    return fixed_time


@pytest.fixture
def sample_oura_sleep_response():
    """Sample Oura sleep endpoint response."""
    return {
        "data": [{
            "id": "sleep-123",
            "day": "2026-01-14",
            "bedtime_start": "2026-01-14T23:30:00-05:00",
            "bedtime_end": "2026-01-15T07:15:00-05:00",
            "time_in_bed": 27900,  # 465 min
            "total_sleep_duration": 25200,  # 420 min
            "awake_time": 2700,  # 45 min
            "latency": 600,  # 10 min
            "deep_sleep_duration": 4200,  # 70 min
            "light_sleep_duration": 14400,  # 240 min
            "rem_sleep_duration": 6600,  # 110 min
            "efficiency": 90,
            "restless_periods": 12,
            "average_heart_rate": 55,
            "lowest_heart_rate": 48,
            "average_hrv": 52,
            "average_breath": 14.5,
            "heart_rate": {
                "items": [55, 54, 52, 50, 48, 49, 51, 53, 55, 56, 57, 58]
            },
            "hrv": {
                "items": [45, 48, 52, 55, 58, 54, 50, 48, 52, 55, 50, 48]
            },
            "sleep_phase_5_min": "1122233322114422331122"
        }]
    }


@pytest.fixture
def sample_oura_daily_sleep_response():
    """Sample Oura daily_sleep endpoint response."""
    return {
        "data": [{
            "id": "daily-sleep-123",
            "day": "2026-01-15",
            "score": 82,
            "contributors": {
                "deep_sleep": 85,
                "efficiency": 90,
                "latency": 95,
                "rem_sleep": 78,
                "restfulness": 80,
                "timing": 88,
                "total_sleep": 75
            }
        }]
    }


@pytest.fixture
def sample_oura_readiness_response():
    """Sample Oura daily_readiness endpoint response."""
    return {
        "data": [{
            "id": "readiness-123",
            "day": "2026-01-15",
            "score": 78,
            "temperature_deviation": 0.15,
            "contributors": {
                "activity_balance": 85,
                "body_temperature": 95,
                "hrv_balance": 72,
                "previous_day_activity": 80,
                "previous_night": 88,
                "recovery_index": 90,
                "resting_heart_rate": 92,
                "sleep_balance": 78
            }
        }]
    }


@pytest.fixture
def sample_complete_oura_data(
    sample_oura_sleep_response,
    sample_oura_daily_sleep_response,
    sample_oura_readiness_response
):
    """Complete Oura data structure as returned by get_oura_daily_data()."""
    return {
        "sleep": sample_oura_sleep_response["data"],
        "daily_sleep": sample_oura_daily_sleep_response["data"],
        "daily_readiness": sample_oura_readiness_response["data"],
        "daily_activity": []
    }


@pytest.fixture
def sample_baselines():
    """Sample baselines structure."""
    return {
        "last_updated": "2026-01-14T10:00:00",
        "dates": [f"2026-01-{i:02d}" for i in range(1, 15)],
        "data_points": 14,
        "window_days": 60,
        "metrics": {
            "sleep_score": {"mean": 75.0, "std": 8.0, "values": [72, 78, 75, 80, 70, 75, 78, 72, 76, 74, 77, 73, 75, 79]},
            "hrv": {"mean": 48.0, "std": 6.0, "values": [45, 50, 48, 52, 44, 48, 50, 46, 49, 47, 51, 45, 48, 52]},
            "deep_sleep_minutes": {"mean": 65.0, "std": 12.0, "values": [60, 70, 65, 75, 55, 65, 70, 60, 68, 62, 72, 58, 65, 72]},
            "light_sleep_minutes": {"mean": 220.0, "std": 25.0, "values": [210, 230, 220, 240, 200, 220, 230, 210, 225, 215, 235, 205, 220, 235]},
            "rem_sleep_minutes": {"mean": 95.0, "std": 15.0, "values": [90, 100, 95, 105, 85, 95, 100, 90, 98, 92, 102, 88, 95, 105]},
            "readiness": {"mean": 72.0, "std": 7.0, "values": [70, 75, 72, 78, 68, 72, 75, 70, 74, 71, 76, 69, 72, 77]},
            "resting_hr": {"mean": 52.0, "std": 4.0, "values": [50, 54, 52, 56, 48, 52, 54, 50, 53, 51, 55, 49, 52, 55]},
            "sleep_efficiency": {"mean": 88.0, "std": 4.0, "values": [86, 90, 88, 92, 84, 88, 90, 86, 89, 87, 91, 85, 88, 91]},
            "latency_minutes": {"mean": 12.0, "std": 5.0, "values": [10, 14, 12, 16, 8, 12, 14, 10, 13, 11, 15, 9, 12, 15]},
            "total_sleep_minutes": {"mean": 410.0, "std": 30.0, "values": [400, 420, 410, 430, 390, 410, 420, 400, 415, 405, 425, 395, 410, 425]},
        }
    }


@pytest.fixture
def sample_intervention_new_format():
    """Sample intervention file in new format."""
    return {
        "date": "2026-01-15",
        "entries": [
            {
                "time": "19:30",
                "raw": "took 2 magnesium capsules",
                "parsed": [{"type": "supplement", "name": "magnesium", "quantity": 2, "form": "capsule"}]
            },
            {
                "time": "21:00",
                "raw": "20 min sauna",
                "parsed": None
            }
        ]
    }


@pytest.fixture
def sample_intervention_old_format():
    """Sample intervention file in old format (for migration testing)."""
    return {
        "date": "2026-01-15",
        "interventions": [
            {
                "type": "supplement",
                "name": "magnesium",
                "details": "400mg",
                "timestamp": "2026-01-15T19:30:00-05:00"
            },
            {
                "type": "behavior",
                "name": "sauna",
                "details": "20 min",
                "timestamp": "2026-01-15T21:00:00-05:00"
            }
        ]
    }


@pytest.fixture
def mock_anthropic_client():
    """Mock Anthropic client for testing Claude API calls."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"parsed": []}')]
    mock_client.messages.create.return_value = mock_response
    return mock_client


@pytest.fixture
def sample_oura_stress_response():
    """Sample Oura daily_stress endpoint response (values in seconds from API)."""
    return {
        "data": [{
            "id": "stress-123",
            "day": "2026-01-15",
            "stress_high": 2700,  # 45 minutes in seconds
            "recovery_high": 10800,  # 180 minutes in seconds
            "day_summary": "restored"
        }]
    }


@pytest.fixture
def sample_oura_workouts_response():
    """Sample Oura workout endpoint response."""
    return {
        "data": [
            {
                "id": "workout-123",
                "activity": "cycling",
                "calories": 350,
                "day": "2026-01-15",
                "distance": 15000.0,
                "start_datetime": "2026-01-15T07:00:00-05:00",
                "end_datetime": "2026-01-15T07:45:00-05:00",
                "intensity": "moderate",
                "label": None,
                "source": "manual"
            },
            {
                "id": "workout-124",
                "activity": "strength_training",
                "calories": 200,
                "day": "2026-01-15",
                "distance": None,
                "start_datetime": "2026-01-15T18:00:00-05:00",
                "end_datetime": "2026-01-15T18:30:00-05:00",
                "intensity": "hard",
                "label": "Evening lift",
                "source": "manual"
            }
        ]
    }


@pytest.fixture
def sample_oura_heartrate_response():
    """Sample Oura heartrate endpoint response (daytime readings)."""
    return {
        "data": [
            {"bpm": 72, "source": "awake", "timestamp": "2026-01-15T09:00:00-05:00"},
            {"bpm": 75, "source": "awake", "timestamp": "2026-01-15T09:05:00-05:00"},
            {"bpm": 68, "source": "awake", "timestamp": "2026-01-15T10:00:00-05:00"},
            {"bpm": 85, "source": "workout", "timestamp": "2026-01-15T07:30:00-05:00"},
            {"bpm": 70, "source": "awake", "timestamp": "2026-01-15T12:00:00-05:00"},
            {"bpm": 65, "source": "awake", "timestamp": "2026-01-15T14:00:00-05:00"},
        ]
    }


@pytest.fixture
def sample_complete_oura_data_with_new_endpoints(
    sample_oura_sleep_response,
    sample_oura_daily_sleep_response,
    sample_oura_readiness_response,
    sample_oura_stress_response,
    sample_oura_workouts_response,
    sample_oura_heartrate_response
):
    """Complete Oura data including stress, workouts, and daytime HR."""
    return {
        "sleep": sample_oura_sleep_response["data"],
        "daily_sleep": sample_oura_daily_sleep_response["data"],
        "daily_readiness": sample_oura_readiness_response["data"],
        "daily_activity": [{"score": 85, "steps": 8500}],
        "daily_stress": sample_oura_stress_response["data"],
        "workouts": sample_oura_workouts_response["data"],
        "daytime_hr": sample_oura_heartrate_response["data"],
    }
