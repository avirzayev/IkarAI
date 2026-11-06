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
