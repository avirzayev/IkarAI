import json
from datetime import datetime, timedelta, timezone

import pytest

import rethink

NOW = datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc)
PIVOT = {
    "triggers": ["daily review due"], "main_limit": "fleet", "changes": ["buy ships"],
    "expected": "stock moves", "check_by": "2026-11-08", "sources": ["wiki"],
    "grade_of_previous": "none",
}


def _result(tmp_path, is_error=False, text="done"):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"is_error": is_error, "result": text}))
    return path


def _runs(tmp_path, hours_ago):
    with (tmp_path / "rethink_runs.jsonl").open("a") as handle:
        for ago in hours_ago:
            moment = (NOW - timedelta(hours=ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
            handle.write(json.dumps({"time": moment, "ok": True}) + "\n")


def test_gate_allows_when_no_runs(tmp_path):
    assert rethink.gate(tmp_path, NOW)[0] is True


def test_gate_blocks_within_six_hours(tmp_path):
    _runs(tmp_path, [5])
    allowed, why = rethink.gate(tmp_path, NOW)
    assert allowed is False
    assert "6h" in why


def test_gate_allows_at_six_hours(tmp_path):
    _runs(tmp_path, [6])
    assert rethink.gate(tmp_path, NOW)[0] is True


def test_gate_blocks_fourth_run_in_24_hours(tmp_path):
    _runs(tmp_path, [20, 14, 8])
    allowed, why = rethink.gate(tmp_path, NOW)
    assert allowed is False
    assert "3" in why


def test_gate_ignores_runs_older_than_24_hours(tmp_path):
    _runs(tmp_path, [40, 30, 25])
    assert rethink.gate(tmp_path, NOW)[0] is True


def test_failed_rethink_counts_toward_gate_but_not_daily(tmp_path):
    assert rethink.record_rethink(tmp_path, _result(tmp_path, is_error=True), 0, NOW) is False
    assert rethink.gate(tmp_path, NOW + timedelta(hours=1))[0] is False
    assert not (tmp_path / "last_rethink.txt").exists()


def test_successful_rethink_updates_last_rethink(tmp_path):
    assert rethink.record_rethink(tmp_path, _result(tmp_path), 0, NOW) is True
    assert (tmp_path / "last_rethink.txt").read_text().strip() == "2026-11-06T10:00:00Z"


def test_record_cycle_success_writes_timestamp_and_resets_failures(tmp_path):
    (tmp_path / "model_failures.txt").write_text("2")
    assert rethink.record_cycle(tmp_path, _result(tmp_path), 0, NOW) is True
    assert rethink.consecutive_failures(tmp_path) == 0
    assert (tmp_path / "last_model_success.txt").read_text().strip() == "2026-11-06T10:00:00Z"


def test_record_cycle_failure_counts_up(tmp_path):
    result = _result(tmp_path, is_error=True)
    rethink.record_cycle(tmp_path, result, 0, NOW)
    rethink.record_cycle(tmp_path, result, 0, NOW)
    assert rethink.consecutive_failures(tmp_path) == 2
    assert not (tmp_path / "last_model_success.txt").exists()


def test_nonzero_exit_or_bad_json_is_failure(tmp_path):
    assert rethink.record_cycle(tmp_path, _result(tmp_path), 1, NOW) is False
    bad = tmp_path / "bad.json"
    bad.write_text("oops")
    assert rethink.record_cycle(tmp_path, bad, 0, NOW) is False
    assert rethink.record_cycle(tmp_path, tmp_path / "missing.json", 0, NOW) is False
    assert rethink.consecutive_failures(tmp_path) == 3


def test_consecutive_failures_tolerates_garbage(tmp_path):
    (tmp_path / "model_failures.txt").write_text("nope")
    assert rethink.consecutive_failures(tmp_path) == 0


def test_last_error_reads_last_run(tmp_path):
    (tmp_path / "last_run.json").write_text(json.dumps({"result": "OAuth expired"}))
    assert rethink.last_error(tmp_path) == "OAuth expired"
    (tmp_path / "last_run.json").unlink()
    assert rethink.last_error(tmp_path) == "unknown"


def test_consume_request_removes_file(tmp_path):
    (tmp_path / "rethink_request.txt").write_text("x")
    rethink.consume_request(tmp_path)
    rethink.consume_request(tmp_path)
    assert rethink.read_request(tmp_path) is None


def test_add_pivot_appends_with_time(tmp_path, monkeypatch):
    monkeypatch.delenv("IKARAI_STRATEGY_READ_ONLY", raising=False)
    rethink.add_pivot(tmp_path, PIVOT, NOW)
    pivots = rethink.recent_pivots(tmp_path, 5)
    assert pivots[0]["time"] == "2026-11-06T10:00:00Z"
    assert pivots[0]["main_limit"] == "fleet"


def test_add_pivot_rejects_missing_field(tmp_path, monkeypatch):
    monkeypatch.delenv("IKARAI_STRATEGY_READ_ONLY", raising=False)
    with pytest.raises(ValueError, match="expected"):
        rethink.add_pivot(tmp_path, {k: v for k, v in PIVOT.items() if k != "expected"}, NOW)


def test_add_pivot_refused_in_proposal_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("IKARAI_STRATEGY_READ_ONLY", "1")
    with pytest.raises(ValueError, match="proposal mode"):
        rethink.add_pivot(tmp_path, PIVOT, NOW)


def test_recent_pivots_limits_and_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("IKARAI_STRATEGY_READ_ONLY", raising=False)
    assert rethink.recent_pivots(tmp_path, 5) == []
    for _ in range(3):
        rethink.add_pivot(tmp_path, PIVOT, NOW)
    assert len(rethink.recent_pivots(tmp_path, 2)) == 2


def test_cli_record_cycle_and_pivots(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("IKARAI_STRATEGY_READ_ONLY", raising=False)
    session = tmp_path / "session"
    session.mkdir()
    result = _result(session)
    assert rethink.main(["record-cycle", str(result), "--exit-code", "0"], project_root=tmp_path, now=NOW) == 0
    assert (session / "last_model_success.txt").exists()
    assert rethink.main(["pivot", json.dumps(PIVOT)], project_root=tmp_path, now=NOW) == 0
    assert rethink.main(["pivots", "--last", "1"], project_root=tmp_path, now=NOW) == 0
    assert "fleet" in capsys.readouterr().out


def test_cli_pivot_invalid_json_or_fields_returns_error(tmp_path, capsys):
    (tmp_path / "session").mkdir()
    assert rethink.main(["pivot", "not json"], project_root=tmp_path, now=NOW) == 1
    assert rethink.main(["pivot", "{}"], project_root=tmp_path, now=NOW) == 1
