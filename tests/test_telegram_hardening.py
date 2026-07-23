"""Focused tests for safe Telegram delivery and callback helpers."""

from unittest.mock import MagicMock

import pytest
import requests

from oura_agent.telegram import client


def _response(status=200, text="ok", message_id=1, headers=None, body=None):
    response = MagicMock()
    response.status_code = status
    response.ok = 200 <= status < 300
    response.text = text
    response.headers = headers or {}
    response.json.return_value = body or {
        "ok": response.ok,
        "result": {"message_id": message_id},
    }
    return response


class TestSecretSafeDiagnostics:
    TOKEN = "123456789:" + ("A" * 32)

    def test_redacts_token_from_urls_and_bare_diagnostics(self):
        diagnostic = (
            "GET https://api.telegram.org/bot"
            f"{self.TOKEN}/sendMessage failed; credential={self.TOKEN}"
        )

        redacted = client._safe_telegram_diagnostic(diagnostic)

        assert self.TOKEN not in redacted
        assert "api.telegram.org/bot[REDACTED]/sendMessage" in redacted
        assert redacted.count("[REDACTED]") == 2

    def test_all_client_exception_logs_redact_bot_token(self, monkeypatch, caplog):
        def fail(*args, **kwargs):
            raise requests.ConnectionError(
                "connection failed for "
                f"https://api.telegram.org/bot{self.TOKEN}/sendMessage"
            )

        monkeypatch.setattr(client, "_telegram_post", fail)

        assert client.send_telegram("hello", self.TOKEN, "chat") is False
        assert (
            client.send_telegram_message("hello", self.TOKEN, "chat")
            is None
        )
        assert (
            client.edit_telegram_message("hello", 1, self.TOKEN, "chat")
            is False
        )
        assert (
            client.answer_callback_query("callback", self.TOKEN)
            is False
        )

        assert self.TOKEN not in caplog.text
        assert "api.telegram.org/bot[REDACTED]/sendMessage" in caplog.text

    def test_api_error_body_is_redacted_before_logging(self, monkeypatch, caplog):
        monkeypatch.setattr(
            client,
            "_telegram_post",
            lambda *args, **kwargs: _response(
                status=400,
                text=f"bad request for {self.TOKEN}",
            ),
        )

        assert client.send_telegram("hello", self.TOKEN, "chat") is False

        assert self.TOKEN not in caplog.text
        assert "[REDACTED]" in caplog.text

    def test_uncertain_delivery_suppresses_token_bearing_exception_chain(
        self,
        monkeypatch,
    ):
        def fail(*args, **kwargs):
            raise requests.ReadTimeout(
                "timed out at "
                f"https://api.telegram.org/bot{self.TOKEN}/sendMessage"
            )

        monkeypatch.setattr(client.requests, "post", fail)

        with pytest.raises(client.TelegramDeliveryUncertain) as raised:
            client.send_telegram_message(
                "daily card",
                self.TOKEN,
                "chat",
                raise_on_uncertain=True,
            )

        assert raised.value.__suppress_context__ is True
        assert raised.value.__cause__ is None
        assert self.TOKEN not in str(raised.value)


class TestSemanticSplitting:
    def test_keeps_normal_markdown_entity_atomic(self):
        entity = "*" + ("important " * 250) + "*"
        message = ("intro " * 450) + "\n\n" + entity + "\n\n" + ("tail " * 450)

        chunks = client.split_telegram_message(message, limit=4096)

        assert len(chunks) >= 2
        assert all(len(chunk) <= 4096 for chunk in chunks)
        assert sum(entity in chunk for chunk in chunks) == 1

    def test_rebalances_entity_longer_than_message_limit(self):
        message = "*" + ("a" * 9000) + "*"

        chunks = client.split_telegram_message(message, limit=4096)

        assert len(chunks) == 3
        assert all(len(chunk) <= 4096 for chunk in chunks)
        assert all(chunk.startswith("*") and chunk.endswith("*") for chunk in chunks)
        assert sum(len(chunk[1:-1]) for chunk in chunks) == 9000

    def test_rebalances_long_fenced_code_without_exceeding_limit(self):
        message = "```\n" + ("code line\n" * 1000) + "```"

        chunks = client.split_telegram_message(message, limit=4096)

        assert len(chunks) >= 2
        assert all(len(chunk) <= 4096 for chunk in chunks)
        assert all(chunk.startswith("```") and chunk.endswith("```") for chunk in chunks)

    def test_prefers_paragraph_or_line_boundaries(self):
        message = ("first paragraph " * 40) + "\n\n" + ("second paragraph " * 40)

        chunks = client.split_telegram_message(message, limit=1000)

        assert all(len(chunk) <= 1000 for chunk in chunks)
        assert all(chunk.strip() for chunk in chunks)
        assert chunks[0].endswith("paragraph")


class TestTelegramRetries:
    def test_retries_429_and_respects_retry_after_header(self, monkeypatch):
        responses = [
            _response(status=429, headers={"Retry-After": "3"}),
            _response(status=200),
        ]
        sleeps = []
        monkeypatch.setattr(client.requests, "post", lambda *a, **k: responses.pop(0))
        monkeypatch.setattr(client.time, "sleep", sleeps.append)

        result = client._telegram_post("https://example.test", {"x": 1})

        assert result.status_code == 200
        assert sleeps == [3.0]

    def test_retries_server_error(self, monkeypatch):
        responses = [_response(status=503), _response(status=200)]
        calls = []
        monkeypatch.setattr(
            client.requests,
            "post",
            lambda *a, **k: calls.append((a, k)) or responses.pop(0),
        )
        monkeypatch.setattr(client.time, "sleep", lambda _: None)

        result = client._telegram_post("https://example.test", {"x": 1})

        assert result.status_code == 200
        assert len(calls) == 2

    def test_does_not_retry_ordinary_4xx(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            client.requests,
            "post",
            lambda *a, **k: calls.append((a, k)) or _response(status=400),
        )
        monkeypatch.setattr(
            client.time,
            "sleep",
            lambda _: (_ for _ in ()).throw(AssertionError("must not sleep")),
        )

        result = client._telegram_post("https://example.test", {"x": 1})

        assert result.status_code == 400
        assert len(calls) == 1

    def test_at_most_once_send_does_not_retry_ambiguous_transport_failure(
        self,
        monkeypatch,
    ):
        calls = []

        def fail(*args, **kwargs):
            calls.append((args, kwargs))
            raise requests.ReadTimeout("response was not received")

        monkeypatch.setattr(client.requests, "post", fail)
        monkeypatch.setattr(
            client.time,
            "sleep",
            lambda _: (_ for _ in ()).throw(AssertionError("must not retry")),
        )

        with pytest.raises(client.TelegramDeliveryUncertain):
            client.send_telegram_message(
                "daily card",
                "bot",
                "chat",
                raise_on_uncertain=True,
            )

        assert len(calls) == 1


class TestInlineKeyboardAndCallbacks:
    def test_keyboard_is_attached_to_final_chunk(self, monkeypatch):
        payloads = []

        def fake_post(url, json=None, timeout=None):
            payloads.append(json)
            return _response(status=200, message_id=len(payloads))

        monkeypatch.setattr(client.requests, "post", fake_post)
        keyboard = [[{"text": "Useful", "callback_data": "useful:1"}]]

        first_id = client.send_telegram_message(
            ("paragraph\n\n" * 700),
            "bot",
            "chat",
            reply_markup=keyboard,
        )

        assert first_id == 1
        assert len(payloads) >= 2
        assert all("reply_markup" not in payload for payload in payloads[:-1])
        assert payloads[-1]["reply_markup"] == {"inline_keyboard": keyboard}

    def test_answer_callback_query(self, monkeypatch):
        captured = {}

        def fake_post(url, json=None, timeout=None):
            captured["url"] = url
            captured["payload"] = json
            return _response(status=200)

        monkeypatch.setattr(client.requests, "post", fake_post)

        assert client.answer_callback_query(
            "callback-1",
            "bot",
            text="Saved",
        )
        assert captured["url"].endswith("/answerCallbackQuery")
        assert captured["payload"]["callback_query_id"] == "callback-1"
        assert captured["payload"]["text"] == "Saved"


class TestStreamerHardening:
    def test_final_long_response_edits_then_sends_continuations(self, monkeypatch):
        edits = []
        sends = []

        def fake_send(text, token, chat_id, reply_markup=None):
            sends.append((text, reply_markup))
            return len(sends)

        def fake_edit(text, message_id, token, chat_id, reply_markup=None):
            edits.append((text, reply_markup))
            return True

        monkeypatch.setattr(client, "send_telegram_message", fake_send)
        monkeypatch.setattr(client, "edit_telegram_message", fake_edit)

        streamer = client.TelegramStreamer("bot", "chat", min_edit_interval=0)
        streamer.start("Thinking...")
        sends.clear()
        keyboard = [[{"text": "Useful", "callback_data": "useful:1"}]]
        final = ("first section " * 350) + "\n\n" + ("second section " * 350)

        assert streamer.finalize(final, reply_markup=keyboard)
        assert len(edits) == 1
        assert len(edits[0][0]) <= client.TELEGRAM_MESSAGE_LIMIT
        assert sends
        assert all(len(text) <= client.TELEGRAM_MESSAGE_LIMIT for text, _ in sends)
        assert sends[-1][1] == keyboard

    def test_streaming_preview_never_exceeds_limit(self, monkeypatch):
        edits = []
        monkeypatch.setattr(client, "send_telegram_message", lambda *a, **k: 7)
        monkeypatch.setattr(
            client,
            "edit_telegram_message",
            lambda text, *a, **k: edits.append(text) or True,
        )

        streamer = client.TelegramStreamer("bot", "chat", min_edit_interval=0)
        streamer.start("Thinking...")
        streamer.append_delta("x" * 6000)

        assert edits
        assert all(len(text) <= client.TELEGRAM_MESSAGE_LIMIT for text in edits)

    def test_failed_edit_is_not_marked_sent(self, monkeypatch):
        fallback = []
        monkeypatch.setattr(client, "send_telegram_message", lambda *a, **k: 7)
        monkeypatch.setattr(client, "edit_telegram_message", lambda *a, **k: False)
        monkeypatch.setattr(
            client,
            "send_telegram",
            lambda *a, **k: fallback.append((a, k)) or True,
        )

        streamer = client.TelegramStreamer("bot", "chat", min_edit_interval=0)
        streamer.start("Thinking...")

        assert streamer.finalize("Final answer") is False
        assert streamer._last_sent_text == "Thinking..."
        assert fallback
