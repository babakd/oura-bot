"""
Tests for the agent module with tool use.
"""

import json
import pytest
from unittest.mock import MagicMock, patch

import modal_agent
from oura_agent.claude import agent


class TestExecuteTool:
    """Tests for the execute_tool function."""

    def test_get_metrics_filters_by_date(self, temp_data_dir, mock_now_nyc):
        """Test get_metrics tool filters by date range."""
        # Create sample metrics
        metrics_dir = temp_data_dir / "metrics"
        for i in range(10, 16):
            date = f"2026-01-{i:02d}"
            with open(metrics_dir / f"{date}.json", "w") as f:
                json.dump({
                    "date": date,
                    "summary": {"sleep_score": 70 + i}
                }, f)

        result = agent.execute_tool("get_metrics", {
            "start_date": "2026-01-12",
            "end_date": "2026-01-14"
        })

        data = json.loads(result)
        assert len(data) == 3
        dates = [d["date"] for d in data]
        assert "2026-01-12" in dates
        assert "2026-01-13" in dates
        assert "2026-01-14" in dates
        assert "2026-01-10" not in dates

    def test_get_detailed_sleep_returns_data(self, temp_data_dir, mock_now_nyc):
        """Test get_detailed_sleep returns detailed sleep data."""
        metrics_dir = temp_data_dir / "metrics"
        with open(metrics_dir / "2026-01-15.json", "w") as f:
            json.dump({
                "date": "2026-01-15",
                "summary": {"sleep_score": 82},
                "detailed_sleep": {
                    "bedtime_start": "2026-01-14T23:00:00",
                    "total_sleep_minutes": 450,
                    "deep_sleep_pct": 18
                }
            }, f)

        result = agent.execute_tool("get_detailed_sleep", {"date": "2026-01-15"})

        data = json.loads(result)
        assert data["bedtime_start"] == "2026-01-14T23:00:00"
        assert data["total_sleep_minutes"] == 450

    def test_get_detailed_sleep_missing_date(self, temp_data_dir, mock_now_nyc):
        """Test get_detailed_sleep returns error for missing date."""
        result = agent.execute_tool("get_detailed_sleep", {"date": "2026-01-01"})

        data = json.loads(result)
        assert "error" in data

    def test_get_interventions_filters_by_date(self, temp_data_dir, mock_now_nyc):
        """Test get_interventions filters by date range."""
        interventions_dir = temp_data_dir / "interventions"
        for i in range(10, 16):
            date = f"2026-01-{i:02d}"
            with open(interventions_dir / f"{date}.jsonl", "w") as f:
                f.write(json.dumps({"time": "10:00", "cleaned": f"Test {i}"}) + "\n")

        result = agent.execute_tool("get_interventions", {
            "start_date": "2026-01-12",
            "end_date": "2026-01-14"
        })

        data = json.loads(result)
        assert "2026-01-12" in data
        assert "2026-01-13" in data
        assert "2026-01-14" in data
        assert "2026-01-10" not in data

    def test_get_baselines_returns_simplified(self, temp_data_dir):
        """Test get_baselines returns simplified structure (no values arrays)."""
        baselines_data = {
            "data_points": 14,
            "last_updated": "2026-01-15T10:00:00",
            "metrics": {
                "sleep_score": {"mean": 75.0, "std": 8.0, "values": [70, 75, 80]},
                "hrv": {"mean": 48.0, "std": 6.0, "values": [45, 48, 51]}
            }
        }
        with open(temp_data_dir / "baselines.json", "w") as f:
            json.dump(baselines_data, f)

        result = agent.execute_tool("get_baselines", {})

        data = json.loads(result)
        assert data["data_points"] == 14
        assert "values" not in data["metrics"]["sleep_score"]
        assert data["metrics"]["sleep_score"]["mean"] == 75.0

    def test_log_intervention_saves(self, temp_data_dir, mock_now_nyc):
        """Test log_intervention saves intervention."""
        result = agent.execute_tool("log_intervention", {
            "raw_text": "took 2 mag",
            "normalized": "Magnesium 2 capsules"
        })

        data = json.loads(result)
        assert data["status"] == "logged"
        assert data["normalized"] == "Magnesium 2 capsules"

        # Verify it was saved
        entries = modal_agent.get_today_interventions()
        assert len(entries) == 1
        assert entries[0]["cleaned"] == "Magnesium 2 capsules"

    def test_get_today_interventions_returns_entries(self, temp_data_dir, mock_now_nyc):
        """Test get_today_interventions returns today's entries."""
        modal_agent.save_intervention_raw("test 1", "Test intervention 1")
        modal_agent.save_intervention_raw("test 2", "Test intervention 2")

        result = agent.execute_tool("get_today_interventions", {})

        data = json.loads(result)
        assert len(data) == 2

    def test_get_recent_briefs_returns_briefs(self, temp_data_dir, mock_now_nyc):
        """Test get_recent_briefs returns brief content."""
        briefs_dir = temp_data_dir / "briefs"
        for i in range(12, 15):
            date = f"2026-01-{i:02d}"
            with open(briefs_dir / f"{date}.md", "w") as f:
                f.write(f"*TL;DR*\nBrief for {date}")

        result = agent.execute_tool("get_recent_briefs", {"days": 3})

        data = json.loads(result)
        assert len(data) == 3

    def test_unknown_tool_returns_error(self):
        """Test unknown tool returns error."""
        result = agent.execute_tool("unknown_tool", {})

        data = json.loads(result)
        assert "error" in data
        assert "Unknown tool" in data["error"]


class TestHandleMessageWithAgent:
    """Tests for the agent loop."""

    def test_simple_response_no_tools(self, temp_data_dir, mock_now_nyc, monkeypatch):
        """Test agent returns response without using tools."""
        # Create mock response without tool use
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(type="thinking", thinking="..."),
            MagicMock(type="text", text="Hello! How can I help?")
        ]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: mock_client)

        # Mock prompt loading
        monkeypatch.setattr(agent, "_get_agent_prompt", lambda: "Test prompt")

        result = agent.handle_message_with_agent("fake-key", "hello")

        assert "Hello" in result
        # Conversation should be saved
        history = modal_agent.load_conversation_history()
        assert len(history) == 2

    def test_tool_use_and_response(self, temp_data_dir, mock_now_nyc, monkeypatch):
        """Test agent uses tool and returns final response."""
        # Create sample data
        metrics_dir = temp_data_dir / "metrics"
        with open(metrics_dir / "2026-01-15.json", "w") as f:
            json.dump({
                "date": "2026-01-15",
                "summary": {"sleep_score": 82},
                "detailed_sleep": {"total_sleep_minutes": 450}
            }, f)

        # First call: uses tool
        tool_use_block = MagicMock()
        tool_use_block.type = "tool_use"
        tool_use_block.name = "get_detailed_sleep"
        tool_use_block.input = {"date": "2026-01-15"}
        tool_use_block.id = "tool-123"

        progress_block = MagicMock()
        progress_block.type = "text"
        progress_block.text = "Looking at your sleep..."

        first_response = MagicMock()
        first_response.content = [progress_block, tool_use_block]

        # Second call: final response
        final_text_block = MagicMock()
        final_text_block.type = "text"
        final_text_block.text = "You slept 450 minutes last night."

        second_response = MagicMock()
        second_response.content = [final_text_block]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [first_response, second_response]

        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: mock_client)
        monkeypatch.setattr(agent, "_get_agent_prompt", lambda: "Test prompt")

        result = agent.handle_message_with_agent("fake-key", "how did I sleep?")

        assert "450" in result
        assert mock_client.messages.create.call_count == 2

    def test_progress_callback_called(self, temp_data_dir, mock_now_nyc, monkeypatch):
        """Test progress callback is called when tool is used."""
        # Tool use response
        tool_use_block = MagicMock()
        tool_use_block.type = "tool_use"
        tool_use_block.name = "get_baselines"
        tool_use_block.input = {}
        tool_use_block.id = "tool-456"

        progress_block = MagicMock()
        progress_block.type = "text"
        progress_block.text = "Checking your baselines..."

        first_response = MagicMock()
        first_response.content = [progress_block, tool_use_block]

        # Final response
        final_block = MagicMock()
        final_block.type = "text"
        final_block.text = "Your baseline HRV is 48ms."

        second_response = MagicMock()
        second_response.content = [final_block]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [first_response, second_response]

        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: mock_client)
        monkeypatch.setattr(agent, "_get_agent_prompt", lambda: "Test prompt")

        progress_messages = []

        def capture_progress(text):
            progress_messages.append(text)

        result = agent.handle_message_with_agent(
            "fake-key",
            "what's my baseline?",
            send_progress=capture_progress
        )

        assert len(progress_messages) == 1
        assert "baselines" in progress_messages[0].lower()

    def test_intervention_logging_via_tool(self, temp_data_dir, mock_now_nyc, monkeypatch):
        """Test agent logs intervention using log_intervention tool."""
        # Tool use for logging
        tool_use_block = MagicMock()
        tool_use_block.type = "tool_use"
        tool_use_block.name = "log_intervention"
        tool_use_block.input = {
            "raw_text": "took 2 magnesium",
            "normalized": "Magnesium 2 capsules"
        }
        tool_use_block.id = "tool-789"

        first_response = MagicMock()
        first_response.content = [tool_use_block]

        # Final response
        final_block = MagicMock()
        final_block.type = "text"
        final_block.text = "Logged magnesium. That's 1 thing today."

        second_response = MagicMock()
        second_response.content = [final_block]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [first_response, second_response]

        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: mock_client)
        monkeypatch.setattr(agent, "_get_agent_prompt", lambda: "Test prompt")

        result = agent.handle_message_with_agent("fake-key", "took 2 magnesium")

        assert "Logged" in result

        # Verify intervention was saved
        entries = modal_agent.get_today_interventions()
        assert len(entries) == 1
        assert entries[0]["cleaned"] == "Magnesium 2 capsules"

    def test_max_iterations_limit(self, temp_data_dir, mock_now_nyc, monkeypatch):
        """Test agent stops after max iterations."""
        # Create response that always uses a tool
        tool_use_block = MagicMock()
        tool_use_block.type = "tool_use"
        tool_use_block.name = "get_baselines"
        tool_use_block.input = {}
        tool_use_block.id = "tool-loop"

        loop_response = MagicMock()
        loop_response.content = [tool_use_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = loop_response

        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: mock_client)
        monkeypatch.setattr(agent, "_get_agent_prompt", lambda: "Test prompt")

        result = agent.handle_message_with_agent("fake-key", "infinite loop test")

        assert "able to complete" in result.lower()
        # Should have been called 5 times (max_iterations)
        assert mock_client.messages.create.call_count == 5

    def test_api_error_handling(self, temp_data_dir, mock_now_nyc, monkeypatch):
        """Test graceful handling of API errors."""
        import anthropic

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = anthropic.APIError(
            message="Rate limited",
            request=MagicMock(),
            body=None
        )

        monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: mock_client)
        monkeypatch.setattr(agent, "_get_agent_prompt", lambda: "Test prompt")

        result = agent.handle_message_with_agent("fake-key", "test")

        assert "error" in result.lower()

    def test_conversation_history_loaded(self, temp_data_dir, mock_now_nyc, monkeypatch):
        """Test conversation history is loaded and included."""
        # Add some conversation history
        modal_agent.save_conversation_message("user", "Previous question")
        modal_agent.save_conversation_message("assistant", "Previous answer")

        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Current response")]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: mock_client)
        monkeypatch.setattr(agent, "_get_agent_prompt", lambda: "Test prompt")

        agent.handle_message_with_agent("fake-key", "current question")

        # Check that messages include history
        call_args = mock_client.messages.create.call_args
        messages = call_args.kwargs["messages"]
        assert len(messages) == 3  # 2 history + 1 current
        assert messages[0]["content"] == "Previous question"


class TestAgentPrompt:
    """Tests for agent prompt loading."""

    def test_prompt_includes_date(self, temp_data_dir, mock_now_nyc, monkeypatch):
        """Test agent prompt includes current date."""
        # Create a test prompt file
        prompts_dir = temp_data_dir / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        with open(prompts_dir / "agent.md", "w") as f:
            f.write("Today is {current_date}. Help the user.")

        from oura_agent import prompts
        monkeypatch.setattr(prompts, "get_prompts_dir", lambda: prompts_dir)

        prompt = agent._get_agent_prompt()

        assert "2026-01-15" in prompt

    def test_missing_prompt_returns_empty(self, temp_data_dir, monkeypatch):
        """Test missing prompt file returns empty string."""
        # Need to patch load_prompt in the agent module since it's already imported
        def raise_error(name):
            raise FileNotFoundError("No prompt")

        monkeypatch.setattr(agent, "load_prompt", raise_error)

        prompt = agent._get_agent_prompt()

        assert prompt == ""


class TestToolDefinitions:
    """Tests for tool definitions."""

    def test_tools_have_required_fields(self):
        """Test all tools have required fields."""
        for tool in agent.TOOLS:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert "type" in tool["input_schema"]

    def test_log_intervention_tool_exists(self):
        """Test log_intervention tool is defined."""
        tool_names = [t["name"] for t in agent.TOOLS]
        assert "log_intervention" in tool_names

    def test_data_tools_exist(self):
        """Test all expected data tools are defined."""
        tool_names = [t["name"] for t in agent.TOOLS]
        expected = [
            "get_metrics",
            "get_detailed_sleep",
            "get_interventions",
            "get_baselines",
            "get_today_interventions",
            "get_recent_briefs"
        ]
        for name in expected:
            assert name in tool_names
