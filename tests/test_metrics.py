import json
from datetime import datetime, timedelta, timezone

import metrics

NOW = datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc)


def _state():
    return {
        "gold": 320848,
        "gold_per_hour": 2897,
        "transporters": {"free": 0, "max": 12},
        "cities": [
            {
                "name": "Kernel", "population": 868, "max_population": 868,
                "resources": {"wood": 973000, "wine": 152410, "marble": 6},
                "max_resources": 973693,
                "production_per_hour": {"wood": 2192, "tradegood": 343},
            },
            {
                "name": "Daemon", "population": 472, "max_population": 1026,
                "resources": {"wood": 3493, "wine": 4, "marble": 716356},
                "max_resources": 881684,
                "production_per_hour": {"wood": 366, "tradegood": 912},
            },
        ],
    }


def test_build_line_totals_and_fleet_capacity():
    line = metrics.build_line(_state(), ["heavy: x"], NOW)
    assert line["time"] == "2026-11-06T10:00:00Z"
    assert line["population"] == 1340
    assert line["production"] == 3813
    assert line["ships"] == 12
    assert line["fleet_capacity"] == 6000
    assert line["towns"] == 2
    assert line["wake"] == ["heavy: x"]


def test_build_line_marks_resources_at_cap():
    line = metrics.build_line(_state(), [], NOW)
    assert line["cities"][0]["at_cap"] == ["wood"]
    assert line["cities"][1]["at_cap"] == []


def test_build_line_keeps_unknown_areas_null():
    line = metrics.build_line(_state(), [], NOW)
    assert line["military"] is None
    assert line["ranking"] is None


def test_build_line_tolerates_sparse_state():
    line = metrics.build_line({"cities": [{"name": "X"}]}, [], NOW)
    assert line["population"] == 0
    assert line["fleet_capacity"] == 0
    assert line["cities"][0]["stock"] == {}


def test_append_and_load_round_trip_and_since_filter(tmp_path):
    old = metrics.build_line(_state(), [], NOW - timedelta(days=2))
    new = metrics.build_line(_state(), [], NOW)
    metrics.append_line(tmp_path, old, NOW - timedelta(days=2))
    metrics.append_line(tmp_path, new, NOW)
    assert len(metrics.load_lines(tmp_path)) == 2
    assert len(metrics.load_lines(tmp_path, since=NOW - timedelta(days=1))) == 1


def test_load_skips_corrupt_lines(tmp_path):
    (tmp_path / "metrics.jsonl").write_text('not json\n{"time": "2026-11-06T10:00:00Z"}\n')
    assert len(metrics.load_lines(tmp_path)) == 1


def test_append_prunes_lines_older_than_retention(tmp_path):
    ancient = metrics.build_line(_state(), [], NOW - timedelta(days=91))
    (tmp_path / "metrics.jsonl").write_text(json.dumps(ancient) + "\n")
    metrics.append_line(tmp_path, metrics.build_line(_state(), [], NOW), NOW)
    lines = metrics.load_lines(tmp_path)
    assert [l["time"] for l in lines] == ["2026-11-06T10:00:00Z"]


def test_summary_shows_trend_and_unavailable_not_zero():
    first = metrics.build_line(_state(), [], NOW - timedelta(days=1))
    last = metrics.build_line(_state(), [], NOW)
    last["population"] = first["population"] * 2
    text = metrics.format_summary([first, last])
    assert "population: 1,340 -> 2,680 (+100.0%)" in text
    assert "military: unavailable" in text
    assert "ranking: unavailable" in text
    assert "Daemon: population 472/1,026 (46%)" in text


def test_summary_with_no_data():
    assert "no scoreboard data" in metrics.format_summary([])


def test_cli_summary_prints_from_session_dir(tmp_path, capsys):
    (tmp_path / "session").mkdir()
    metrics.append_line(tmp_path / "session", metrics.build_line(_state(), [], NOW), NOW)
    assert metrics.main(["summary", "--days", "100000"], project_root=tmp_path) == 0
    assert "population" in capsys.readouterr().out
