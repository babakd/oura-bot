"""
Tests for the tool-using morning brief (Option A) and green-day collapse (Option F).

Replaces the old `generate_brief_with_claude` one-shot: the brief now runs
through the same tool loop as the chat agent, seeded only with last night's
metrics. Historical context is pulled via tools on demand.
"""

import json
from unittest.mock import MagicMock

import pytest

from oura_agent.claude import agent, handlers


class TestBriefAgentShape:
    """The brief is a tool-using agent now, not a one-shot call."""

    def test_generate_brief_with_claude_removed(self):
        """Old one-shot entry point should be gone."""
        assert not hasattr(handlers, "generate_brief_with_claude"), \
            "old generate_brief_with_claude should be replaced by generate_brief_with_agent"

    def test_generate_brief_with_agent_exists(self):
        assert callable(getattr(handlers, "generate_brief_with_agent", None))

    def test_brief_agent_signature_is_lean(self):
        """Brief should take only api_key, today, metrics, detailed_sleep, detailed_workouts."""
        import inspect

        sig = inspect.signature(handlers.generate_brief_with_agent)
        params = list(sig.parameters.keys())
        # No more baselines, historical_metrics, historical_interventions, recent_briefs args
        for banned in ("baselines", "historical_metrics", "historical_interventions", "recent_briefs"):
            assert banned not in params, f"{banned} should not be a brief arg — fetch via tools"

    def test_brief_tools_include_chat_tools_plus_code_execution(self):
        """BRIEF_TOOLS = chat agent TOOLS + code_execution server tool."""
        brief_tools = agent.BRIEF_TOOLS
        names = [t.get("name") for t in brief_tools]
        # All chat agent tools present
        for t in agent.TOOLS:
            assert t["name"] in names, f"chat tool {t['name']} missing from brief tools"
        # Plus code_execution
        assert any("code_execution" in str(t.get("type", "") + t.get("name", "")) for t in brief_tools)


class TestBriefAgentLoop:
    """The brief runs iterations when Claude calls client tools."""

    def _mock_client(self, monkeypatch, responses):
        """responses is a list of MagicMock responses returned in order."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = responses
        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: mock_client)
        return mock_client

    def _text_response(self, text):
        r = MagicMock()
        r.content = [MagicMock(type="text", text=text)]
        return r

    def _tool_use_response(self, name, inp, tool_id="t1"):
        tu = MagicMock()
        tu.type = "tool_use"
        tu.name = name
        tu.input = inp
        tu.id = tool_id
        r = MagicMock()
        r.content = [tu]
        return r

    def test_brief_iterates_on_client_tool_use(self, temp_data_dir, mock_now_nyc, monkeypatch):
        """When Claude calls get_baselines, the loop executes it and calls Claude again."""
        # Seed a baselines file so the get_baselines tool has real data
        (temp_data_dir / "baselines.json").write_text(json.dumps({
            "data_points": 14,
            "metrics": {"hrv": {"mean": 48.0, "std": 5.0}},
        }))

        mock_client = self._mock_client(
            monkeypatch,
            [
                self._tool_use_response("get_baselines", {}),
                self._text_response("*TL;DR*\n• Brief body"),
            ],
        )

        brief = handlers.generate_brief_with_agent(
            "fake-key", "2026-01-15",
            metrics={"sleep_score": 82, "hrv": 55},
            detailed_sleep={"total_sleep_minutes": 450},
            detailed_workouts=[],
        )
        assert "*TL;DR*" in brief
        assert mock_client.messages.create.call_count == 2

    def test_brief_concatenates_multiple_text_blocks_in_final_turn(
        self, temp_data_dir, mock_now_nyc, monkeypatch
    ):
        """When code_execution runs, the final turn has multiple text blocks — concatenate them."""
        final = MagicMock()
        final.content = [
            MagicMock(type="text", text="Running the numbers..."),
            MagicMock(type="text", text="*TL;DR*\n• Real answer"),
        ]
        self._mock_client(monkeypatch, [final])

        brief = handlers.generate_brief_with_agent(
            "fake-key", "2026-01-15",
            metrics={"sleep_score": 82},
            detailed_sleep={"total_sleep_minutes": 450},
            detailed_workouts=[],
        )
        assert "Running the numbers" in brief
        assert "Real answer" in brief

    def test_brief_uses_opus_4_8_and_cached_system(
        self, temp_data_dir, mock_now_nyc, monkeypatch
    ):
        mock_client = self._mock_client(
            monkeypatch, [self._text_response("*TL;DR*\nok")]
        )
        handlers.generate_brief_with_agent(
            "fake-key", "2026-01-15",
            metrics={"sleep_score": 82},
            detailed_sleep={"total_sleep_minutes": 450},
            detailed_workouts=[],
        )
        call = mock_client.messages.create.call_args
        assert call.kwargs["model"] == "claude-opus-4-8"
        system = call.kwargs["system"]
        assert isinstance(system, list)
        assert system[0].get("cache_control", {}).get("type") == "ephemeral"

    def test_brief_seed_is_minimal(self, temp_data_dir, mock_now_nyc, monkeypatch):
        """The user message should NOT carry 28 days of history — that's what tools are for."""
        mock_client = self._mock_client(
            monkeypatch, [self._text_response("*TL;DR*\nok")]
        )
        handlers.generate_brief_with_agent(
            "fake-key", "2026-01-15",
            metrics={"sleep_score": 82},
            detailed_sleep={"total_sleep_minutes": 450},
            detailed_workouts=[],
        )
        call = mock_client.messages.create.call_args
        seed_text = call.kwargs["messages"][0]["content"]
        # Seed size check — old prompt was ~8000 chars of structured dump.
        assert len(seed_text) < 3000, f"seed too large ({len(seed_text)} chars) — should be <3000"
        # Seed shouldn't contain historical 28-day dump markers
        assert "HISTORICAL METRICS (last 28 days)" not in seed_text
        assert "INTERVENTIONS (last 28 days)" not in seed_text
        assert "RECENT BRIEFS (for continuity)" not in seed_text

    def test_brief_handles_missing_sleep(self, temp_data_dir, mock_now_nyc, monkeypatch):
        """Brief with sleep_recorded=False in seed still runs the loop."""
        mock_client = self._mock_client(
            monkeypatch,
            [self._text_response("*TL;DR*\n• Sleep not recorded\n• Focus on activity")]
        )
        brief = handlers.generate_brief_with_agent(
            "fake-key", "2026-01-15",
            metrics={"sleep_recorded": False, "sleep_note": "Ring removed during sleep"},
            detailed_sleep={},
            detailed_workouts=[],
        )
        assert "Sleep not recorded" in brief

    def test_brief_loop_has_higher_iteration_budget_than_chat(
        self, temp_data_dir, mock_now_nyc, monkeypatch
    ):
        """Chat caps at 5; brief should allow more (it does deeper analysis)."""
        # Always return tool use → exhaust iterations
        mock_client = self._mock_client(
            monkeypatch,
            [self._tool_use_response("get_baselines", {})] * 20,
        )
        (temp_data_dir / "baselines.json").write_text(json.dumps({
            "data_points": 1, "metrics": {}
        }))
        with pytest.raises(RuntimeError, match="exhausted"):
            handlers.generate_brief_with_agent(
                "fake-key", "2026-01-15",
                metrics={"sleep_score": 82},
                detailed_sleep={},
                detailed_workouts=[],
            )
        # Should allow at least 8 iterations before giving up
        assert mock_client.messages.create.call_count >= 8


class TestGreenDayCollapse:
    """Option F — prompt should tell Claude to collapse METRICS on boring days."""

    def test_prompt_mentions_collapse_on_green_days(self):
        from oura_agent.prompts import load_prompt
        prompt = load_prompt("morning_brief")
        lowered = prompt.lower()
        # Looking for phrasing that instructs collapse when metrics are within range
        assert any(
            kw in lowered
            for kw in ("collapse", "one-liner", "compress", "within range")
        ), "prompt should instruct collapsing METRICS on green days"

    def test_prompt_says_omit_empty_alerts(self):
        from oura_agent.prompts import load_prompt
        prompt = load_prompt("morning_brief")
        lowered = prompt.lower()
        # Prompt should tell Claude to OMIT the ALERTS section when nothing's wrong,
        # not emit "None."
        assert "omit" in lowered and "alert" in lowered, \
            "prompt should say to omit the ALERTS section when empty"

    def test_prompt_mentions_available_tools(self):
        from oura_agent.prompts import load_prompt
        prompt = load_prompt("morning_brief")
        lowered = prompt.lower()
        # Prompt should reference the tools
        assert "get_metrics" in lowered or "get_baselines" in lowered, \
            "prompt should reference the available tools by name"
        assert "code_execution" in lowered or "code execution" in lowered, \
            "prompt should mention code_execution is available"

    def test_prompt_does_not_contain_rigid_example_table(self):
        """The 'Example Guardrails' table anchored Claude to specific numbers.
        New prompt should either drop it or soften it significantly."""
        from oura_agent.prompts import load_prompt
        prompt = load_prompt("morning_brief")
        # Old table header was "| Metric | Suggested Concern Level | Contextual Notes |"
        assert "| Metric | Suggested Concern Level" not in prompt, \
            "drop the rigid guardrails table — it anchors Claude to specific thresholds"
