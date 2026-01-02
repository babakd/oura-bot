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
        """Test saving intervention creates JSONL file."""
        entry = modal_agent.save_intervention_raw("took 2 magnesium capsules")

        assert entry["raw"] == "took 2 magnesium capsules"
        assert entry["cleaned"] == "took 2 magnesium capsules"  # Falls back to raw when no cleaned provided
        assert entry["time"] == "10:30"  # From mock_now_nyc

        # Check JSONL file was created
        interventions_file = temp_data_dir / "interventions" / "2026-01-15.jsonl"
        assert interventions_file.exists()

        # Read JSONL format
        with open(interventions_file) as f:
            lines = [json.loads(line) for line in f if line.strip()]

        assert len(lines) == 1
        assert lines[0]["raw"] == "took 2 magnesium capsules"

    def test_save_intervention_raw_appends_to_existing(self, temp_data_dir, mock_now_nyc):
        """Test saving multiple interventions appends to same JSONL file."""
        modal_agent.save_intervention_raw("magnesium 400mg")
        modal_agent.save_intervention_raw("20 min sauna")
        modal_agent.save_intervention_raw("glass of wine")

        interventions_file = temp_data_dir / "interventions" / "2026-01-15.jsonl"
        with open(interventions_file) as f:
            lines = [json.loads(line) for line in f if line.strip()]

        assert len(lines) == 3
        assert lines[0]["raw"] == "magnesium 400mg"
        assert lines[1]["raw"] == "20 min sauna"
        assert lines[2]["raw"] == "glass of wine"

    def test_save_intervention_raw_atomic_append(self, temp_data_dir, mock_now_nyc):
        """Test that save_intervention_raw uses atomic append (no read required)."""
        # Create a JSONL file with existing content
        interventions_file = temp_data_dir / "interventions" / "2026-01-15.jsonl"
        with open(interventions_file, "w") as f:
            f.write(json.dumps({"time": "08:00", "raw": "morning coffee", "parsed": None}) + "\n")

        # Add new entry via save_intervention_raw
        modal_agent.save_intervention_raw("evening supplement")

        with open(interventions_file) as f:
            lines = [json.loads(line) for line in f if line.strip()]

        assert len(lines) == 2
        assert lines[0]["raw"] == "morning coffee"
        assert lines[1]["raw"] == "evening supplement"


class TestLoadInterventions:
    """Tests for load_interventions() function."""

    def test_load_interventions_no_file(self, temp_data_dir):
        """Test loading interventions when file doesn't exist."""
        data = modal_agent.load_interventions("2026-01-15")

        assert data["date"] == "2026-01-15"
        assert data["entries"] == []

    def test_load_interventions_jsonl_format(self, temp_data_dir):
        """Test loading interventions from JSONL format."""
        interventions_file = temp_data_dir / "interventions" / "2026-01-15.jsonl"
        with open(interventions_file, "w") as f:
            f.write(json.dumps({"time": "19:30", "raw": "took 2 magnesium capsules", "parsed": [{"type": "supplement"}]}) + "\n")
            f.write(json.dumps({"time": "21:15", "raw": "20 min sauna", "parsed": None}) + "\n")

        data = modal_agent.load_interventions("2026-01-15")

        assert data["date"] == "2026-01-15"
        assert len(data["entries"]) == 2
        assert data["entries"][0]["raw"] == "took 2 magnesium capsules"
        assert data["entries"][0]["parsed"] is not None
        assert data["entries"][1]["parsed"] is None

    def test_load_interventions_legacy_json_format(self, temp_data_dir, sample_intervention_new_format):
        """Test loading interventions from legacy JSON format."""
        interventions_file = temp_data_dir / "interventions" / "2026-01-15.json"
        with open(interventions_file, "w") as f:
            json.dump(sample_intervention_new_format, f)

        data = modal_agent.load_interventions("2026-01-15")

        assert data["date"] == "2026-01-15"
        assert len(data["entries"]) == 2
        assert data["entries"][0]["raw"] == "took 2 magnesium capsules"

    def test_load_interventions_prefers_jsonl_over_json(self, temp_data_dir):
        """Test that JSONL is preferred over legacy JSON when both exist."""
        # Create both files with different content
        jsonl_file = temp_data_dir / "interventions" / "2026-01-15.jsonl"
        with open(jsonl_file, "w") as f:
            f.write(json.dumps({"time": "10:00", "raw": "from jsonl", "parsed": None}) + "\n")

        json_file = temp_data_dir / "interventions" / "2026-01-15.json"
        with open(json_file, "w") as f:
            json.dump({"date": "2026-01-15", "entries": [{"time": "10:00", "raw": "from json", "parsed": None}]}, f)

        data = modal_agent.load_interventions("2026-01-15")

        # Should load from JSONL
        assert data["entries"][0]["raw"] == "from jsonl"

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
        assert "cleaned" in first_entry
        assert first_entry["time"] == "19:30"


class TestLoadHistoricalInterventions:
    """Tests for load_historical_interventions() function."""

    def test_load_historical_interventions_empty(self, temp_data_dir, mock_now_nyc):
        """Test loading historical interventions with no files."""
        data = modal_agent.load_historical_interventions(days=7)
        assert data == {}

    def test_load_historical_interventions_multiple_days(self, temp_data_dir, mock_now_nyc):
        """Test loading interventions from multiple days."""
        # Create intervention files for a few days (JSONL format)
        for day_offset in [0, 1, 3]:  # Today, yesterday, 3 days ago
            date = f"2026-01-{15 - day_offset:02d}"
            with open(temp_data_dir / "interventions" / f"{date}.jsonl", "w") as f:
                f.write(json.dumps({"time": "10:00", "raw": f"test intervention {day_offset}", "parsed": None}) + "\n")

        historical = modal_agent.load_historical_interventions(days=7)

        assert len(historical) == 3
        assert "2026-01-15" in historical
        assert "2026-01-14" in historical
        assert "2026-01-12" in historical

    def test_load_historical_interventions_respects_window(self, temp_data_dir, mock_now_nyc):
        """Test that only interventions within window are loaded."""
        # Create intervention from 30 days ago
        old_date = "2025-12-16"
        with open(temp_data_dir / "interventions" / f"{old_date}.jsonl", "w") as f:
            f.write(json.dumps({"time": "10:00", "raw": "old entry", "parsed": None}) + "\n")

        # Create intervention from today
        today_date = "2026-01-15"
        with open(temp_data_dir / "interventions" / f"{today_date}.jsonl", "w") as f:
            f.write(json.dumps({"time": "10:00", "raw": "today entry", "parsed": None}) + "\n")

        # Load only last 7 days
        historical = modal_agent.load_historical_interventions(days=7)

        assert today_date in historical
        assert old_date not in historical

    def test_load_historical_interventions_skips_empty_entries(self, temp_data_dir, mock_now_nyc):
        """Test that days with no entries are not included."""
        # Create empty JSONL file
        date = "2026-01-15"
        with open(temp_data_dir / "interventions" / f"{date}.jsonl", "w") as f:
            pass  # Empty file

        historical = modal_agent.load_historical_interventions(days=7)

        assert date not in historical


class TestSaveInterventions:
    """Tests for save_interventions() function."""

    def test_save_interventions_writes_jsonl_file(self, temp_data_dir):
        """Test saving interventions writes to JSONL file."""
        data = {
            "date": "2026-01-15",
            "entries": [
                {"time": "10:00", "raw": "test", "parsed": [{"type": "supplement", "name": "test"}]}
            ]
        }

        modal_agent.save_interventions("2026-01-15", data)

        interventions_file = temp_data_dir / "interventions" / "2026-01-15.jsonl"
        assert interventions_file.exists()

        with open(interventions_file) as f:
            lines = [json.loads(line) for line in f if line.strip()]

        assert len(lines) == 1
        assert lines[0]["raw"] == "test"

    def test_save_interventions_removes_legacy_json(self, temp_data_dir):
        """Test saving interventions removes legacy JSON file."""
        # Create legacy JSON file
        json_file = temp_data_dir / "interventions" / "2026-01-15.json"
        with open(json_file, "w") as f:
            json.dump({"date": "2026-01-15", "entries": []}, f)

        # Save via save_interventions
        data = {"date": "2026-01-15", "entries": [{"time": "10:00", "raw": "test", "parsed": None}]}
        modal_agent.save_interventions("2026-01-15", data)

        # Legacy file should be removed
        assert not json_file.exists()

        # JSONL file should exist
        jsonl_file = temp_data_dir / "interventions" / "2026-01-15.jsonl"
        assert jsonl_file.exists()

    def test_save_interventions_overwrites_existing(self, temp_data_dir):
        """Test saving interventions overwrites existing JSONL file."""
        # Create initial file
        interventions_file = temp_data_dir / "interventions" / "2026-01-15.jsonl"
        with open(interventions_file, "w") as f:
            f.write(json.dumps({"time": "08:00", "raw": "old", "parsed": None}) + "\n")

        # Overwrite with new data
        new_data = {"date": "2026-01-15", "entries": [{"time": "10:00", "raw": "new", "parsed": None}]}
        modal_agent.save_interventions("2026-01-15", new_data)

        with open(interventions_file) as f:
            lines = [json.loads(line) for line in f if line.strip()]

        assert len(lines) == 1
        assert lines[0]["raw"] == "new"


class TestGetTodayInterventions:
    """Tests for get_today_interventions() function."""

    def test_get_today_interventions_empty(self, temp_data_dir, mock_now_nyc):
        """Test getting today's interventions when none exist."""
        entries = modal_agent.get_today_interventions()
        assert entries == []

    def test_get_today_interventions_jsonl_format(self, temp_data_dir, mock_now_nyc):
        """Test getting today's interventions in JSONL format."""
        with open(temp_data_dir / "interventions" / "2026-01-15.jsonl", "w") as f:
            f.write(json.dumps({"time": "10:00", "raw": "test 1", "parsed": None}) + "\n")
            f.write(json.dumps({"time": "11:00", "raw": "test 2", "parsed": None}) + "\n")

        entries = modal_agent.get_today_interventions()

        assert len(entries) == 2
        assert entries[0]["raw"] == "test 1"
        assert entries[1]["raw"] == "test 2"

    def test_get_today_interventions_legacy_json_format(self, temp_data_dir, mock_now_nyc):
        """Test getting today's interventions handles legacy JSON format."""
        data = {
            "date": "2026-01-15",
            "entries": [
                {"time": "10:00", "raw": "test legacy", "parsed": None}
            ]
        }
        with open(temp_data_dir / "interventions" / "2026-01-15.json", "w") as f:
            json.dump(data, f)

        entries = modal_agent.get_today_interventions()

        assert len(entries) == 1
        assert entries[0]["raw"] == "test legacy"

    def test_get_today_interventions_old_format(self, temp_data_dir, mock_now_nyc):
        """Test getting today's interventions handles old format migration."""
        data = {
            "date": "2026-01-15",
            "interventions": [
                {"type": "supplement", "name": "magnesium", "timestamp": "2026-01-15T10:00:00"}
            ]
        }
        with open(temp_data_dir / "interventions" / "2026-01-15.json", "w") as f:
            json.dump(data, f)

        entries = modal_agent.get_today_interventions()

        # Should migrate and return entries
        assert len(entries) == 1
