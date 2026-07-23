"""Focused tests for setup wizard configuration ordering."""

import stat
from types import SimpleNamespace

import requests

from scripts import setup


def test_apply_profile_timezone_uses_final_schedule_timezone():
    profile = {
        "user": {
            "name": "Test",
            "timezone": "America/New_York",
        }
    }

    result = setup.apply_profile_timezone(profile, "America/Los_Angeles")

    assert result is profile
    assert profile["user"]["timezone"] == "America/Los_Angeles"


def test_apply_profile_timezone_handles_missing_user_mapping():
    profile = {"preferences": {"communication_style": "thoughtful_coach"}}

    setup.apply_profile_timezone(profile, "Europe/London")

    assert profile["user"]["timezone"] == "Europe/London"


def test_apply_profile_timezone_preserves_skipped_profile():
    assert setup.apply_profile_timezone(None, "Asia/Tokyo") is None


def test_save_env_file_enforces_user_only_permissions(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("stale=value\n")
    env_file.chmod(0o644)

    setup.save_env_file(
        {
            "ANTHROPIC_API_KEY": "secret",
            "TELEGRAM_WEBHOOK_SECRET": "webhook-secret",
        },
        env_file,
    )

    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert "ANTHROPIC_API_KEY=secret" in env_file.read_text()


def test_create_modal_secrets_uses_private_temp_files_not_argv(monkeypatch):
    captured = []

    def fake_run(args, **kwargs):
        dotenv_path = args[args.index("--from-dotenv") + 1]
        path = setup.Path(dotenv_path)
        captured.append(
            {
                "args": list(args),
                "contents": path.read_text(),
                "mode": stat.S_IMODE(path.stat().st_mode),
                "path": path,
            }
        )
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(setup.subprocess, "run", fake_run)
    config = {
        "ANTHROPIC_API_KEY": "anthropic-secret",
        "OURA_ACCESS_TOKEN": "oura-secret",
        "TELEGRAM_BOT_TOKEN": "telegram-secret",
        "TELEGRAM_CHAT_ID": "123",
        "TELEGRAM_WEBHOOK_SECRET": "webhook-secret",
    }

    assert setup.create_modal_secrets(config) is True
    assert len(captured) == 3
    assert all(item["mode"] == 0o600 for item in captured)
    assert all(not item["path"].exists() for item in captured)

    argument_text = " ".join(
        argument
        for item in captured
        for argument in item["args"]
    )
    for value in config.values():
        assert value not in argument_text

    combined_contents = "\n".join(item["contents"] for item in captured)
    for value in config.values():
        assert value in combined_contents


def test_telegram_validation_error_does_not_echo_token(monkeypatch):
    token = "123456789:" + ("A" * 32)

    def fail(*args, **kwargs):
        raise requests.ConnectionError(
            f"failed at https://api.telegram.org/bot{token}/getMe"
        )

    monkeypatch.setattr(setup.requests, "get", fail)

    valid, message, details = setup.validate_telegram_bot(token)

    assert valid is False
    assert details == {}
    assert token not in message
    assert message == "Network error (ConnectionError)"


def test_profile_upload_uses_private_file_not_profile_argv(monkeypatch):
    captured = {}
    profile = {
        "user": {"name": "Private Name"},
        "preferences": {"primary_goals": ["sleep"]},
    }

    def fake_run(args, **kwargs):
        path = setup.Path(args[args.index(setup.MODAL_DATA_VOLUME) + 1])
        captured["args"] = list(args)
        captured["contents"] = path.read_text()
        captured["mode"] = stat.S_IMODE(path.stat().st_mode)
        captured["path"] = path
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(setup.subprocess, "run", fake_run)

    success, error = setup.upload_profile_to_modal(profile)

    assert success is True
    assert error == ""
    assert captured["mode"] == 0o600
    assert not captured["path"].exists()
    assert json_text(profile) == json_text(setup.json.loads(captured["contents"]))
    assert "Private Name" not in " ".join(captured["args"])


def json_text(value):
    return setup.json.dumps(value, sort_keys=True)
