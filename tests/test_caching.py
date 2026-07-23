"""
Tier 1 tests: prompt caching, model bump, widened conversation window.

These tests verify the new architecture behavior:
- CLAUDE_MODEL is Opus 4.8
- generate_brief_with_claude uses cached system prompt
- handle_message_with_agent uses cached system prompt + tools, with current date
  passed in a separate (uncached) system block
- load_conversation_history supports days_back filter instead of today_only
"""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

import modal_agent
from oura_agent import config
from oura_agent.claude import agent, handlers
from oura_agent.storage import conversations


class TestModelBump:
    def test_model_is_opus_4_8(self):
        assert config.CLAUDE_MODEL == "claude-opus-4-8"
        assert modal_agent.CLAUDE_MODEL == "claude-opus-4-8"


class TestBriefPromptCaching:
    def _make_mock_client(self, monkeypatch):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="*TL;DR*\nbrief body")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: mock_client)
        return mock_client

    def test_brief_system_prompt_is_cached_block_list(self, monkeypatch):
        mock_client = self._make_mock_client(monkeypatch)

        handlers.generate_brief_with_agent(
            "fake-key", "2026-01-15",
            {"sleep_score": 82},
            {"bedtime_start": "x", "bedtime_end": "y"},
            [],
        )

        call = mock_client.messages.create.call_args
        system = call.kwargs["system"]
        assert isinstance(system, list), "system must be a list of blocks for caching"
        assert len(system) >= 1
        first = system[0]
        assert first["type"] == "text"
        assert first.get("cache_control", {}).get("type") == "ephemeral"
        assert first["text"], "cached block must contain the system prompt"

    def test_brief_uses_opus_4_8(self, monkeypatch):
        mock_client = self._make_mock_client(monkeypatch)
        handlers.generate_brief_with_agent(
            "fake-key", "2026-01-15",
            {"sleep_score": 82},
            {"bedtime_start": "x", "bedtime_end": "y"},
            [],
        )
        call = mock_client.messages.create.call_args
        assert call.kwargs["model"] == "claude-opus-4-8"


class TestAgentPromptCaching:
    def _make_tool_then_final(self, monkeypatch):
        """Agent calls one tool, then returns final text."""
        tool_use = MagicMock()
        tool_use.type = "tool_use"
        tool_use.name = "get_baselines"
        tool_use.input = {}
        tool_use.id = "t1"
        first_resp = MagicMock()
        first_resp.content = [tool_use]

        final_text = MagicMock()
        final_text.type = "text"
        final_text.text = "Your baseline HRV is 48ms."
        second_resp = MagicMock()
        second_resp.content = [final_text]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [first_resp, second_resp]
        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: mock_client)
        return mock_client

    def test_system_is_block_list_with_cache_on_static_block(
        self, temp_data_dir, mock_now_nyc, monkeypatch
    ):
        mock_client = self._make_tool_then_final(monkeypatch)
        agent.handle_message_with_agent("fake-key", "hello")
        call = mock_client.messages.create.call_args_list[0]
        system = call.kwargs["system"]
        assert isinstance(system, list)
        assert len(system) >= 2, "expect static+date blocks"
        assert system[0].get("cache_control", {}).get("type") == "ephemeral"
        # Static block must NOT contain today's date (so cache holds across days)
        assert "2026-01-15" not in system[0]["text"]
        # Second block must carry the current date
        assert any("2026-01-15" in b.get("text", "") for b in system[1:])

    def test_tools_still_passed(self, temp_data_dir, mock_now_nyc, monkeypatch):
        mock_client = self._make_tool_then_final(monkeypatch)
        agent.handle_message_with_agent("fake-key", "hello")
        call = mock_client.messages.create.call_args_list[0]
        assert "tools" in call.kwargs
        assert len(call.kwargs["tools"]) > 0

    def test_agent_uses_opus_4_8(self, temp_data_dir, mock_now_nyc, monkeypatch):
        mock_client = self._make_tool_then_final(monkeypatch)
        agent.handle_message_with_agent("fake-key", "hello")
        call = mock_client.messages.create.call_args_list[0]
        assert call.kwargs["model"] == "claude-opus-4-8"


class TestConversationWindowWidening:
    def test_load_conversation_supports_days_back(self, temp_data_dir, mock_now_nyc):
        """Messages within days_back window are returned; older are filtered out."""
        conv_file = temp_data_dir / "conversations" / "history.jsonl"
        now = mock_now_nyc
        two_days_ago = (now - timedelta(days=2)).isoformat()
        five_days_ago = (now - timedelta(days=5)).isoformat()

        with open(conv_file, "w") as f:
            f.write(json.dumps({"timestamp": five_days_ago, "role": "user", "content": "old"}) + "\n")
            f.write(json.dumps({"timestamp": two_days_ago, "role": "user", "content": "recent"}) + "\n")
            f.write(json.dumps({"timestamp": now.isoformat(), "role": "user", "content": "today"}) + "\n")

        msgs = conversations.load_conversation_history(limit=20, days_back=3)
        contents = [m["content"] for m in msgs]
        assert "recent" in contents
        assert "today" in contents
        assert "old" not in contents

    def test_agent_loads_multi_day_history(
        self, temp_data_dir, mock_now_nyc, monkeypatch
    ):
        """Agent should see yesterday's messages, not just today's."""
        conv_file = temp_data_dir / "conversations" / "history.jsonl"
        now = mock_now_nyc
        yesterday = (now - timedelta(days=1)).isoformat()

        with open(conv_file, "w") as f:
            f.write(json.dumps({"timestamp": yesterday, "role": "user", "content": "yesterday msg"}) + "\n")
            f.write(json.dumps({"timestamp": yesterday, "role": "assistant", "content": "yesterday reply"}) + "\n")

        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(type="text", text="ok")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp
        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: mock_client)

        agent.handle_message_with_agent("fake-key", "today question")
        call = mock_client.messages.create.call_args
        messages = call.kwargs["messages"]
        # 2 history + 1 current = 3 total
        assert len(messages) == 3
        assert messages[0]["content"] == "yesterday msg"


class TestDeadCodeRemoval:
    def test_handle_message_removed(self):
        assert not hasattr(handlers, "handle_message"), "legacy handle_message should be deleted"

    def test_build_chat_context_removed(self):
        assert not hasattr(handlers, "build_chat_context"), "build_chat_context should be deleted"

    def test_format_intervention_response_removed(self):
        assert not hasattr(handlers, "format_intervention_response"), \
            "format_intervention_response should be deleted"

    def test_chat_system_prompt_removed(self):
        from oura_agent import prompts
        assert not hasattr(prompts, "CHAT_SYSTEM_PROMPT"), \
            "CHAT_SYSTEM_PROMPT should be removed"

    def test_chat_md_file_removed(self):
        from oura_agent.prompts import get_prompts_dir
        chat_md = get_prompts_dir() / "chat.md"
        assert not chat_md.exists(), "prompts/chat.md should be deleted"
