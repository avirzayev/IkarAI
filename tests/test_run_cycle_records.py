"""Launcher: the run is recorded before the strategist starts."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_run_cycle import REPO, _project, _run  # noqa: E402


def test_run_record_is_written_and_completed(tmp_path):
    project = _project(tmp_path)
    _run(project, "rethink: daily review due", "live")
    rows = [json.loads(r) for r in (project / "session" / "rethink_runs.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["ok"] is True and rows[0]["reasons"] == ["daily review due"]


def test_unwritable_run_record_skips_strategist_but_runs_normal_cycle(tmp_path):
    project = _project(tmp_path)
    (project / "session" / "rethink_runs.jsonl").mkdir()  # a directory: begin-run cannot write it
    proc, calls = _run(project, "rethink: daily review due\nheavy: x", "live")
    assert [c.split()[0] for c in calls] == ["NORMAL"]
    assert "[rethink] could not record the run; skipping strategist" in (
        project / "session" / "hourly.log"
    ).read_text()
    assert proc.returncode == 0


def test_strategist_runs_with_kill_after():
    assert "timeout -k 60 1800" in (REPO / "scripts" / "run_hourly_cycle.sh").read_text()
