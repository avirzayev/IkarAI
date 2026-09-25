"""Pre-tick gate — routine state + chores pass with no LLM involvement.

Called every minute by `run_hourly_cycle.sh` before it considers waking
the LLM. See docs/CHORES.md §3 for the full spec.

    1. `game.py state --json` (run as a subprocess, so this works
       regardless of game.py's internals) -> session/state.json.
       Failure here means everything downstream is unknown, so it wakes
       the LLM immediately with reason "state unavailable".
    2. Runs chores.py in-process (`chores.py run`) -> session/chores_last.json.
    3. Evaluates wake conditions; if any apply, writes them (one per
       line, `light:`/`heavy:` prefixed) to session/wake_reasons.txt and
       exits 10.
    4. Otherwise computes next_wake (earliest construction/queue
       completion + 2 min, clamped to [+10min, +3h]), writes it via
       kb.py's own `wake` command (so the Daily Log header updates the
       same way a cycle does), and exits 0.

Any unexpected exception is caught at the top level: printed, recorded
as a heavy wake reason, and treated as exit 10 — fail open, never
silently block the ticker.
"""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import chores  # noqa: E402
import kb  # noqa: E402
import notion_client as nc  # noqa: E402
from config import load_config  # noqa: E402

MIN_WAKE_MINUTES = 10
MAX_WAKE_MINUTES = 180
WAKE_LEAD_MINUTES = 2
LLM_CYCLE_STALE_HOURS = 6
REVIEW_STALE_HOURS = 24


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _write_reasons(session_dir: Path, reasons: list) -> None:
    text = "\n".join(reasons)
    (session_dir / "wake_reasons.txt").write_text(text + "\n" if reasons else "")


def _read_timestamp(path: Path):
    if not path.exists():
        return None
    text = path.read_text().strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# step 1 — state
# ---------------------------------------------------------------------------


def _run_game_state(project_root: Path, session_dir: Path) -> dict | None:
    game_py = project_root / "scripts" / "game.py"
    try:
        proc = subprocess.run(
            [sys.executable, str(game_py), "state", "--json"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"state unavailable: could not run game.py state: {exc}")
        return None
    if proc.returncode != 0:
        print(f"state unavailable: game.py state exited {proc.returncode}: {proc.stderr.strip()}")
        return None
    try:
        state = json.loads(proc.stdout)
    except ValueError as exc:
        print(f"state unavailable: non-JSON output from game.py state: {exc}")
        return None
    (session_dir / "state.json").write_text(json.dumps(state, indent=2))
    return state


# ---------------------------------------------------------------------------
# step 3 — wake conditions
# ---------------------------------------------------------------------------


def _human_required_reasons(token: str, db_id: str) -> list:
    if not db_id:
        return []
    reasons = []
    for row in nc.query_database(token, db_id):
        blocks = nc.get_block_children(token, row["id"])
        if len(blocks) > 2:  # Question heading + question text + a reply
            reasons.append(f"heavy: human required row answered: {nc.page_title(row)}")
    return reasons


def _alert_reasons(state: dict) -> list:
    reasons = []
    alerts = state.get("alerts") or {}
    if alerts.get("under_attack"):
        reasons.append("heavy: under attack")
    unread = alerts.get("unread_messages")
    if unread:
        reasons.append(f"heavy: {unread} unread message(s)")
    return reasons


def _construction_reasons(state: dict) -> list:
    reasons = []
    for city in state.get("cities") or []:
        if not city.get("construction"):
            label = city.get("name") or city.get("id") or "?"
            reasons.append(f"heavy: city {label} has no construction in progress")
    return reasons


def _chores_result_reasons(result: dict) -> list:
    reasons = []
    for f in result.get("failures") or []:
        reasons.append(f"heavy: chore failed: {f.get('chore')} ({f.get('reason')})")
    for name in result.get("disabled") or []:
        reasons.append(f"heavy: chore auto-disabled: {name}")
    for name in result.get("pending_review") or []:
        reasons.append(f"light: chore pending review/promotion: {name}")
    return reasons


def _staleness_reasons(session_dir: Path, now_utc: datetime) -> list:
    reasons = []
    last_cycle = _read_timestamp(session_dir / "last_llm_cycle.txt")
    if last_cycle is None or now_utc - last_cycle > timedelta(hours=LLM_CYCLE_STALE_HOURS):
        reasons.append("heavy: last LLM cycle over 6h ago")
    last_review = _read_timestamp(session_dir / "last_review.txt")
    if last_review is None or now_utc - last_review > timedelta(hours=REVIEW_STALE_HOURS):
        reasons.append("light: daily chore review due")
    return reasons


# ---------------------------------------------------------------------------
# step 4 — next_wake
# ---------------------------------------------------------------------------


def _next_wake_minutes(state: dict) -> int:
    candidates = []
    for item in state.get("queue") or []:
        done_in = item.get("done_in_min")
        if isinstance(done_in, (int, float)) and not isinstance(done_in, bool):
            candidates.append(done_in)
    for city in state.get("cities") or []:
        for item in city.get("construction") or []:
            done_in = item.get("done_in_min")
            if isinstance(done_in, (int, float)) and not isinstance(done_in, bool):
                candidates.append(done_in)
    if not candidates:
        return MAX_WAKE_MINUTES
    minutes = min(candidates) + WAKE_LEAD_MINUTES
    return max(MIN_WAKE_MINUTES, min(MAX_WAKE_MINUTES, round(minutes)))


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def run(project_root: Path, now: datetime | None = None) -> int:
    project_root = Path(project_root)
    session_dir = project_root / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    env_path = project_root / ".env"

    try:
        config = load_config(env_path)
    except (KeyError, FileNotFoundError) as exc:
        print(f"pretick error: config unavailable: {exc}")
        _write_reasons(session_dir, [f"heavy: pretick error: config unavailable: {exc}"])
        return 10

    tz = ZoneInfo(config.timezone)
    now_utc = now if now is not None else datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    try:
        state = _run_game_state(project_root, session_dir)
        if state is None:
            _write_reasons(session_dir, ["heavy: state unavailable"])
            return 10

        chores_rc = chores.main(["run"], project_root=project_root, now=now_utc)
        if chores_rc != 0:
            print(f"chores.py run exited {chores_rc}")
        chores_result = {}
        result_path = session_dir / "chores_last.json"
        if result_path.exists():
            try:
                chores_result = json.loads(result_path.read_text())
            except ValueError:
                chores_result = {}

        reasons = []
        reasons.extend(_human_required_reasons(config.notion_token, config.notion_human_required_db_id))
        reasons.extend(_alert_reasons(state))
        reasons.extend(_chores_result_reasons(chores_result))
        reasons.extend(_construction_reasons(state))
        reasons.extend(_staleness_reasons(session_dir, now_utc))

        if reasons:
            _write_reasons(session_dir, reasons)
            print("\n".join(reasons))
            return 10

        _write_reasons(session_dir, [])
        wake_minutes = _next_wake_minutes(state)
        kb_rc = kb.main(["wake", f"+{wake_minutes}"], project_root=project_root, now=now_utc)
        if kb_rc != 0:
            print("warning: kb.py wake did not exit cleanly — next_wake may be stale")
        print(f"OK pretick: no wake reasons, next_wake in {wake_minutes}m")
        return 0
    except Exception as exc:  # fail open — never silently block the ticker
        print(f"pretick error: {exc}")
        _write_reasons(session_dir, [f"heavy: pretick error: {exc}"])
        return 10


def main(argv: list | None = None) -> int:
    del argv  # pretick takes no arguments
    return run(PROJECT_ROOT)


if __name__ == "__main__":
    sys.exit(main())
