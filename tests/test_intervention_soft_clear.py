"""Tests for recoverable intervention clearing and undo."""

from oura_agent.storage import interventions


def test_soft_clear_snapshots_without_unlinking(
    temp_data_dir,
    mock_now_nyc,
    monkeypatch,
):
    interventions.save_intervention_raw("magnesium")
    interventions.save_intervention_raw("sauna")
    original = temp_data_dir / "interventions" / "2026-01-15.jsonl"

    def fail_unlink(*args, **kwargs):
        raise AssertionError("soft clear must not unlink intervention data")

    monkeypatch.setattr(type(original), "unlink", fail_unlink)

    result = interventions.soft_clear_interventions("2026-01-15")

    assert result["status"] == "cleared"
    assert result["cleared_count"] == 2
    assert original.exists()
    assert interventions.load_interventions("2026-01-15")["entries"] == []
    snapshots = interventions.list_clear_snapshots("2026-01-15")
    assert snapshots[0]["snapshot_id"] == result["snapshot_id"]
    assert len(snapshots[0]["entries"]) == 2


def test_undo_merges_entries_logged_after_clear(temp_data_dir, mock_now_nyc):
    interventions.save_intervention_raw("magnesium")
    cleared = interventions.soft_clear_interventions("2026-01-15")
    interventions.save_intervention_raw("coffee")

    restored = interventions.undo_clear_interventions("2026-01-15")
    entries = interventions.load_interventions("2026-01-15")["entries"]

    assert restored["status"] == "restored"
    assert restored["snapshot_id"] == cleared["snapshot_id"]
    assert [entry["raw"] for entry in entries] == ["magnesium", "coffee"]
    assert interventions.list_clear_snapshots("2026-01-15")
    assert interventions.undo_clear_interventions("2026-01-15")["status"] == "no_snapshot"


def test_explicit_restore_is_idempotent(temp_data_dir, mock_now_nyc):
    interventions.save_intervention_raw("magnesium")
    cleared = interventions.soft_clear_interventions("2026-01-15")

    interventions.restore_interventions_snapshot(cleared["snapshot_id"])
    interventions.restore_interventions_snapshot(cleared["snapshot_id"])
    entries = interventions.load_interventions("2026-01-15")["entries"]

    assert [entry["raw"] for entry in entries] == ["magnesium"]


def test_soft_clear_empty_day_is_noop(temp_data_dir, mock_now_nyc):
    result = interventions.soft_clear_interventions("2026-01-15")

    assert result == {
        "status": "empty",
        "date": "2026-01-15",
        "snapshot_id": None,
        "cleared_count": 0,
    }
    assert interventions.list_clear_snapshots("2026-01-15") == []


def test_webhook_mutations_are_idempotent_by_update_id(
    temp_data_dir,
    mock_now_nyc,
):
    first = interventions.save_intervention_raw(
        "magnesium",
        source_update_id=100,
    )
    duplicate = interventions.save_intervention_raw(
        "magnesium duplicate retry",
        source_update_id=100,
    )

    assert duplicate == first
    assert len(interventions.load_interventions("2026-01-15")["entries"]) == 1

    cleared = interventions.soft_clear_interventions(
        "2026-01-15",
        source_update_id=101,
    )
    retried_clear = interventions.soft_clear_interventions(
        "2026-01-15",
        source_update_id=101,
    )
    assert retried_clear == cleared
    assert len(interventions.list_clear_snapshots("2026-01-15")) == 1

    restored = interventions.undo_clear_interventions(
        "2026-01-15",
        source_update_id=102,
    )
    retried_restore = interventions.undo_clear_interventions(
        "2026-01-15",
        source_update_id=102,
    )
    assert retried_restore == restored
    assert [entry["raw"] for entry in interventions.load_interventions(
        "2026-01-15"
    )["entries"]] == ["magnesium"]
