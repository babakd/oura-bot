"""
Tests for new Oura API endpoints: daily_stress, heartrate, and workouts.
"""

import pytest

import modal_agent


class TestExtractMetricsStress:
    """Tests for stress metrics extraction."""

    def test_extract_stress_metrics(self, sample_oura_stress_response):
        """Test extraction of daily_stress metrics."""
        data = {
            "daily_stress": sample_oura_stress_response["data"],
            "daily_sleep": [],
            "sleep": [],
            "daily_readiness": [],
        }
        metrics = modal_agent.extract_metrics(data)

        assert metrics["stress_high"] == 45
        assert metrics["recovery_high"] == 180
        assert metrics["stress_day_summary"] == "restored"

    def test_extract_stress_metrics_missing(self):
        """Test extraction when stress data is missing."""
        data = {
            "daily_stress": [],
            "daily_sleep": [],
            "sleep": [],
            "daily_readiness": [],
        }
        metrics = modal_agent.extract_metrics(data)

        assert "stress_high" not in metrics
        assert "recovery_high" not in metrics


class TestExtractMetricsWorkouts:
    """Tests for workout metrics extraction."""

    def test_extract_workout_metrics_single(self):
        """Test extraction with a single workout."""
        data = {
            "workouts": [{
                "activity": "running",
                "calories": 400,
                "start_datetime": "2026-01-15T08:00:00-05:00",
                "end_datetime": "2026-01-15T08:45:00-05:00",
            }],
            "daily_sleep": [],
            "sleep": [],
            "daily_readiness": [],
        }
        metrics = modal_agent.extract_metrics(data)

        assert metrics["workout_count"] == 1
        assert metrics["workout_calories"] == 400
        assert metrics["workout_minutes"] == 45
        assert metrics["workout_activities"] == ["running"]

    def test_extract_workout_metrics_multiple(self, sample_oura_workouts_response):
        """Test extraction with multiple workouts."""
        data = {
            "workouts": sample_oura_workouts_response["data"],
            "daily_sleep": [],
            "sleep": [],
            "daily_readiness": [],
        }
        metrics = modal_agent.extract_metrics(data)

        assert metrics["workout_count"] == 2
        assert metrics["workout_calories"] == 550  # 350 + 200
        assert metrics["workout_minutes"] == 75  # 45 + 30
        assert "cycling" in metrics["workout_activities"]
        assert "strength_training" in metrics["workout_activities"]

    def test_extract_workout_metrics_missing(self):
        """Test extraction when no workouts."""
        data = {
            "workouts": [],
            "daily_sleep": [],
            "sleep": [],
            "daily_readiness": [],
        }
        metrics = modal_agent.extract_metrics(data)

        assert "workout_count" not in metrics
        assert "workout_calories" not in metrics

    def test_extract_workout_with_null_calories(self):
        """Test extraction when calories is null."""
        data = {
            "workouts": [{
                "activity": "yoga",
                "calories": None,
                "start_datetime": "2026-01-15T06:00:00-05:00",
                "end_datetime": "2026-01-15T06:30:00-05:00",
            }],
            "daily_sleep": [],
            "sleep": [],
            "daily_readiness": [],
        }
        metrics = modal_agent.extract_metrics(data)

        assert metrics["workout_count"] == 1
        assert metrics["workout_calories"] == 0  # None treated as 0
        assert metrics["workout_minutes"] == 30


class TestExtractMetricsDaytimeHR:
    """Tests for daytime heart rate metrics extraction."""

    def test_extract_daytime_hr_metrics(self, sample_oura_heartrate_response):
        """Test extraction of daytime HR metrics."""
        data = {
            "daytime_hr": sample_oura_heartrate_response["data"],
            "daily_sleep": [],
            "sleep": [],
            "daily_readiness": [],
        }
        metrics = modal_agent.extract_metrics(data)

        # 6 readings: 72, 75, 68, 85, 70, 65
        assert metrics["daytime_hr_avg"] == 72.5  # (72+75+68+85+70+65)/6
        assert metrics["daytime_hr_min"] == 65
        assert metrics["daytime_hr_max"] == 85
        assert metrics["daytime_hr_samples"] == 6

    def test_extract_daytime_hr_empty(self):
        """Test extraction when no daytime HR data."""
        data = {
            "daytime_hr": [],
            "daily_sleep": [],
            "sleep": [],
            "daily_readiness": [],
        }
        metrics = modal_agent.extract_metrics(data)

        assert "daytime_hr_avg" not in metrics
        assert "daytime_hr_min" not in metrics


class TestExtractMetricsComplete:
    """Tests for complete data extraction including new endpoints."""

    def test_extract_all_metrics(self, sample_complete_oura_data_with_new_endpoints):
        """Test extraction with all data sources."""
        metrics = modal_agent.extract_metrics(sample_complete_oura_data_with_new_endpoints)

        # Original metrics
        assert metrics["sleep_score"] == 82
        assert metrics["hrv"] == 52
        assert metrics["readiness"] == 78

        # Stress metrics
        assert metrics["stress_high"] == 45
        assert metrics["recovery_high"] == 180

        # Workout metrics
        assert metrics["workout_count"] == 2
        assert metrics["workout_calories"] == 550

        # Daytime HR metrics
        assert metrics["daytime_hr_samples"] == 6


class TestWorkoutDurationMinutes:
    """Tests for _workout_duration_minutes helper function."""

    def test_valid_duration(self):
        """Test with valid start and end times."""
        result = modal_agent._workout_duration_minutes(
            "2026-01-15T08:00:00-05:00",
            "2026-01-15T09:30:00-05:00"
        )
        assert result == 90

    def test_missing_start(self):
        """Test with missing start time."""
        result = modal_agent._workout_duration_minutes(
            None,
            "2026-01-15T09:30:00-05:00"
        )
        assert result == 0

    def test_missing_end(self):
        """Test with missing end time."""
        result = modal_agent._workout_duration_minutes(
            "2026-01-15T08:00:00-05:00",
            None
        )
        assert result == 0

    def test_invalid_format(self):
        """Test with invalid datetime format."""
        result = modal_agent._workout_duration_minutes(
            "invalid",
            "2026-01-15T09:30:00-05:00"
        )
        assert result == 0


class TestExtractDetailedWorkouts:
    """Tests for extract_detailed_workouts function."""

    def test_extract_detailed_workouts_multiple(self, sample_oura_workouts_response):
        """Test extraction with multiple workouts."""
        data = {"workouts": sample_oura_workouts_response["data"]}
        result = modal_agent.extract_detailed_workouts(data)

        assert len(result) == 2

        # First workout (cycling)
        assert result[0]["activity"] == "cycling"
        assert result[0]["intensity"] == "moderate"
        assert result[0]["duration_minutes"] == 45
        assert result[0]["calories"] == 350
        assert result[0]["distance_meters"] == 15000.0

        # Second workout (strength)
        assert result[1]["activity"] == "strength_training"
        assert result[1]["intensity"] == "hard"
        assert result[1]["duration_minutes"] == 30
        assert result[1]["calories"] == 200
        assert result[1]["label"] == "Evening lift"

    def test_extract_detailed_workouts_empty(self):
        """Test extraction with no workouts."""
        data = {"workouts": []}
        result = modal_agent.extract_detailed_workouts(data)
        assert result == []

    def test_extract_detailed_workouts_missing_key(self):
        """Test extraction when workouts key is missing."""
        data = {}
        result = modal_agent.extract_detailed_workouts(data)
        assert result == []


class TestNewBaselines:
    """Tests for new baseline metrics."""

    def test_load_baselines_includes_new_metrics(self, temp_data_dir):
        """Test that default baselines include new metrics."""
        baselines = modal_agent.load_baselines()

        # New metrics should be present
        assert "stress_high" in baselines["metrics"]
        assert "recovery_high" in baselines["metrics"]
        assert "daytime_hr_avg" in baselines["metrics"]
        assert "workout_minutes" in baselines["metrics"]
        assert "workout_calories" in baselines["metrics"]

        # Check default values
        assert baselines["metrics"]["stress_high"]["mean"] == 60
        assert baselines["metrics"]["recovery_high"]["mean"] == 120
        assert baselines["metrics"]["daytime_hr_avg"]["mean"] == 70

    def test_update_baselines_with_new_metrics(self, mock_now_nyc):
        """Test updating baselines with new metric types."""
        baselines = {
            "last_updated": None,
            "dates": [],
            "data_points": 0,
            "window_days": 60,
            "metrics": {
                "stress_high": {"mean": 60, "std": 30, "values": []},
                "recovery_high": {"mean": 120, "std": 45, "values": []},
                "daytime_hr_avg": {"mean": 70, "std": 8, "values": []},
                "workout_minutes": {"mean": 30, "std": 20, "values": []},
            }
        }

        new_metrics = {
            "stress_high": 50,
            "recovery_high": 150,
            "daytime_hr_avg": 72.5,
            "workout_minutes": 45,
        }

        updated = modal_agent.update_baselines(baselines, new_metrics, "2026-01-15")

        assert updated["metrics"]["stress_high"]["values"] == [50]
        assert updated["metrics"]["recovery_high"]["values"] == [150]
        assert updated["metrics"]["daytime_hr_avg"]["values"] == [72.5]
        assert updated["metrics"]["workout_minutes"]["values"] == [45]
