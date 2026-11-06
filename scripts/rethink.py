"""Rethink triggers, safeguards, run records and pivot history.

Trigger evaluation is pure: it reads scoreboard lines and session files and
returns reason strings. scripts/pretick.py decides what to do with them.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
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

CONDITION_COOLDOWN_HOURS = 72
MIN_HOURS_BETWEEN_RUNS = 6
MAX_RUNS_PER_DAY = 3
KEEP_RUN_RECORDS = 200

RUNS_FILE = "rethink_runs.jsonl"
SHADOW_RUNS_FILE = "rethink_shadow_runs.jsonl"
LAST_SHADOW_FILE = "last_rethink_shadow.txt"
FAILURES_FILE = "model_failures.txt"
PIVOTS_FILE = "pivots.jsonl"
PIVOT_FIELDS = ("triggers", "main_limit", "changes", "expected", "check_by", "sources", "grade_of_previous")


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
    return " ".join(path.read_text().split()) or "no reason given"


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


def _daily(session_dir: Path, now: datetime, last_file: str = LAST_RETHINK_FILE) -> list:
    last = read_timestamp(Path(session_dir) / last_file)
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


def evaluate(lines: list, session_dir: Path, now: datetime, last_file: str = LAST_RETHINK_FILE) -> list:
    return (
        _daily(session_dir, now, last_file)
        + _stagnant(lines, now)
        + _stuck(lines)
        + _stranded(lines)
        + _wasted_cap(lines, now)
        + _empty_city(lines, now)
        + _outage(session_dir, now)
        + _requested(session_dir)
    )


_ALWAYS_KEPT = ("daily review due", "requested:")


def condition_key(reason: str) -> str:
    """A stable key for a trigger, without the numbers that change between ticks."""
    if reason.startswith("stranded stock:"):
        return reason.split(",")[0]
    if reason.startswith("empty city:"):
        return reason.split(" at ")[0]
    for prefix in ("stagnant", "stuck", "return from outage"):
        if reason.startswith(prefix + ":"):
            return prefix
    return reason


def fresh_reasons(fired: list, session_dir: Path, now: datetime, runs_file: str = RUNS_FILE) -> list:
    """Drop conditions already reviewed by a successful run within the cooldown."""
    reviewed = set()
    for row in _read_jsonl(Path(session_dir) / runs_file):
        if row.get("ok") is not True:
            continue
        try:
            when = metrics.parse_time(row["time"])
        except (KeyError, TypeError, ValueError):
            continue
        if timedelta(0) <= now - when < timedelta(hours=CONDITION_COOLDOWN_HOURS):
            reviewed.update(condition_key(str(r)) for r in row.get("reasons") or [])
    return [r for r in fired if r.startswith(_ALWAYS_KEPT) or condition_key(r) not in reviewed]


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


def gate(session_dir: Path, now: datetime, runs_file: str = RUNS_FILE) -> tuple:
    """Whether a rethink may run now. Failed runs count, so a broken strategist can't loop."""
    times = []
    for row in _read_jsonl(Path(session_dir) / runs_file):
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


def _write_rows(path: Path, rows: list) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    os.replace(temp, path)


def begin_run(session_dir: Path, reasons: list, now: datetime, runs_file: str = RUNS_FILE) -> None:
    """Record a run before it starts, so a killed run still counts toward the gate."""
    path = Path(session_dir) / runs_file
    rows = _read_jsonl(path)[-(KEEP_RUN_RECORDS - 1):]
    rows.append({"time": _format_time(now), "ok": None, "reasons": list(reasons)})
    _write_rows(path, rows)


def record_rethink(session_dir: Path, result_path: Path, exit_code: int, now: datetime) -> bool:
    session_dir = Path(session_dir)
    ok = _run_ok(result_path, exit_code)
    path = session_dir / RUNS_FILE
    rows = _read_jsonl(path)[-KEEP_RUN_RECORDS:]
    if rows and isinstance(rows[-1], dict) and rows[-1].get("ok") is None and "ok" in rows[-1]:
        rows[-1]["ok"] = ok
    else:
        rows = rows[-(KEEP_RUN_RECORDS - 1):]
        rows.append({"time": _format_time(now), "ok": ok})
    _write_rows(path, rows)
    if ok:
        (session_dir / LAST_RETHINK_FILE).write_text(_format_time(now) + "\n")
    return ok


def record_shadow(session_dir: Path, reasons: list, now: datetime) -> None:
    begin_run(session_dir, reasons, now, SHADOW_RUNS_FILE)
    path = Path(session_dir) / SHADOW_RUNS_FILE
    rows = _read_jsonl(path)
    rows[-1]["ok"] = True
    _write_rows(path, rows)
    (Path(session_dir) / LAST_SHADOW_FILE).write_text(_format_time(now) + "\n")


def last_error(session_dir: Path) -> str:
    try:
        raw = (Path(session_dir) / "last_run.json").read_text()
    except OSError:
        return "unknown"
    try:
        data = json.loads(raw)
    except ValueError:
        return raw.strip()[:500] or "unknown"
    if isinstance(data, dict):
        detail = data.get("result") or data.get("errors")
        if detail:
            return str(detail)[:500]
    return "unknown"


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
    sub.add_parser("begin-run")
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
        print(f"[rethink] strategist run {'ok' if ok else 'FAILED'}")
        return 0
    if args.command == "begin-run":
        reasons = [
            line.strip().removeprefix("rethink:").strip()
            for line in sys.stdin.read().splitlines()
            if line.strip()
        ]
        try:
            begin_run(session_dir, reasons, now)
        except OSError as exc:
            print(f"[rethink] could not record the run: {exc}", file=sys.stderr)
            return 1
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
