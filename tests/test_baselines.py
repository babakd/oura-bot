"""
Tests for baseline functions: load_baselines(), update_baselines().
"""

import json
import pytest

import modal_agent


class TestLoadBaselines:
    """Tests for load_baselines() function."""

    def test_load_baselines_no_file(self, temp_data_dir):
        """Test loading baselines when no file exists returns defaults."""
        baselines = modal_agent.load_baselines()

        assert baselines["last_updated"] is None
        assert baselines["dates"] == []
        assert baselines["data_points"] == 0
        assert baselines["window_days"] == 60

        # Check default metric values
        assert baselines["metrics"]["sleep_score"]["mean"] == 75
        assert baselines["metrics"]["hrv"]["mean"] == 45
        assert baselines["metrics"]["deep_sleep_minutes"]["mean"] == 70

    def test_load_baselines_existing_file(self, temp_data_dir, sample_baselines):
        """Test loading baselines from existing file."""
        # Write baselines file
        with open(temp_data_dir / "baselines.json", "w") as f:
            json.dump(sample_baselines, f)

        baselines = modal_agent.load_baselines()

        assert baselines["data_points"] == 14
        assert len(baselines["dates"]) == 14
        assert baselines["metrics"]["sleep_score"]["mean"] == 75.0
        assert baselines["metrics"]["hrv"]["mean"] == 48.0


class TestUpdateBaselines:
    """Tests for update_baselines() function."""

    def test_update_baselines_first_entry(self, mock_now_nyc):
        """Test adding first data point to empty baselines."""
        baselines = {
            "last_updated": None,
            "dates": [],
            "data_points": 0,
            "window_days": 60,
            "metrics": {
                "sleep_score": {"mean": 75, "std": 10, "values": []},
                "hrv": {"mean": 45, "std": 10, "values": []},
            }
        }

        new_metrics = {"sleep_score": 80, "hrv": 50}

        updated = modal_agent.update_baselines(baselines, new_metrics, "2026-01-15")

        assert "2026-01-15" in updated["dates"]
        assert updated["metrics"]["sleep_score"]["values"] == [80]
        assert updated["metrics"]["hrv"]["values"] == [50]
        # With single value, mean = value, std = 0
        assert updated["metrics"]["sleep_score"]["mean"] == 80
        assert updated["metrics"]["sleep_score"]["std"] == 0

    def test_update_baselines_multiple_entries(self, mock_now_nyc):
        """Test adding multiple data points calculates correct stats."""
        baselines = {
            "last_updated": None,
            "dates": ["2026-01-14"],
            "data_points": 1,
            "window_days": 60,
            "metrics": {
                "sleep_score": {"mean": 80, "std": 0, "values": [80]},
            }
        }

        new_metrics = {"sleep_score": 70}
        updated = modal_agent.update_baselines(baselines, new_metrics, "2026-01-15")

        assert len(updated["dates"]) == 2
        assert updated["metrics"]["sleep_score"]["values"] == [80, 70]
        assert updated["metrics"]["sleep_score"]["mean"] == 75.0  # (80+70)/2

    def test_update_baselines_deduplicates(self, mock_now_nyc, sample_baselines):
        """Test that adding same date twice doesn't duplicate."""
        # sample_baselines has 14 dates ending at 2026-01-14
        existing_date = "2026-01-14"
        original_count = len(sample_baselines["dates"])

        new_metrics = {"sleep_score": 99}
        updated = modal_agent.update_baselines(sample_baselines, new_metrics, existing_date)

        # Should not add duplicate date
        assert len(updated["dates"]) == original_count
        # Values should not change
        assert 99 not in updated["metrics"]["sleep_score"]["values"]

    def test_update_baselines_respects_window(self, mock_now_nyc):
        """Test that baselines respect the window size limit."""
        # Create baselines with window_days=5
        baselines = {
            "last_updated": None,
            "dates": ["2026-01-10", "2026-01-11", "2026-01-12", "2026-01-13", "2026-01-14"],
            "data_points": 5,
            "window_days": 5,
            "metrics": {
                "sleep_score": {"mean": 75, "std": 5, "values": [70, 72, 75, 78, 80]},
            }
        }

        new_metrics = {"sleep_score": 85}
        updated = modal_agent.update_baselines(baselines, new_metrics, "2026-01-15", window=5)

        # Should still have 5 dates, oldest dropped
        assert len(updated["dates"]) == 5
        assert "2026-01-10" not in updated["dates"]
        assert "2026-01-15" in updated["dates"]

        # Values should be updated
        assert len(updated["metrics"]["sleep_score"]["values"]) == 5
        assert 85 in updated["metrics"]["sleep_score"]["values"]

    def test_update_baselines_handles_none_values(self, mock_now_nyc):
        """Test that None values in metrics are skipped."""
        baselines = {
            "last_updated": None,
            "dates": [],
            "data_points": 0,
            "window_days": 60,
            "metrics": {
                "sleep_score": {"mean": 75, "std": 10, "values": []},
                "hrv": {"mean": 45, "std": 10, "values": []},
            }
        }

        new_metrics = {"sleep_score": 80, "hrv": None}
        updated = modal_agent.update_baselines(baselines, new_metrics, "2026-01-15")

        assert updated["metrics"]["sleep_score"]["values"] == [80]
        assert updated["metrics"]["hrv"]["values"] == []  # Should not add None

    def test_update_baselines_updates_timestamp(self, mock_now_nyc):
        """Test that last_updated is set after update."""
        baselines = {
            "last_updated": None,
            "dates": [],
            "data_points": 0,
            "window_days": 60,
            "metrics": {
                "sleep_score": {"mean": 75, "std": 10, "values": []},
            }
        }

        updated = modal_agent.update_baselines(baselines, {"sleep_score": 80}, "2026-01-15")

        assert updated["last_updated"] is not None
        assert "2026-01-15" in updated["last_updated"]

    def test_update_baselines_ignores_unknown_metrics(self, mock_now_nyc):
        """Test that unknown metrics in new_metrics are ignored."""
        baselines = {
            "last_updated": None,
            "dates": [],
            "data_points": 0,
            "window_days": 60,
            "metrics": {
                "sleep_score": {"mean": 75, "std": 10, "values": []},
            }
        }

        new_metrics = {"sleep_score": 80, "unknown_metric": 999}
        updated = modal_agent.update_baselines(baselines, new_metrics, "2026-01-15")

        assert "unknown_metric" not in updated["metrics"]
        assert updated["metrics"]["sleep_score"]["values"] == [80]
