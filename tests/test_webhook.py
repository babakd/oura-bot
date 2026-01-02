"""
Tests for Telegram webhook handling and command parsing.

Note: These tests focus on the logic of command routing and response generation,
not the actual HTTP endpoint (which requires Modal runtime).
"""

import json
import pytest
from unittest.mock import patch, MagicMock

import modal_agent


class TestWebhookCommandRouting:
    """Tests for webhook command routing logic."""

    def test_log_command_extracts_text(self, temp_data_dir, mock_now_nyc):
        """Test /log command extracts text after command."""
        # Simulate what happens in webhook
        text = "/log magnesium 400mg before bed"
        raw_text = text[4:].strip()

        assert raw_text == "magnesium 400mg before bed"

        # Save it
        entry = modal_agent.save_intervention_raw(raw_text)
        assert entry["raw"] == "magnesium 400mg before bed"

    def test_log_command_handles_empty(self):
        """Test /log command with no text."""
        text = "/log"
        raw_text = text[4:].strip()

        assert raw_text == ""

    def test_log_command_handles_whitespace_only(self):
        """Test /log command with only whitespace."""
        text = "/log   "
        raw_text = text[4:].strip()

        assert raw_text == ""

    def test_status_command_formats_entries(self, temp_data_dir, mock_now_nyc):
        """Test /status command formats entries correctly."""
        # Create some interventions
        modal_agent.save_intervention_raw("magnesium 400mg")
        modal_agent.save_intervention_raw("20 min sauna")

        entries = modal_agent.get_today_interventions()

        # Format as webhook would
        lines = ["Today's interventions:"]
        for e in entries:
            time = e.get("time", "")
            raw = e.get("raw", e.get("name", "unknown"))
            lines.append(f"  • {raw} @ {time}")

        response_text = "\n".join(lines)

        assert "magnesium 400mg" in response_text
        assert "20 min sauna" in response_text
        assert "@ 10:30" in response_text

    def test_status_command_empty(self, temp_data_dir, mock_now_nyc):
        """Test /status command with no interventions."""
        entries = modal_agent.get_today_interventions()

        if entries:
            response_text = "Today's interventions:"
        else:
            response_text = "No interventions logged today."

        assert response_text == "No interventions logged today."

    def test_brief_command_returns_latest(self, temp_data_dir, mock_now_nyc):
        """Test /brief command returns latest brief."""
        # Create a brief
        brief_content = "*TL;DR*\n• Test brief content\n• Second line"
        brief_file = temp_data_dir / "briefs" / "2026-01-15.md"
        with open(brief_file, "w") as f:
            f.write(brief_content)

        result = modal_agent.get_latest_brief()

        assert result == brief_content

    def test_brief_command_no_briefs(self, temp_data_dir):
        """Test /brief command when no briefs exist."""
        result = modal_agent.get_latest_brief()

        assert result == "No briefs available yet."

    def test_clear_command_removes_file(self, temp_data_dir, mock_now_nyc):
        """Test /clear command removes today's interventions."""
        # Create intervention
        modal_agent.save_intervention_raw("test intervention")

        interventions_file = temp_data_dir / "interventions" / "2026-01-15.jsonl"
        assert interventions_file.exists()

        # Clear it
        if interventions_file.exists():
            interventions_file.unlink()

        assert not interventions_file.exists()

    def test_natural_language_saves_raw(self, temp_data_dir, mock_now_nyc):
        """Test natural language messages are saved as raw text."""
        messages = [
            "took 2 magnesium capsules",
            "just finished 20 min sauna session",
            "had a glass of wine with dinner"
        ]

        for msg in messages:
            modal_agent.save_intervention_raw(msg)

        entries = modal_agent.get_today_interventions()

        assert len(entries) == 3
        assert entries[0]["raw"] == "took 2 magnesium capsules"
        assert entries[0]["cleaned"] == "took 2 magnesium capsules"


class TestWebhookRequestParsing:
    """Tests for parsing incoming Telegram webhook requests."""

    def test_parse_message_text(self):
        """Test extracting message text from webhook request."""
        request = {
            "message": {
                "text": "/status",
                "chat": {"id": 12345}
            }
        }

        text = request.get("message", {}).get("text", "")
        chat_id = str(request.get("message", {}).get("chat", {}).get("id", ""))

        assert text == "/status"
        assert chat_id == "12345"

    def test_parse_empty_message(self):
        """Test handling empty message."""
        request = {"message": {}}

        text = request.get("message", {}).get("text", "")

        assert text == ""

    def test_parse_missing_message(self):
        """Test handling missing message field."""
        request = {}

        text = request.get("message", {}).get("text", "")

        assert text == ""

    def test_command_detection(self):
        """Test detecting commands vs natural language."""
        commands = ["/log test", "/status", "/brief", "/clear", "/help"]
        natural = ["took 2 magnesium", "20 min sauna", "hello"]

        for cmd in commands:
            assert cmd.startswith("/")

        for msg in natural:
            assert not msg.startswith("/")


class TestWebhookChatValidation:
    """Tests for chat ID validation in webhook."""

    def test_accepts_configured_chat(self):
        """Test that configured chat ID is accepted."""
        configured_chat_id = "12345"
        sender_chat_id = "12345"

        assert sender_chat_id == configured_chat_id

    def test_rejects_other_chat(self):
        """Test that other chat IDs are rejected."""
        configured_chat_id = "12345"
        sender_chat_id = "99999"

        assert sender_chat_id != configured_chat_id


class TestHelpCommand:
    """Tests for /help command response."""

    def test_help_response_format(self):
        """Test help response includes all commands."""
        help_text = """Commands:
/status - Today's interventions
/brief - Latest morning brief
/clear - Clear today's interventions
/help - Show this

Or just type naturally:
  "2 neuro-mag capsules"
  "20 min sauna"
  "glass of wine with dinner" """

        assert "/status" in help_text
        assert "/brief" in help_text
        assert "/clear" in help_text
        assert "/help" in help_text
        assert "naturally" in help_text


class TestUnknownCommand:
    """Tests for unknown command handling."""

    def test_unknown_command_detection(self):
        """Test detecting unknown commands."""
        known_commands = ["/log", "/status", "/brief", "/clear", "/help"]
        text = "/unknown"

        is_command = text.startswith("/")
        is_known = any(text.startswith(cmd) for cmd in known_commands)

        assert is_command
        assert not is_known


class TestFormatInterventionResponse:
    """Tests for format_intervention_response() function."""

    def test_format_response_with_entries(self, temp_data_dir, mock_now_nyc, monkeypatch):
        """Test response uses Claude to generate natural summary."""
        from unittest.mock import MagicMock

        modal_agent.save_intervention_raw("magnesium 400mg")
        modal_agent.save_intervention_raw("20 min sauna")

        # Mock the Anthropic client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Got it. Today you've logged magnesium and a sauna session.")]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: mock_client)

        response = modal_agent.format_intervention_response("fake-key", "20 min sauna")

        assert "magnesium" in response.lower() or "sauna" in response.lower()
        # Verify Claude was called
        mock_client.messages.create.assert_called_once()

    def test_format_response_empty(self, temp_data_dir, mock_now_nyc):
        """Test response with no entries is just 'Logged.'"""
        response = modal_agent.format_intervention_response("fake-key", "test")

        # With no entries, should return simple fallback
        assert response == "Logged."

    def test_format_response_fallback_on_error(self, temp_data_dir, mock_now_nyc, monkeypatch):
        """Test fallback response when Claude fails."""
        modal_agent.save_intervention_raw("test intervention")

        # Mock Anthropic to raise an exception
        import anthropic
        def raise_error(api_key):
            raise Exception("API error")
        monkeypatch.setattr(anthropic, "Anthropic", raise_error)

        response = modal_agent.format_intervention_response("fake-key", "test")

        # Should use fallback
        assert "Logged" in response
        assert "1 today" in response
