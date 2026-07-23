"""
End-to-end tests for the morning brief pipeline.

Tests the full flow: Oura fetch -> extraction -> Claude analysis -> Telegram send
"""

import json
import pytest
from types import SimpleNamespace


def _generated_card(text):
    """Build the model boundary object returned by the daily-card generator."""
    return SimpleNamespace(
        text=text,
        card={
            "headline": "Protect tonight's recovery",
            "observation": "Recovery is the clearest signal in today's data.",
            "decision": "Keep today's training easy and protect bedtime.",
            "action_domain": "recovery",
            "evidence_keys": ["readiness"],
            "confidence": "medium",
            "no_action": False,
            "review_after": "Review tomorrow morning",
            "expected_outcome": "A steadier recovery signal tomorrow.",
        },
        model="claude-opus-4-8",
        stop_reason="end_turn",
        fallback_used=False,
        deterministic_fallback=False,
    )


class TestMorningBriefE2E:
    """End-to-end tests for the morning_brief() function."""

    def test_morning_brief_success(
        self,
        temp_data_dir,
        mock_now_nyc,
        sample_oura_sleep_response,
        sample_oura_daily_sleep_response,
        sample_oura_readiness_response,
        sample_oura_stress_response,
        sample_oura_workouts_response,
        sample_oura_heartrate_response,
        monkeypatch,
    ):
        """Test successful morning brief generation end-to-end."""
        import modal_agent

        # Mock credentials
        env_vars = {
            "OURA_ACCESS_TOKEN": "test-oura-token",
            "ANTHROPIC_API_KEY": "test-anthropic-key",
            "TELEGRAM_BOT_TOKEN": "test-bot-token",
            "TELEGRAM_CHAT_ID": "test-chat-id",
        }
        monkeypatch.setattr("os.environ.get", lambda k, default=None: env_vars.get(k, default))

        # Build sleep data response (wake-date based)
        sleep_data = {
            "sleep": sample_oura_sleep_response["data"],
            "daily_sleep": sample_oura_daily_sleep_response["data"],
            "daily_readiness": sample_oura_readiness_response["data"],
        }

        # Build activity data response (calendar-date based)
        activity_data = {
            "daily_activity": [{"score": 85, "steps": 8500}],
            "daily_stress": sample_oura_stress_response["data"],
            "workouts": sample_oura_workouts_response["data"],
            "daytime_hr": sample_oura_heartrate_response["data"],
        }

        # Mock Oura API calls
        monkeypatch.setattr(modal_agent, "get_oura_sleep_data", lambda token, date: sleep_data)
        monkeypatch.setattr(modal_agent, "get_oura_activity_data", lambda token, date: activity_data)

        # Mock the structured model boundary. Deterministic packet construction,
        # persistence, and delivery still run end-to-end.
        mock_brief_content = "# Test Brief\n\nThis is a mock morning brief."
        monkeypatch.setattr(
            modal_agent,
            "generate_daily_card",
            lambda *args, **kwargs: _generated_card(mock_brief_content),
        )

        # Mock Telegram send
        telegram_calls = []
        def mock_send_telegram_message(
            msg,
            token,
            chat_id,
            reply_markup=None,
            **kwargs,
        ):
            telegram_calls.append({
                "message": msg,
                "token": token,
                "chat_id": chat_id,
                "reply_markup": reply_markup,
            })
            return 123
        monkeypatch.setattr(
            modal_agent,
            "send_telegram_message",
            mock_send_telegram_message,
        )

        # Mock volume.commit (can't run outside Modal)
        monkeypatch.setattr(modal_agent.volume, "commit", lambda: None)

        # Run the morning brief
        result = modal_agent.morning_brief.local()

        # Verify success
        assert result["status"] == "success"
        assert result["date"] == "2026-01-15"
        assert "metrics" not in result
        assert modal_agent.coordination.get(
            f"telegram-delivery:{result['card_id']}"
        )["state"] == "sent"

        # Verify brief was saved
        brief_file = temp_data_dir / "briefs" / "2026-01-15.md"
        assert brief_file.exists()
        assert brief_file.read_text() == mock_brief_content

        # Verify metrics were saved (for today's sleep)
        metrics_file = temp_data_dir / "metrics" / "2026-01-15.json"
        assert metrics_file.exists()
        saved_metrics = json.loads(metrics_file.read_text())
        assert "summary" in saved_metrics

        # Verify baselines were updated
        baselines_file = temp_data_dir / "baselines.json"
        assert baselines_file.exists()
        baselines = json.loads(baselines_file.read_text())
        assert "metrics" in baselines
        assert "2026-01-15" in baselines.get("dates", [])

        # Verify Telegram was called
        assert len(telegram_calls) == 1
        assert "*Morning Brief" in telegram_calls[0]["message"]
        assert mock_brief_content in telegram_calls[0]["message"]
        assert telegram_calls[0]["reply_markup"]["inline_keyboard"]

    def test_morning_brief_no_sleep_data(
        self,
        temp_data_dir,
        mock_now_nyc,
        monkeypatch,
    ):
        """Test that partial brief is generated when no sleep data is recorded (ring removed)."""
        import modal_agent

        # Mock credentials
        env_vars = {
            "OURA_ACCESS_TOKEN": "test-oura-token",
            "ANTHROPIC_API_KEY": "test-anthropic-key",
            "TELEGRAM_BOT_TOKEN": "test-bot-token",
            "TELEGRAM_CHAT_ID": "test-chat-id",
        }
        monkeypatch.setattr("os.environ.get", lambda k, default=None: env_vars.get(k, default))

        # Return empty sleep data (ring removed during sleep)
        empty_sleep_data = {
            "sleep": [],
            "daily_sleep": [],
            "daily_readiness": [],
        }
        monkeypatch.setattr(modal_agent, "get_oura_sleep_data", lambda token, date: empty_sleep_data)

        # Return activity data (we should still get activity data)
        activity_data = {
            "daily_activity": [{"score": 75, "steps": 8000}],
            "daily_stress": [{"stress_high": 3600, "recovery_high": 1800, "day_summary": "normal"}],
            "workouts": [],
            "daytime_hr": [{"bpm": 70}],
        }
        monkeypatch.setattr(modal_agent, "get_oura_activity_data", lambda token, date: activity_data)

        # Mock Claude to generate partial brief
        mock_brief_content = "# Partial Brief\n\nSleep not recorded. Focus on activity data."
        monkeypatch.setattr(
            modal_agent,
            "generate_daily_card",
            lambda *args, **kwargs: _generated_card(mock_brief_content),
        )

        # Mock Telegram send
        telegram_calls = []
        def mock_send_telegram_message(
            msg,
            token,
            chat_id,
            reply_markup=None,
            **kwargs,
        ):
            telegram_calls.append({"message": msg, "reply_markup": reply_markup})
            return 124
        monkeypatch.setattr(
            modal_agent,
            "send_telegram_message",
            mock_send_telegram_message,
        )

        # Mock volume.commit
        monkeypatch.setattr(modal_agent.volume, "commit", lambda: None)

        # Run the morning brief
        result = modal_agent.morning_brief.local()

        # Verify success status (partial brief generated)
        assert result["status"] == "success"
        assert result["date"] == "2026-01-15"

        # Health metrics stay in the private Volume rather than the function
        # result payload.
        metrics_file = temp_data_dir / "metrics" / "2026-01-15.json"
        saved_metrics = json.loads(metrics_file.read_text())
        assert saved_metrics["summary"].get("sleep_recorded") is False
        assert "metrics" not in result

        # Verify brief was sent to Telegram
        assert len(telegram_calls) == 1
        assert "*Morning Brief" in telegram_calls[0]["message"]

        # Verify brief was saved
        brief_file = temp_data_dir / "briefs" / "2026-01-15.md"
        assert brief_file.exists()

    def test_morning_brief_first_run(
        self,
        temp_data_dir,
        mock_now_nyc,
        sample_oura_sleep_response,
        sample_oura_daily_sleep_response,
        sample_oura_readiness_response,
        monkeypatch,
    ):
        """Test morning brief works on first run with no existing baselines."""
        import modal_agent

        # Mock credentials
        env_vars = {
            "OURA_ACCESS_TOKEN": "test-oura-token",
            "ANTHROPIC_API_KEY": "test-anthropic-key",
            "TELEGRAM_BOT_TOKEN": "test-bot-token",
            "TELEGRAM_CHAT_ID": "test-chat-id",
        }
        monkeypatch.setattr("os.environ.get", lambda k, default=None: env_vars.get(k, default))

        # Build sleep data response
        sleep_data = {
            "sleep": sample_oura_sleep_response["data"],
            "daily_sleep": sample_oura_daily_sleep_response["data"],
            "daily_readiness": sample_oura_readiness_response["data"],
        }

        # Minimal activity data
        activity_data = {
            "daily_activity": [],
            "daily_stress": [],
            "workouts": [],
            "daytime_hr": [],
        }

        # Mock Oura API calls
        monkeypatch.setattr(modal_agent, "get_oura_sleep_data", lambda token, date: sleep_data)
        monkeypatch.setattr(modal_agent, "get_oura_activity_data", lambda token, date: activity_data)

        # Track the deterministic packet passed to the structured model boundary.
        card_calls = []
        def mock_generate_card(api_key, packet):
            card_calls.append({"api_key": api_key, "packet": packet})
            return _generated_card("# First Run Brief")
        monkeypatch.setattr(modal_agent, "generate_daily_card", mock_generate_card)

        # Mock Telegram and volume
        monkeypatch.setattr(
            modal_agent,
            "send_telegram_message",
            lambda *args, **kwargs: 125,
        )
        monkeypatch.setattr(modal_agent.volume, "commit", lambda: None)

        # Run the morning brief
        result = modal_agent.morning_brief.local()

        # Verify success
        assert result["status"] == "success"

        # Verify the model selector was called once with a fully built packet.
        assert len(card_calls) == 1
        assert card_calls[0]["api_key"] == "test-anthropic-key"
        assert card_calls[0]["packet"]["date"] == "2026-01-15"
        assert "sleep_score" in card_calls[0]["packet"]["metrics"]

        # Verify baselines file was created (still loaded for post-brief update path)
        baselines_file = temp_data_dir / "baselines.json"
        assert baselines_file.exists()


class TestMorningBriefEdgeCases:
    """Edge case tests for the morning brief pipeline."""

    def test_morning_brief_telegram_failure_still_saves(
        self,
        temp_data_dir,
        mock_now_nyc,
        sample_oura_sleep_response,
        sample_oura_daily_sleep_response,
        sample_oura_readiness_response,
        monkeypatch,
    ):
        """Test that brief is saved even if Telegram fails."""
        import modal_agent

        # Mock credentials
        env_vars = {
            "OURA_ACCESS_TOKEN": "test-oura-token",
            "ANTHROPIC_API_KEY": "test-anthropic-key",
            "TELEGRAM_BOT_TOKEN": "test-bot-token",
            "TELEGRAM_CHAT_ID": "test-chat-id",
        }
        monkeypatch.setattr("os.environ.get", lambda k, default=None: env_vars.get(k, default))

        # Build data responses
        sleep_data = {
            "sleep": sample_oura_sleep_response["data"],
            "daily_sleep": sample_oura_daily_sleep_response["data"],
            "daily_readiness": sample_oura_readiness_response["data"],
        }
        activity_data = {
            "daily_activity": [],
            "daily_stress": [],
            "workouts": [],
            "daytime_hr": [],
        }

        # Mock Oura API calls
        monkeypatch.setattr(modal_agent, "get_oura_sleep_data", lambda token, date: sleep_data)
        monkeypatch.setattr(modal_agent, "get_oura_activity_data", lambda token, date: activity_data)

        # Mock Claude
        mock_brief = "# Brief Content"
        monkeypatch.setattr(
            modal_agent,
            "generate_daily_card",
            lambda *args, **kwargs: _generated_card(mock_brief),
        )

        # Mock Telegram to fail
        monkeypatch.setattr(
            modal_agent,
            "send_telegram_message",
            lambda *args, **kwargs: None,
        )
        # The generic failure notification is a separate best-effort send.
        monkeypatch.setattr(modal_agent, "send_telegram", lambda *args: True)

        # Mock volume
        monkeypatch.setattr(modal_agent.volume, "commit", lambda: None)

        # Delivery failure is surfaced so Modal can retry/alert, while the
        # generated artifact remains safely persisted.
        with pytest.raises(RuntimeError, match="Telegram delivery failed"):
            modal_agent.morning_brief.local()

        # Verify brief was saved
        brief_file = temp_data_dir / "briefs" / "2026-01-15.md"
        assert brief_file.exists()
        assert brief_file.read_text() == mock_brief

        delivery_states = [
            value
            for key, value in modal_agent.coordination.values.items()
            if key.startswith("telegram-delivery:")
        ]
        assert len(delivery_states) == 1
        assert delivery_states[0]["state"] == "failed"

    def test_distributed_sent_marker_prevents_ambiguous_duplicate(
        self,
        temp_data_dir,
        mock_now_nyc,
        monkeypatch,
        sample_baselines,
    ):
        """A crash after Telegram acceptance must not trigger a second send."""
        import modal_agent
        from oura_agent.insights import (
            build_daily_insight_packet,
            default_card_from_packet,
            render_daily_card,
        )
        from oura_agent.storage.recommendations import save_daily_card

        packet = build_daily_insight_packet(
            "2026-01-15",
            {"sleep_recorded": True, "sleep_score": 80},
            {"bedtime_end": "2026-01-15T07:15:00-05:00"},
            [],
            sample_baselines,
        )
        card = default_card_from_packet(packet)
        entry = save_daily_card(
            "2026-01-15",
            card,
            render_daily_card(card, packet),
            packet,
            model="deterministic",
        )
        modal_agent.coordination.put(
            f"telegram-delivery:{entry['id']}",
            {"state": "sent", "message_id": 999},
        )
        monkeypatch.setattr(modal_agent.volume, "commit", lambda: None)

        monkeypatch.setattr(
            modal_agent,
            "get_oura_sleep_data",
            lambda *args: (_ for _ in ()).throw(
                AssertionError("duplicate run must stop before Oura fetch")
            ),
        )

        result = modal_agent.morning_brief.local(force=False)

        assert result["status"] == "already_sent"
        assert result["card_id"] == entry["id"]
