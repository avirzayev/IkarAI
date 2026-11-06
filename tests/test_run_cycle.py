import json
import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

STUB_CLAUDE = """#!/usr/bin/env bash
kind=NORMAL
case "$*" in *STRATEGIST-PROMPT*) kind=STRATEGIST ;; esac
model=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  if [ "${args[$i]}" = "--model" ]; then model="${args[$((i + 1))]}"; fi
done
reasons="$(printf '%s' "$2" | tr '\\n' '|')"
echo "$kind model=$model ro=${IKARAI_READ_ONLY:-0} sro=${IKARAI_STRATEGY_READ_ONLY:-0} reasons=$reasons" >> "$STUB_LOG"
if [ "${STUB_FAIL:-}" = "$kind" ]; then
  echo '{"is_error": true, "result": "stub failure"}'
  exit 1
fi
echo '{"is_error": false, "result": "ok"}'
"""

STUB_PRETICK = """import os, pathlib, sys
pathlib.Path("session/wake_reasons.txt").write_text(os.environ["STUB_REASONS"] + "\\n")
sys.exit(10)
"""

STUB_KB = """import pathlib, sys
if sys.argv[1] == "wake":
    pathlib.Path("session/next_wake.txt").write_text("2030-01-01T00:00:00Z\\n")
"""


def _project(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "session").mkdir()
    shutil.copy(REPO / "scripts" / "run_hourly_cycle.sh", tmp_path / "scripts")
    shutil.copy(REPO / "scripts" / "rethink.py", tmp_path / "scripts")
    shutil.copy(REPO / "scripts" / "metrics.py", tmp_path / "scripts")
    (tmp_path / "scripts" / "pretick.py").write_text(STUB_PRETICK)
    (tmp_path / "scripts" / "kb.py").write_text(STUB_KB)
    (tmp_path / "scripts" / "log_usage.py").write_text("")
    (tmp_path / "RUNBOOK.md").write_text("RUNBOOK-PROMPT")
    (tmp_path / "RETHINK.md").write_text("STRATEGIST-PROMPT")
    (tmp_path / ".env").write_text("TIMEZONE=UTC\n")
    stub = tmp_path / "claude"
    stub.write_text(STUB_CLAUDE)
    stub.chmod(0o755)
    return tmp_path


def _run(project, reasons, mode, **env):
    log = project / "stub.log"
    full_env = {
        **os.environ,
        "CLAUDE_BIN": str(project / "claude"),
        "STUB_LOG": str(log),
        "STUB_REASONS": reasons,
        "IKARAI_RETHINK_MODE": mode,
        **env,
    }
    proc = subprocess.run(
        ["bash", str(project / "scripts" / "run_hourly_cycle.sh")],
        cwd=project, env=full_env, capture_output=True, text=True,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return proc, calls


def test_no_rethink_reason_runs_only_the_normal_cycle(tmp_path):
    project = _project(tmp_path)
    _, calls = _run(project, "heavy: city A idle", "live")
    assert len(calls) == 1
    assert calls[0].startswith("NORMAL")


def test_live_mode_runs_strategist_first_with_read_only_game(tmp_path):
    project = _project(tmp_path)
    _, calls = _run(project, "rethink: daily review due\nheavy: city A idle", "live")
    assert [c.split()[0] for c in calls] == ["STRATEGIST", "NORMAL"]
    assert "model=claude-opus-5-5" in calls[0]
    assert " ro=1 " in calls[0]
    assert " sro=0 " in calls[0]
    assert "rethink: daily review due" in calls[0]


def test_normal_cycle_has_no_rethink_lines_and_is_not_read_only(tmp_path):
    project = _project(tmp_path)
    _, calls = _run(project, "rethink: daily review due\nheavy: city A idle", "live")
    assert " ro=0 " in calls[1]
    assert "rethink:" not in calls[1]
    assert "heavy: city A idle" in calls[1]


def test_propose_mode_also_locks_strategy_writes(tmp_path):
    project = _project(tmp_path)
    _, calls = _run(project, "rethink: daily review due", "propose")
    assert " sro=1 " in calls[0]
    assert " sro=0 " in calls[1]


def test_only_rethink_reasons_still_gives_the_normal_cycle_a_reason(tmp_path):
    project = _project(tmp_path)
    _, calls = _run(project, "rethink: daily review due", "live")
    assert "after strategist run" in calls[1]


def test_log_and_off_modes_do_not_run_the_strategist(tmp_path):
    for mode in ("log", "off"):
        project = tmp_path / mode
        project.mkdir()
        _project(project)
        _, calls = _run(project, "rethink: daily review due\nheavy: x", mode)
        assert [c.split()[0] for c in calls] == ["NORMAL"]


def test_strategist_failure_does_not_block_the_normal_cycle(tmp_path):
    project = _project(tmp_path)
    _, calls = _run(project, "rethink: daily review due", "live", STUB_FAIL="STRATEGIST")
    assert [c.split()[0] for c in calls] == ["STRATEGIST", "NORMAL"]
    runs = (project / "session" / "rethink_runs.jsonl").read_text().splitlines()
    assert json.loads(runs[0])["ok"] is False
    assert not (project / "session" / "last_rethink.txt").exists()


def test_results_are_recorded(tmp_path):
    project = _project(tmp_path)
    _run(project, "rethink: daily review due", "live")
    assert (project / "session" / "last_rethink.txt").exists()
    assert (project / "session" / "last_model_success.txt").exists()
    assert (project / "session" / "model_failures.txt").read_text().strip() == "0"


def test_normal_failure_counts_up(tmp_path):
    project = _project(tmp_path)
    _run(project, "heavy: x", "log", STUB_FAIL="NORMAL")
    assert (project / "session" / "model_failures.txt").read_text().strip() == "1"


def test_rethink_settings_override_model(tmp_path):
    project = _project(tmp_path)
    _, calls = _run(project, "rethink: daily review due", "live", IKARAI_RETHINK_MODEL="claude-fable-5-1")
    assert "model=claude-fable-5-1" in calls[0]


def test_strategist_reason_alone_does_not_mark_chore_review_done(tmp_path):
    project = _project(tmp_path)
    _run(project, "rethink: daily review due", "live")
    assert not (project / "session" / "last_review.txt").exists()


def test_repo_rethink_instructions_keep_the_hard_rules():
    text = (REPO / "RETHINK.md").read_text()
    for phrase in ("real-money", "captcha", "kb.py knowledge add", "rethink_proposal.md", "scripts/rethink.py pivot"):
        assert phrase in text


def test_runbook_covers_strategy_format_rethink_and_attack_policy():
    text = (REPO / "RUNBOOK.md").read_text()
    for phrase in ("MAIN LIMIT", "kb.py rethink", "DECISIONS", "retaliation risk", "attackBarbarianVillage"):
        assert phrase in text


def test_env_example_documents_rethink_settings():
    text = (REPO / ".env.example").read_text()
    for name in ("IKARAI_RETHINK_MODE", "IKARAI_RETHINK_MODEL", "IKARAI_RETHINK_EFFORT", "IKARAI_RETHINK_MAX_BUDGET_USD"):
        assert name in text
