"""One-shot Ikariam game CLI.

Wraps ikariam_client + session_store so a single command does an entire
game HTTP call (nav or action) and prints a compact, token-cheap summary
instead of forcing the caller to write a throwaway python3 -c script and
then dig through raw JSON by hand.

Usage (from repo root):
    python3 scripts/game.py session [--city ID]
    python3 scripts/game.py set-cookie [--cookie VALUE]   (or pipe via stdin)
    python3 scripts/game.py nav <view> [key=value ...] [--city ID] [--grep REGEX] [--max N]
    python3 scripts/game.py action <action> [function] [key=value ...] [--grep REGEX] [--max N]

Every call that receives an HTTP response persists the rotated
actionRequest token *before* printing anything, saves the raw response to
session/responses/<label>.json and session/responses/last.json, and prints
that path at the end so the caller can jq/grep it if it genuinely needs
more detail than the summary gives.
"""

import html
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import ikariam_client as ic  # noqa: E402
import session_store as ss  # noqa: E402
from config import parse_env_file  # noqa: E402

DEFAULT_CITY_ID = "355955"

RESOURCE_LABELS = [
    ("resource", "wood"),
    ("1", "wine"),
    ("2", "marble"),
    ("3", "crystal"),
    ("4", "sulfur"),
]

NOISE_KEY_TERMS = (
    "ingamead",
    "chat",
    "friend",
    "advisor",
    "citydropdownmenu",
    "cityleftmenu",
)


# --------------------------------------------------------------------------
# Small pure formatting helpers
# --------------------------------------------------------------------------


def _fmt_num(value) -> str:
    if value is None:
        return "?"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return str(int(round(value)))
    return str(value)


def _fmt_signed(value) -> str:
    rounded = int(round(value))
    sign = "+" if rounded >= 0 else ""
    return f"{sign}{rounded}"


def summarize_header(header: dict) -> str:
    """One compact line from updateGlobalData.headerData."""
    if not header:
        return ""

    segments = []

    gold = header.get("gold")
    if gold is not None:
        seg = f"gold={_fmt_num(gold)}"
        income = header.get("income")
        if income is not None:
            upkeep = header.get("upkeep") or 0
            net = income - upkeep
            if round(net) != 0:
                seg += f" ({_fmt_signed(net)}/h)"
        segments.append(seg)

    current = header.get("currentResources") or {}
    maxres = header.get("maxResources") or {}
    res_prod = header.get("resourceProduction")
    trade_prod = header.get("tradegoodProduction")
    produced = header.get("producedTradegood")
    produced_key = str(produced) if produced is not None else None

    res_bits = []
    for key, label in RESOURCE_LABELS:
        if key not in current:
            continue
        bit = f"{label} {_fmt_num(current[key])}"
        if key == "resource" and key in maxres:
            bit += f"/{_fmt_num(maxres[key])}"
        rate = None
        if key == "resource":
            rate = res_prod
        elif produced_key == key:
            rate = trade_prod
        if rate is not None and round(rate) != 0:
            bit += f" ({_fmt_signed(rate)}/h)"
        res_bits.append(bit)
    if res_bits:
        segments.append(" ".join(res_bits))

    citizens = current.get("citizens")
    population = current.get("population")
    if citizens is not None or population is not None:
        bits = []
        if citizens is not None:
            bits.append(f"citizens {_fmt_num(citizens)}")
        if population is not None:
            bits.append(f"pop {_fmt_num(population)}")
        segments.append(" ".join(bits))

    free_t = header.get("freeTransporters")
    max_t = header.get("maxTransporters")
    if free_t is not None and max_t is not None:
        segments.append(f"transporters {_fmt_num(free_t)}/{_fmt_num(max_t)}")

    ap = header.get("maxActionPoints")
    if ap is not None:
        segments.append(f"AP {_fmt_num(ap)}")

    return " | ".join(segments)


def _remaining_minutes(target_epoch, now) -> int | None:
    if now is None or target_epoch is None:
        return None
    try:
        remaining = (float(target_epoch) - float(now)) / 60
    except (TypeError, ValueError):
        return None
    return max(0, round(remaining))


def summarize_queue(queue_eta_raw, now) -> str | None:
    """queueETA is a JSON-encoded string: {"version": ..., "items": [...]}."""
    if not queue_eta_raw:
        return None
    data = queue_eta_raw
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (TypeError, ValueError):
            return None
    if not isinstance(data, dict):
        return None
    items = data.get("items")
    if not items:
        return None

    bits = []
    for item in items:
        if not isinstance(item, dict):
            bits.append(str(item))
            continue
        label = item.get("type") or item.get("name") or item.get("action") or "task"
        target = None
        for key in ("time", "endTime", "end", "completed"):
            if item.get(key) is not None:
                target = item.get(key)
                break
        remaining = _remaining_minutes(target, now)
        if remaining is not None:
            bits.append(f"{label} {remaining}m")
        else:
            bits.append(str(label))
    return "queue: " + ", ".join(bits)


def summarize_construction(background: dict, now) -> str | None:
    """Building-in-construction info from backgroundData's own countdown
    fields (underConstruction / startUpgradeTime / endUpgradeTime)."""
    if not isinstance(background, dict):
        return None
    under = background.get("underConstruction")
    try:
        under_idx = int(under)
    except (TypeError, ValueError):
        return None
    if under_idx < 0:
        return None
    end = background.get("endUpgradeTime")
    if end in (None, -1):
        return None
    remaining = _remaining_minutes(end, now)
    if remaining is None:
        return None
    positions = background.get("position") or []
    label = "?"
    if 0 <= under_idx < len(positions) and isinstance(positions[under_idx], dict):
        label = positions[under_idx].get("building", "?")
    return f"building: {label}@{under_idx} completes in {remaining}m"


def summarize_city_positions(background: dict) -> str | None:
    """`pos:building Llevel` for each occupied slot, city view only."""
    if not isinstance(background, dict):
        return None
    positions = background.get("position")
    if not positions:
        return None
    try:
        under_idx = int(background.get("underConstruction"))
    except (TypeError, ValueError):
        under_idx = -1

    bits = []
    for idx, slot in enumerate(positions):
        if not isinstance(slot, dict) or slot.get("buildingId") is None:
            continue
        building = slot.get("building", "?")
        level = slot.get("level", "?")
        bit = f"{idx}:{building} L{level}"
        if idx == under_idx:
            bit += " (constructing)"
        bits.append(bit)
    if not bits:
        return None
    return " | ".join(bits)


def status_lines(global_data: dict) -> list:
    """All status-block lines derived from a single updateGlobalData payload."""
    if not isinstance(global_data, dict):
        return []
    lines = []
    header_line = summarize_header(global_data.get("headerData") or {})
    if header_line:
        lines.append(header_line)

    now = global_data.get("time")
    queue_line = summarize_queue(global_data.get("queueETA"), now)
    if queue_line:
        lines.append(queue_line)

    background = global_data.get("backgroundData")
    if isinstance(background, dict):
        constr_line = summarize_construction(background, now)
        if constr_line:
            lines.append(constr_line)
        positions_line = summarize_city_positions(background)
        if positions_line:
            lines.append(positions_line)

    return lines


# --------------------------------------------------------------------------
# HTML / view-summary helpers
# --------------------------------------------------------------------------

_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")
_BLOCK_BREAK_RE = re.compile(r"(?i)<\s*(br|/p|/div|/tr|/li|/h[1-6]|/table)\s*/?\s*>")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")


def html_to_text(raw: str) -> str:
    """Strip tags/scripts/styles, unescape entities, collapse whitespace,
    roughly one line per block element."""
    if not raw:
        return ""
    text = _SCRIPT_STYLE_RE.sub(" ", raw)
    text = _BLOCK_BREAK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    lines = []
    for line in text.splitlines():
        collapsed = _WS_RE.sub(" ", line).strip()
        if collapsed:
            lines.append(collapsed)
    return "\n".join(lines)


def _is_blob(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if re.match(r"^(https?://|data:)", stripped):
        return True
    if len(stripped) > 200 and " " not in stripped:
        return True
    # Serialized JSON dumped straight into a template field (e.g. new_js_params)
    # is data, not display text — drop it rather than truncate it mid-object.
    if len(stripped) > 200 and stripped[0] in "{[":
        return True
    return False


def _is_noise_key(key: str) -> bool:
    lowered = key.lower()
    return any(term in lowered for term in NOISE_KEY_TERMS)


def _template_value_text(value):
    if isinstance(value, dict):
        text = value.get("text")
        if text is None:
            return None
        return str(text)
    if isinstance(value, bool):
        return None
    if isinstance(value, (str, int, float)):
        return str(value)
    return None


def summarize_template_data(template: dict) -> list:
    """`key: text` lines from updateTemplateData, noise dropped."""
    if not isinstance(template, dict):
        return []
    lines = []
    for key, value in template.items():
        if _is_noise_key(key):
            continue
        text = _template_value_text(value)
        if text is None:
            continue
        text = html_to_text(text).replace("\n", " ").strip()
        text = _WS_RE.sub(" ", text)
        if not text or text == "0":
            continue
        if _is_blob(text):
            continue
        if len(text) > 160:
            text = text[:159] + "…"
        lines.append(f"{key}: {text}")
    return lines


def response_dict(response: list) -> dict:
    """[[name, payload], ...] -> {name: payload}."""
    data = {}
    if not isinstance(response, list):
        return data
    for item in response:
        if isinstance(item, list) and len(item) >= 2:
            data[item[0]] = item[1]
    return data


def build_view_summary(data: dict, path, max_chars: int = 2500, grep: str | None = None) -> str:
    lines = []
    change_view = data.get("changeView")
    if isinstance(change_view, list) and len(change_view) >= 2 and isinstance(change_view[1], str):
        lines.extend(html_to_text(change_view[1]).splitlines())
    lines.extend(summarize_template_data(data.get("updateTemplateData")))
    lines = [ln for ln in lines if ln.strip()]

    if grep:
        pattern = re.compile(grep, re.IGNORECASE)
        lines = [ln for ln in lines if pattern.search(ln)]

    text = "\n".join(lines)
    if max_chars is not None and len(text) > max_chars:
        truncated = text[:max_chars].rstrip()
        text = f"{truncated}\n… [truncated, full response: {path}]"
    return text


def extract_feedback_lines(data: dict) -> list:
    lines = []
    error = data.get("error")
    if error:
        lines.append(f"ERROR: {error}")
    feedback = data.get("provideFeedback")
    if isinstance(feedback, list):
        for item in feedback:
            text = item.get("text") if isinstance(item, dict) else item
            if not text:
                continue
            clean = html_to_text(str(text)).replace("\n", " ").strip()
            if clean:
                lines.append(f"FEEDBACK: {clean}")
    return lines


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------

_FLAG_NAMES = {"--city": "city", "--grep": "grep", "--max": "max"}


def _split_flags(tokens: list) -> tuple:
    """Pull --city/--grep/--max VALUE pairs out of a token list."""
    options = {}
    remaining = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _FLAG_NAMES:
            if i + 1 >= len(tokens):
                raise ValueError(f"{tok} requires a value")
            options[_FLAG_NAMES[tok]] = tokens[i + 1]
            i += 2
            continue
        remaining.append(tok)
        i += 1
    return remaining, options


def parse_kv_args(tokens: list) -> dict:
    """["cityId=355955", "wood=17"] -> {"cityId": "355955", "wood": "17"}."""
    result = {}
    for tok in tokens:
        if "=" not in tok:
            raise ValueError(f"expected key=value, got: {tok}")
        key, _, value = tok.partition("=")
        result[key] = value
    return result


def parse_action_args(remaining: list) -> tuple:
    """action name, optional function positional, then key=value params."""
    if not remaining:
        raise ValueError("action requires an action name")
    action = remaining[0]
    rest = remaining[1:]
    function = None
    if rest and "=" not in rest[0]:
        function = rest[0]
        rest = rest[1:]
    params = parse_kv_args(rest)
    return action, function, params


# --------------------------------------------------------------------------
# Env / session plumbing
# --------------------------------------------------------------------------


def _env_values(env_path: Path) -> dict:
    if not env_path.exists():
        return {}
    return parse_env_file(env_path)


def _server(env_path: Path) -> str:
    return _env_values(env_path).get("IKARIAM_SERVER", "")


def _city_id(env_path: Path, override: str | None) -> str:
    if override:
        return override
    return _env_values(env_path).get("IKARIAM_CITY_ID") or DEFAULT_CITY_ID


def _save_response(session_dir: Path, label: str, response) -> Path:
    responses_dir = session_dir / "responses"
    responses_dir.mkdir(parents=True, exist_ok=True)
    safe_label = re.sub(r"[^A-Za-z0-9_.-]", "_", label) or "response"
    body = json.dumps(response, indent=2)
    path = responses_dir / f"{safe_label}.json"
    path.write_text(body)
    (responses_dir / "last.json").write_text(body)
    return path


# --------------------------------------------------------------------------
# Subcommands
# --------------------------------------------------------------------------


def _cmd_set_cookie(rest: list, session_dir: Path, env_path: Path) -> int:
    remaining, _ = _split_flags(rest)
    cookie = None
    i = 0
    while i < len(remaining):
        if remaining[i] == "--cookie" and i + 1 < len(remaining):
            cookie = remaining[i + 1]
            i += 2
            continue
        i += 1
    if cookie is None:
        cookie = sys.stdin.read().strip()
    if not cookie:
        print("SESSION_INVALID: no cookie provided")
        return 2

    ss.write_cookie(session_dir, cookie)

    server = _server(env_path)
    if not server:
        print("SESSION_INVALID: IKARIAM_SERVER not configured in .env")
        return 2

    try:
        token = ic.bootstrap_action_request(server, cookie)
    except ic.SessionInvalidError as exc:
        print(f"SESSION_INVALID: {exc}")
        return 2

    ss.write_action_request(session_dir, token)
    print("COOKIE_SET")
    return 0


def _cmd_session(rest: list, session_dir: Path, env_path: Path) -> int:
    remaining, options = _split_flags(rest)
    del remaining  # session takes no positionals
    city_id = _city_id(env_path, options.get("city"))
    server = _server(env_path)

    try:
        cookie = ss.read_cookie(session_dir)
        token = ss.read_action_request(session_dir)
    except ss.SessionError as exc:
        print(f"SESSION_INVALID: {exc}")
        return 2

    try:
        response = ic.call_nav(
            server, cookie, token, "townHall",
            {"cityId": city_id, "position": "0", "currentCityId": city_id},
        )
        new_token = ic.extract_action_request(response)
    except ic.SessionInvalidError as exc:
        print(f"SESSION_INVALID: {exc}")
        return 2

    ss.write_action_request(session_dir, new_token)
    path = _save_response(session_dir, "session", response)

    print("SESSION_OK")
    data = response_dict(response)
    for line in status_lines(data.get("updateGlobalData") or {}):
        print(line)
    print(f"[full response: {path}]")
    return 0


def _cmd_nav(rest: list, session_dir: Path, env_path: Path) -> int:
    remaining, options = _split_flags(rest)
    if not remaining:
        print("SESSION_INVALID: nav requires a view name")
        return 2
    view = remaining[0]
    try:
        kv = parse_kv_args(remaining[1:])
    except ValueError as exc:
        print(f"SESSION_INVALID: {exc}")
        return 2

    city_id = _city_id(env_path, options.get("city"))
    kv.setdefault("cityId", city_id)
    kv.setdefault("currentCityId", city_id)
    server = _server(env_path)

    try:
        cookie = ss.read_cookie(session_dir)
        token = ss.read_action_request(session_dir)
    except ss.SessionError as exc:
        print(f"SESSION_INVALID: {exc}")
        return 2

    try:
        response = ic.call_nav(server, cookie, token, view, kv)
        new_token = ic.extract_action_request(response)
    except ic.SessionInvalidError as exc:
        print(f"SESSION_INVALID: {exc}")
        return 2

    ss.write_action_request(session_dir, new_token)
    path = _save_response(session_dir, view, response)

    data = response_dict(response)
    for line in status_lines(data.get("updateGlobalData") or {}):
        print(line)
    max_chars = int(options.get("max", 2500))
    summary = build_view_summary(data, path, max_chars=max_chars, grep=options.get("grep"))
    if summary:
        print(summary)
    print(f"[full response: {path}]")
    return 0


def _cmd_action(rest: list, session_dir: Path, env_path: Path) -> int:
    remaining, options = _split_flags(rest)
    try:
        action, function, kv = parse_action_args(remaining)
    except ValueError as exc:
        print(f"SESSION_INVALID: {exc}")
        return 2

    server = _server(env_path)

    try:
        cookie = ss.read_cookie(session_dir)
        token = ss.read_action_request(session_dir)
    except ss.SessionError as exc:
        print(f"SESSION_INVALID: {exc}")
        return 2

    try:
        response = ic.call_action(server, cookie, token, action, function or "", kv)
        new_token = ic.extract_action_request(response)
    except ic.SessionInvalidError as exc:
        print(f"SESSION_INVALID: {exc}")
        return 2

    ss.write_action_request(session_dir, new_token)
    label = f"{action}_{function}" if function else action
    path = _save_response(session_dir, label, response)

    data = response_dict(response)
    for line in extract_feedback_lines(data):
        print(line)
    for line in status_lines(data.get("updateGlobalData") or {}):
        print(line)
    max_chars = int(options.get("max", 2500))
    summary = build_view_summary(data, path, max_chars=max_chars, grep=options.get("grep"))
    if summary:
        print(summary)
    print(f"[full response: {path}]")
    return 0


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main(argv=None, project_root: Path | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(project_root) if project_root is not None else PROJECT_ROOT

    if not args:
        print("usage: game.py <session|set-cookie|nav|action> ...")
        return 2

    sub, rest = args[0], args[1:]

    session_dir = root / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "responses").mkdir(parents=True, exist_ok=True)
    env_path = root / ".env"

    try:
        if sub == "set-cookie":
            return _cmd_set_cookie(rest, session_dir, env_path)
        if sub == "session":
            return _cmd_session(rest, session_dir, env_path)
        if sub == "nav":
            return _cmd_nav(rest, session_dir, env_path)
        if sub == "action":
            return _cmd_action(rest, session_dir, env_path)
    except ValueError as exc:
        print(f"SESSION_INVALID: {exc}")
        return 2

    print(f"unknown subcommand: {sub}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
