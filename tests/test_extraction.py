"""
Tests for data extraction functions: extract_metrics() and extract_detailed_sleep().
"""

import json
from pathlib import Path

import pytest

# Import the functions we're testing
import modal_agent


class TestExtractMetrics:
    """Tests for extract_metrics() function."""

    def test_extract_metrics_complete_data(self, sample_complete_oura_data):
        """Test extraction with complete Oura data."""
        metrics = modal_agent.extract_metrics(sample_complete_oura_data)

        assert metrics["sleep_score"] == 82
        assert metrics["deep_sleep_minutes"] == 70
        assert metrics["light_sleep_minutes"] == 240
        assert metrics["rem_sleep_minutes"] == 110
        assert metrics["total_sleep_minutes"] == 420
        assert metrics["sleep_efficiency"] == 90
        assert metrics["hrv"] == 52
        assert metrics["avg_hr"] == 55
        assert metrics["avg_breath"] == 14.5
        assert metrics["latency_minutes"] == 10
        assert metrics["restless_periods"] == 12
        assert metrics["resting_hr"] == 48
        assert metrics["readiness"] == 78
        assert metrics["temperature_deviation"] == 0.15

    def test_extract_metrics_empty_data(self):
        """Test extraction with empty Oura data."""
        metrics = modal_agent.extract_metrics({})
        assert metrics == {}

    def test_extract_metrics_missing_sleep(self):
        """Test extraction when sleep data is missing."""
        data = {
            "daily_sleep": [{"score": 80}],
            "daily_readiness": [{"score": 75, "temperature_deviation": 0.1}],
            "sleep": [],
            "daily_activity": []
        }
        metrics = modal_agent.extract_metrics(data)

        assert metrics["sleep_score"] == 80
        assert metrics["readiness"] == 75
        assert metrics.get("hrv") is None
        assert metrics.get("deep_sleep_minutes") is None

    def test_extract_metrics_missing_readiness(self):
        """Test extraction when readiness data is missing."""
        data = {
            "daily_sleep": [{"score": 80}],
            "daily_readiness": [],
            "sleep": [],
            "daily_activity": []
        }
        metrics = modal_agent.extract_metrics(data)

        assert metrics["sleep_score"] == 80
        assert metrics.get("readiness") is None
        assert metrics.get("temperature_deviation") is None

    def test_extract_metrics_with_activity(self):
        """Test extraction includes activity data when present."""
        data = {
            "daily_sleep": [],
            "daily_readiness": [],
            "sleep": [],
            "daily_activity": [{"score": 85, "steps": 10000}]
        }
        metrics = modal_agent.extract_metrics(data)

        assert metrics["activity_score"] == 85
        assert metrics["steps"] == 10000

    def test_extract_metrics_from_fixture_file(self):
        """Test extraction using the JSON fixture file."""
        fixture_path = Path(__file__).parent / "fixtures" / "oura_complete.json"
        with open(fixture_path) as f:
            data = json.load(f)

        metrics = modal_agent.extract_metrics(data)

        assert metrics["sleep_score"] == 82
        assert metrics["hrv"] == 52
        assert metrics["readiness"] == 78
        assert metrics["activity_score"] == 85
        assert metrics["steps"] == 8500


class TestExtractDetailedSleep:
    """Tests for extract_detailed_sleep() function."""

    def test_extract_detailed_sleep_complete(self, sample_complete_oura_data):
        """Test detailed sleep extraction with complete data."""
        detailed = modal_agent.extract_detailed_sleep(sample_complete_oura_data)

        # Basic timing
        assert detailed["bedtime_start"] == "2026-01-14T23:30:00-05:00"
        assert detailed["bedtime_end"] == "2026-01-15T07:15:00-05:00"
        assert detailed["time_in_bed_minutes"] == 465
        assert detailed["total_sleep_minutes"] == 420
        assert detailed["awake_minutes"] == 45
        assert detailed["latency_minutes"] == 10

        # Sleep stages
        assert detailed["deep_sleep_minutes"] == 70
        assert detailed["light_sleep_minutes"] == 240
        assert detailed["rem_sleep_minutes"] == 110

        # Quality metrics
        assert detailed["efficiency"] == 90
        assert detailed["restless_periods"] == 12

        # Vitals
        assert detailed["average_hr"] == 55
        assert detailed["lowest_hr"] == 48
        assert detailed["average_hrv"] == 52
        assert detailed["average_breath"] == 14.5

    def test_extract_detailed_sleep_hr_stats(self, sample_complete_oura_data):
        """Test HR time series statistics extraction."""
        detailed = modal_agent.extract_detailed_sleep(sample_complete_oura_data)

        assert detailed["hr_min"] == 48
        assert detailed["hr_max"] == 58
        assert detailed["hr_range"] == 10
        assert "hr_first_third_avg" in detailed
        assert "hr_last_third_avg" in detailed

    def test_extract_detailed_sleep_hrv_stats(self, sample_complete_oura_data):
        """Test HRV time series statistics extraction."""
        detailed = modal_agent.extract_detailed_sleep(sample_complete_oura_data)

        assert detailed["hrv_min"] == 45
        assert detailed["hrv_max"] == 58
        assert detailed["hrv_range"] == 13
        assert "hrv_first_third_avg" in detailed
        assert "hrv_last_third_avg" in detailed

    def test_extract_detailed_sleep_phase_analysis(self, sample_complete_oura_data):
        """Test sleep phase percentage calculations."""
        detailed = modal_agent.extract_detailed_sleep(sample_complete_oura_data)

        # Should have phase percentages
        assert "deep_sleep_pct" in detailed
        assert "light_sleep_pct" in detailed
        assert "rem_sleep_pct" in detailed
        assert "awake_pct" in detailed
        assert "phase_transitions" in detailed

        # Percentages should sum to ~100
        total_pct = (
            detailed["deep_sleep_pct"] +
            detailed["light_sleep_pct"] +
            detailed["rem_sleep_pct"] +
            detailed["awake_pct"]
        )
        assert 99.0 <= total_pct <= 101.0

    def test_extract_detailed_sleep_empty(self):
        """Test detailed sleep extraction with no sleep data."""
        detailed = modal_agent.extract_detailed_sleep({"sleep": []})
        assert detailed == {}

    def test_extract_detailed_sleep_missing_optional_fields(self):
        """Test extraction handles missing optional fields gracefully."""
        data = {
            "sleep": [{
                "bedtime_start": "2026-01-14T23:00:00-05:00",
                "bedtime_end": "2026-01-15T07:00:00-05:00",
                "time_in_bed": 28800,
                "total_sleep_duration": 25200,
                # Missing: heart_rate, hrv, sleep_phase_5_min, etc.
            }]
        }
        detailed = modal_agent.extract_detailed_sleep(data)

        assert detailed["bedtime_start"] == "2026-01-14T23:00:00-05:00"
        assert detailed["time_in_bed_minutes"] == 480
        # HR/HRV stats should not be present
        assert "hr_min" not in detailed
        assert "hrv_min" not in detailed

    def test_extract_detailed_sleep_with_readiness_contributors(self):
        """Test extraction includes readiness contributors when embedded."""
        fixture_path = Path(__file__).parent / "fixtures" / "oura_complete.json"
        with open(fixture_path) as f:
            data = json.load(f)

        detailed = modal_agent.extract_detailed_sleep(data)

        # Should have readiness data from embedded readiness object
        assert detailed.get("readiness_score") == 78
        assert detailed.get("temperature_deviation") == 0.15
        assert detailed.get("contributor_activity_balance") == 85
        assert detailed.get("contributor_hrv_balance") == 72
