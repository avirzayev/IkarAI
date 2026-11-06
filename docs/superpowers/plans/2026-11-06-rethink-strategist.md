# Rethink Strategist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the IkarAI agent notice a failing plan and change direction, using a scoreboard, rethink triggers and a separate read-only strategist run.

**Architecture:** Each pre-tick records a metrics line and evaluates triggers without the model. When a trigger passes the safeguards, the launcher runs a strategist (`RETHINK.md`, stronger model, game read-only) that rewrites the Strategy, then runs the normal cycle.

**Tech Stack:** Python 3.12 stdlib, bash, pytest, `claude -p`, Notion through the existing `scripts/kb.py`.

**Spec:** `docs/superpowers/specs/2026-11-06-rethink-strategist-design.md`

## Global Constraints

- Metrics and trigger errors must never block a cycle: log and continue (fail open).
- Hard rules cannot be changed: no real-money purchases, no logging in, no captchas, no anti-bot evasion, no unverified game actions.
- Safeguards: at least 6 hours between rethink runs, at most 3 per rolling 24 hours, failed runs count.
- Timestamps in session files use `%Y-%m-%dT%H:%M:%SZ` (UTC), matching `pretick._read_timestamp`.
- `IKARAI_RETHINK_MODE` values: `off`, `log` (default), `propose`, `live`.
- Strategist defaults: model `claude-opus-5-5`, effort `high`, budget `5` USD.
- The Strategy page cap stays 3,500 characters (`kb.STRATEGY_CHAR_CAP`).
- No new dependencies. Run tests with `python3 -m pytest -q`; the baseline is 346 passing.
- Never run a second live copy of the agent: the game session is shared.

## File Structure

| File | Responsibility |
|---|---|
| `scripts/metrics.py` (new) | Builds, stores, prunes and summarizes scoreboard lines |
| `scripts/rethink.py` (new) | Triggers, safeguards, run and cycle records, pivot history |
| `scripts/pretick.py` | Wires metrics, triggers and the failure alert into the tick |
| `scripts/kb.py` | `rethink` command, `DECISIONS` validation, strategy read-only guard |
| `scripts/game.py` | Read-only guard for `action` and `set-cookie` |
| `scripts/run_hourly_cycle.sh` | Runs the strategist before the normal cycle, records results |
| `RETHINK.md` (new) | Strategist instructions |
| `RUNBOOK.md` | Follows the new Strategy format, adds `kb.py rethink` and the attack policy |
| `.env.example`, `README.md` | Documents the settings |

**Spec note:** the saved `militaryAdvisor` response only lists movements and occupied towns, not army strength. `military` and `ranking` therefore stay `null`, with no extra game traffic, until a source is found. Task 8 records this in the spec.

---

### Task 1: Scoreboard

**Files:**
- Create: `scripts/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Produces:
  - `metrics.build_line(state: dict, reasons: list, now: datetime) -> dict`
  - `metrics.append_line(session_dir: Path, line: dict, now: datetime) -> None`
  - `metrics.load_lines(session_dir: Path, since: datetime | None = None) -> list`
  - `metrics.parse_time(text: str) -> datetime`
  - `metrics.format_summary(lines: list) -> str`
  - `metrics.main(argv=None, project_root=None) -> int`
  - Line keys: `time`, `population`, `production`, `gold`, `gold_per_hour`, `ships`, `fleet_capacity`, `towns`, `military`, `ranking`, `cities`, `wake`. City keys: `name`, `population`, `max_population`, `stock`, `max_stock`, `production`, `at_cap`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_metrics.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_metrics.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'metrics'`

- [ ] **Step 3: Implement `scripts/metrics.py`**

```python
"""Scoreboard: one JSON line per pre-tick in session/metrics.jsonl, no model use.

Areas are tracked separately on purpose: a combined score could hide a collapse
in one area behind a gain in another. `military` and `ranking` stay null until a
data source exists, and are reported as "unavailable", never as zero.

    python3 scripts/metrics.py summary [--days 7]
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
METRICS_FILE = "metrics.jsonl"
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
RETENTION_DAYS = 90
CARGO_PER_SHIP = 500
CAP_RATIO = 0.98


def parse_time(text: str) -> datetime:
    return datetime.strptime(text, TIME_FORMAT).replace(tzinfo=timezone.utc)


def _format_time(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(TIME_FORMAT)


def _num(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return 0


def _city_line(city: dict) -> dict:
    stock = {k: _num(v) for k, v in (city.get("resources") or {}).items()}
    max_stock = _num(city.get("max_resources"))
    production = sum(_num(v) for v in (city.get("production_per_hour") or {}).values())
    at_cap = sorted(k for k, v in stock.items() if max_stock and v >= max_stock * CAP_RATIO)
    return {
        "name": city.get("name") or city.get("id") or "?",
        "population": _num(city.get("population")),
        "max_population": _num(city.get("max_population")),
        "stock": stock,
        "max_stock": max_stock,
        "production": production,
        "at_cap": at_cap,
    }


def build_line(state: dict, reasons: list, now: datetime) -> dict:
    cities = [_city_line(c) for c in state.get("cities") or []]
    ships = _num((state.get("transporters") or {}).get("max"))
    return {
        "time": _format_time(now),
        "population": sum(c["population"] for c in cities),
        "production": sum(c["production"] for c in cities),
        "gold": _num(state.get("gold")),
        "gold_per_hour": _num(state.get("gold_per_hour")),
        "ships": ships,
        "fleet_capacity": ships * CARGO_PER_SHIP,
        "towns": len(cities),
        "military": None,
        "ranking": None,
        "cities": cities,
        "wake": list(reasons),
    }


def load_lines(session_dir: Path, since: datetime | None = None) -> list:
    path = Path(session_dir) / METRICS_FILE
    if not path.exists():
        return []
    lines = []
    for raw in path.read_text().splitlines():
        try:
            line = json.loads(raw)
            moment = parse_time(line["time"])
        except (ValueError, KeyError, TypeError):
            continue
        if since is None or moment >= since:
            lines.append(line)
    return lines


def _prune(path: Path, now: datetime) -> None:
    cutoff = now - timedelta(days=RETENTION_DAYS)
    raw_lines = path.read_text().splitlines()
    kept = []
    for raw in raw_lines:
        try:
            if parse_time(json.loads(raw)["time"]) >= cutoff:
                kept.append(raw)
        except (ValueError, KeyError, TypeError):
            continue
    if len(kept) != len(raw_lines):
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(f"{raw}\n" for raw in kept))
        os.replace(tmp, path)


def append_line(session_dir: Path, line: dict, now: datetime) -> None:
    path = Path(session_dir) / METRICS_FILE
    with path.open("a") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    _prune(path, now)


def _trend(first, last) -> str:
    if first:
        return f"{first:,.0f} -> {last:,.0f} ({(last - first) / first * 100:+.1f}%)"
    return f"{first:,.0f} -> {last:,.0f}"


def format_summary(lines: list) -> str:
    if not lines:
        return "no scoreboard data yet"
    first, last = lines[0], lines[-1]
    out = [
        f"Scoreboard: {len(lines)} samples, {first['time']} to {last['time']}",
        f"population: {_trend(first['population'], last['population'])}",
        f"production/h: {_trend(first['production'], last['production'])}",
        f"gold: {_trend(first['gold'], last['gold'])} (now {last['gold_per_hour']:+,.0f}/h)",
        f"fleet: {first['ships']:,.0f} -> {last['ships']:,.0f} ships ({last['fleet_capacity']:,.0f} cargo per trip)",
        f"towns: {first['towns']} -> {last['towns']}",
        "military: " + ("unavailable" if last["military"] is None else str(last["military"])),
        "ranking: " + ("unavailable" if last["ranking"] is None else str(last["ranking"])),
    ]
    for city in last["cities"]:
        filled = city["population"] / city["max_population"] * 100 if city["max_population"] else 0
        stock = " ".join(f"{k} {v:,.0f}" for k, v in city["stock"].items())
        cap = f" | AT CAP: {', '.join(city['at_cap'])}" if city["at_cap"] else ""
        out.append(
            f"{city['name']}: population {city['population']:,.0f}/{city['max_population']:,.0f} "
            f"({filled:.0f}%) | {stock}{cap}"
        )
    return "\n".join(out)


def main(argv: list | None = None, project_root=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_summary = sub.add_parser("summary")
    p_summary.add_argument("--days", type=int, default=7)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    root = Path(project_root) if project_root is not None else PROJECT_ROOT
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    print(format_summary(load_lines(root / "session", since=since)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_metrics.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/metrics.py tests/test_metrics.py
git commit -m "feat: add scoreboard metrics"
```

---

### Task 2: Rethink triggers

**Files:**
- Create: `scripts/rethink.py`
- Test: `tests/test_rethink.py`

**Interfaces:**
- Consumes: `metrics.parse_time`, `metrics.build_line`, line keys from Task 1.
- Produces:
  - `rethink.mode() -> str`
  - `rethink.read_timestamp(path: Path) -> datetime | None`
  - `rethink.read_request(session_dir: Path) -> str | None`
  - `rethink.evaluate(lines: list, session_dir: Path, now: datetime) -> list` of reason strings without a prefix.
  - Constants: `DAILY_HOURS=24`, `STAGNANT_HOURS=48`, `STAGNANT_GROWTH=0.01`, `STUCK_WAKES=6`, `STRANDED_HIGH_LOADS=20`, `STRANDED_LOW_LOADS=1`, `SUSTAINED_HOURS=24`, `EMPTY_CITY_RATIO=0.70`, `OUTAGE_HOURS=12`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rethink.py`:

```python
from datetime import datetime, timedelta, timezone

import metrics
import rethink

NOW = datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc)


def _stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _todays_state():
    """The real 2026-11-06 empire, trimmed to the fields metrics reads."""
    def city(name, pop, max_pop, wood, wine, marble, crystal, sulfur, cap, prod):
        return {
            "name": name, "population": pop, "max_population": max_pop,
            "resources": {"wood": wood, "wine": wine, "marble": marble, "crystal": crystal, "sulfur": sulfur},
            "max_resources": cap,
            "production_per_hour": {"wood": prod, "tradegood": 100},
        }
    return {
        "gold": 320848, "gold_per_hour": 2897, "transporters": {"free": 0, "max": 12},
        "cities": [
            city("Kernel", 868, 868, 971774, 152410, 6, 180, 669, 973693, 2192),
            city("Daemon", 472, 1026, 3493, 4, 716356, 0, 345, 881684, 366),
            city("Syscall", 329, 722, 207195, 16, 12, 232963, 2900, 785056, 370),
            city("Async", 319, 630, 126345, 64, 49, 47, 150710, 785056, 224),
        ],
    }


def _healthy_state(growth=1.0):
    def city(name):
        return {
            "name": name,
            "population": 800 * growth,
            "max_population": 1000,
            "resources": {"wood": 50000, "marble": 50000},
            "max_resources": 500000,
            "production_per_hour": {"wood": 1000 * growth, "tradegood": 500 * growth},
        }
    return {"gold": 1000, "gold_per_hour": 10, "transporters": {"max": 12}, "cities": [city("A"), city("B")]}


def _series(state_for_hours_ago, hours=60, step=6, wake=None):
    lines = []
    for ago in range(hours, -1, -step):
        moment = NOW - timedelta(hours=ago)
        lines.append(metrics.build_line(state_for_hours_ago(ago), wake or [], moment))
    return lines


def _fresh_session(tmp_path):
    (tmp_path / "last_rethink.txt").write_text(_stamp(NOW - timedelta(hours=1)))
    (tmp_path / "last_model_success.txt").write_text(_stamp(NOW - timedelta(hours=1)))
    return tmp_path


def test_replay_of_todays_state_fires_the_three_expected_triggers(tmp_path):
    reasons = rethink.evaluate(_series(lambda ago: _todays_state()), _fresh_session(tmp_path), NOW)
    joined = "\n".join(reasons)
    assert "stranded stock: marble in Daemon" in joined
    assert "wasted cap: Kernel wood" in joined
    assert "empty city: Daemon" in joined
    assert "empty city: Kernel" not in joined


def test_healthy_growing_series_fires_only_daily(tmp_path):
    session = _fresh_session(tmp_path)
    (session / "last_rethink.txt").write_text(_stamp(NOW - timedelta(hours=25)))
    lines = _series(lambda ago: _healthy_state(growth=1.0 - ago * 0.001))
    assert rethink.evaluate(lines, session, NOW) == ["daily review due"]


def test_daily_fires_when_never_run(tmp_path):
    session = _fresh_session(tmp_path)
    (session / "last_rethink.txt").unlink()
    assert "daily review due" in rethink.evaluate([], session, NOW)


def test_stagnant_needs_48_hours_of_data(tmp_path):
    short = _series(lambda ago: _healthy_state(), hours=47)
    assert not any("stagnant" in r for r in rethink.evaluate(short, _fresh_session(tmp_path), NOW))


def test_stagnant_fires_on_flat_series(tmp_path):
    lines = _series(lambda ago: _healthy_state(), hours=60)
    assert any("stagnant" in r for r in rethink.evaluate(lines, _fresh_session(tmp_path), NOW))


def test_stagnant_does_not_fire_if_either_measure_grew(tmp_path):
    def state(ago):
        result = _healthy_state()
        if ago == 0:
            result["cities"][0]["production_per_hour"]["wood"] = 2000
        return result
    lines = _series(state, hours=60)
    assert not any("stagnant" in r for r in rethink.evaluate(lines, _fresh_session(tmp_path), NOW))


def test_stuck_fires_on_six_identical_wakes(tmp_path):
    lines = _series(lambda ago: _healthy_state(growth=1 - ago * 0.001), hours=30, wake=["heavy: city A idle"])
    assert any("stuck" in r for r in rethink.evaluate(lines, _fresh_session(tmp_path), NOW))


def test_stuck_ignores_ticks_without_wakes_and_needs_six(tmp_path):
    lines = _series(lambda ago: _healthy_state(growth=1 - ago * 0.001), hours=24, wake=["heavy: city A idle"])
    assert len(lines) == 5
    assert not any("stuck" in r for r in rethink.evaluate(lines, _fresh_session(tmp_path), NOW))


def test_stuck_does_not_fire_when_reasons_differ(tmp_path):
    lines = _series(lambda ago: _healthy_state(growth=1 - ago * 0.001), hours=30, wake=["heavy: city A idle"])
    lines[-1]["wake"] = ["heavy: city B idle"]
    assert not any("stuck" in r for r in rethink.evaluate(lines, _fresh_session(tmp_path), NOW))


def test_stranded_needs_a_fleet(tmp_path):
    state = _todays_state()
    state["transporters"]["max"] = 0
    lines = [metrics.build_line(state, [], NOW)]
    assert not any("stranded" in r for r in rethink.evaluate(lines, _fresh_session(tmp_path), NOW))


def test_wasted_cap_needs_24_continuous_hours(tmp_path):
    def state(ago):
        result = _healthy_state(growth=1 - ago * 0.001)
        if ago <= 12:
            result["cities"][0]["resources"]["wood"] = 499000
        return result
    lines = _series(state, hours=60)
    assert not any("wasted cap" in r for r in rethink.evaluate(lines, _fresh_session(tmp_path), NOW))


def test_empty_city_needs_history_covering_24_hours(tmp_path):
    lines = _series(lambda ago: _todays_state(), hours=23, step=6)
    assert not any("empty city" in r for r in rethink.evaluate(lines, _fresh_session(tmp_path), NOW))


def test_outage_fires_after_12_hours_without_success(tmp_path):
    session = _fresh_session(tmp_path)
    (session / "last_model_success.txt").write_text(_stamp(NOW - timedelta(hours=13)))
    assert any("return from outage" in r for r in rethink.evaluate([], session, NOW))


def test_outage_stays_quiet_when_success_file_is_missing(tmp_path):
    session = _fresh_session(tmp_path)
    (session / "last_model_success.txt").unlink()
    assert not any("outage" in r for r in rethink.evaluate([], session, NOW))


def test_requested_fires_with_reason_text(tmp_path):
    session = _fresh_session(tmp_path)
    (session / "rethink_request.txt").write_text("plan looks wrong\n")
    assert "requested: plan looks wrong" in rethink.evaluate([], session, NOW)


def test_mode_defaults_to_log_and_rejects_unknown(monkeypatch):
    monkeypatch.delenv("IKARAI_RETHINK_MODE", raising=False)
    assert rethink.mode() == "log"
    monkeypatch.setenv("IKARAI_RETHINK_MODE", "LIVE")
    assert rethink.mode() == "live"
    monkeypatch.setenv("IKARAI_RETHINK_MODE", "banana")
    assert rethink.mode() == "log"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_rethink.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'rethink'`

- [ ] **Step 3: Implement the triggers in `scripts/rethink.py`**

```python
"""Rethink triggers, safeguards, run records and pivot history.

Trigger evaluation is pure: it reads scoreboard lines and session files and
returns reason strings. scripts/pretick.py decides what to do with them.
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import metrics  # noqa: E402

VALID_MODES = ("off", "log", "propose", "live")
DEFAULT_MODE = "log"

DAILY_HOURS = 24
STAGNANT_HOURS = 48
STAGNANT_GROWTH = 0.01
STUCK_WAKES = 6
STRANDED_HIGH_LOADS = 20
STRANDED_LOW_LOADS = 1
SUSTAINED_HOURS = 24
EMPTY_CITY_RATIO = 0.70
OUTAGE_HOURS = 12

LAST_RETHINK_FILE = "last_rethink.txt"
LAST_SUCCESS_FILE = "last_model_success.txt"
REQUEST_FILE = "rethink_request.txt"


def mode() -> str:
    value = os.environ.get("IKARAI_RETHINK_MODE", DEFAULT_MODE).strip().lower()
    return value if value in VALID_MODES else DEFAULT_MODE


def read_timestamp(path: Path) -> datetime | None:
    try:
        return metrics.parse_time(Path(path).read_text().strip())
    except (OSError, ValueError):
        return None


def read_request(session_dir: Path) -> str | None:
    path = Path(session_dir) / REQUEST_FILE
    if not path.exists():
        return None
    return path.read_text().strip() or "no reason given"


def _sustained(lines: list, now: datetime, hours: int, predicate) -> bool:
    """True if predicate held on every line from at or before now-hours onward."""
    cutoff = now - timedelta(hours=hours)
    start = None
    for index, line in enumerate(lines):
        if metrics.parse_time(line["time"]) <= cutoff:
            start = index
    if start is None:
        return False
    return all(predicate(line) for line in lines[start:])


def _city(line: dict, name: str) -> dict | None:
    return next((c for c in line["cities"] if c["name"] == name), None)


def _daily(session_dir: Path, now: datetime) -> list:
    last = read_timestamp(Path(session_dir) / LAST_RETHINK_FILE)
    if last is None or now - last >= timedelta(hours=DAILY_HOURS):
        return ["daily review due"]
    return []


def _stagnant(lines: list, now: datetime) -> list:
    if not lines:
        return []
    cutoff = now - timedelta(hours=STAGNANT_HOURS)
    older = [l for l in lines if metrics.parse_time(l["time"]) <= cutoff]
    if not older:
        return []
    base, last = older[-1], lines[-1]
    if not base["population"] or not base["production"]:
        return []
    population = (last["population"] - base["population"]) / base["population"]
    production = (last["production"] - base["production"]) / base["production"]
    if population < STAGNANT_GROWTH and production < STAGNANT_GROWTH:
        return [f"stagnant: population {population:+.1%}, production {production:+.1%} in {STAGNANT_HOURS}h"]
    return []


def _stuck(lines: list) -> list:
    wakes = [l["wake"] for l in lines if l.get("wake")]
    if len(wakes) < STUCK_WAKES:
        return []
    recent = [frozenset(w) for w in wakes[-STUCK_WAKES:]]
    if len(set(recent)) == 1:
        return [f"stuck: same wake reasons {STUCK_WAKES} times: {'; '.join(sorted(recent[0]))}"]
    return []


def _stranded(lines: list) -> list:
    if not lines:
        return []
    last = lines[-1]
    capacity = last["fleet_capacity"]
    if not capacity:
        return []
    resources = sorted({r for c in last["cities"] for r in c["stock"]})
    reasons = []
    for resource in resources:
        holders = [(c["stock"].get(resource, 0), c["name"]) for c in last["cities"]]
        most, name = max(holders)
        least = min(amount for amount, _ in holders)
        if most > STRANDED_HIGH_LOADS * capacity and least < STRANDED_LOW_LOADS * capacity:
            reasons.append(f"stranded stock: {resource} in {name}, {most / capacity:.0f} fleet loads")
    return reasons


def _wasted_cap(lines: list, now: datetime) -> list:
    if not lines:
        return []
    reasons = []
    for city in lines[-1]["cities"]:
        for resource in city["at_cap"]:
            def held(line, name=city["name"], res=resource):
                match = _city(line, name)
                return match is not None and res in match["at_cap"]
            if _sustained(lines, now, SUSTAINED_HOURS, held):
                reasons.append(f"wasted cap: {city['name']} {resource}")
    return reasons


def _empty_city(lines: list, now: datetime) -> list:
    if not lines:
        return []
    reasons = []
    for city in lines[-1]["cities"]:
        def held(line, name=city["name"]):
            match = _city(line, name)
            return (
                match is not None
                and match["max_population"] > 0
                and match["population"] / match["max_population"] < EMPTY_CITY_RATIO
            )
        if _sustained(lines, now, SUSTAINED_HOURS, held):
            filled = city["population"] / city["max_population"]
            reasons.append(f"empty city: {city['name']} at {filled:.0%} of max population")
    return reasons


def _outage(session_dir: Path, now: datetime) -> list:
    last = read_timestamp(Path(session_dir) / LAST_SUCCESS_FILE)
    if last is not None and now - last >= timedelta(hours=OUTAGE_HOURS):
        return [f"return from outage: no successful model cycle since {last:%Y-%m-%d %H:%M} UTC"]
    return []


def _requested(session_dir: Path) -> list:
    request = read_request(session_dir)
    return [f"requested: {request}"] if request else []


def evaluate(lines: list, session_dir: Path, now: datetime) -> list:
    return (
        _daily(session_dir, now)
        + _stagnant(lines, now)
        + _stuck(lines)
        + _stranded(lines)
        + _wasted_cap(lines, now)
        + _empty_city(lines, now)
        + _outage(session_dir, now)
        + _requested(session_dir)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_rethink.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/rethink.py tests/test_rethink.py
git commit -m "feat: add rethink triggers"
```

---

### Task 3: Safeguards, run records and pivot history

**Files:**
- Modify: `scripts/rethink.py`
- Test: `tests/test_rethink_records.py`

**Interfaces:**
- Consumes: `rethink.LAST_RETHINK_FILE`, `rethink.LAST_SUCCESS_FILE`, `rethink.REQUEST_FILE`, `metrics.TIME_FORMAT`.
- Produces:
  - `rethink.gate(session_dir: Path, now: datetime) -> tuple[bool, str]`
  - `rethink.consume_request(session_dir: Path) -> None`
  - `rethink.record_cycle(session_dir: Path, result_path: Path, exit_code: int, now: datetime) -> bool`
  - `rethink.record_rethink(session_dir: Path, result_path: Path, exit_code: int, now: datetime) -> bool`
  - `rethink.consecutive_failures(session_dir: Path) -> int`
  - `rethink.last_error(session_dir: Path) -> str`
  - `rethink.add_pivot(session_dir: Path, pivot: dict, now: datetime) -> None`, raising `ValueError` on missing fields or in proposal mode
  - `rethink.recent_pivots(session_dir: Path, count: int) -> list`
  - `rethink.main(argv=None, project_root=None, now=None) -> int`, with commands `record-cycle PATH --exit-code N`, `record-rethink PATH --exit-code N`, `pivot JSON`, `pivots --last N`
  - Files: `rethink_runs.jsonl` (`{"time", "ok"}`), `model_failures.txt` (integer), `pivots.jsonl`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rethink_records.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_rethink_records.py -q`
Expected: FAIL with `AttributeError: module 'rethink' has no attribute 'gate'`

- [ ] **Step 3: Implement**

In `scripts/rethink.py`, change the imports at the top to:

```python
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
```

Add below the existing constants:

```python
MIN_HOURS_BETWEEN_RUNS = 6
MAX_RUNS_PER_DAY = 3
KEEP_RUN_RECORDS = 200

RUNS_FILE = "rethink_runs.jsonl"
FAILURES_FILE = "model_failures.txt"
PIVOTS_FILE = "pivots.jsonl"
PIVOT_FIELDS = ("triggers", "main_limit", "changes", "expected", "check_by", "sources", "grade_of_previous")
```

Append to the end of the file:

```python
def _format_time(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(metrics.TIME_FORMAT)


def _read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    rows = []
    for raw in path.read_text().splitlines():
        try:
            rows.append(json.loads(raw))
        except ValueError:
            continue
    return rows


def gate(session_dir: Path, now: datetime) -> tuple:
    """Whether a rethink may run now. Failed runs count, so a broken strategist can't loop."""
    times = []
    for row in _read_jsonl(Path(session_dir) / RUNS_FILE):
        try:
            times.append(metrics.parse_time(row["time"]))
        except (KeyError, TypeError, ValueError):
            continue
    if any(now - t < timedelta(hours=MIN_HOURS_BETWEEN_RUNS) for t in times):
        return False, f"last run under {MIN_HOURS_BETWEEN_RUNS}h ago"
    if sum(1 for t in times if now - t < timedelta(hours=24)) >= MAX_RUNS_PER_DAY:
        return False, f"already {MAX_RUNS_PER_DAY} runs in 24h"
    return True, "ok"


def consume_request(session_dir: Path) -> None:
    (Path(session_dir) / REQUEST_FILE).unlink(missing_ok=True)


def _run_ok(result_path: Path, exit_code: int) -> bool:
    if exit_code != 0:
        return False
    try:
        return not json.loads(Path(result_path).read_text()).get("is_error")
    except (OSError, ValueError, AttributeError):
        return False


def consecutive_failures(session_dir: Path) -> int:
    try:
        return int((Path(session_dir) / FAILURES_FILE).read_text().strip())
    except (OSError, ValueError):
        return 0


def record_cycle(session_dir: Path, result_path: Path, exit_code: int, now: datetime) -> bool:
    session_dir = Path(session_dir)
    ok = _run_ok(result_path, exit_code)
    if ok:
        (session_dir / LAST_SUCCESS_FILE).write_text(_format_time(now) + "\n")
        (session_dir / FAILURES_FILE).write_text("0\n")
    else:
        (session_dir / FAILURES_FILE).write_text(f"{consecutive_failures(session_dir) + 1}\n")
    return ok


def record_rethink(session_dir: Path, result_path: Path, exit_code: int, now: datetime) -> bool:
    session_dir = Path(session_dir)
    ok = _run_ok(result_path, exit_code)
    path = session_dir / RUNS_FILE
    rows = _read_jsonl(path)[-(KEEP_RUN_RECORDS - 1):]
    rows.append({"time": _format_time(now), "ok": ok})
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    if ok:
        (session_dir / LAST_RETHINK_FILE).write_text(_format_time(now) + "\n")
    return ok


def last_error(session_dir: Path) -> str:
    try:
        result = json.loads((Path(session_dir) / "last_run.json").read_text()).get("result")
    except (OSError, ValueError, AttributeError):
        return "unknown"
    return str(result)[:500] if result else "unknown"


def add_pivot(session_dir: Path, pivot: dict, now: datetime) -> None:
    if os.environ.get("IKARAI_STRATEGY_READ_ONLY") == "1":
        raise ValueError("refused in proposal mode: write session/rethink_proposal.md instead")
    missing = [f for f in PIVOT_FIELDS if f not in pivot]
    if missing:
        raise ValueError(f"pivot is missing fields: {', '.join(missing)}")
    row = {"time": _format_time(now), **{f: pivot[f] for f in PIVOT_FIELDS}}
    with (Path(session_dir) / PIVOTS_FILE).open("a") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def recent_pivots(session_dir: Path, count: int) -> list:
    return _read_jsonl(Path(session_dir) / PIVOTS_FILE)[-count:]


def main(argv: list | None = None, project_root=None, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("record-cycle", "record-rethink"):
        p_record = sub.add_parser(name)
        p_record.add_argument("result_path")
        p_record.add_argument("--exit-code", type=int, required=True)
    p_pivot = sub.add_parser("pivot")
    p_pivot.add_argument("json_text")
    p_pivots = sub.add_parser("pivots")
    p_pivots.add_argument("--last", type=int, default=10)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    root = Path(project_root) if project_root is not None else PROJECT_ROOT
    session_dir = root / "session"
    now = now if now is not None else datetime.now(timezone.utc)

    if args.command == "record-cycle":
        record_cycle(session_dir, Path(args.result_path), args.exit_code, now)
        return 0
    if args.command == "record-rethink":
        ok = record_rethink(session_dir, Path(args.result_path), args.exit_code, now)
        print(f"[rethink] strategist run {'ok' if ok else 'FAILED'}; Strategy unchanged if failed")
        return 0
    if args.command == "pivot":
        try:
            add_pivot(session_dir, json.loads(args.json_text), now)
        except ValueError as exc:
            print(f"pivot refused: {exc}", file=sys.stderr)
            return 1
        print("OK pivot recorded")
        return 0
    for row in recent_pivots(session_dir, args.last):
        print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_rethink.py tests/test_rethink_records.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/rethink.py tests/test_rethink_records.py
git commit -m "feat: add rethink safeguards, run records and pivot history"
```

---

### Task 4: Pre-tick wiring and failure alert

**Files:**
- Modify: `scripts/pretick.py` (imports, new helpers, the reasons block in `run`)
- Test: `tests/test_pretick.py` (append)

**Interfaces:**
- Consumes: `metrics.build_line/append_line/load_lines`; `rethink.mode/evaluate/gate/consume_request/consecutive_failures/last_error`.
- Produces: wake reasons prefixed `rethink: `, hourly-log lines prefixed `[rethink-shadow]` and `[rethink] suppressed`, and Human Required row `Model cycles failing — check Claude login`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pretick.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_pretick.py -q`
Expected: the new tests FAIL (for example, `metrics.jsonl` does not exist); existing tests PASS.

- [ ] **Step 3: Implement**

In `scripts/pretick.py`, add below `import notion_client as nc  # noqa: E402`:

```python
import metrics  # noqa: E402
import rethink  # noqa: E402
```

Add below `REVIEW_STALE_HOURS = 24`:

```python
RETHINK_HISTORY_DAYS = 8
ALERT_AFTER_FAILURES = 3
ALERT_TITLE = "Model cycles failing — check Claude login"
```

Add above the `# step 4 — next_wake` section:

```python
# ---------------------------------------------------------------------------
# rethink triggers and failure alert (never block the tick)
# ---------------------------------------------------------------------------


def _rethink_reasons(session_dir: Path, state: dict, base_reasons: list, now_utc: datetime) -> list:
    try:
        metrics.append_line(session_dir, metrics.build_line(state, base_reasons, now_utc), now_utc)
        mode = rethink.mode()
        if mode == "off":
            return []
        lines = metrics.load_lines(session_dir, since=now_utc - timedelta(days=RETHINK_HISTORY_DAYS))
        fired = rethink.evaluate(lines, session_dir, now_utc)
        if not fired:
            return []
        if mode == "log":
            for reason in fired:
                print(f"[rethink-shadow] {reason}")
            rethink.consume_request(session_dir)
            return []
        allowed, why = rethink.gate(session_dir, now_utc)
        if not allowed:
            print(f"[rethink] suppressed ({why}): {'; '.join(fired)}")
            return []
        rethink.consume_request(session_dir)
        return [f"rethink: {reason}" for reason in fired]
    except Exception as exc:  # fail open — the normal cycle must still run
        print(f"[rethink] error: {exc}")
        return []


def _file_failure_alert(session_dir: Path, project_root: Path, now_utc: datetime) -> None:
    try:
        failures = rethink.consecutive_failures(session_dir)
        if failures < ALERT_AFTER_FAILURES:
            return
        question = (
            f"{failures} model cycles in a row have failed. Last error: "
            f"{rethink.last_error(session_dir)}. Check the Claude login on the server."
        )
        # `human create` skips the row if one with this title is already open.
        kb.main(["human", "create", ALERT_TITLE, question], project_root=project_root, now=now_utc)
    except Exception as exc:
        print(f"[alert] could not file failure alert: {exc}")
```

In `run`, replace:

```python
        reasons.extend(_staleness_reasons(session_dir, now_utc))

        if reasons:
```

with:

```python
        reasons.extend(_staleness_reasons(session_dir, now_utc))
        _file_failure_alert(session_dir, project_root, now_utc)
        reasons.extend(_rethink_reasons(session_dir, state, reasons, now_utc))

        if reasons:
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_pretick.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/pretick.py tests/test_pretick.py
git commit -m "feat: wire scoreboard, rethink triggers and failure alert into pretick"
```

---

### Task 5: `kb.py` rethink command, decision validation, proposal guard

**Files:**
- Modify: `scripts/kb.py` (`cmd_strategy_set`, `cmd_strategy_section`, `cmd_log_add`, `_build_parser`, new helpers)
- Test: `tests/test_kb.py` (append)

**Interfaces:**
- Consumes: `session/rethink_request.txt` path name matches `rethink.REQUEST_FILE`.
- Produces: `kb.py rethink "reason"`; `kb.validate_decisions(text: str) -> None`, raising `KbError`; `IKARAI_STRATEGY_READ_ONLY=1` refuses `strategy set`, `strategy section` and `log add`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_kb.py`:

```python
# ---------------------------------------------------------------------------
# rethink support
# ---------------------------------------------------------------------------


def test_rethink_writes_request_file(tmp_path):
    _write_env(tmp_path)
    assert kb.main(["rethink", "plan looks wrong"], project_root=tmp_path) == 0
    assert (tmp_path / "session" / "rethink_request.txt").read_text().strip() == "plan looks wrong"


def test_strategy_set_refuses_decision_without_recheck(tmp_path, capsys):
    _write_env(tmp_path)
    text = "## DECISIONS\nno warehouses - because stock is far below cap"
    assert kb.main(["strategy", "set", text], project_root=tmp_path) != 0
    assert "missing 'recheck'" in capsys.readouterr().err


def test_strategy_set_refuses_decision_without_reason(tmp_path, capsys):
    _write_env(tmp_path)
    text = "## DECISIONS\nno warehouses - recheck if any city reaches 90%"
    assert kb.main(["strategy", "set", text], project_root=tmp_path) != 0
    assert "missing 'because'" in capsys.readouterr().err


def test_strategy_set_accepts_valid_decisions_and_ignores_other_sections(tmp_path):
    _write_env(tmp_path)
    text = (
        "## PLAN\nbuy ships\n"
        "## DECISIONS\nno warehouses - because stock is far below cap - recheck if any city reaches 90%\n"
        "## LOGISTICS\nFleet 12 ships"
    )
    with patch("kb.nc.get_block_children", return_value=[]), patch("kb.nc.append_blocks"):
        assert kb.main(["strategy", "set", text], project_root=tmp_path) == 0


def test_strategy_section_validates_decisions_heading(tmp_path, capsys):
    _write_env(tmp_path)
    assert kb.main(["strategy", "section", "Decisions", "no ships"], project_root=tmp_path) != 0
    assert "missing 'because'" in capsys.readouterr().err


def test_strategy_writes_and_log_refused_in_proposal_mode(tmp_path, monkeypatch, capsys):
    _write_env(tmp_path)
    monkeypatch.setenv("IKARAI_STRATEGY_READ_ONLY", "1")
    assert kb.main(["strategy", "set", "x"], project_root=tmp_path) != 0
    assert kb.main(["strategy", "section", "Next", "x"], project_root=tmp_path) != 0
    assert kb.main(["log", "add", "x"], project_root=tmp_path) != 0
    assert "proposal mode" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_kb.py -q -k "rethink or decision or proposal"`
Expected: FAIL (`invalid choice: 'rethink'`, and validation tests reaching Notion or returning 0)

- [ ] **Step 3: Implement**

Confirm `re` is imported at the top of `scripts/kb.py`; add `import re` if it is missing.

Add above `def cmd_strategy_show`:

```python
DECISIONS_HEADING = "decisions"


def _refuse_in_proposal_mode(what: str) -> None:
    if os.environ.get("IKARAI_STRATEGY_READ_ONLY") == "1":
        raise KbError(f"{what} is refused in proposal mode: write session/rethink_proposal.md instead")


def validate_decisions(text: str) -> None:
    """Every DECISIONS line needs a reason and a re-check condition, so a rule
    can never go stale without something saying when to look at it again."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        lowered = line.lower()
        missing = [word for word in ("because", "recheck") if word not in lowered]
        if missing:
            raise KbError(
                f"DECISIONS line is missing '{missing[0]}': {line!r}. Format: "
                "'no warehouses - because stock is far below cap - recheck if any city reaches 90%'"
            )


def _decisions_body(page_text: str) -> str | None:
    match = re.search(
        rf"^#+\s*{DECISIONS_HEADING}\s*$(.*?)(?=^#+\s|\Z)",
        page_text,
        flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else None


def cmd_rethink(ctx: Ctx, args) -> int:
    (ctx.session_dir / "rethink_request.txt").write_text(args.reason.strip() + "\n")
    print("OK rethink requested")
    return 0
```

In `cmd_strategy_set`, add as the first line of the function:

```python
    _refuse_in_proposal_mode("strategy set")
```

and add below the `STRATEGY_CHAR_CAP` length check, before `strategy_id = ...`:

```python
    body = _decisions_body(text)
    if body is not None:
        validate_decisions(body)
```

In `cmd_strategy_section`, replace the lines from the start of the function body through `heading = args.heading.strip().lstrip("#").strip()` with the following. Validation now runs before any Notion read:

```python
    _refuse_in_proposal_mode("strategy section")
    text = _read_text_arg(args, "strategy section")
    if not text:
        raise KbError("section text is empty")
    heading = args.heading.strip().lstrip("#").strip()
    if heading.lower() == DECISIONS_HEADING:
        validate_decisions(text)
    strategy_id = ctx.config.notion_strategy_page_id
    blocks = nc.get_block_children(ctx.token, strategy_id)
```

In `cmd_log_add`, add as the first line of the function:

```python
    _refuse_in_proposal_mode("log add")
```

In `_build_parser`, add immediately above `p_human = sub.add_parser("human")`:

```python
    p_rethink = sub.add_parser("rethink", help="ask for a strategist review of the plan")
    p_rethink.add_argument("reason")
    p_rethink.set_defaults(func=cmd_rethink)

```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_kb.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/kb.py tests/test_kb.py
git commit -m "feat: add kb rethink command, decision validation and proposal guard"
```

---

### Task 6: `game.py` read-only mode

**Files:**
- Modify: `scripts/game.py` (`_cmd_action`, `_cmd_set_cookie`, new helper)
- Test: `tests/test_game.py` (append)

**Interfaces:**
- Produces: with `IKARAI_READ_ONLY=1`, `action` (directly or inside `batch`) and `set-cookie` print a line starting `READ_ONLY:` and return 2. `nav`, `state`, `find`, `cities` and `session` still work.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_game.py`:

```python
# ---------------------------------------------------------------------------
# read-only mode (strategist)
# ---------------------------------------------------------------------------


def test_read_only_refuses_action(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("IKARAI_READ_ONLY", "1")
    assert game.main(["action", "IslandScreen", "workerPlan", "cityId=1"], project_root=tmp_path) == 2
    assert "READ_ONLY" in capsys.readouterr().out


def test_read_only_refuses_set_cookie(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("IKARAI_READ_ONLY", "1")
    assert game.main(["set-cookie", "--cookie", "x=1"], project_root=tmp_path) == 2
    assert "READ_ONLY" in capsys.readouterr().out
    assert not (tmp_path / "session" / "cookie.txt").exists()


def test_read_only_batch_refuses_action_lines_but_runs_nav(tmp_path, capsys, monkeypatch):
    import io
    monkeypatch.setenv("IKARAI_READ_ONLY", "1")
    monkeypatch.setattr("sys.stdin", io.StringIO("nav townHall\naction IslandScreen workerPlan\n"))
    with patch("game._cmd_nav", return_value=0) as mock_nav:
        assert game.main(["batch"], project_root=tmp_path) == 2
    mock_nav.assert_called_once()
    assert "READ_ONLY" in capsys.readouterr().out


def test_read_only_allows_nav(tmp_path, monkeypatch):
    monkeypatch.setenv("IKARAI_READ_ONLY", "1")
    with patch("game._cmd_nav", return_value=0) as mock_nav:
        assert game.main(["nav", "townHall"], project_root=tmp_path) == 0
    mock_nav.assert_called_once()


def test_actions_not_refused_without_flag(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("IKARAI_READ_ONLY", raising=False)
    game.main(["action", "IslandScreen", "workerPlan", "cityId=1"], project_root=tmp_path)
    assert "READ_ONLY" not in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_game.py -q -k read_only`
Expected: FAIL (no `READ_ONLY` output)

- [ ] **Step 3: Implement**

Confirm `os` is imported at the top of `scripts/game.py`; add `import os` if it is missing.

Add above `def _cmd_set_cookie`:

```python
def _refuse_if_read_only(what: str) -> bool:
    """The strategist plans but doesn't act. This guards against mistakes; it
    isn't a security boundary, because the strategist also has Bash."""
    if os.environ.get("IKARAI_READ_ONLY") == "1":
        print(f"READ_ONLY: {what} refused; the strategist plans and the normal cycle acts")
        return True
    return False
```

Add as the first lines of `_cmd_set_cookie`:

```python
    if _refuse_if_read_only("set-cookie"):
        return 2
```

Add as the first lines of `_cmd_action`:

```python
    if _refuse_if_read_only("action"):
        return 2
```

`_cmd_batch` calls `_cmd_action`, so batch lines are covered.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_game.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/game.py tests/test_game.py
git commit -m "feat: add read-only mode to game.py"
```

---

### Task 7: Launcher and strategist instructions

**Files:**
- Modify: `scripts/run_hourly_cycle.sh`
- Create: `RETHINK.md`
- Test: `tests/test_run_cycle.py`

**Interfaces:**
- Consumes: `rethink.py record-cycle` and `record-rethink` (Task 3), `metrics.py summary` (Task 1), `kb.py rethink` (Task 5), `IKARAI_READ_ONLY` (Task 6), `IKARAI_STRATEGY_READ_ONLY` (Task 5), `rethink:` reasons (Task 4).
- Produces: `session/last_rethink_run.json`, `session/rethink_proposal.md` (written by the strategist in proposal mode).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_run_cycle.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_run_cycle.py -q`
Expected: FAIL (`STRATEGIST` calls missing and `RETHINK.md` not found)

- [ ] **Step 3: Update `scripts/run_hourly_cycle.sh`**

Replace:

```bash
# Only-light reasons (chore review/promotion, periodic check-in) may run on
# a cheaper model/effort if configured.
if ! grep -qv '^light:' <<< "$REASONS"; then
```

with:

```bash
# `rethink:` reasons are for the strategist; the normal cycle gets the rest.
RETHINK_MODE="$(tr '[:upper:]' '[:lower:]' <<< "${IKARAI_RETHINK_MODE:-log}")"
RETHINK_REASONS="$(grep '^rethink:' <<< "$REASONS" || true)"
NORMAL_REASONS="$(grep -v '^rethink:' <<< "$REASONS" || true)"
if [ -z "$NORMAL_REASONS" ]; then
  NORMAL_REASONS="heavy: routine check after strategist run"
fi

# Only-light reasons (chore review/promotion, periodic check-in) may run on
# a cheaper model/effort if configured.
if ! grep -qv '^light:' <<< "$NORMAL_REASONS"; then
```

Replace:

```bash
echo "[wake] model=$MODEL effort=$EFFORT reasons: $(tr '\n' ';' <<< "$REASONS")" >> session/hourly.log
```

with:

```bash
echo "[wake] model=$MODEL effort=$EFFORT reasons: $(tr '\n' ';' <<< "$NORMAL_REASONS")" >> session/hourly.log
```

Insert immediately above `WAKE_BEFORE="$(stat -c %Y session/next_wake.txt 2>/dev/null || echo none)"`:

```bash
LEAN_ARGS=(
  --output-format json
  --setting-sources project,local
  --settings '{"autoMemoryEnabled": false}'
  --strict-mcp-config
  --no-chrome
  --disable-slash-commands
  --tools "Bash,Read,Write,Edit,WebSearch,WebFetch"
  --allowedTools "Bash,Read,Write,Edit,WebSearch,WebFetch"
)

# Strategist: plans only (game read-only), then the normal cycle carries it
# out. Its failure leaves the Strategy unchanged and never blocks the cycle.
if [ -n "$RETHINK_REASONS" ] && { [ "$RETHINK_MODE" = "propose" ] || [ "$RETHINK_MODE" = "live" ]; }; then
  RETHINK_MODEL="${IKARAI_RETHINK_MODEL:-claude-opus-5-5}"
  RETHINK_EFFORT="${IKARAI_RETHINK_EFFORT:-high}"
  RETHINK_BUDGET="${IKARAI_RETHINK_MAX_BUDGET_USD:-5}"
  STRATEGY_READ_ONLY=0
  if [ "$RETHINK_MODE" = "propose" ]; then
    STRATEGY_READ_ONLY=1
  fi
  echo "[rethink] mode=$RETHINK_MODE model=$RETHINK_MODEL reasons: $(tr '\n' ';' <<< "$RETHINK_REASONS")" >> session/hourly.log
  set +e
  IKARAI_READ_ONLY=1 IKARAI_STRATEGY_READ_ONLY="$STRATEGY_READ_ONLY" timeout 1800 "$CLAUDE_BIN" -p "Run one strategist review now, following the instructions in your system prompt (mode: $RETHINK_MODE). It was triggered by:
$RETHINK_REASONS" \
    --model "$RETHINK_MODEL" \
    --effort "$RETHINK_EFFORT" \
    "${LEAN_ARGS[@]}" \
    --append-system-prompt "$(cat "$PROJECT_ROOT/RETHINK.md")" \
    --max-budget-usd "$RETHINK_BUDGET" \
    < /dev/null > session/last_rethink_run.json 2>> session/hourly.log
  RETHINK_STATUS=$?
  set -e
  python3 scripts/rethink.py record-rethink session/last_rethink_run.json --exit-code "$RETHINK_STATUS" >> session/hourly.log 2>&1 || true
  python3 scripts/log_usage.py session/last_rethink_run.json --exit-code "$RETHINK_STATUS" >> session/hourly.log 2>&1 || true
fi

```

Replace the normal cycle's prompt and flags. The existing text from `"$CLAUDE_BIN" -p` through `--allowedTools` becomes:

```bash
"$CLAUDE_BIN" -p "Run one IkarAI cycle now, following the runbook in your system prompt. The pre-tick check woke you for:
$NORMAL_REASONS" \
  --model "$MODEL" \
  --effort "$EFFORT" \
  "${LEAN_ARGS[@]}" \
  --append-system-prompt "$(cat "$PROJECT_ROOT/RUNBOOK.md")" \
```

Keep the existing `"${BUDGET_ARGS[@]}"` and redirection lines that follow.

Replace:

```bash
if grep -qi 'review' <<< "$REASONS"; then
```

with:

```bash
if grep -qi 'review' <<< "$NORMAL_REASONS"; then
```

This keeps a `rethink: daily review due` line from marking the chore review as done, and keeps today's behaviour for every other reason.

Insert immediately above the final `python3 scripts/log_usage.py session/last_run.json ...` line:

```bash
python3 scripts/rethink.py record-cycle session/last_run.json --exit-code "$STATUS" >> session/hourly.log 2>&1 || true
```

- [ ] **Step 4: Create `RETHINK.md`**

```markdown
# IkarAI Strategist

You are the strategist for the IkarAI empire in Ikariam. Normal cycles carry
out the plan. Your job is to decide whether the plan is right, and change it
if it isn't. A normal cycle runs straight after you and acts on what you write.

## Hard rules (you cannot change these)

- Never execute a real-money purchase.
- Never log in, solve a captcha, automate a browser or evade anti-bot checks.
- Never use a game action that isn't verified in the Action Catalog.
- Take no game actions yourself. `game.py action` is disabled for this run;
  `nav`, `state` and `find` still work.
- Do not run `kb.py wake`. The normal cycle sets the next wake.

Everything else is open to challenge, including earlier decisions, the
priority order and anything marked as policy.

## Goals

Owner's default priority order: economy (population and production) >
military > ranking > expansion. You may reorder these when the numbers justify
it, but you must write down why and what you expect to happen.

## What to read

Put these in one Bash call:

    python3 scripts/metrics.py summary
    python3 scripts/game.py state
    python3 scripts/kb.py context
    python3 scripts/rethink.py pivots --last 10

A value shown as "unavailable" is unknown. Do not treat it as zero.

## What to do, in order

1. **Grade past pivots.** For each pivot whose `check_by` has passed, did the
   numbers do what it predicted? If not, work out why before planning further.
2. **Find the main limit.** Name the one thing most holding back the top
   priority, with numbers. Check whether stock, gold, ships or workers are idle.
   Plans built for scarcity go stale once surplus appears, and the reverse.
3. **Challenge the rules.** Re-justify every assumption, DECLINED item and
   DECISIONS line against today's numbers. Drop any you can't justify. Trust a
   stored claim only after checking it against current state.
4. **Research.** Check the mechanics behind each option you are considering:
   `kb.py knowledge search` first, then the Ikariam wiki (ikariam.fandom.com,
   using its API), then WebSearch. Also look for mechanics the empire isn't
   using yet (buildings, research, government, actions), for a way to read
   server ranking and army strength, and for how attacks on players work. Save
   useful findings with `kb.py knowledge add`, marked "(external research)".
   When the wiki and the live game disagree, the live game wins.
5. **Compare options.** Set at least two real alternatives against the current
   plan, by expected payoff toward the priorities.
6. **Decide.** Keep or change the plan and priorities.

Token-saving habits from the normal runbook don't bind you here. Think properly;
the run has a budget cap. Stop once you've decided.

## What to write

Your prompt gives the mode.

### Mode: propose

Write everything to `session/rethink_proposal.md` and nothing else (knowledge
base additions are allowed). Include your grades, main limit, the rules you
challenged, the options you compared, your decision, and the full Strategy
page you would set. Strategy writes, log entries and pivot records are refused
in this mode.

### Mode: live

1. **Strategy page**, with `kb.py strategy set --file PATH` (3,500 characters
   at most; the old version is archived). Use these sections, and keep the
   reference sections normal cycles rely on (empire, logistics, diplomacy):
   - `## GOALS`: priority order and why
   - `## MAIN LIMIT`: the current bottleneck, in numbers
   - `## PLAN`: ordered, concrete steps
   - `## DECISIONS`: active rules, replacing any old DECLINED section. Each line
     needs a reason and a re-check condition, or `kb.py` refuses it:
     `no warehouses - because stock is far below cap - recheck if any city reaches 90%`
2. **Pivot record**, even if you changed nothing (then use an empty `changes`):

       python3 scripts/rethink.py pivot '{"triggers": [...], "main_limit": "...", "changes": [...], "expected": "...", "check_by": "YYYY-MM-DD", "sources": [...], "grade_of_previous": "..."}'

3. **Daily Log**: `kb.py log add` with a short entry: what you changed, why, and
   what you expect to happen.

End with a 3–6 line summary.
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_run_cycle.py -q`
Expected: all PASS

Run: `bash -n scripts/run_hourly_cycle.sh`
Expected: no output

- [ ] **Step 6: Commit**

```bash
git add scripts/run_hourly_cycle.sh RETHINK.md tests/test_run_cycle.py
git commit -m "feat: run the strategist before the normal cycle"
```

---

### Task 8: Runbook, settings docs and spec note

**Files:**
- Modify: `RUNBOOK.md`, `.env.example`, `README.md`, `docs/superpowers/specs/2026-11-06-rethink-strategist-design.md`
- Test: `tests/test_run_cycle.py` (append)

**Interfaces:**
- Consumes: `kb.py rethink` (Task 5).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_run_cycle.py`:

```python
def test_runbook_covers_strategy_format_rethink_and_attack_policy():
    text = (REPO / "RUNBOOK.md").read_text()
    for phrase in ("MAIN LIMIT", "kb.py rethink", "DECISIONS", "retaliation risk", "attackBarbarianVillage"):
        assert phrase in text


def test_env_example_documents_rethink_settings():
    text = (REPO / ".env.example").read_text()
    for name in ("IKARAI_RETHINK_MODE", "IKARAI_RETHINK_MODEL", "IKARAI_RETHINK_EFFORT", "IKARAI_RETHINK_MAX_BUDGET_USD"):
        assert name in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_run_cycle.py -q -k "runbook or env_example"`
Expected: FAIL

- [ ] **Step 3: Update `RUNBOOK.md`**

Insert immediately above `## This is a one-shot run`:

```markdown
## Plan and goals

A separate strategist reviews the plan and writes it to the Strategy page. Follow
its `GOALS`, `MAIN LIMIT` and `PLAN` sections. Choose builds by payoff toward the
goals, not by what happens to be affordable.

Don't reverse a `DECISIONS` line unless its re-check condition is met. If the plan
looks wrong, or the numbers contradict it, run `kb.py rethink "reason"` and carry
on with what you can. The next strategist run picks it up.

**Attacks.** You may attack anyone on your own judgment. Record the target, expected
gain and retaliation risk in the Daily Log. The rule against unverified actions
still applies: only `attackBarbarianVillage` is catalogued, so attacks on players
require first discovering and recording the real request.

```

In the `Strategy.` step (step 5), replace:

```markdown
5. **Strategy.** Only if the plan changed: `kb.py strategy section` for
   the part that changed. The page is the *current* plan, not a changelog.
```

with:

```markdown
5. **Strategy.** Only if the plan changed: `kb.py strategy section` for
   the part that changed. The page is the *current* plan, not a changelog.
   Each `DECISIONS` line needs a reason and a re-check condition:
   `no warehouses - because stock is far below cap - recheck if any city reaches 90%`.
```

In the Commands section, add this entry to the `kb.py` list, directly after the `diplomacy list|upsert` line:

```markdown
- `rethink "reason"` — ask the strategist to review the plan
```

- [ ] **Step 4: Update `.env.example`**

Append below the `IKARAI_SKIP_PRETICK` line:

```bash
# Strategist (scripts/run_hourly_cycle.sh, RETHINK.md). Defaults shown.
# IKARAI_RETHINK_MODE=log       # off | log (triggers logged only) | propose (writes session/rethink_proposal.md) | live
# IKARAI_RETHINK_MODEL=claude-opus-5-5
# IKARAI_RETHINK_EFFORT=high
# IKARAI_RETHINK_MAX_BUDGET_USD=5
```

- [ ] **Step 5: Update `README.md`**

Add a section immediately above the heading of the section containing the `make run-once` table row. Find it with `grep -n "make run-once" README.md`.

```markdown
### Strategist

Normal cycles carry out the plan. A separate strategist run (`RETHINK.md`, game
read-only) decides what the plan should be. Each pre-tick records a scoreboard line
in `session/metrics.jsonl` and checks rethink triggers: daily, stagnant, stuck,
stranded stock, wasted cap, empty city, return from outage, or a `kb.py rethink`
request. Rethinks are limited to one per 6 hours and 3 per 24 hours.
`IKARAI_RETHINK_MODE` controls it: `off`, `log` (default; triggers are only logged),
`propose` (writes `session/rethink_proposal.md`) or `live`. Pivots are recorded in
`session/pivots.jsonl`. After 3 failed model cycles in a row, the pre-tick files a
Human Required row.

```

- [ ] **Step 6: Update the spec's military note**

In `docs/superpowers/specs/2026-11-06-rethink-strategist-design.md`, replace:

```markdown
- Military comes from a read-only `view:militaryAdvisor` read, at most once per 6
  hours to limit game traffic. Field extraction is to be confirmed during
  implementation by inspecting a real response; until then it stays `null`.
```

with:

```markdown
- Military stays `null` for now, with no extra game traffic. The saved
  `view:militaryAdvisor` response (checked 2026-11-06) only lists movements and
  occupied towns, not army strength. Finding a source is on the strategist's
  research list.
```

- [ ] **Step 7: Run the whole suite**

Run: `python3 -m pytest -q`
Expected: all PASS, with more than the 346 baseline tests

- [ ] **Step 8: Commit**

```bash
git add RUNBOOK.md .env.example README.md tests/test_run_cycle.py docs/superpowers/specs/2026-11-06-rethink-strategist-design.md
git commit -m "docs: document the strategist in the runbook and settings"
```

---

### Task 9: Rollout

Run this from the controlling session, not a subagent. Stages 2 to 4 change the live server, and each has a gate. Run server steps only while `session/run.lock` is free.

- [ ] **Step 1: Open the PR**

```bash
git push -u origin HEAD
gh pr create --title "Add rethink strategist" --body "Implements docs/superpowers/specs/2026-11-06-rethink-strategist-design.md"
```

Expected: PR URL. **Gate:** the owner merges it.

- [ ] **Step 2: Check whether the server needs a deploy key**

```bash
gh repo view avirzayev/IkarAI --json visibility -q .visibility
```

If `PRIVATE`, as user `ikarai` on the server, create a read-only key and register it:

```bash
ssh-keygen -t ed25519 -N '' -f ~/.ssh/ikarai_deploy
cat ~/.ssh/ikarai_deploy.pub
```

From the local machine, with that public key saved as `ikarai_deploy.pub`:

```bash
gh repo deploy-key add ikarai_deploy.pub --repo avirzayev/IkarAI --title ikarai-vps
```

- [ ] **Step 3: Rebuild the server's `.git` and pull (log mode)**

As user `ikarai` on the server, while the lock is free:

```bash
cd ~/IkarAI
mv .git .git.corrupt-20261106
git init -q -b master
git remote add origin git@github.com:avirzayev/IkarAI.git
GIT_SSH_COMMAND='ssh -i ~/.ssh/ikarai_deploy -o IdentitiesOnly=yes' git fetch -q origin
git reset -q --mixed 175c11b
git status --short
```

Expected: `git status` shows nothing tracked as changed, because the code matched 175c11b on 2026-11-06. If it shows changes, stop and inspect them. Also confirm that `session/` and `.env` are ignored.

```bash
GIT_SSH_COMMAND='ssh -i ~/.ssh/ikarai_deploy -o IdentitiesOnly=yes' git pull -q --ff-only origin master
git log --oneline -1
```

Expected: the merge commit. If `pytest` is installed on the server, also run `python3 -m pytest -q` and expect all tests to pass. `IKARAI_RETHINK_MODE` stays unset, so it runs in `log` mode.

- [ ] **Step 4: Watch log mode for about a day**

```bash
grep -E '\[rethink' session/hourly.log | tail -40
python3 scripts/metrics.py summary
```

Expected: `metrics.jsonl` grows with each tick and shadow reasons match reality. **Gate:** tune thresholds in a follow-up commit if there are false alarms.

- [ ] **Step 5: One supervised rethink**

As user `ikarai`, while the lock is free, set `IKARAI_RETHINK_MODE=propose` in `.env` and run `python3 scripts/kb.py rethink "supervised first run"`. Then run `scripts/run_hourly_cycle.sh` once, and set the mode back to `log`. Read `session/rethink_proposal.md` and `session/last_rethink_run.json` (cost).

Expected: a reasoned proposal, the Strategy page unchanged and the cost recorded. **Gate:** the owner reads the proposal and decides whether to go live.

- [ ] **Step 6: Go live**

Set `IKARAI_RETHINK_MODE=live` in `.env`. Report the first few pivots from `python3 scripts/rethink.py pivots --last 5` against `python3 scripts/metrics.py summary`, and report measured cost against the $1–3 estimate.
