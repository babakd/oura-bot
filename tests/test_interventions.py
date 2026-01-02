"""
Tests for intervention functions: save_intervention_raw(), load_interventions(),
load_historical_interventions(), and format migration.
"""

import json
import pytest

import modal_agent


class TestSaveInterventionRaw:
    """Tests for save_intervention_raw() function."""

    def test_save_intervention_raw_creates_file(self, temp_data_dir, mock_now_nyc):
        """Test saving intervention creates file with correct structure."""
        entry = modal_agent.save_intervention_raw("took 2 magnesium capsules")

        assert entry["raw"] == "took 2 magnesium capsules"
        assert entry["parsed"] is None
        assert entry["time"] == "10:30"  # From mock_now_nyc

        # Check file was created
        interventions_file = temp_data_dir / "interventions" / "2026-01-15.json"
        assert interventions_file.exists()

        with open(interventions_file) as f:
            data = json.load(f)

        assert data["date"] == "2026-01-15"
        assert len(data["entries"]) == 1
        assert data["entries"][0]["raw"] == "took 2 magnesium capsules"

    def test_save_intervention_raw_appends_to_existing(self, temp_data_dir, mock_now_nyc):
        """Test saving multiple interventions appends to same file."""
        modal_agent.save_intervention_raw("magnesium 400mg")
        modal_agent.save_intervention_raw("20 min sauna")
        modal_agent.save_intervention_raw("glass of wine")

        interventions_file = temp_data_dir / "interventions" / "2026-01-15.json"
        with open(interventions_file) as f:
            data = json.load(f)

        assert len(data["entries"]) == 3
        assert data["entries"][0]["raw"] == "magnesium 400mg"
        assert data["entries"][1]["raw"] == "20 min sauna"
        assert data["entries"][2]["raw"] == "glass of wine"

    def test_save_intervention_raw_preserves_existing_entries(self, temp_data_dir, mock_now_nyc):
        """Test saving new intervention preserves existing entries."""
        # Create file with existing entry
        existing_data = {
            "date": "2026-01-15",
            "entries": [
                {"time": "08:00", "raw": "morning coffee", "parsed": None}
            ]
        }
        interventions_file = temp_data_dir / "interventions" / "2026-01-15.json"
        with open(interventions_file, "w") as f:
            json.dump(existing_data, f)

        # Add new entry
        modal_agent.save_intervention_raw("evening supplement")

        with open(interventions_file) as f:
            data = json.load(f)

        assert len(data["entries"]) == 2
        assert data["entries"][0]["raw"] == "morning coffee"
        assert data["entries"][1]["raw"] == "evening supplement"


class TestLoadInterventions:
    """Tests for load_interventions() function."""

    def test_load_interventions_no_file(self, temp_data_dir):
        """Test loading interventions when file doesn't exist."""
        data = modal_agent.load_interventions("2026-01-15")

        assert data["date"] == "2026-01-15"
        assert data["entries"] == []

    def test_load_interventions_new_format(self, temp_data_dir, sample_intervention_new_format):
        """Test loading interventions in new format."""
        interventions_file = temp_data_dir / "interventions" / "2026-01-15.json"
        with open(interventions_file, "w") as f:
            json.dump(sample_intervention_new_format, f)

        data = modal_agent.load_interventions("2026-01-15")

        assert data["date"] == "2026-01-15"
        assert len(data["entries"]) == 2
        assert data["entries"][0]["raw"] == "took 2 magnesium capsules"
        assert data["entries"][0]["parsed"] is not None
        assert data["entries"][1]["parsed"] is None

    def test_load_interventions_migrates_old_format(self, temp_data_dir, sample_intervention_old_format):
        """Test loading interventions migrates old format to new format."""
        interventions_file = temp_data_dir / "interventions" / "2026-01-15.json"
        with open(interventions_file, "w") as f:
            json.dump(sample_intervention_old_format, f)

        data = modal_agent.load_interventions("2026-01-15")

        # Should have entries array now
        assert "entries" in data
        assert len(data["entries"]) == 2

        # Old format should be migrated
        assert "interventions" not in data

        # Check first entry was migrated correctly
        first_entry = data["entries"][0]
        assert "raw" in first_entry
        assert "parsed" in first_entry
        assert first_entry["time"] == "19:30"


class TestLoadHistoricalInterventions:
    """Tests for load_historical_interventions() function."""

    def test_load_historical_interventions_empty(self, temp_data_dir, mock_now_nyc):
        """Test loading historical interventions with no files."""
        data = modal_agent.load_historical_interventions(days=7)
        assert data == {}

    def test_load_historical_interventions_multiple_days(self, temp_data_dir, mock_now_nyc):
        """Test loading interventions from multiple days."""
        # Create intervention files for a few days
        for day_offset in [0, 1, 3]:  # Today, yesterday, 3 days ago
            date = f"2026-01-{15 - day_offset:02d}"
            data = {
                "date": date,
                "entries": [{"time": "10:00", "raw": f"test intervention {day_offset}", "parsed": None}]
            }
            with open(temp_data_dir / "interventions" / f"{date}.json", "w") as f:
                json.dump(data, f)

        historical = modal_agent.load_historical_interventions(days=7)

        assert len(historical) == 3
        assert "2026-01-15" in historical
        assert "2026-01-14" in historical
        assert "2026-01-12" in historical

    def test_load_historical_interventions_respects_window(self, temp_data_dir, mock_now_nyc):
        """Test that only interventions within window are loaded."""
        # Create intervention from 30 days ago
        old_date = "2025-12-16"
        data = {"date": old_date, "entries": [{"time": "10:00", "raw": "old entry", "parsed": None}]}
        with open(temp_data_dir / "interventions" / f"{old_date}.json", "w") as f:
            json.dump(data, f)

        # Create intervention from today
        today_date = "2026-01-15"
        data = {"date": today_date, "entries": [{"time": "10:00", "raw": "today entry", "parsed": None}]}
        with open(temp_data_dir / "interventions" / f"{today_date}.json", "w") as f:
            json.dump(data, f)

        # Load only last 7 days
        historical = modal_agent.load_historical_interventions(days=7)

        assert today_date in historical
        assert old_date not in historical

    def test_load_historical_interventions_skips_empty_entries(self, temp_data_dir, mock_now_nyc):
        """Test that days with no entries are not included."""
        # Create file with empty entries
        date = "2026-01-15"
        data = {"date": date, "entries": []}
        with open(temp_data_dir / "interventions" / f"{date}.json", "w") as f:
            json.dump(data, f)

        historical = modal_agent.load_historical_interventions(days=7)

        assert date not in historical


class TestSaveInterventions:
    """Tests for save_interventions() function."""

    def test_save_interventions_writes_file(self, temp_data_dir):
        """Test saving interventions writes to correct file."""
        data = {
            "date": "2026-01-15",
            "entries": [
                {"time": "10:00", "raw": "test", "parsed": [{"type": "supplement", "name": "test"}]}
            ]
        }

        modal_agent.save_interventions("2026-01-15", data)

        interventions_file = temp_data_dir / "interventions" / "2026-01-15.json"
        assert interventions_file.exists()

        with open(interventions_file) as f:
            saved_data = json.load(f)

        assert saved_data == data

    def test_save_interventions_overwrites_existing(self, temp_data_dir):
        """Test saving interventions overwrites existing file."""
        # Create initial file
        initial_data = {"date": "2026-01-15", "entries": [{"time": "08:00", "raw": "old", "parsed": None}]}
        interventions_file = temp_data_dir / "interventions" / "2026-01-15.json"
        with open(interventions_file, "w") as f:
            json.dump(initial_data, f)

        # Overwrite with new data
        new_data = {"date": "2026-01-15", "entries": [{"time": "10:00", "raw": "new", "parsed": None}]}
        modal_agent.save_interventions("2026-01-15", new_data)

        with open(interventions_file) as f:
            saved_data = json.load(f)

        assert len(saved_data["entries"]) == 1
        assert saved_data["entries"][0]["raw"] == "new"


class TestGetTodayInterventions:
    """Tests for get_today_interventions() function."""

    def test_get_today_interventions_empty(self, temp_data_dir, mock_now_nyc):
        """Test getting today's interventions when none exist."""
        entries = modal_agent.get_today_interventions()
        assert entries == []

    def test_get_today_interventions_new_format(self, temp_data_dir, mock_now_nyc):
        """Test getting today's interventions in new format."""
        data = {
            "date": "2026-01-15",
            "entries": [
                {"time": "10:00", "raw": "test 1", "parsed": None},
                {"time": "11:00", "raw": "test 2", "parsed": None}
            ]
        }
        with open(temp_data_dir / "interventions" / "2026-01-15.json", "w") as f:
            json.dump(data, f)

        entries = modal_agent.get_today_interventions()

        assert len(entries) == 2
        assert entries[0]["raw"] == "test 1"
        assert entries[1]["raw"] == "test 2"

    def test_get_today_interventions_old_format(self, temp_data_dir, mock_now_nyc):
        """Test getting today's interventions handles old format."""
        data = {
            "date": "2026-01-15",
            "interventions": [
                {"type": "supplement", "name": "magnesium", "timestamp": "2026-01-15T10:00:00"}
            ]
        }
        with open(temp_data_dir / "interventions" / "2026-01-15.json", "w") as f:
            json.dump(data, f)

        entries = modal_agent.get_today_interventions()

        # Should fall back to interventions key
        assert len(entries) == 1
        assert entries[0]["name"] == "magnesium"
