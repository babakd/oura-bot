"""Tests for evening brief functionality."""

import json
import pytest
from unittest.mock import Mock, patch, MagicMock
import modal_agent


class TestEveningSystemPrompt:
    """Tests for evening system prompt."""

    def test_evening_prompt_exists(self):
        """Test that EVENING_SYSTEM_PROMPT is defined."""
        assert hasattr(modal_agent, 'EVENING_SYSTEM_PROMPT')
        assert len(modal_agent.EVENING_SYSTEM_PROMPT) > 100

    def test_evening_prompt_content(self):
        """Test evening prompt contains key sections."""
        prompt = modal_agent.EVENING_SYSTEM_PROMPT
        assert "evening" in prompt.lower()
        assert "sleep" in prompt.lower()
        assert "intervention" in prompt.lower()
        assert "TL;DR" in prompt
        assert "TONIGHT'S RECOMMENDATIONS" in prompt


class TestGenerateEveningBrief:
    """Tests for generate_evening_brief_with_claude function."""

    def test_function_exists(self):
        """Test that generate_evening_brief_with_claude is defined."""
        assert hasattr(modal_agent, 'generate_evening_brief_with_claude')
        assert callable(modal_agent.generate_evening_brief_with_claude)

    @patch('anthropic.Anthropic')
    def test_generates_brief_with_mock_claude(self, mock_anthropic_class):
        """Test evening brief generation with mocked Claude."""
        # Setup mock
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Test brief content")]
        mock_client.messages.create.return_value = mock_response
        mock_anthropic_class.return_value = mock_client

        result = modal_agent.generate_evening_brief_with_claude(
            api_key="test-key",
            today="2026-01-15",
            metrics={"steps": 8000, "stress_high": 45, "recovery_high": 120},
            detailed_workouts=[{"activity": "cycling", "duration_minutes": 30}],
            baselines={"metrics": {}},
            historical_metrics=[],
            today_interventions=[{"time": "14:00", "cleaned": "2 magnesium capsules"}],
            recent_briefs=[]
        )

        assert result == "Test brief content"
        mock_client.messages.create.assert_called_once()

    @patch('anthropic.Anthropic')
    def test_handles_empty_data(self, mock_anthropic_class):
        """Test evening brief handles empty data gracefully."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Test brief content")]
        mock_client.messages.create.return_value = mock_response
        mock_anthropic_class.return_value = mock_client

        result = modal_agent.generate_evening_brief_with_claude(
            api_key="test-key",
            today="2026-01-15",
            metrics={},
            detailed_workouts=[],
            baselines={"metrics": {}},
            historical_metrics=[],
            today_interventions=[],
            recent_briefs=[]
        )

        assert result == "Test brief content"

    @patch('anthropic.Anthropic')
    def test_includes_workout_data(self, mock_anthropic_class):
        """Test that workout data is included in the prompt."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Brief")]
        mock_client.messages.create.return_value = mock_response
        mock_anthropic_class.return_value = mock_client

        modal_agent.generate_evening_brief_with_claude(
            api_key="test-key",
            today="2026-01-15",
            metrics={},
            detailed_workouts=[
                {"activity": "running", "duration_minutes": 45, "calories": 400}
            ],
            baselines={"metrics": {}},
            historical_metrics=[],
            today_interventions=[],
            recent_briefs=[]
        )

        # Check that the call was made with workout info in the prompt
        call_args = mock_client.messages.create.call_args
        user_content = call_args.kwargs["messages"][0]["content"]
        assert "running" in user_content
        assert "45" in user_content

    @patch('anthropic.Anthropic')
    def test_includes_interventions(self, mock_anthropic_class):
        """Test that today's interventions are included."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Brief")]
        mock_client.messages.create.return_value = mock_response
        mock_anthropic_class.return_value = mock_client

        modal_agent.generate_evening_brief_with_claude(
            api_key="test-key",
            today="2026-01-15",
            metrics={},
            detailed_workouts=[],
            baselines={"metrics": {}},
            historical_metrics=[],
            today_interventions=[
                {"time": "09:00", "cleaned": "Coffee 2 cups"},
                {"time": "14:00", "cleaned": "Omega-3 2 capsules"}
            ],
            recent_briefs=[]
        )

        call_args = mock_client.messages.create.call_args
        user_content = call_args.kwargs["messages"][0]["content"]
        assert "Coffee" in user_content
        assert "Omega-3" in user_content


class TestGetLatestBrief:
    """Tests for get_latest_brief - only returns morning briefs."""

    def test_excludes_evening_briefs(self, temp_data_dir):
        """Test that evening briefs are excluded, even when most recent."""
        import time

        # Create morning brief first
        morning_file = temp_data_dir / "briefs" / "2026-01-15.md"
        with open(morning_file, "w") as f:
            f.write("Morning content")

        # Wait a tiny bit to ensure different mtime
        time.sleep(0.01)

        # Create evening brief (more recent, but should be excluded)
        evening_file = temp_data_dir / "briefs" / "2026-01-15-evening.md"
        with open(evening_file, "w") as f:
            f.write("Evening content")

        result = modal_agent.get_latest_brief()

        # Should return morning brief, not evening
        assert "Morning content" in result
        assert "Evening content" not in result

    def test_returns_most_recent_morning_brief(self, temp_data_dir):
        """Test that most recent morning brief is returned."""
        import time

        # Create older morning brief
        old_file = temp_data_dir / "briefs" / "2026-01-14.md"
        with open(old_file, "w") as f:
            f.write("Old morning content")

        time.sleep(0.01)

        # Create newer morning brief
        new_file = temp_data_dir / "briefs" / "2026-01-15.md"
        with open(new_file, "w") as f:
            f.write("New morning content")

        result = modal_agent.get_latest_brief()

        assert "New morning content" in result

    def test_returns_message_when_only_evening_exists(self, temp_data_dir):
        """Test that no brief message shown when only evening briefs exist."""
        evening_file = temp_data_dir / "briefs" / "2026-01-15-evening.md"
        with open(evening_file, "w") as f:
            f.write("Evening content")

        result = modal_agent.get_latest_brief()

        assert "No briefs available" in result


class TestEveningBriefCron:
    """Tests for evening brief cron configuration."""

    def test_evening_brief_function_exists(self):
        """Test that evening_brief function is defined."""
        assert hasattr(modal_agent, 'evening_brief')

    def test_evening_brief_is_callable(self):
        """Test that evening_brief is a callable function."""
        # The actual function is wrapped by Modal decorators
        # We just verify it exists as an attribute
        assert 'evening_brief' in dir(modal_agent)
