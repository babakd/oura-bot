"""
End-to-end tests for the morning brief pipeline.

Tests the full flow: Oura fetch -> extraction -> Claude analysis -> Telegram send
"""

import json
import pytest
from unittest.mock import MagicMock, patch, call
from pathlib import Path


class TestMorningBriefE2E:
    """End-to-end tests for the morning_brief() function."""

    def test_morning_brief_success(
        self,
        temp_data_dir,
        mock_now_nyc,
        sample_oura_sleep_response,
        sample_oura_daily_sleep_response,
        sample_oura_readiness_response,
        sample_oura_stress_response,
        sample_oura_workouts_response,
        sample_oura_heartrate_response,
        monkeypatch,
    ):
        """Test successful morning brief generation end-to-end."""
        import modal_agent

        # Mock credentials
        env_vars = {
            "OURA_ACCESS_TOKEN": "test-oura-token",
            "ANTHROPIC_API_KEY": "test-anthropic-key",
            "TELEGRAM_BOT_TOKEN": "test-bot-token",
            "TELEGRAM_CHAT_ID": "test-chat-id",
        }
        monkeypatch.setattr("os.environ.get", lambda k, default=None: env_vars.get(k, default))

        # Build sleep data response (wake-date based)
        sleep_data = {
            "sleep": sample_oura_sleep_response["data"],
            "daily_sleep": sample_oura_daily_sleep_response["data"],
            "daily_readiness": sample_oura_readiness_response["data"],
        }

        # Build activity data response (calendar-date based)
        activity_data = {
            "daily_activity": [{"score": 85, "steps": 8500}],
            "daily_stress": sample_oura_stress_response["data"],
            "workouts": sample_oura_workouts_response["data"],
            "daytime_hr": sample_oura_heartrate_response["data"],
        }

        # Mock Oura API calls
        monkeypatch.setattr(modal_agent, "get_oura_sleep_data", lambda token, date: sleep_data)
        monkeypatch.setattr(modal_agent, "get_oura_activity_data", lambda token, date: activity_data)

        # Mock Claude API call
        mock_brief_content = "# Test Brief\n\nThis is a mock morning brief."
        monkeypatch.setattr(
            modal_agent,
            "generate_brief_with_claude",
            lambda *args, **kwargs: mock_brief_content
        )

        # Mock Telegram send
        telegram_calls = []
        def mock_send_telegram(msg, token, chat_id):
            telegram_calls.append({"message": msg, "token": token, "chat_id": chat_id})
            return True
        monkeypatch.setattr(modal_agent, "send_telegram", mock_send_telegram)

        # Mock volume.commit (can't run outside Modal)
        monkeypatch.setattr(modal_agent.volume, "commit", lambda: None)

        # Run the morning brief
        result = modal_agent.morning_brief.local()

        # Verify success
        assert result["status"] == "success"
        assert result["date"] == "2026-01-15"
        assert "metrics" in result

        # Verify brief was saved
        brief_file = temp_data_dir / "briefs" / "2026-01-15.md"
        assert brief_file.exists()
        assert brief_file.read_text() == mock_brief_content

        # Verify metrics were saved (for today's sleep)
        metrics_file = temp_data_dir / "metrics" / "2026-01-15.json"
        assert metrics_file.exists()
        saved_metrics = json.loads(metrics_file.read_text())
        assert "summary" in saved_metrics

        # Verify baselines were updated
        baselines_file = temp_data_dir / "baselines.json"
        assert baselines_file.exists()
        baselines = json.loads(baselines_file.read_text())
        assert "metrics" in baselines
        assert "2026-01-15" in baselines.get("dates", [])

        # Verify Telegram was called
        assert len(telegram_calls) == 1
        assert "*Morning Brief" in telegram_calls[0]["message"]
        assert mock_brief_content in telegram_calls[0]["message"]

    def test_morning_brief_no_sleep_data(
        self,
        temp_data_dir,
        mock_now_nyc,
        monkeypatch,
    ):
        """Test that partial brief is generated when no sleep data is recorded (ring removed)."""
        import modal_agent

        # Mock credentials
        env_vars = {
            "OURA_ACCESS_TOKEN": "test-oura-token",
            "ANTHROPIC_API_KEY": "test-anthropic-key",
            "TELEGRAM_BOT_TOKEN": "test-bot-token",
            "TELEGRAM_CHAT_ID": "test-chat-id",
        }
        monkeypatch.setattr("os.environ.get", lambda k, default=None: env_vars.get(k, default))

        # Return empty sleep data (ring removed during sleep)
        empty_sleep_data = {
            "sleep": [],
            "daily_sleep": [],
            "daily_readiness": [],
        }
        monkeypatch.setattr(modal_agent, "get_oura_sleep_data", lambda token, date: empty_sleep_data)

        # Return activity data (we should still get activity data)
        activity_data = {
            "daily_activity": [{"score": 75, "steps": 8000}],
            "daily_stress": [{"stress_high": 3600, "recovery_high": 1800, "day_summary": "normal"}],
            "workouts": [],
            "daytime_hr": [{"bpm": 70}],
        }
        monkeypatch.setattr(modal_agent, "get_oura_activity_data", lambda token, date: activity_data)

        # Mock Claude to generate partial brief
        mock_brief_content = "# Partial Brief\n\nSleep not recorded. Focus on activity data."
        monkeypatch.setattr(
            modal_agent,
            "generate_brief_with_claude",
            lambda *args, **kwargs: mock_brief_content
        )

        # Mock Telegram send
        telegram_calls = []
        def mock_send_telegram(msg, token, chat_id):
            telegram_calls.append({"message": msg})
            return True
        monkeypatch.setattr(modal_agent, "send_telegram", mock_send_telegram)

        # Mock volume.commit
        monkeypatch.setattr(modal_agent.volume, "commit", lambda: None)

        # Run the morning brief
        result = modal_agent.morning_brief.local()

        # Verify success status (partial brief generated)
        assert result["status"] == "success"
        assert result["date"] == "2026-01-15"

        # Verify metrics include sleep_recorded flag
        assert "metrics" in result
        assert result["metrics"].get("sleep_recorded") == False

        # Verify brief was sent to Telegram
        assert len(telegram_calls) == 1
        assert "*Morning Brief" in telegram_calls[0]["message"]

        # Verify brief was saved
        brief_file = temp_data_dir / "briefs" / "2026-01-15.md"
        assert brief_file.exists()

    def test_morning_brief_first_run(
        self,
        temp_data_dir,
        mock_now_nyc,
        sample_oura_sleep_response,
        sample_oura_daily_sleep_response,
        sample_oura_readiness_response,
        monkeypatch,
    ):
        """Test morning brief works on first run with no existing baselines."""
        import modal_agent

        # Mock credentials
        env_vars = {
            "OURA_ACCESS_TOKEN": "test-oura-token",
            "ANTHROPIC_API_KEY": "test-anthropic-key",
            "TELEGRAM_BOT_TOKEN": "test-bot-token",
            "TELEGRAM_CHAT_ID": "test-chat-id",
        }
        monkeypatch.setattr("os.environ.get", lambda k, default=None: env_vars.get(k, default))

        # Build sleep data response
        sleep_data = {
            "sleep": sample_oura_sleep_response["data"],
            "daily_sleep": sample_oura_daily_sleep_response["data"],
            "daily_readiness": sample_oura_readiness_response["data"],
        }

        # Minimal activity data
        activity_data = {
            "daily_activity": [],
            "daily_stress": [],
            "workouts": [],
            "daytime_hr": [],
        }

        # Mock Oura API calls
        monkeypatch.setattr(modal_agent, "get_oura_sleep_data", lambda token, date: sleep_data)
        monkeypatch.setattr(modal_agent, "get_oura_activity_data", lambda token, date: activity_data)

        # Track Claude API call arguments
        claude_calls = []
        def mock_generate_brief(*args, **kwargs):
            claude_calls.append({"args": args, "kwargs": kwargs})
            return "# First Run Brief"
        monkeypatch.setattr(modal_agent, "generate_brief_with_claude", mock_generate_brief)

        # Mock Telegram and volume
        monkeypatch.setattr(modal_agent, "send_telegram", lambda *args: True)
        monkeypatch.setattr(modal_agent.volume, "commit", lambda: None)

        # Run the morning brief
        result = modal_agent.morning_brief.local()

        # Verify success
        assert result["status"] == "success"

        # Verify Claude was called with default baselines (first run)
        assert len(claude_calls) == 1
        # args[5] is baselines in generate_brief_with_claude
        baselines_arg = claude_calls[0]["args"][5]
        assert "metrics" in baselines_arg

        # Verify baselines file was created
        baselines_file = temp_data_dir / "baselines.json"
        assert baselines_file.exists()


class TestMorningBriefEdgeCases:
    """Edge case tests for the morning brief pipeline."""

    def test_morning_brief_telegram_failure_still_saves(
        self,
        temp_data_dir,
        mock_now_nyc,
        sample_oura_sleep_response,
        sample_oura_daily_sleep_response,
        sample_oura_readiness_response,
        monkeypatch,
    ):
        """Test that brief is saved even if Telegram fails."""
        import modal_agent

        # Mock credentials
        env_vars = {
            "OURA_ACCESS_TOKEN": "test-oura-token",
            "ANTHROPIC_API_KEY": "test-anthropic-key",
            "TELEGRAM_BOT_TOKEN": "test-bot-token",
            "TELEGRAM_CHAT_ID": "test-chat-id",
        }
        monkeypatch.setattr("os.environ.get", lambda k, default=None: env_vars.get(k, default))

        # Build data responses
        sleep_data = {
            "sleep": sample_oura_sleep_response["data"],
            "daily_sleep": sample_oura_daily_sleep_response["data"],
            "daily_readiness": sample_oura_readiness_response["data"],
        }
        activity_data = {
            "daily_activity": [],
            "daily_stress": [],
            "workouts": [],
            "daytime_hr": [],
        }

        # Mock Oura API calls
        monkeypatch.setattr(modal_agent, "get_oura_sleep_data", lambda token, date: sleep_data)
        monkeypatch.setattr(modal_agent, "get_oura_activity_data", lambda token, date: activity_data)

        # Mock Claude
        mock_brief = "# Brief Content"
        monkeypatch.setattr(modal_agent, "generate_brief_with_claude", lambda *args, **kwargs: mock_brief)

        # Mock Telegram to fail
        monkeypatch.setattr(modal_agent, "send_telegram", lambda *args: False)

        # Mock volume
        monkeypatch.setattr(modal_agent.volume, "commit", lambda: None)

        # Run the morning brief
        result = modal_agent.morning_brief.local()

        # Verify success (should still succeed even with Telegram failure)
        assert result["status"] == "success"

        # Verify brief was saved
        brief_file = temp_data_dir / "briefs" / "2026-01-15.md"
        assert brief_file.exists()
        assert brief_file.read_text() == mock_brief
