import json
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import log_usage as lu

RESULT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "num_turns": 42,
    "duration_ms": 330000,
    "total_cost_usd": 0.81234,
    "result": "Cycle complete. Built a Shipyard.",
    "usage": {
        "input_tokens": 10,
        "cache_read_input_tokens": 900000,
        "cache_creation_input_tokens": 40000,
        "output_tokens": 12000,
        "output_tokens_details": {"thinking_tokens": 8000},
    },
    "modelUsage": {"claude-sonnet-5": {"costUSD": 0.81234}},
}


def test_parse_result_totals_all_token_kinds():
    stats = lu.parse_result(json.dumps(RESULT))
    assert stats["status"] == "ok"
    assert stats["turns"] == 42
    assert stats["total"] == 10 + 900000 + 40000 + 12000
    assert stats["thinking"] == 8000
    assert stats["cost"] == 0.8123
    assert stats["duration_min"] == 5.5
    assert stats["model"] == "claude-sonnet-5"
    assert stats["summary"] == "Cycle complete. Built a Shipyard."


def test_parse_result_marks_nonzero_exit_as_error():
    assert lu.parse_result(json.dumps(RESULT), exit_code=1)["status"] == "error"


def test_parse_result_marks_max_turns_subtype_as_error():
    data = dict(RESULT, subtype="error_max_turns")
    assert lu.parse_result(json.dumps(data))["status"] == "error"


def test_parse_result_tolerates_non_json_output():
    stats = lu.parse_result("Invalid API key", exit_code=1)
    assert stats["status"] == "error"
    assert stats["total"] == 0
    assert stats["summary"] == "Invalid API key"


def test_parse_result_empty_output_still_has_summary():
    assert lu.parse_result("", exit_code=137)["summary"] == "no output (exit 137)"


def test_format_line_is_one_readable_line():
    line = lu.format_line(lu.parse_result(json.dumps(RESULT)))
    assert "\n" not in line
    assert "total 952,010 tokens" in line
    assert "turns 42" in line
    assert "$0.81" in line


def test_build_properties_uses_local_time_title_and_numbers():
    now = datetime(2026, 9, 25, 14, 30, tzinfo=ZoneInfo("Asia/Jerusalem"))
    props = lu.build_properties(lu.parse_result(json.dumps(RESULT)), now)
    assert props["Run"]["title"][0]["text"]["content"] == "2026-09-25 14:30 IDT"
    assert props["Date"] == {"date": {"start": "2026-09-25T14:30:00+03:00"}}
    assert props["Total Tokens"] == {"number": 952010}
    assert props["Status"] == {"select": {"name": "ok"}}
    assert set(props) == set(lu.TOKEN_USAGE_SCHEMA)


def _write_env(tmp_path, extra=""):
    (tmp_path / ".env").write_text(
        "IKARIAM_USERNAME=u\nIKARIAM_SERVER=s\nNOTION_TOKEN=tok\n"
        "NOTION_ROOT_PAGE_ID=root-1\nTIMEZONE=Asia/Jerusalem\n" + extra
    )


@patch("log_usage.nc.create_page")
def test_main_writes_row_to_existing_db(mock_create, tmp_path, capsys):
    _write_env(tmp_path, "NOTION_TOKEN_USAGE_DB_ID=db-usage\n")
    result = tmp_path / "last_run.json"
    result.write_text(json.dumps(RESULT))
    assert lu.main([str(result)], project_root=tmp_path) == 0
    args, _ = mock_create.call_args
    assert args[1] == {"type": "database_id", "database_id": "db-usage"}
    assert args[2]["Turns"] == {"number": 42}
    out = capsys.readouterr().out
    assert "Cycle complete" in out and "[usage] ok" in out


@patch("log_usage.nc.create_page")
@patch("bootstrap_kb.ensure_database", return_value="db-new")
def test_main_creates_db_lazily_and_persists_id(mock_ensure, mock_create, tmp_path):
    _write_env(tmp_path)
    result = tmp_path / "last_run.json"
    result.write_text(json.dumps(RESULT))
    assert lu.main([str(result)], project_root=tmp_path) == 0
    mock_ensure.assert_called_once_with("tok", "root-1", "Token Usage", lu.TOKEN_USAGE_SCHEMA)
    assert "NOTION_TOKEN_USAGE_DB_ID=db-new" in (tmp_path / ".env").read_text()


@patch("log_usage.nc.create_page", side_effect=RuntimeError("notion down"))
def test_main_survives_notion_failure(mock_create, tmp_path, capsys):
    _write_env(tmp_path, "NOTION_TOKEN_USAGE_DB_ID=db-usage\n")
    result = tmp_path / "last_run.json"
    result.write_text(json.dumps(RESULT))
    assert lu.main([str(result)], project_root=tmp_path) == 1
    assert "notion down" in capsys.readouterr().err


@patch("log_usage.nc.create_page")
def test_main_logs_error_row_when_result_file_missing(mock_create, tmp_path):
    _write_env(tmp_path, "NOTION_TOKEN_USAGE_DB_ID=db-usage\n")
    assert lu.main([str(tmp_path / "missing.json"), "--exit-code", "1"], project_root=tmp_path) == 0
    assert mock_create.call_args[0][2]["Status"] == {"select": {"name": "error"}}
