import json
import subprocess
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pretick


def _write_env(tmp_path, **overrides):
    values = {
        "IKARIAM_USERNAME": "u@example.com",
        "IKARIAM_SERVER": "s1.example.com",
        "NOTION_TOKEN": "ntn_test",
        "NOTION_ROOT_PAGE_ID": "root-page-id",
        "NOTION_HUMAN_REQUIRED_DB_ID": "human-db",
        "NOTION_CHORES_DB_ID": "chores-db",
        "TIMEZONE": "UTC",
    }
    values.update(overrides)
    (tmp_path / ".env").write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
    (tmp_path / "session").mkdir(exist_ok=True)


def _state(**overrides):
    state = {
        "time": 1790336336,
        "gold": 30456,
        "alerts": {"under_attack": False, "unread_messages": 0},
        "queue": [{"city": "Async", "done_in_min": 108}],
        "transporters": {"free": 8, "max": 8},
        "cities": [
            {
                "id": "355955", "name": "Kernel",
                "construction": [{"building": "Shipyard", "position": 1, "done_in_min": 14}],
            },
        ],
    }
    state.update(overrides)
    return state


def _ok_subprocess(state):
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = json.dumps(state)
    proc.stderr = ""
    return proc


def _page(id_, title):
    return {
        "object": "page", "id": id_,
        "properties": {"Name": {"type": "title", "title": [{"plain_text": title}]}},
    }


def _block(id_, text):
    return {"object": "block", "id": id_, "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": text}]}}


NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# step 1 — state unavailable
# ---------------------------------------------------------------------------


def test_state_unavailable_on_nonzero_exit_writes_reason_and_exits_10(tmp_path):
    _write_env(tmp_path)
    proc = MagicMock(returncode=1, stdout="", stderr="boom")
    with patch("pretick.subprocess.run", return_value=proc):
        rc = pretick.run(tmp_path, now=NOW)
    assert rc == 10
    reasons = (tmp_path / "session" / "wake_reasons.txt").read_text()
    assert "heavy: state unavailable" in reasons


def test_state_unavailable_on_bad_json_writes_reason(tmp_path):
    _write_env(tmp_path)
    proc = MagicMock(returncode=0, stdout="not json", stderr="")
    with patch("pretick.subprocess.run", return_value=proc):
        rc = pretick.run(tmp_path, now=NOW)
    assert rc == 10
    assert "heavy: state unavailable" in (tmp_path / "session" / "wake_reasons.txt").read_text()


def test_state_unavailable_on_subprocess_timeout(tmp_path):
    _write_env(tmp_path)
    with patch("pretick.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="game.py", timeout=120)):
        rc = pretick.run(tmp_path, now=NOW)
    assert rc == 10
    assert "heavy: state unavailable" in (tmp_path / "session" / "wake_reasons.txt").read_text()


def test_state_saved_to_session_state_json_on_success(tmp_path):
    _write_env(tmp_path)
    state = _state()
    with patch("pretick.subprocess.run", return_value=_ok_subprocess(state)), \
         patch("pretick.chores.main", return_value=0), \
         patch("pretick.nc.query_database", return_value=[]), \
         patch("pretick.kb.main", return_value=0):
        pretick.run(tmp_path, now=NOW)
    saved = json.loads((tmp_path / "session" / "state.json").read_text())
    assert saved["gold"] == 30456


# ---------------------------------------------------------------------------
# wake conditions -> exit 10
# ---------------------------------------------------------------------------


def _run_with(tmp_path, state, chores_result=None, human_rows=None, human_blocks=None, last_llm_cycle=None, last_review=None):
    _write_env(tmp_path)
    if last_llm_cycle is not None:
        (tmp_path / "session" / "last_llm_cycle.txt").write_text(last_llm_cycle)
    if last_review is not None:
        (tmp_path / "session" / "last_review.txt").write_text(last_review)

    def chores_main_side_effect(argv, project_root=None, now=None):
        (project_root / "session" / "chores_last.json").write_text(
            json.dumps(chores_result or {"executed": [], "dry_runs": [], "failures": [], "disabled": [], "pending_review": []})
        )
        return 0

    human_rows = human_rows or []

    def query_side_effect(token, db_id, **kwargs):
        return human_rows

    def blocks_side_effect(token, page_id):
        return (human_blocks or {}).get(page_id, [_block("q1", "question")])

    with patch("pretick.subprocess.run", return_value=_ok_subprocess(state)), \
         patch("pretick.chores.main", side_effect=chores_main_side_effect), \
         patch("pretick.nc.query_database", side_effect=query_side_effect), \
         patch("pretick.nc.get_block_children", side_effect=blocks_side_effect), \
         patch("pretick.kb.main", return_value=0) as mock_kb_wake:
        rc = pretick.run(tmp_path, now=NOW)
    return rc, mock_kb_wake


def test_human_required_answered_wakes_heavy(tmp_path):
    rows = [_page("h1", "Session expired")]
    blocks = {"h1": [_block("q1", "Question"), _block("q2", "the question"), _block("r1", "reply text")]}
    rc, _ = _run_with(
        tmp_path, _state(),
        last_llm_cycle=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        last_review=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        human_rows=rows, human_blocks=blocks,
    )
    assert rc == 10
    reasons = (tmp_path / "session" / "wake_reasons.txt").read_text()
    assert "heavy: human required row answered: Session expired" in reasons


def test_human_required_unanswered_does_not_wake(tmp_path):
    rows = [_page("h1", "Session expired")]
    blocks = {"h1": [_block("q1", "Question"), _block("q2", "the question")]}
    rc, _ = _run_with(
        tmp_path, _state(),
        last_llm_cycle=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        last_review=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        human_rows=rows, human_blocks=blocks,
    )
    assert rc == 0


def test_under_attack_wakes_heavy(tmp_path):
    state = _state(alerts={"under_attack": True, "unread_messages": 0})
    rc, _ = _run_with(
        tmp_path, state,
        last_llm_cycle=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        last_review=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    assert rc == 10
    assert "heavy: under attack" in (tmp_path / "session" / "wake_reasons.txt").read_text()


def test_unread_messages_wakes_heavy(tmp_path):
    state = _state(alerts={"under_attack": False, "unread_messages": 3})
    rc, _ = _run_with(
        tmp_path, state,
        last_llm_cycle=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        last_review=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    assert rc == 10
    assert "3 unread message" in (tmp_path / "session" / "wake_reasons.txt").read_text()


def test_chore_failure_wakes_heavy_and_pending_review_wakes_light(tmp_path):
    chores_result = {
        "executed": [], "dry_runs": [], "failures": [{"chore": "x", "reason": "boom"}],
        "disabled": ["y"], "pending_review": ["z"],
    }
    rc, _ = _run_with(
        tmp_path, _state(), chores_result=chores_result,
        last_llm_cycle=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        last_review=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    assert rc == 10
    reasons = (tmp_path / "session" / "wake_reasons.txt").read_text()
    assert "heavy: chore failed: x (boom)" in reasons
    assert "heavy: chore auto-disabled: y" in reasons
    assert "light: chore pending review/promotion: z" in reasons


def test_city_without_construction_wakes_heavy(tmp_path):
    state = _state(cities=[{"id": "1", "name": "Empty", "construction": []}])
    rc, _ = _run_with(
        tmp_path, state,
        last_llm_cycle=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        last_review=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    assert rc == 10
    assert "heavy: city Empty has no construction in progress" in (tmp_path / "session" / "wake_reasons.txt").read_text()


def test_stale_llm_cycle_wakes_heavy(tmp_path):
    stale = (NOW - timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rc, _ = _run_with(tmp_path, _state(), last_llm_cycle=stale, last_review=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert rc == 10
    assert "heavy: last LLM cycle over 6h ago" in (tmp_path / "session" / "wake_reasons.txt").read_text()


def test_missing_llm_cycle_file_wakes_heavy(tmp_path):
    rc, _ = _run_with(tmp_path, _state(), last_review=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert rc == 10
    assert "heavy: last LLM cycle over 6h ago" in (tmp_path / "session" / "wake_reasons.txt").read_text()


def test_stale_review_wakes_light(tmp_path):
    stale = (NOW - timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rc, _ = _run_with(tmp_path, _state(), last_llm_cycle=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), last_review=stale)
    assert rc == 10
    assert "light: daily chore review due" in (tmp_path / "session" / "wake_reasons.txt").read_text()


def test_missing_review_file_wakes_light(tmp_path):
    rc, _ = _run_with(tmp_path, _state(), last_llm_cycle=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert rc == 10
    assert "light: daily chore review due" in (tmp_path / "session" / "wake_reasons.txt").read_text()


# ---------------------------------------------------------------------------
# no wake reasons -> exit 0, next_wake math
# ---------------------------------------------------------------------------


def test_no_reasons_exits_0_and_writes_empty_reasons_file(tmp_path):
    rc, mock_kb = _run_with(
        tmp_path, _state(),
        last_llm_cycle=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        last_review=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    assert rc == 0
    assert (tmp_path / "session" / "wake_reasons.txt").read_text() == ""
    mock_kb.assert_called_once()
    argv = mock_kb.call_args.args[0]
    assert argv[0] == "wake"


def test_next_wake_uses_earliest_completion_plus_2_minutes():
    state = _state(
        queue=[{"city": "Async", "done_in_min": 50}],
        cities=[{"id": "1", "construction": [{"building": "X", "position": 0, "done_in_min": 20}]}],
    )
    assert pretick._next_wake_minutes(state) == 22


def test_next_wake_floors_at_10_minutes():
    state = _state(queue=[{"city": "Async", "done_in_min": 1}], cities=[])
    assert pretick._next_wake_minutes(state) == 10


def test_next_wake_caps_at_180_minutes():
    state = _state(queue=[{"city": "Async", "done_in_min": 1000}], cities=[])
    assert pretick._next_wake_minutes(state) == 180


def test_next_wake_defaults_to_cap_when_nothing_in_progress():
    state = _state(queue=[], cities=[{"id": "1", "construction": []}])
    assert pretick._next_wake_minutes(state) == 180


def test_no_reasons_calls_kb_wake_with_computed_minutes(tmp_path):
    state = _state(
        queue=[], cities=[{"id": "1", "name": "Kernel", "construction": [{"building": "X", "position": 0, "done_in_min": 30}]}],
    )
    rc, mock_kb = _run_with(
        tmp_path, state,
        last_llm_cycle=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        last_review=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    assert rc == 0
    argv = mock_kb.call_args.args[0]
    assert argv == ["wake", "+32"]


# ---------------------------------------------------------------------------
# fail open
# ---------------------------------------------------------------------------


def test_unexpected_exception_fails_open_exit_10(tmp_path):
    _write_env(tmp_path)
    with patch("pretick.subprocess.run", return_value=_ok_subprocess(_state())), \
         patch("pretick.chores.main", side_effect=RuntimeError("kaboom")):
        rc = pretick.run(tmp_path, now=NOW)
    assert rc == 10
    reasons = (tmp_path / "session" / "wake_reasons.txt").read_text()
    assert "heavy: pretick error: kaboom" in reasons


def test_missing_config_fails_open_exit_10(tmp_path):
    (tmp_path / "session").mkdir(exist_ok=True)
    # no .env written at all
    rc = pretick.run(tmp_path, now=NOW)
    assert rc == 10
    assert "heavy: pretick error" in (tmp_path / "session" / "wake_reasons.txt").read_text()


# ---------------------------------------------------------------------------
# rethink wiring
# ---------------------------------------------------------------------------


def _quiet_session(tmp_path):
    stamp = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    (tmp_path / "session" / "last_llm_cycle.txt").write_text(stamp)
    (tmp_path / "session" / "last_review.txt").write_text(stamp)


def _run_with_mocks(tmp_path, state=None, kb_main=None):
    with patch("pretick.subprocess.run", return_value=_ok_subprocess(state or _state())), \
         patch("pretick.chores.main", return_value=0), \
         patch("pretick.nc.query_database", return_value=[]), \
         patch("pretick.kb.main", kb_main or MagicMock(return_value=0)):
        return pretick.run(tmp_path, now=NOW)


def test_every_tick_appends_a_metrics_line(tmp_path, monkeypatch):
    monkeypatch.delenv("IKARAI_RETHINK_MODE", raising=False)
    _write_env(tmp_path)
    _quiet_session(tmp_path)
    assert _run_with_mocks(tmp_path) == 0
    lines = (tmp_path / "session" / "metrics.jsonl").read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["time"] == "2026-09-25T12:00:00Z"


def test_log_mode_only_shadows_and_stays_out_of_wake_reasons(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("IKARAI_RETHINK_MODE", "log")
    _write_env(tmp_path)
    _quiet_session(tmp_path)
    assert _run_with_mocks(tmp_path) == 0
    assert "rethink:" not in (tmp_path / "session" / "wake_reasons.txt").read_text()
    assert "[rethink-shadow] daily review due" in capsys.readouterr().out


def test_log_mode_consumes_a_request_so_it_does_not_repeat(tmp_path, monkeypatch):
    monkeypatch.setenv("IKARAI_RETHINK_MODE", "log")
    _write_env(tmp_path)
    _quiet_session(tmp_path)
    (tmp_path / "session" / "rethink_request.txt").write_text("why")
    _run_with_mocks(tmp_path)
    assert not (tmp_path / "session" / "rethink_request.txt").exists()


def test_off_mode_skips_triggers_but_keeps_the_scoreboard(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("IKARAI_RETHINK_MODE", "off")
    _write_env(tmp_path)
    _quiet_session(tmp_path)
    assert _run_with_mocks(tmp_path) == 0
    assert "rethink" not in capsys.readouterr().out
    assert (tmp_path / "session" / "metrics.jsonl").exists()


def test_live_mode_wakes_with_rethink_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("IKARAI_RETHINK_MODE", "live")
    _write_env(tmp_path)
    _quiet_session(tmp_path)
    assert _run_with_mocks(tmp_path) == 10
    assert "rethink: daily review due" in (tmp_path / "session" / "wake_reasons.txt").read_text()


def test_live_mode_consumes_request_when_it_fires(tmp_path, monkeypatch):
    monkeypatch.setenv("IKARAI_RETHINK_MODE", "live")
    _write_env(tmp_path)
    _quiet_session(tmp_path)
    (tmp_path / "session" / "rethink_request.txt").write_text("why")
    _run_with_mocks(tmp_path)
    assert "rethink: requested: why" in (tmp_path / "session" / "wake_reasons.txt").read_text()
    assert not (tmp_path / "session" / "rethink_request.txt").exists()


def test_safeguard_suppresses_and_keeps_request(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("IKARAI_RETHINK_MODE", "live")
    _write_env(tmp_path)
    _quiet_session(tmp_path)
    (tmp_path / "session" / "rethink_request.txt").write_text("why")
    recent = (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (tmp_path / "session" / "rethink_runs.jsonl").write_text(json.dumps({"time": recent, "ok": True}) + "\n")
    assert _run_with_mocks(tmp_path) == 0
    assert "[rethink] suppressed" in capsys.readouterr().out
    assert (tmp_path / "session" / "rethink_request.txt").exists()


def test_attack_still_wakes_while_rethink_is_suppressed(tmp_path, monkeypatch):
    monkeypatch.setenv("IKARAI_RETHINK_MODE", "live")
    _write_env(tmp_path)
    _quiet_session(tmp_path)
    recent = (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (tmp_path / "session" / "rethink_runs.jsonl").write_text(json.dumps({"time": recent, "ok": True}) + "\n")
    state = _state(alerts={"under_attack": True, "unread_messages": 0})
    assert _run_with_mocks(tmp_path, state=state) == 10
    reasons = (tmp_path / "session" / "wake_reasons.txt").read_text()
    assert "heavy: under attack" in reasons
    assert "rethink:" not in reasons


def test_metrics_failure_does_not_block_the_tick(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("IKARAI_RETHINK_MODE", "live")
    _write_env(tmp_path)
    _quiet_session(tmp_path)
    with patch("pretick.metrics.build_line", side_effect=RuntimeError("boom")):
        assert _run_with_mocks(tmp_path) == 0
    assert "[rethink] error: boom" in capsys.readouterr().out


def test_alert_files_human_row_after_three_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("IKARAI_RETHINK_MODE", "off")
    _write_env(tmp_path)
    _quiet_session(tmp_path)
    (tmp_path / "session" / "model_failures.txt").write_text("3")
    (tmp_path / "session" / "last_run.json").write_text(json.dumps({"result": "OAuth expired"}))
    kb_main = MagicMock(return_value=0)
    _run_with_mocks(tmp_path, kb_main=kb_main)
    human_calls = [c for c in kb_main.call_args_list if c.args[0][:2] == ["human", "create"]]
    assert len(human_calls) == 1
    assert human_calls[0].args[0][2] == "Model cycles failing — check Claude login"
    assert "OAuth expired" in human_calls[0].args[0][3]


def test_no_alert_below_three_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("IKARAI_RETHINK_MODE", "off")
    _write_env(tmp_path)
    _quiet_session(tmp_path)
    (tmp_path / "session" / "model_failures.txt").write_text("2")
    kb_main = MagicMock(return_value=0)
    _run_with_mocks(tmp_path, kb_main=kb_main)
    assert not [c for c in kb_main.call_args_list if c.args[0][:2] == ["human", "create"]]


def test_alert_failure_does_not_block_the_tick(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("IKARAI_RETHINK_MODE", "off")
    _write_env(tmp_path)
    _quiet_session(tmp_path)
    (tmp_path / "session" / "model_failures.txt").write_text("5")
    def kb_main(args, **kwargs):
        if args[:2] == ["human", "create"]:
            raise RuntimeError("notion down")
        return 0
    assert _run_with_mocks(tmp_path, kb_main=kb_main) == 0
    assert "[alert] could not file" in capsys.readouterr().out
