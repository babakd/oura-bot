"""Decision-first daily product: packet, grounded card, and feedback loop."""

import json
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from oura_agent.insights import (
    build_daily_insight_packet,
    default_card_from_packet,
    normalize_card,
    render_daily_card,
    validate_model_card,
)


def _packet(
    sample_baselines,
    *,
    current=None,
    sleep_recorded=True,
    fetch_status=None,
    metric_provenance=None,
):
    metrics = {
        "sleep_score": 76,
        "readiness": 73,
        "hrv": 47,
        "resting_hr": 52,
        "deep_sleep_minutes": 67,
        "sleep_recorded": sleep_recorded,
    }
    metrics.update(current or {})
    history = [
        {
            "date": f"2026-01-{day:02d}",
            "summary": {
                "sleep_score": 72 + day % 7,
                "readiness": 69 + day % 6,
                "hrv": 44 + day % 8,
                "resting_hr": 50 + day % 5,
                "deep_sleep_minutes": 58 + day,
            },
        }
        for day in range(1, 15)
    ]
    return build_daily_insight_packet(
        "2026-01-15",
        metrics,
        {"bedtime_end": "2026-01-15T07:15:00-05:00"},
        reversed(history),  # input order must not matter
        sample_baselines,
        fetch_status=fetch_status,
        generated_at=datetime(
            2026, 1, 15, 10, 30, tzinfo=ZoneInfo("America/New_York")
        ),
        metric_provenance=metric_provenance,
    )


def test_normal_day_allows_no_action_and_is_short(sample_baselines):
    packet = _packet(sample_baselines)
    card = normalize_card(default_card_from_packet(packet), packet)
    rendered = render_daily_card(card, packet)

    assert packet["state"] == "typical"
    assert card["no_action"] is True
    assert "Follow your normal plan" in rendered
    assert len(rendered) <= 900
    assert "Oura through 7:15 AM" in rendered


def test_population_defaults_are_never_presented_as_personal_baseline():
    from oura_agent.storage.baselines import get_default_baselines

    packet = build_daily_insight_packet(
        "2026-01-15",
        {
            "hrv": 15,
            "readiness": 50,
            "sleep_score": 55,
            "sleep_recorded": True,
        },
        {"bedtime_end": "2026-01-15T07:15:00-05:00"},
        [],
        get_default_baselines(),
    )
    card = default_card_from_packet(packet)
    rendered = render_daily_card(card, packet)

    assert packet["state"] == "baseline_building"
    assert packet["metrics"]["hrv"]["baseline_n"] == 0
    assert packet["metrics"]["hrv"]["baseline_mean"] is None
    assert packet["metrics"]["hrv"]["z_score"] is None
    assert card["no_action"] is True
    assert "Building your personal baseline" in rendered
    assert "vs usual" not in rendered


def test_caution_card_uses_deterministic_numbers(sample_baselines):
    packet = _packet(
        sample_baselines,
        current={"hrv": 30, "readiness": 55},
    )
    card = normalize_card(default_card_from_packet(packet), packet)
    rendered = render_daily_card(card, packet)

    assert packet["state"] == "caution"
    assert card["no_action"] is False
    assert "vs usual" in rendered
    assert "diagnosis" in rendered
    assert len(rendered) <= 900


def test_caution_fallback_chooses_caution_not_larger_positive_anomaly(
    sample_baselines,
):
    packet = _packet(
        sample_baselines,
        current={"sleep_score": 99, "hrv": 38.4},
    )

    assert packet["state"] == "caution"
    assert packet["ranked_candidates"][0]["metric_key"] == "sleep_score"
    assert packet["ranked_candidates"][0]["polarity"] == "positive"

    card = default_card_from_packet(packet)
    assert card["evidence_keys"][0] == "hrv"
    assert card["action_domain"] == "recovery"
    assert card["headline"].startswith("HRV")


def test_caution_model_selection_must_lead_with_caution_evidence(
    sample_baselines,
):
    packet = _packet(
        sample_baselines,
        current={"sleep_score": 99, "hrv": 38.4},
    )
    positive_only_action = {
        "headline": "Sleep is unusually strong",
        "observation": "The positive sleep signal stands out.",
        "decision": "Change the plan based on sleep.",
        "action_domain": "sleep_quality",
        "evidence_keys": ["sleep_score"],
        "confidence": "medium",
        "no_action": False,
        "review_after": "tomorrow",
        "expected_outcome": "see what follows",
    }

    with pytest.raises(ValueError, match="caution evidence"):
        validate_model_card(positive_only_action, packet)

    normalized = normalize_card(positive_only_action, packet)
    assert normalized["evidence_keys"][0] == "hrv"
    assert normalized["action_domain"] == "recovery"


def test_missing_sleep_does_not_infer_deprivation(sample_baselines):
    packet = _packet(sample_baselines, sleep_recorded=False)
    card = default_card_from_packet(packet)
    rendered = render_daily_card(card, packet)

    assert packet["state"] == "sleep_missing"
    assert "not inferring recovery" in rendered
    assert "/regen-brief" in rendered
    assert "sleep deprivation" not in rendered.lower()


def test_invalid_model_evidence_rejects_the_whole_selection(sample_baselines):
    packet = _packet(sample_baselines)
    fallback = normalize_card(default_card_from_packet(packet), packet)
    normalized = normalize_card(
        {
            "headline": "Steady day",
            "observation": "No meaningful deviation.",
            "decision": "Follow the plan.",
            "action_domain": "no_action",
            "evidence_keys": ["sleep_score", "invented_metric", "hrv"],
            "confidence": "high",
            "no_action": True,
            "review_after": "tomorrow",
            "expected_outcome": "steady routine",
        },
        packet,
    )
    assert normalized == fallback


def test_yesterday_activity_keeps_its_source_date_and_is_not_duplicated(
    sample_baselines,
):
    baselines = deepcopy(sample_baselines)
    baselines["metrics"]["stress_high"] = {
        "mean": 65,
        "std": 10,
        "values": [50, 60, 70, 80],
    }
    history = [
        {"date": "2026-01-12", "summary": {"stress_high": 50}},
        {"date": "2026-01-13", "summary": {"stress_high": 60}},
        # Yesterday's value has already been saved before packet construction.
        {"date": "2026-01-14", "summary": {"stress_high": 90}},
        {"date": "2026-01-15", "summary": {"sleep_score": 76}},
    ]

    packet = build_daily_insight_packet(
        "2026-01-15",
        {"stress_high": 90, "sleep_recorded": True},
        {"bedtime_end": "2026-01-15T07:15:00-05:00"},
        history,
        baselines,
        metric_provenance={
            "stress_high": {
                "source_date": "2026-01-14",
                "source": "activity",
            }
        },
    )

    stress = packet["metrics"]["stress_high"]
    assert stress["source_date"] == "2026-01-14"
    assert stress["source"] == "activity"
    assert stress["previous"] == 60
    assert stress["delta_from_previous"] == 30
    assert stress["recent_7_average"] == pytest.approx(66.7)
    assert stress["evidence"].startswith("Yesterday’s High stress 90 min")


def test_required_empty_endpoint_is_partial_and_visible(sample_baselines):
    packet = _packet(
        sample_baselines,
        fetch_status={
            "daily_sleep": {"ok": True, "count": 1, "required": True},
            "daily_readiness": {"ok": True, "count": 0, "required": True},
            "sleep": {"ok": True, "count": 1, "required": True},
        },
    )
    model_card = {
        "headline": "A normal day",
        "observation": "No material change.",
        "decision": "Add an extra workout.",
        "action_domain": "no_action",
        "evidence_keys": ["sleep_score"],
        "confidence": "high",
        "no_action": True,
        "review_after": "tomorrow",
        "expected_outcome": "steady routine",
    }
    normalized = normalize_card(model_card, packet)
    rendered = render_daily_card(normalized, packet)

    assert packet["freshness"]["data_quality"] == "partial"
    assert packet["freshness"]["required_empty_endpoints"] == ["daily_readiness"]
    assert normalized["confidence"] == "medium"
    assert normalized["decision"] == "Follow your normal plan."
    assert "Oura through 7:15 AM · partial" in rendered


def test_stale_source_is_explicit_in_freshness_label(sample_baselines):
    packet = _packet(
        sample_baselines,
        fetch_status={
            "daily_sleep": {"ok": True, "count": 1, "required": True},
            "daily_readiness": {"ok": True, "count": 1, "required": True},
            "sleep": {
                "ok": True,
                "count": 0,
                "required": True,
                "stale": True,
            },
        },
    )
    rendered = render_daily_card(default_card_from_packet(packet), packet)

    assert packet["freshness"]["data_quality"] == "partial"
    assert "stale/partial" in rendered


def test_no_action_and_domain_invariants_reject_model_contradictions(
    sample_baselines,
):
    packet = _packet(sample_baselines)
    contradictory = {
        "headline": "Push today",
        "observation": "A hard session is warranted.",
        "decision": "Train harder than planned.",
        "action_domain": "recovery",
        "evidence_keys": ["readiness"],
        "confidence": "high",
        "no_action": False,
        "review_after": "tomorrow",
        "expected_outcome": "more fitness",
    }

    with pytest.raises(ValueError, match="ordinary day"):
        validate_model_card(contradictory, packet)

    normalized = normalize_card(contradictory, packet)
    assert normalized["no_action"] is True
    assert normalized["action_domain"] == "no_action"
    assert normalized["decision"] == "Follow your normal plan."


def test_action_domain_must_match_primary_valid_evidence(sample_baselines):
    packet = _packet(
        sample_baselines,
        current={"hrv": 30, "readiness": 55},
    )
    inconsistent = {
        "headline": "Recovery is lower",
        "observation": "Use a flexible plan.",
        "decision": "Let energy and soreness decide intensity.",
        "action_domain": "sleep_quality",
        "evidence_keys": ["hrv"],
        "confidence": "medium",
        "no_action": False,
        "review_after": "tomorrow",
        "expected_outcome": "avoid overreacting",
    }

    with pytest.raises(ValueError, match="action_domain"):
        validate_model_card(inconsistent, packet)


def test_feedback_ledger_is_append_only_and_idempotent(
    tmp_path, monkeypatch, sample_baselines, mock_now_nyc
):
    from oura_agent.storage import recommendations

    ledger_dir = tmp_path / "recommendations"
    monkeypatch.setattr(recommendations, "RECOMMENDATIONS_DIR", ledger_dir)
    monkeypatch.setattr(recommendations, "LEDGER_FILE", ledger_dir / "ledger.jsonl")

    packet = _packet(sample_baselines)
    card = normalize_card(default_card_from_packet(packet), packet)
    entry = recommendations.save_daily_card(
        "2026-01-15",
        card,
        render_daily_card(card, packet),
        packet,
        model="claude-opus-4-8",
    )
    first = recommendations.record_feedback(entry["id"], "not_for_me", update_id=42)
    duplicate = recommendations.record_feedback(entry["id"], "not_for_me", update_id=42)
    summary = recommendations.summarize_feedback()

    assert first == duplicate
    assert summary["feedback_counts"]["not_for_me"] == 1
    assert entry["domain"] in summary["not_for_me_domains"]
    assert len(recommendations.load_ledger()) == 2
    assert not recommendations.LEDGER_FILE.exists()
    assert len(list((ledger_dir / "events").glob("*.json"))) == 2


def test_next_day_outcome_links_only_to_a_delivered_prior_card(
    tmp_path,
    monkeypatch,
    sample_baselines,
    mock_now_nyc,
):
    from oura_agent.storage import recommendations

    ledger_dir = tmp_path / "recommendations"
    monkeypatch.setattr(recommendations, "RECOMMENDATIONS_DIR", ledger_dir)
    monkeypatch.setattr(recommendations, "LEDGER_FILE", ledger_dir / "ledger.jsonl")

    packet = _packet(sample_baselines)
    card = normalize_card(default_card_from_packet(packet), packet)
    entry = recommendations.save_daily_card(
        "2026-01-14",
        card,
        render_daily_card(card, packet),
        packet,
        model="claude-opus-4-8",
    )

    # A saved but undelivered card must not receive an outcome.
    assert recommendations.record_next_day_outcome(
        "2026-01-15",
        {"readiness": 78},
    ) is None

    recommendations.record_delivery(entry["id"], "sent", message_id=42)
    outcome = recommendations.record_next_day_outcome(
        "2026-01-15",
        {
            "readiness": 78,
            "hrv": 51.2,
            "sleep_recorded": True,
            "workout_minutes": 30,
        },
    )

    assert outcome["card_id"] == entry["id"]
    assert outcome["signals"] == {"hrv": 51.2, "readiness": 78}
    assert outcome["interpretation"] == "observed_after_not_attributed"
    assert recommendations.summarize_feedback()["recent_outcomes"] == [
        {
            "card_id": entry["id"],
            "card_date": "2026-01-14",
            "observed_date": "2026-01-15",
            "domain": entry["domain"],
            "signals": {"hrv": 51.2, "readiness": 78},
            "interpretation": "observed_after_not_attributed",
        }
    ]


def test_next_day_outcome_is_idempotent_for_same_card_and_date(
    tmp_path,
    monkeypatch,
    sample_baselines,
    mock_now_nyc,
):
    from oura_agent.storage import recommendations

    ledger_dir = tmp_path / "recommendations"
    monkeypatch.setattr(recommendations, "RECOMMENDATIONS_DIR", ledger_dir)
    monkeypatch.setattr(recommendations, "LEDGER_FILE", ledger_dir / "ledger.jsonl")

    packet = _packet(sample_baselines)
    card = normalize_card(default_card_from_packet(packet), packet)
    entry = recommendations.save_daily_card(
        "2026-01-14",
        card,
        render_daily_card(card, packet),
        packet,
        model="claude-opus-4-8",
    )
    recommendations.record_delivery(entry["id"], "sent", message_id=42)

    first = recommendations.record_next_day_outcome(
        "2026-01-15",
        {"readiness": 78},
    )
    duplicate = recommendations.record_next_day_outcome(
        "2026-01-16",
        {"readiness": 79},
    )

    assert first is not None
    assert duplicate is None
    outcomes = [
        event
        for event in recommendations.load_ledger()
        if event.get("type") == "outcome_observation"
    ]
    assert len(outcomes) == 1


def test_structured_card_generation_and_fable_refusal_fallback(
    monkeypatch, sample_baselines
):
    from oura_agent.claude import brief_card

    packet = _packet(sample_baselines)
    selected = {
        "headline": "A steady baseline day",
        "observation": "No signal warrants a plan change.",
        "decision": "Follow your normal plan.",
        "action_domain": "no_action",
        "evidence_keys": ["sleep_score", "readiness"],
        "confidence": "high",
        "no_action": True,
        "review_after": "tomorrow",
        "expected_outcome": "maintain a stable routine",
    }
    refused = SimpleNamespace(
        stop_reason="refusal",
        content=[],
        model="claude-fable-5",
    )
    served = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=json.dumps(selected))],
        model="claude-opus-4-8",
    )
    calls = []

    class Messages:
        def create(self, **kwargs):
            calls.append(kwargs["model"])
            return refused if len(calls) == 1 else served

    monkeypatch.setattr(
        brief_card.anthropic,
        "Anthropic",
        lambda api_key: SimpleNamespace(messages=Messages()),
    )
    monkeypatch.setattr(brief_card, "CLAUDE_MODEL", "claude-fable-5")

    result = brief_card.generate_daily_card("test-key", packet)

    assert calls == ["claude-fable-5", "claude-opus-4-8"]
    assert result.fallback_used is True
    assert result.model == "claude-opus-4-8"
    assert result.card["no_action"] is True
    assert len(result.text) <= 900


def test_missing_sleep_bypasses_model_and_serves_safe_card(
    monkeypatch, sample_baselines
):
    from oura_agent.claude import brief_card

    packet = _packet(sample_baselines, sleep_recorded=False)

    def should_not_construct_client(*args, **kwargs):
        raise AssertionError("model must not be called with missing required sleep")

    monkeypatch.setattr(
        brief_card.anthropic,
        "Anthropic",
        should_not_construct_client,
    )

    result = brief_card.generate_daily_card("test-key", packet)

    assert result.deterministic_fallback is True
    assert result.stop_reason == "data_quality_guardrail"
    assert result.card["action_domain"] == "data_quality"
    assert result.card["confidence"] == "low"
    assert "not inferring recovery" in result.text


def test_invalid_structured_evidence_falls_back_deterministically(
    monkeypatch, sample_baselines
):
    from oura_agent.claude import brief_card

    packet = _packet(sample_baselines)
    invalid = {
        "headline": "Steady day",
        "observation": "No meaningful deviation.",
        "decision": "Follow your normal plan.",
        "action_domain": "no_action",
        "evidence_keys": ["invented_metric"],
        "confidence": "high",
        "no_action": True,
        "review_after": "tomorrow",
        "expected_outcome": "steady routine",
    }
    response = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=json.dumps(invalid))],
        model="claude-opus-4-8",
    )

    monkeypatch.setattr(
        brief_card.anthropic,
        "Anthropic",
        lambda api_key: SimpleNamespace(
            messages=SimpleNamespace(create=lambda **kwargs: response)
        ),
    )

    result = brief_card.generate_daily_card("test-key", packet)

    assert result.deterministic_fallback is True
    assert result.stop_reason == "invalid_selection"
    assert result.card == normalize_card(default_card_from_packet(packet), packet)


def test_model_numbers_are_discarded_from_prose(monkeypatch, sample_baselines):
    from oura_agent.claude import brief_card

    packet = _packet(
        sample_baselines,
        current={"hrv": 30, "readiness": 55},
    )
    selected = {
        "headline": "Readiness is 99",
        "observation": "HRV improved by 400 percent.",
        "decision": "Run 20 miles.",
        "action_domain": "recovery",
        "evidence_keys": ["hrv"],
        "confidence": "high",
        "no_action": False,
        "review_after": "tomorrow",
        "expected_outcome": "Gain 10 points",
    }
    response = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=json.dumps(selected))],
        model="claude-opus-4-8",
    )

    class Messages:
        def create(self, **kwargs):
            return response

    monkeypatch.setattr(
        brief_card.anthropic,
        "Anthropic",
        lambda api_key: SimpleNamespace(messages=Messages()),
    )
    result = brief_card.generate_daily_card("test-key", packet)

    # The only numbers left are computed in the deterministic Signals line.
    prose = result.text.split("*Signals:*", 1)[0]
    assert not any(character.isdigit() for character in prose)


def test_run_lock_and_update_idempotency(tmp_path, monkeypatch, mock_now_nyc):
    from oura_agent.storage import runs

    monkeypatch.setattr(runs, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(runs, "UPDATES_FILE", tmp_path / "runs" / "updates.jsonl")
    monkeypatch.setattr(runs, "RUN_LEDGER_FILE", tmp_path / "runs" / "ledger.jsonl")
    monkeypatch.setattr(runs, "now_nyc", lambda: mock_now_nyc)

    assert runs.acquire_daily_lock("2026-01-15", "run-a") is True
    assert runs.acquire_daily_lock("2026-01-15", "run-b") is False
    runs.release_daily_lock("2026-01-15", "run-b")
    assert runs.acquire_daily_lock("2026-01-15", "run-b") is False
    runs.release_daily_lock("2026-01-15", "run-a")
    assert runs.acquire_daily_lock("2026-01-15", "run-b") is True

    assert runs.mark_update_processed(123) is True
    assert runs.mark_update_processed("123") is False


def test_distributed_lock_and_update_claims_are_atomic(
    tmp_path, monkeypatch, mock_now_nyc
):
    from oura_agent.storage import runs

    class Coordination:
        def __init__(self):
            self.values = {}

        def put(self, key, value, *, skip_if_exists=False):
            if skip_if_exists and key in self.values:
                return False
            self.values[key] = value
            return True

        def get(self, key, default=None):
            return self.values.get(key, default)

        def pop(self, key, default=None):
            return self.values.pop(key, default)

    monkeypatch.setattr(runs, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(runs, "now_nyc", lambda: mock_now_nyc)
    coordination = Coordination()

    assert runs.acquire_daily_lock(
        "2026-01-15", "run-a", coordination=coordination
    )
    assert not runs.acquire_daily_lock(
        "2026-01-15", "run-b", coordination=coordination
    )
    runs.release_daily_lock("2026-01-15", "run-b", coordination=coordination)
    assert not runs.acquire_daily_lock(
        "2026-01-15", "run-b", coordination=coordination
    )
    runs.release_daily_lock("2026-01-15", "run-a", coordination=coordination)
    assert runs.acquire_daily_lock(
        "2026-01-15", "run-b", coordination=coordination
    )

    assert runs.mark_update_processed(456, coordination=coordination)
    assert not runs.mark_update_processed("456", coordination=coordination)

    claim = runs.claim_update(789, coordination=coordination)
    assert claim
    assert runs.claim_update(789, coordination=coordination) is None
    assert runs.fail_update(789, claim, coordination=coordination)
    retry_claim = runs.claim_update(789, coordination=coordination)
    assert retry_claim and retry_claim != claim
    assert runs.complete_update(789, retry_claim, coordination=coordination)
    assert runs.claim_update(789, coordination=coordination) is None

    coordination.put(
        "telegram-update:790",
        {
            "update_id": "790",
            "claim_id": "dead-worker",
            "state": "claimed",
            "claimed_at": "2026-01-15T10:20:00-05:00",
        },
    )
    recovered_claim = runs.claim_update(
        790,
        coordination=coordination,
        stale_after_minutes=6,
    )
    assert recovered_claim and recovered_claim != "dead-worker"

    coordination.put(
        "telegram-update:791",
        {
            "update_id": "791",
            "claim_id": "live-worker",
            "state": "claimed",
            "claimed_at": "2026-01-15T10:29:00-05:00",
        },
    )
    assert (
        runs.claim_update(
            791,
            coordination=coordination,
            stale_after_minutes=6,
        )
        is None
    )
