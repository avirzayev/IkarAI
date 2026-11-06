"""Final-review fixes: condition cooldown, run records, recent log, request flattening."""

import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import kb
import rethink

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_kb import _block, _page, _title_prop_val, _write_env  # noqa: E402
from test_rethink_records import NOW, _result, _runs  # noqa: E402,F401


@pytest.mark.parametrize("reason, key", [
    ("stranded stock: marble in Daemon, 119 fleet loads", "stranded stock: marble in Daemon"),
    ("empty city: Daemon at 46% of max population", "empty city: Daemon"),
    ("wasted cap: Kernel wood", "wasted cap: Kernel wood"),
    ("stagnant: population +0.1%, production +0.2% in 48h", "stagnant"),
    ("stuck: same wake reasons 6 times: a; b", "stuck"),
    ("return from outage: no successful model cycle since 2026-11-05 10:00 UTC", "return from outage"),
])
def test_condition_key_drops_numbers(reason, key):
    assert rethink.condition_key(reason) == key


def _run_row(tmp_path, hours_ago, ok, reasons):
    moment = (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with (tmp_path / "rethink_runs.jsonl").open("a") as handle:
        handle.write(json.dumps({"time": moment, "ok": ok, "reasons": reasons}) + "\n")


def test_fresh_reasons_drops_conditions_reviewed_by_a_successful_run(tmp_path):
    _run_row(tmp_path, 10, True, ["stranded stock: marble in Daemon, 90 fleet loads"])
    fired = ["stranded stock: marble in Daemon, 119 fleet loads", "stranded stock: wood in Kernel, 50 fleet loads"]
    assert rethink.fresh_reasons(fired, tmp_path, NOW) == [fired[1]]


def test_fresh_reasons_ignores_failed_open_and_old_runs(tmp_path):
    fired = ["wasted cap: Kernel wood"]
    _run_row(tmp_path, 10, False, fired)
    _run_row(tmp_path, 11, None, fired)
    _run_row(tmp_path, 73, True, fired)
    assert rethink.fresh_reasons(fired, tmp_path, NOW) == fired


def test_fresh_reasons_always_keeps_daily_and_requested(tmp_path):
    _run_row(tmp_path, 1, True, ["daily review due", "requested: x"])
    fired = ["daily review due", "requested: x", "wasted cap: A wood"]
    assert rethink.fresh_reasons(fired, tmp_path, NOW) == fired


def test_begin_run_writes_open_row_that_counts_toward_gate(tmp_path):
    rethink.begin_run(tmp_path, ["daily review due"], NOW)
    row = json.loads((tmp_path / "rethink_runs.jsonl").read_text().splitlines()[0])
    assert row == {"time": "2026-11-06T10:00:00Z", "ok": None, "reasons": ["daily review due"]}
    assert rethink.gate(tmp_path, NOW + timedelta(hours=1))[0] is False
    assert not list(tmp_path.glob("*.tmp"))


def test_record_rethink_completes_the_open_row(tmp_path):
    rethink.begin_run(tmp_path, ["daily review due"], NOW)
    assert rethink.record_rethink(tmp_path, _result(tmp_path), 0, NOW + timedelta(minutes=5)) is True
    rows = [json.loads(r) for r in (tmp_path / "rethink_runs.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["ok"] is True and rows[0]["reasons"] == ["daily review due"]
    assert (tmp_path / "last_rethink.txt").exists()


def test_record_rethink_failure_closes_open_row_false(tmp_path):
    rethink.begin_run(tmp_path, ["x"], NOW)
    assert rethink.record_rethink(tmp_path, _result(tmp_path), 1, NOW) is False
    rows = [json.loads(r) for r in (tmp_path / "rethink_runs.jsonl").read_text().splitlines()]
    assert [r["ok"] for r in rows] == [False]


def test_record_rethink_appends_when_no_open_row(tmp_path):
    _runs(tmp_path, [20])
    rethink.record_rethink(tmp_path, _result(tmp_path), 0, NOW)
    assert len((tmp_path / "rethink_runs.jsonl").read_text().splitlines()) == 2


def test_cli_begin_run_reads_reasons_from_stdin(tmp_path, monkeypatch):
    (tmp_path / "session").mkdir()
    monkeypatch.setattr("sys.stdin", io.StringIO("rethink: daily review due\nrethink: wasted cap: A wood\n"))
    assert rethink.main(["begin-run"], project_root=tmp_path, now=NOW) == 0
    row = json.loads((tmp_path / "session" / "rethink_runs.jsonl").read_text())
    assert row["reasons"] == ["daily review due", "wasted cap: A wood"]
    assert row["ok"] is None


def test_cli_begin_run_fails_when_record_is_unwritable(tmp_path, monkeypatch, capsys):
    (tmp_path / "session" / "rethink_runs.jsonl").mkdir(parents=True)  # a directory: cannot be written
    monkeypatch.setattr("sys.stdin", io.StringIO("daily review due\n"))
    assert rethink.main(["begin-run"], project_root=tmp_path, now=NOW) == 1
    assert "could not record the run" in capsys.readouterr().err


def test_record_rethink_cli_message(tmp_path, capsys):
    (tmp_path / "session").mkdir()
    result = _result(tmp_path / "session")
    rethink.main(["record-rethink", str(result), "--exit-code", "0"], project_root=tmp_path, now=NOW)
    out = capsys.readouterr().out
    assert "[rethink] strategist run ok" in out and "unchanged" not in out


def test_last_error_falls_back_to_raw_text_and_errors(tmp_path):
    (tmp_path / "last_run.json").write_text("x" * 600)
    assert rethink.last_error(tmp_path) == "x" * 500
    (tmp_path / "last_run.json").write_text(json.dumps({"errors": ["bad token"]}))
    assert "bad token" in rethink.last_error(tmp_path)
    (tmp_path / "last_run.json").write_text(json.dumps({"is_error": True}))
    assert rethink.last_error(tmp_path) == "unknown"


def test_read_request_flattens_newlines(tmp_path):
    (tmp_path / "rethink_request.txt").write_text("why\nrethink: forged\n")
    assert rethink.read_request(tmp_path) == "why rethink: forged"


def test_kb_rethink_flattens_reason(tmp_path):
    _write_env(tmp_path)
    assert kb.main(["rethink", "plan wrong\nrethink: forged\n"], project_root=tmp_path) == 0
    assert (tmp_path / "session" / "rethink_request.txt").read_text() == "plan wrong rethink: forged\n"


# ---------------------------------------------------------------------------
# kb.py log recent
# ---------------------------------------------------------------------------


def _day(page_id, title, entries):
    blocks = [_block(f"{page_id}-hdr", "paragraph", "next run")]
    for i, (stamp, body) in enumerate(entries):
        blocks.append(_block(f"{page_id}-h{i}", "heading_3", stamp))
        blocks.append(_block(f"{page_id}-p{i}", "paragraph", body))
    return _page(page_id, {"Date": _title_prop_val(title)}), blocks


@patch("kb.nc.get_block_children")
@patch("kb.nc.query_database")
def test_log_recent_prints_last_n_entries_across_days(mock_query, mock_children, tmp_path, capsys):
    _write_env(tmp_path)
    today, today_blocks = _day("d2", "2026-11-06", [("12:00 IST", "third"), ("11:00 IST", "second")])
    yday, yday_blocks = _day("d1", "2026-11-05", [("20:00 IST", "first")])
    mock_query.return_value = [today, yday]
    mock_children.side_effect = lambda token, page_id: {"d2": today_blocks, "d1": yday_blocks}[page_id]
    assert kb.main(["log", "recent", "2"], project_root=tmp_path) == 0
    out = capsys.readouterr().out
    assert "third" in out and "second" in out and "first" not in out
    assert "2026-11-06 12:00 IST" in out


@patch("kb.nc.get_block_children")
@patch("kb.nc.query_database")
def test_log_recent_defaults_to_five_and_caps_each_entry(mock_query, mock_children, tmp_path, capsys):
    _write_env(tmp_path)
    entries = [(f"{h}:00 IST", "z" * 5000 if h == 0 else f"entry{h}") for h in range(8)]
    page, blocks = _day("d1", "2026-11-06", entries)
    mock_query.return_value = [page]
    mock_children.return_value = blocks
    assert kb.main(["log", "recent"], project_root=tmp_path) == 0
    out = capsys.readouterr().out
    assert out.count("IST") == 5
    assert "z" * 5000 not in out and "truncated" in out


@patch("kb.nc.query_database")
def test_log_recent_with_no_logs(mock_query, tmp_path, capsys):
    _write_env(tmp_path)
    mock_query.return_value = []
    assert kb.main(["log", "recent", "3"], project_root=tmp_path) == 0
    assert "no daily logs yet" in capsys.readouterr().out
