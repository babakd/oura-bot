"""
Tests for the non-destructive production doctor.
"""

from unittest.mock import MagicMock

import scripts.doctor as doctor


def test_redact_modal_url():
    url = "https://babakd--oura-agent-telegram-webhook.modal.run"

    assert doctor.redact_modal_url(url) == "https://<redacted>.modal.run"


def test_check_webhook_reachability_detects_disabled_workspace(monkeypatch):
    def fake_post(*args, **kwargs):
        response = MagicMock()
        response.status_code = 404
        response.text = "modal-http: workspace ac-123 is disabled"
        return response

    monkeypatch.setattr(doctor.requests, "post", fake_post)

    reachable, message = doctor.check_webhook_reachability("https://example.modal.run", None)

    assert reachable is False
    assert "workspace disabled" in message.lower()


def test_check_webhook_reachability_detects_secret_rejection(monkeypatch):
    def fake_post(*args, **kwargs):
        response = MagicMock()
        response.status_code = 401
        response.text = '{"ok": false, "error": "unauthorized"}'
        return response

    monkeypatch.setattr(doctor.requests, "post", fake_post)

    reachable, message = doctor.check_webhook_reachability("https://example.modal.run", "bad-secret")

    assert reachable is False
    assert "secret" in message.lower()


def test_sanitize_diagnostic_redacts_all_known_secrets_and_bot_urls(monkeypatch):
    secrets = {
        "TELEGRAM_BOT_TOKEN": "123456789:telegram-secret",
        "TELEGRAM_WEBHOOK_SECRET": "webhook-secret",
        "ANTHROPIC_API_KEY": "sk-ant-sensitive",
        "OURA_ACCESS_TOKEN": "oura-sensitive",
    }
    for key, value in secrets.items():
        monkeypatch.setenv(key, value)

    diagnostic = (
        "request failed at "
        "https://api.telegram.org/bot123456789:telegram-secret/getWebhookInfo "
        "with webhook-secret sk-ant-sensitive oura-sensitive"
    )

    sanitized = doctor.sanitize_diagnostic(diagnostic)

    assert all(secret not in sanitized for secret in secrets.values())
    assert "https://api.telegram.org/bot<redacted>/getWebhookInfo" in sanitized


def test_reachability_response_body_cannot_echo_secrets(monkeypatch):
    secret = "top-secret-webhook-value"
    bot_token = "123456789:telegram-secret"
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", bot_token)

    def fake_post(*args, **kwargs):
        response = MagicMock()
        response.status_code = 500
        response.text = (
            f"failed secret={secret} "
            f"url=https://api.telegram.org/bot{bot_token}/getMe"
        )
        return response

    monkeypatch.setattr(doctor.requests, "post", fake_post)

    reachable, message = doctor.check_webhook_reachability(
        "https://example.modal.run", secret
    )

    assert reachable is False
    assert secret not in message
    assert bot_token not in message
    assert "<redacted>" in message


def test_reachability_exception_text_is_not_returned(monkeypatch):
    secret = "top-secret-webhook-value"

    def fail_with_secret(*args, **kwargs):
        raise RuntimeError(f"request failed with {secret}")

    monkeypatch.setattr(doctor.requests, "post", fail_with_secret)

    reachable, message = doctor.check_webhook_reachability(
        "https://example.modal.run", secret
    )

    assert reachable is False
    assert secret not in message
    assert "RuntimeError" in message


def test_main_never_prints_exception_text_that_contains_token(monkeypatch, capsys):
    bot_token = "123456789:telegram-secret"
    required = {
        "TELEGRAM_BOT_TOKEN": bot_token,
        "TELEGRAM_CHAT_ID": "123456789",
        "TELEGRAM_WEBHOOK_SECRET": "webhook-secret",
        "ANTHROPIC_API_KEY": "sk-ant-sensitive",
        "OURA_ACCESS_TOKEN": "oura-sensitive",
    }
    for key, value in required.items():
        monkeypatch.setenv(key, value)

    monkeypatch.setattr(doctor, "load_local_env", lambda: None)

    def fail_with_secret(*args, **kwargs):
        raise RuntimeError(
            f"https://api.telegram.org/bot{bot_token}/getWebhookInfo failed"
        )

    monkeypatch.setattr(doctor, "get_webhook_info", fail_with_secret)

    assert doctor.main() == 1
    output = capsys.readouterr().out
    assert bot_token not in output
    assert "RuntimeError" in output
