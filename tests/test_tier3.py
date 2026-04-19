"""
Tier 3 tests: code_execution tool on morning brief, correlate_intervention tool on agent.
"""

import json
from unittest.mock import MagicMock

import pytest

import modal_agent
from oura_agent.claude import agent, handlers


class TestBriefCodeExecution:
    """generate_brief_with_claude should include the server-side code_execution tool."""

    def _make_client(self, monkeypatch, text_blocks):
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(type="text", text=t) for t in text_blocks]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp
        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: mock_client)
        return mock_client

    def test_brief_passes_code_execution_tool(self, monkeypatch, sample_baselines):
        mock_client = self._make_client(monkeypatch, ["*TL;DR*\nbrief body"])

        handlers.generate_brief_with_claude(
            "fake-key", "2026-01-15", {"sleep_score": 82},
            {"bedtime_start": "x", "bedtime_end": "y"},
            [], sample_baselines, [], {}, [],
        )
        call = mock_client.messages.create.call_args
        tools = call.kwargs.get("tools")
        assert tools is not None and len(tools) >= 1
        code_exec = next((t for t in tools if "code_execution" in str(t.get("type", "") + t.get("name", ""))), None)
        assert code_exec is not None, f"code_execution tool missing from {tools}"

    def test_brief_concatenates_multiple_text_blocks(self, monkeypatch, sample_baselines):
        """With code execution, Claude emits multiple text blocks interleaved with tool use.
        Our handler should combine them into a single brief."""
        self._make_client(
            monkeypatch,
            [
                "Analyzing your data...",
                "*TL;DR*\n• Core finding\n\n*METRICS*\n...",
            ],
        )

        brief = handlers.generate_brief_with_claude(
            "fake-key", "2026-01-15", {"sleep_score": 82},
            {"bedtime_start": "x", "bedtime_end": "y"},
            [], sample_baselines, [], {}, [],
        )
        assert "*TL;DR*" in brief
        assert "Core finding" in brief


class TestCorrelateInterventionTool:
    """A new correlate_intervention tool for the agent."""

    def test_tool_exists(self):
        tool_names = [t["name"] for t in agent.TOOLS]
        assert "correlate_intervention" in tool_names

    def test_tool_schema(self):
        tool = next(t for t in agent.TOOLS if t["name"] == "correlate_intervention")
        props = tool["input_schema"]["properties"]
        assert "substance" in props
        assert "metric" in props
        assert "days" in props

    def test_correlate_matches_intervention_days(self, temp_data_dir, mock_now_nyc):
        """Nights following a magnesium log should be grouped under 'with'; others under 'without'."""
        metrics_dir = temp_data_dir / "metrics"
        # Nights 2026-01-10..2026-01-15 — sleep score on each day represents that night's sleep.
        scores = {
            "2026-01-10": 70,
            "2026-01-11": 85,  # magnesium taken on the 10th → night of the 10th = score on 11th
            "2026-01-12": 74,
            "2026-01-13": 88,  # magnesium on 12th
            "2026-01-14": 72,
            "2026-01-15": 90,  # magnesium on 14th
        }
        for d, s in scores.items():
            with open(metrics_dir / f"{d}.json", "w") as f:
                json.dump({"date": d, "summary": {"sleep_score": s}}, f)

        # Interventions — magnesium on the 10th, 12th, 14th (evenings)
        interventions_dir = temp_data_dir / "interventions"
        for d in ["2026-01-10", "2026-01-12", "2026-01-14"]:
            with open(interventions_dir / f"{d}.jsonl", "w") as f:
                f.write(json.dumps({
                    "time": "21:00",
                    "cleaned": "Magnesium 400mg",
                    "raw": "took magnesium",
                }) + "\n")
        # Unrelated intervention on 2026-01-11 — should NOT match
        with open(interventions_dir / "2026-01-11.jsonl", "w") as f:
            f.write(json.dumps({
                "time": "09:00",
                "cleaned": "Coffee",
                "raw": "had coffee",
            }) + "\n")

        result = agent.execute_tool("correlate_intervention", {
            "substance": "magnesium",
            "metric": "sleep_score",
            "days": 30,
        })
        data = json.loads(result)
        assert data["metric"] == "sleep_score"
        assert data["substance"] == "magnesium"
        # "with" = nights after a magnesium day: 11, 13, 15 → scores 85, 88, 90
        assert data["n_with"] == 3
        assert abs(data["mean_with"] - (85 + 88 + 90) / 3) < 0.01
        # "without" = nights without a magnesium day prior: 10, 12, 14 → 70, 74, 72
        assert data["n_without"] == 3
        assert abs(data["mean_without"] - (70 + 74 + 72) / 3) < 0.01
        # delta = mean_with - mean_without (positive = substance associated with higher metric)
        assert data["delta"] > 0

    def test_correlate_no_intervention_days_returns_zero(
        self, temp_data_dir, mock_now_nyc
    ):
        metrics_dir = temp_data_dir / "metrics"
        for i, score in [(10, 70), (11, 75), (12, 80)]:
            with open(metrics_dir / f"2026-01-{i:02d}.json", "w") as f:
                json.dump({"date": f"2026-01-{i:02d}", "summary": {"sleep_score": score}}, f)

        result = agent.execute_tool("correlate_intervention", {
            "substance": "magnesium",
            "metric": "sleep_score",
            "days": 30,
        })
        data = json.loads(result)
        assert data["n_with"] == 0
        assert data["mean_with"] is None
        assert data["n_without"] == 3
