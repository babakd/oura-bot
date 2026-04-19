"""
Tier 2 tests: tool consolidation, photo-through-agent, Telegram streaming.
"""

import json
from unittest.mock import MagicMock, call

import pytest

import modal_agent
from oura_agent.claude import agent
from oura_agent.telegram import client as tg_client


class TestToolConsolidation:
    """get_today_interventions and get_detailed_sleep should be merged away."""

    def test_get_today_interventions_tool_removed(self):
        tool_names = [t["name"] for t in agent.TOOLS]
        assert "get_today_interventions" not in tool_names

    def test_get_detailed_sleep_tool_removed(self):
        tool_names = [t["name"] for t in agent.TOOLS]
        assert "get_detailed_sleep" not in tool_names

    def test_get_interventions_is_sole_intervention_tool(self):
        tool_names = [t["name"] for t in agent.TOOLS]
        assert "get_interventions" in tool_names

    def test_get_metrics_supports_include_detailed_flag(self):
        """get_metrics should accept an optional include_detailed flag."""
        tool = next(t for t in agent.TOOLS if t["name"] == "get_metrics")
        props = tool["input_schema"]["properties"]
        assert "include_detailed" in props
        assert props["include_detailed"]["type"] == "boolean"

    def test_get_metrics_without_detailed_excludes_detailed_sleep(
        self, temp_data_dir, mock_now_nyc
    ):
        metrics_dir = temp_data_dir / "metrics"
        with open(metrics_dir / "2026-01-15.json", "w") as f:
            json.dump({
                "date": "2026-01-15",
                "summary": {"sleep_score": 82},
                "detailed_sleep": {"total_sleep_minutes": 450, "hr_first_third_avg": 56},
            }, f)

        result = agent.execute_tool("get_metrics", {
            "start_date": "2026-01-15", "end_date": "2026-01-15",
        })
        data = json.loads(result)
        assert data[0]["date"] == "2026-01-15"
        assert "detailed_sleep" not in data[0]

    def test_get_metrics_with_detailed_includes_detailed_sleep(
        self, temp_data_dir, mock_now_nyc
    ):
        metrics_dir = temp_data_dir / "metrics"
        with open(metrics_dir / "2026-01-15.json", "w") as f:
            json.dump({
                "date": "2026-01-15",
                "summary": {"sleep_score": 82},
                "detailed_sleep": {"total_sleep_minutes": 450, "hr_first_third_avg": 56},
            }, f)

        result = agent.execute_tool("get_metrics", {
            "start_date": "2026-01-15", "end_date": "2026-01-15",
            "include_detailed": True,
        })
        data = json.loads(result)
        assert data[0]["detailed_sleep"]["total_sleep_minutes"] == 450


class TestPhotoThroughAgent:
    """Photos should flow through handle_message_with_agent, not a separate path."""

    def test_agent_accepts_image_data(self, temp_data_dir, mock_now_nyc, monkeypatch):
        """handle_message_with_agent should accept image_data and place it in first user message."""
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(type="text", text="✓ Logged: Vitamin D 5000 IU")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp
        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: mock_client)

        agent.handle_message_with_agent(
            "fake-key",
            "my supplements",
            image_data=b"\xff\xd8\xffFAKEJPG",
        )

        call_args = mock_client.messages.create.call_args
        messages = call_args.kwargs["messages"]
        last_user = messages[-1]
        assert last_user["role"] == "user"
        content = last_user["content"]
        assert isinstance(content, list), "image user message should use list content"
        types = [c.get("type") for c in content]
        assert "image" in types
        assert "text" in types
        text_block = next(c for c in content if c["type"] == "text")
        assert "my supplements" in text_block["text"]

    def test_agent_text_only_stays_string_content(
        self, temp_data_dir, mock_now_nyc, monkeypatch
    ):
        """Text-only messages keep string content (no image block)."""
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(type="text", text="hello")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp
        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: mock_client)

        agent.handle_message_with_agent("fake-key", "hi there")
        call_args = mock_client.messages.create.call_args
        messages = call_args.kwargs["messages"]
        assert messages[-1]["content"] == "hi there"


class TestTelegramEditClient:
    """Telegram client needs send-returning-id and edit capabilities."""

    def test_send_telegram_message_returns_message_id(self, monkeypatch):
        """send_telegram_message should return the Telegram message_id on success."""
        captured = {}

        def fake_post(url, json=None, timeout=None):
            captured["url"] = url
            captured["payload"] = json
            resp = MagicMock()
            resp.ok = True
            resp.json = lambda: {"ok": True, "result": {"message_id": 42, "chat": {"id": 7}}}
            resp.status_code = 200
            return resp

        import requests
        monkeypatch.setattr(requests, "post", fake_post)

        msg_id = tg_client.send_telegram_message("hello", "bot-tok", "chat-7")
        assert msg_id == 42
        assert "sendMessage" in captured["url"]
        assert captured["payload"]["text"] == "hello"

    def test_send_telegram_message_none_on_failure(self, monkeypatch):
        def fake_post(url, json=None, timeout=None):
            resp = MagicMock()
            resp.ok = False
            resp.status_code = 400
            resp.text = "bad request"
            resp.json = lambda: {"ok": False}
            return resp

        import requests
        monkeypatch.setattr(requests, "post", fake_post)

        msg_id = tg_client.send_telegram_message("hello", "bot-tok", "chat-7")
        assert msg_id is None

    def test_edit_telegram_message_calls_edit_api(self, monkeypatch):
        captured = {}

        def fake_post(url, json=None, timeout=None):
            captured["url"] = url
            captured["payload"] = json
            resp = MagicMock()
            resp.ok = True
            resp.status_code = 200
            resp.text = "ok"
            resp.json = lambda: {"ok": True}
            return resp

        import requests
        monkeypatch.setattr(requests, "post", fake_post)

        ok = tg_client.edit_telegram_message("new text", 42, "bot-tok", "chat-7")
        assert ok is True
        assert "editMessageText" in captured["url"]
        assert captured["payload"]["message_id"] == 42
        assert captured["payload"]["chat_id"] == "chat-7"
        assert captured["payload"]["text"] == "new text"


class TestTelegramStreamer:
    """High-level streamer that batches edits for Telegram rate limits."""

    def test_streamer_creates_initial_message(self, monkeypatch):
        sent = []
        edits = []

        def fake_send(text, token, chat_id):
            sent.append(text)
            return 123

        def fake_edit(text, msg_id, token, chat_id):
            edits.append((text, msg_id))
            return True

        monkeypatch.setattr(tg_client, "send_telegram_message", fake_send)
        monkeypatch.setattr(tg_client, "edit_telegram_message", fake_edit)

        streamer = tg_client.TelegramStreamer("bot-tok", "chat-7", min_edit_interval=0.0)
        streamer.start("💭 thinking...")
        assert sent == ["💭 thinking..."]
        assert streamer.message_id == 123

    def test_streamer_edits_buffered_text(self, monkeypatch):
        edits = []
        monkeypatch.setattr(tg_client, "send_telegram_message", lambda *a, **k: 1)
        monkeypatch.setattr(
            tg_client,
            "edit_telegram_message",
            lambda text, mid, tok, cid: edits.append(text) or True,
        )

        streamer = tg_client.TelegramStreamer("b", "c", min_edit_interval=0.0)
        streamer.start("...")
        streamer.replace_text("Hello")
        streamer.replace_text("Hello world")
        streamer.finalize("Hello world!")
        assert edits[-1] == "Hello world!"

    def test_streamer_rate_limits_edits(self, monkeypatch):
        edits = []
        monkeypatch.setattr(tg_client, "send_telegram_message", lambda *a, **k: 1)
        monkeypatch.setattr(
            tg_client,
            "edit_telegram_message",
            lambda text, mid, tok, cid: edits.append(text) or True,
        )

        # Large interval: only finalize() should trigger an edit
        streamer = tg_client.TelegramStreamer("b", "c", min_edit_interval=60.0)
        streamer.start("...")
        streamer.replace_text("a")
        streamer.replace_text("ab")
        streamer.replace_text("abc")
        # None of those should have hit the wire yet
        assert edits == []
        streamer.finalize("abcd")
        assert edits == ["abcd"]


class TestAgentStreaming:
    """Agent should use the streaming API when a streamer is supplied."""

    def _make_stream_mock(self, final_text, tool_use=None):
        """Build a mock for client.messages.stream() context manager."""
        stream_ctx = MagicMock()

        # Text-only streaming path: yield a single text block
        final_block = MagicMock(type="text", text=final_text)
        final_response = MagicMock()
        if tool_use is not None:
            final_response.content = [tool_use]
        else:
            final_response.content = [final_block]

        stream_ctx.__enter__.return_value.get_final_message.return_value = final_response
        # text_stream yields incremental text chunks
        if tool_use is None:
            chunks = [final_text[i:i + 3] for i in range(0, len(final_text), 3)] or [""]
            stream_ctx.__enter__.return_value.text_stream = iter(chunks)
        else:
            stream_ctx.__enter__.return_value.text_stream = iter([])
        stream_ctx.__exit__.return_value = False
        return stream_ctx

    def test_stream_emits_text_to_streamer(
        self, temp_data_dir, mock_now_nyc, monkeypatch
    ):
        captured_updates = []

        class FakeStreamer:
            def __init__(self):
                self.message_id = 1
            def start(self, t):
                captured_updates.append(("start", t))
            def append_delta(self, d):
                captured_updates.append(("delta", d))
            def replace_text(self, t):
                captured_updates.append(("replace", t))
            def finalize(self, t):
                captured_updates.append(("final", t))

        mock_client = MagicMock()
        mock_client.messages.stream.return_value = self._make_stream_mock(
            "Your sleep score was 82."
        )

        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: mock_client)

        result = agent.handle_message_with_agent(
            "fake-key", "how did I sleep?", streamer=FakeStreamer()
        )
        assert "82" in result
        # At least one delta must have been streamed
        assert any(evt == "delta" for evt, _ in captured_updates)
        # Must end with a final commit
        assert captured_updates[-1][0] == "final"

    def test_stream_with_tool_use_iterates(
        self, temp_data_dir, mock_now_nyc, monkeypatch
    ):
        """With a tool call, agent should do a second streaming iteration for the final text."""
        tool_use = MagicMock()
        tool_use.type = "tool_use"
        tool_use.name = "get_baselines"
        tool_use.input = {}
        tool_use.id = "t-x"

        first = self._make_stream_mock("", tool_use=tool_use)
        second = self._make_stream_mock("Baseline HRV is 48ms.")

        mock_client = MagicMock()
        mock_client.messages.stream.side_effect = [first, second]

        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: mock_client)

        class FakeStreamer:
            def __init__(self):
                self.message_id = 1
                self.events = []
            def start(self, t): self.events.append(("start", t))
            def append_delta(self, d): self.events.append(("delta", d))
            def replace_text(self, t): self.events.append(("replace", t))
            def finalize(self, t): self.events.append(("final", t))

        streamer = FakeStreamer()
        # baselines file must exist so the tool can be invoked
        (temp_data_dir / "baselines.json").write_text(json.dumps({
            "data_points": 1, "metrics": {"hrv": {"mean": 48.0, "std": 6.0}},
        }))

        result = agent.handle_message_with_agent(
            "fake-key", "what's my baseline?", streamer=streamer
        )
        assert "48" in result
        assert mock_client.messages.stream.call_count == 2
