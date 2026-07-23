"""Focused regression tests for data-ingestion and baseline reliability."""

import json
from types import SimpleNamespace

import pytest
import requests

from oura_agent.api import oura
from oura_agent.extraction.metrics import extract_activity_metrics
from oura_agent.storage.baselines import load_baselines, save_baselines, update_baselines
from oura_agent.storage.metrics import load_historical_metrics, save_daily_metrics


def _response(status_code, payload=None, text="", headers=None):
    return SimpleNamespace(
        status_code=status_code,
        text=text,
        headers=headers or {},
        json=lambda: payload if payload is not None else {"data": []},
    )


def test_day_collections_use_wide_range_and_exact_day_filter(monkeypatch):
    calls = []

    def fake_fetch(token, endpoint, start_date, end_date):
        calls.append((endpoint, start_date, end_date))
        if endpoint == "sleep":
            return {
                "data": [{
                    "type": "long_sleep",
                    "bedtime_end": "2026-01-15T07:00:00-05:00",
                }]
            }
        return {
            "data": [
                {"day": start_date, "id": f"{endpoint}-target"},
                {"day": end_date, "id": f"{endpoint}-neighbor"},
            ]
        }

    monkeypatch.setattr(oura, "fetch_oura_data", fake_fetch)

    result = oura.get_oura_daily_data(
        "token",
        "2026-01-15",
        context_date="2026-01-14",
    )

    assert calls == [
        ("daily_sleep", "2026-01-15", "2026-01-16"),
        ("daily_readiness", "2026-01-15", "2026-01-16"),
        ("daily_activity", "2026-01-14", "2026-01-15"),
        ("daily_stress", "2026-01-14", "2026-01-15"),
        ("workout", "2026-01-14", "2026-01-15"),
        ("sleep", "2026-01-14", "2026-01-16"),
    ]
    assert result["daily_sleep"] == [
        {"day": "2026-01-15", "id": "daily_sleep-target"}
    ]
    assert result["daily_activity"] == [
        {"day": "2026-01-14", "id": "daily_activity-target"}
    ]
    assert result["daily_stress"] == [
        {"day": "2026-01-14", "id": "daily_stress-target"}
    ]
    assert result["workouts"] == [
        {"day": "2026-01-14", "id": "workout-target"}
    ]
    assert result["_fetch_status"]["daily_activity"]["ok"] is True
    assert result["_fetch_status"]["daily_stress"]["ok"] is True


@pytest.mark.parametrize(
    ("status_code", "expected_type"),
    [
        (401, oura.OuraAuthenticationError),
        (403, oura.OuraAccessError),
        (429, oura.OuraRateLimitError),
        (500, oura.OuraServerError),
        (503, oura.OuraServerError),
    ],
)
def test_fetch_oura_data_raises_typed_http_failures(
    monkeypatch,
    status_code,
    expected_type,
):
    monkeypatch.setattr(
        oura.requests,
        "get",
        lambda *args, **kwargs: _response(
            status_code,
            text="provider error",
            headers={"Retry-After": "7"},
        ),
    )

    # Exercise one attempt directly so transient-error tests do not sleep.
    with pytest.raises(expected_type) as caught:
        oura.fetch_oura_data.__wrapped__(
            "token",
            "daily_sleep",
            "2026-01-15",
            "2026-01-15",
        )

    assert caught.value.endpoint == "daily_sleep"
    assert caught.value.status_code == status_code
    if status_code == 429:
        assert caught.value.retry_after == "7"


def test_fetch_oura_data_wraps_network_failures(monkeypatch):
    def fail(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(oura.requests, "get", fail)

    with pytest.raises(oura.OuraNetworkError) as caught:
        oura.fetch_oura_data.__wrapped__(
            "token",
            "daily_sleep",
            "2026-01-15",
            "2026-01-15",
        )

    assert caught.value.retryable is True
    assert caught.value.status_code is None


def test_required_sleep_fetch_failure_propagates(monkeypatch):
    def fail_required(token, endpoint, start_date, end_date):
        raise oura.OuraAuthenticationError(
            "expired",
            endpoint=endpoint,
            status_code=401,
        )

    monkeypatch.setattr(oura, "fetch_oura_data", fail_required)

    with pytest.raises(oura.OuraAuthenticationError):
        oura.get_oura_sleep_data("token", "2026-01-15")


def test_optional_activity_failure_is_exposed_not_converted_to_zero(monkeypatch):
    def fake_fetch(token, endpoint, start_date, end_date):
        if endpoint == "daily_activity":
            raise oura.OuraNetworkError("offline", endpoint=endpoint)
        if endpoint == "workout":
            raise oura.OuraRateLimitError(
                "slow down",
                endpoint=endpoint,
                status_code=429,
            )
        return {"data": []}

    monkeypatch.setattr(oura, "fetch_oura_data", fake_fetch)
    monkeypatch.setattr(oura, "get_oura_heartrate", lambda token, date: [])

    result = oura.get_oura_activity_data("token", "2026-01-14")
    metrics = extract_activity_metrics(result)

    assert result["_fetch_status"]["daily_activity"] == {
        "ok": False,
        "error_type": "network",
        "status_code": None,
        "retryable": True,
    }
    assert result["_fetch_status"]["workout"]["error_type"] == "rate_limit"
    assert "workout_count" not in metrics
    assert "workout_minutes" not in metrics


def test_successful_zero_stress_and_rest_day_are_explicit_zeros():
    data = {
        "daily_stress": [{
            "stress_high": 0,
            "recovery_high": 0,
            "day_summary": "normal",
        }],
        "workouts": [],
        "_fetch_status": {
            "daily_stress": {"ok": True, "count": 1},
            "workout": {"ok": True, "count": 0},
        },
    }

    metrics = extract_activity_metrics(data)

    assert metrics["stress_high"] == 0
    assert metrics["recovery_high"] == 0
    assert metrics["workout_count"] == 0
    assert metrics["workout_minutes"] == 0
    assert metrics["workout_calories"] == 0


def test_historical_metrics_are_chronological_and_normalize_legacy_schema(
    temp_data_dir,
):
    metrics_dir = temp_data_dir / "metrics"
    (metrics_dir / "2026-01-15.json").write_text(json.dumps({
        "date": "wrong-date-is-not-authoritative",
        "summary": {"sleep_score": 82},
    }))
    (metrics_dir / "2026-01-13.json").write_text(json.dumps({
        "date": "2026-01-13",
        "metrics": {"sleep_score": 77},
    }))
    (metrics_dir / "2026-01-14.json").write_text(json.dumps({
        "date": "2026-01-14",
        "summary": {"sleep_score": 80},
    }))

    history = load_historical_metrics()

    assert [item["date"] for item in history] == [
        "2026-01-13",
        "2026-01-14",
        "2026-01-15",
    ]
    assert history[0]["summary"]["sleep_score"] == 77


def test_sleep_snapshot_merge_clears_stale_sleep_but_preserves_activity(
    temp_data_dir,
):
    metrics_file = temp_data_dir / "metrics" / "2026-01-15.json"
    metrics_file.write_text(json.dumps({
        "date": "2026-01-15",
        "summary": {
            "sleep_recorded": True,
            "sleep_score": 88,
            "hrv": 52,
            "readiness": 84,
            "steps": 9000,
        },
        "detailed_sleep": {"total_sleep_minutes": 460},
        "detailed_workouts": [],
    }))

    save_daily_metrics(
        "2026-01-15",
        {
            "sleep_recorded": False,
            "sleep_note": "No main sleep recorded",
        },
        detailed_sleep=None,
        detailed_workouts=None,
        merge=True,
    )

    saved = json.loads(metrics_file.read_text())
    assert saved["summary"]["sleep_recorded"] is False
    assert saved["summary"]["steps"] == 9000
    assert "sleep_score" not in saved["summary"]
    assert "hrv" not in saved["summary"]
    assert "readiness" not in saved["summary"]
    assert saved["detailed_sleep"] == {}


def test_dated_baseline_observations_replace_without_reordering_or_cross_delete(
    mock_now_nyc,
):
    baselines = {
        "dates": ["2026-01-01", "2026-01-02", "2026-01-03"],
        "metrics": {
            "hrv": {
                "mean": 20,
                "std": 10,
                "values": [10, 30],
                "observations": {
                    "2026-01-01": 10,
                    "2026-01-03": 30,
                },
            },
        },
    }

    updated = update_baselines(
        baselines,
        {"hrv": 20},
        "2026-01-02",
    )

    assert updated["dates"] == [
        "2026-01-01",
        "2026-01-02",
        "2026-01-03",
    ]
    assert updated["metrics"]["hrv"]["observations"] == {
        "2026-01-01": 10,
        "2026-01-02": 20,
        "2026-01-03": 30,
    }
    assert updated["metrics"]["hrv"]["values"] == [10, 20, 30]


def test_sparse_legacy_baseline_keeps_undated_values_instead_of_deleting_one(
    mock_now_nyc,
):
    baselines = {
        "dates": ["2026-01-01", "2026-01-02", "2026-01-03"],
        "metrics": {
            "hrv": {"mean": 20, "std": 10, "values": [10, 30]},
        },
    }

    updated = update_baselines(
        baselines,
        {"hrv": 20},
        "2026-01-02",
    )

    assert updated["dates"] == [
        "2026-01-01",
        "2026-01-02",
        "2026-01-03",
    ]
    assert updated["metrics"]["hrv"]["undated_values"] == [10, 30]
    assert updated["metrics"]["hrv"]["observations"] == {"2026-01-02": 20}
    assert sorted(updated["metrics"]["hrv"]["values"]) == [10, 20, 30]


def test_none_baseline_update_does_not_erase_last_known_observation(mock_now_nyc):
    baselines = {
        "dates": ["2026-01-15"],
        "metrics": {
            "hrv": {
                "mean": 50,
                "std": 0,
                "values": [50],
                "observations": {"2026-01-15": 50},
            },
        },
    }

    updated = update_baselines(
        baselines,
        {"hrv": None},
        "2026-01-15",
    )

    assert updated["metrics"]["hrv"]["observations"] == {
        "2026-01-15": 50,
    }
    assert updated["metrics"]["hrv"]["values"] == [50]


def test_baseline_does_not_reintroduce_a_date_outside_the_window(mock_now_nyc):
    baselines = {
        "dates": ["2026-01-02", "2026-01-03"],
        "metrics": {
            "hrv": {
                "mean": 25,
                "std": 7.1,
                "values": [20, 30],
                "observations": {
                    "2026-01-02": 20,
                    "2026-01-03": 30,
                },
            },
        },
    }

    updated = update_baselines(
        baselines,
        {"hrv": 10},
        "2026-01-01",
        window=2,
    )

    assert updated["dates"] == ["2026-01-02", "2026-01-03"]
    assert "2026-01-01" not in updated["metrics"]["hrv"]["observations"]


def test_baseline_clears_aggregate_when_metric_ages_out(mock_now_nyc):
    baselines = {
        "dates": ["2026-01-01", "2026-01-02"],
        "metrics": {
            "hrv": {
                "mean": 50,
                "std": 0,
                "values": [50],
                "observations": {"2026-01-01": 50},
            },
            "readiness": {
                "mean": 80,
                "std": 0,
                "values": [80],
                "observations": {"2026-01-02": 80},
            },
        },
    }

    updated = update_baselines(
        baselines,
        {"readiness": 82},
        "2026-01-03",
        window=2,
    )

    assert updated["metrics"]["hrv"]["values"] == []
    assert updated["metrics"]["hrv"]["mean"] is None
    assert updated["metrics"]["hrv"]["std"] is None


def test_atomic_baseline_save_retains_and_recovers_last_good_generation(
    temp_data_dir,
):
    baseline_file = temp_data_dir / "baselines.json"
    first = {
        "dates": ["2026-01-14"],
        "metrics": {"hrv": {"mean": 50, "std": 0, "values": [50]}},
    }
    second = {
        "dates": ["2026-01-15"],
        "metrics": {"hrv": {"mean": 52, "std": 0, "values": [52]}},
    }

    save_baselines(first)
    save_baselines(second)
    backup = baseline_file.with_suffix(".json.backup")

    assert json.loads(baseline_file.read_text()) == second
    assert json.loads(backup.read_text()) == first

    baseline_file.write_text("{truncated")
    recovered = load_baselines()
    assert recovered["dates"] == ["2026-01-14"]
    assert recovered["metrics"]["hrv"]["mean"] == 50


def test_baseline_reset_requires_explicit_confirmation(
    temp_data_dir,
    monkeypatch,
):
    import modal_agent

    monkeypatch.setattr(modal_agent.volume, "commit", lambda: None)

    refused = modal_agent.reset_baselines.local()

    assert refused["status"] == "refused"
    assert not (temp_data_dir / "baselines.json").exists()
