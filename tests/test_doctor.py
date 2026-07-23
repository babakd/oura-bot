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
