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
