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
    python3 scripts/game.py cities [--city ID]
    python3 scripts/game.py state [--json] [--city ID]
    python3 scripts/game.py batch [--keep-going]          (reads nav/action lines from stdin)
    python3 scripts/game.py find PATTERN [--file PATH]    (default file: session/responses/last.json)

`state` gathers every own city (like `cities`), extracts the docs/CHORES.md
§1 fields (any field it can't reliably derive is `null` — see build_state()/
build_city_state() docstrings for exactly which and why), and saves the
result to session/responses/../state.json (session/state.json) in addition
to printing it.

`batch` runs a sequence of `nav`/`action` commands (one per stdin line, same
argument syntax as the CLI) in a single process, printing `## <line>` before
each command's usual output; it stops at the first failing line unless
--keep-going is given, and exits non-zero if any line failed.

`find` replaces ad-hoc `python3 -c` digging through session/responses/*.json:
it walks the last saved raw response (or --file PATH) and prints every
`dotted.path = value` line (HTML stripped, values truncated to 160 chars)
whose path or value matches PATTERN case-insensitively, up to 40 lines.

`nav`/`action` accept Action Catalog names directly: a leading `view:` on
the nav view is stripped (`view:researchAdvisor` -> `researchAdvisor`),
and an action of the form `action:Name` or `action:Name:function` is split
into action/function (`action:IslandScreen:workerPlan` -> action
IslandScreen, function workerPlan).

Every call that receives an HTTP response persists the rotated
actionRequest token *before* printing anything, saves the raw response to
session/responses/<label>.json and session/responses/last.json, and prints
that path at the end so the caller can jq/grep it if it genuinely needs
more detail than the summary gives.
"""

import html
import json
import re
import shlex
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import ikariam_client as ic  # noqa: E402
import session_store as ss  # noqa: E402
from config import parse_env_file  # noqa: E402

DEFAULT_CITY_ID = "355955"
# wine_hours_left value for a cellar that isn't shrinking (JSON has no inf).
WINE_NOT_DRAINING = 9999

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


def _to_float(value):
    """Best-effort float conversion. The game API sometimes serializes
    numeric fields (e.g. headerData.gold) as strings instead of numbers."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_num(value) -> str:
    """Round any numeric-looking value (float, int, or numeric string —
    e.g. headerData.gold arrives as `"29479.83975277733"`) to an int."""
    if value is None:
        return "?"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return str(int(round(value)))
    as_float = _to_float(value)
    if as_float is not None:
        return str(int(round(as_float)))
    return str(value)


def _fmt_signed(value) -> str:
    rounded = int(round(value))
    sign = "+" if rounded >= 0 else ""
    return f"{sign}{rounded}"


def _round_or_none(value):
    """Best-effort round-to-int, `None` on anything unparseable (bools are
    intentionally NOT numbers here — same rule as `_to_float`)."""
    as_float = _to_float(value)
    if as_float is None:
        return None
    return int(round(as_float))


def _parse_signed_int(text) -> int | None:
    """"+1,658" / "-4,932" / "308" -> int. `None` on anything unparseable."""
    if text is None:
        return None
    cleaned = str(text).strip().replace(",", "").replace("+", "")
    if not cleaned:
        return None
    try:
        return int(round(float(cleaned)))
    except ValueError:
        return None


def _parse_float(text) -> float | None:
    if text is None:
        return None
    cleaned = str(text).strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_percent_int(text) -> int | None:
    """"3%" -> 3."""
    if text is None:
        return None
    cleaned = str(text).strip().rstrip("%").strip()
    if not cleaned:
        return None
    try:
        return int(round(float(cleaned)))
    except ValueError:
        return None


def _clean_coords(raw) -> str | None:
    """"[53:67] " -> "53:67"."""
    if raw is None:
        return None
    cleaned = str(raw).strip().strip("[]").strip()
    return cleaned or None


def _find_position(background: dict, building_slug: str):
    """First (idx, slot) in backgroundData.position whose `building` slug
    matches (e.g. "tavern") — internal slugs are stable across locales,
    unlike the display `name`. `None` if not built in this city."""
    if not isinstance(background, dict):
        return None
    positions = background.get("position")
    if not positions:
        return None
    for idx, slot in enumerate(positions):
        if isinstance(slot, dict) and slot.get("building") == building_slug:
            return idx, slot
    return None


def summarize_header(header: dict) -> str:
    """One compact line from updateGlobalData.headerData."""
    if not header:
        return ""

    segments = []

    gold = header.get("gold")
    if gold is not None:
        seg = f"gold={_fmt_num(gold)}"
        income = _to_float(header.get("income"))
        if income is not None:
            upkeep = _to_float(header.get("upkeep")) or 0
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


def _parse_queue_eta(queue_eta_raw) -> list:
    """queueETA is a JSON-encoded string: {"version": ..., "items": [...]}.
    Returns the raw `items` list (possibly empty)."""
    if not queue_eta_raw:
        return []
    data = queue_eta_raw
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (TypeError, ValueError):
            return []
    if not isinstance(data, dict):
        return []
    items = data.get("items")
    return items if isinstance(items, list) else []


def summarize_queue(queue_eta_raw, now) -> str | None:
    """Real items are empire-wide, keyed by `cityName` + `timestamp` (not a
    task name/end-time pair), so render `<cityName> in <N>m`."""
    items = _parse_queue_eta(queue_eta_raw)
    if not items:
        return None

    bits = []
    for item in items:
        if not isinstance(item, dict):
            bits.append(str(item))
            continue
        label = item.get("cityName") or item.get("type") or item.get("name") or item.get("action") or "task"
        target = None
        for key in ("timestamp", "time", "endTime", "end", "completed"):
            if item.get(key) is not None:
                target = item.get(key)
                break
        remaining = _remaining_minutes(target, now)
        if remaining is not None:
            bits.append(f"{label} in {remaining}m")
        else:
            bits.append(str(label))
    return "queue: " + ", ".join(bits)


def parse_queue_items(queue_eta_raw, now) -> list:
    """Structured variant of summarize_queue: [{"city": ..., "done_in_min": ...}, ...]."""
    items = _parse_queue_eta(queue_eta_raw)
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = item.get("cityName") or item.get("type") or item.get("name") or item.get("action")
        target = None
        for key in ("timestamp", "time", "endTime", "end", "completed"):
            if item.get(key) is not None:
                target = item.get(key)
                break
        result.append({"city": label, "done_in_min": _remaining_minutes(target, now)})
    return result


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
        slot = positions[under_idx]
        label = slot.get("name") or slot.get("building", "?")
    return f"building: {label}@{under_idx} completes in {remaining}m"


def summarize_city_positions(background: dict) -> str | None:
    """`idx:Name Llevel` for each occupied slot, city view only. Real slots
    carry `name`/`level`/`buildingId`/`isBusy`/`canUpgrade` (a display name
    like "Town Hall"); older/other shapes only have `building` (an internal
    slug like "townhall") — prefer `name`, fall back to `building`. Empty
    ground slots (buildingId None) are skipped."""
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
        name = slot.get("name") or slot.get("building", "?")
        level = slot.get("level", "?")
        bit = f"{idx}:{name} L{level}"
        markers = []
        if idx == under_idx:
            markers.append("constructing")
        elif slot.get("isBusy"):
            markers.append("busy")
        if slot.get("canUpgrade"):
            markers.append("upgradable")
        if markers:
            bit += f" ({', '.join(markers)})"
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


_CITY_STATS_COUNT_KEYS = (
    ("citizens", "CitizenCount"),
    ("wood", "ResourceWorkerCount"),
    ("special", "SpecialWorkerCount"),
    ("sci", "ScientistCount"),
    ("priests", "PriestCount"),
)


def summarize_city_stats(template: dict) -> str | None:
    """Dedicated `city stats:` line for townHall-shaped updateTemplateData
    (growth, happiness, corruption, wine bonus, income, worker counts).

    These js_TownHall* keys are exactly what the agent kept digging for
    with jq/python3 -c — put them in one line up front, printed *before*
    the general (truncatable) template dump, so they survive summarization
    even when unrelated keys would otherwise crowd them out."""
    if not isinstance(template, dict):
        return None

    def text_of(key):
        return _template_value_text(template.get(key))

    bits = []

    growth = text_of("js_TownHallPopulationGrowthValue")
    if growth is not None:
        bits.append(f"growth {growth.strip()}")

    happy = text_of("js_TownHallHappinessLargeText") or text_of("js_TownHallHappinessSmallText")
    happy_value = text_of("js_TownHallHappinessLargeValue")
    if happy is not None:
        label = happy
        if happy_value is not None:
            label += f" ({happy_value})"
        bits.append(f"happy {label}")

    corruption = text_of("js_TownHallCorruption")
    if corruption is not None:
        bits.append(f"corruption {corruption}")

    tavern = text_of("js_TownHallSatisfactionOverviewWineBoniTavernBonusValue")
    serve = text_of("js_TownHallSatisfactionOverviewWineBoniServeBonusValue")
    if tavern is not None or serve is not None:
        total = 0
        for part in (tavern, serve):
            if part is None:
                continue
            digits = re.sub(r"[^\d-]", "", part)
            try:
                total += int(digits) if digits else 0
            except ValueError:
                pass
        bits.append(f"wine {_fmt_signed(total)}")

    income = text_of("js_TownHallIncomeGoldValue")
    if income is not None:
        bits.append(f"income {income.strip()}")

    counts = []
    for label, key in _CITY_STATS_COUNT_KEYS:
        value = template.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            counts.append(f"{label} {_fmt_num(value)}")
    if counts:
        bits.append(" ".join(counts))

    if not bits:
        return None
    return "city stats: " + " | ".join(bits)


def summarize_worker_allocation(template: dict) -> str | None:
    """Compact `workers:` line for a townHall-shaped updateTemplateData —
    current wood/tradegood worker counts plus their hourly production, when
    present. IslandScreen:workerPlan's success response is townHall-shaped
    too (same js_TownHallPopulationGraph* keys — see Action Catalog notes),
    so this line shows up automatically after a worker reallocation without
    a second call. No per-resource "max" cap is included: none of the
    captured real responses carry one (the caps only appear as <input
    max=...> attributes in the workerPlan popup's rendered HTML, which no
    successful saved sample includes)."""
    if not isinstance(template, dict):
        return None

    wood = template.get("ResourceWorkerCount")
    tradegood = template.get("SpecialWorkerCount")
    wood_prod = _parse_signed_int(_template_value_text(template.get("js_TownHallPopulationGraphWoodProduction")))
    trade_prod = _parse_signed_int(_template_value_text(template.get("js_TownHallPopulationGraphTradeGoodProduction")))

    bits = []
    if isinstance(wood, (int, float)) and not isinstance(wood, bool):
        bit = f"wood {_fmt_num(wood)}"
        if wood_prod is not None:
            bit += f" ({_fmt_signed(wood_prod)}/h)"
        bits.append(bit)
    if isinstance(tradegood, (int, float)) and not isinstance(tradegood, bool):
        bit = f"tradegood {_fmt_num(tradegood)}"
        if trade_prod is not None:
            bit += f" ({_fmt_signed(trade_prod)}/h)"
        bits.append(bit)
    if not bits:
        return None
    return "workers: " + " ".join(bits)


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


def looks_like_bad_view(data: dict) -> bool:
    """True when a nav response has no changeView AND an empty
    updateTemplateData ("" or {}) — the game's way of saying the view name
    or params it received were invalid (e.g. a `view:` prefix was passed
    straight through)."""
    template = data.get("updateTemplateData")
    has_template = isinstance(template, dict) and bool(template)
    return not has_template and not data.get("changeView")


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
# Multi-city helpers (`cities` subcommand)
# --------------------------------------------------------------------------

_TRADEGOOD_NAMES = {"1": "wine", "2": "marble", "3": "crystal", "4": "sulfur"}


def _tradegood_name(code) -> str:
    if code is None:
        return "?"
    return _TRADEGOOD_NAMES.get(str(code), str(code))


def own_cities(header: dict) -> list:
    """Own-city entries from headerData.cityDropdownMenu — real shape is
    {"city_<id>": {"id", "name", "coords", "tradegood", "relationship"}, ...}
    plus "additionalInfo"/"selectedCity" string entries to ignore. Sorted by
    id so `cities` output is stable across runs."""
    menu = header.get("cityDropdownMenu") if isinstance(header, dict) else None
    if not isinstance(menu, dict):
        return []
    cities = [
        entry
        for entry in menu.values()
        if isinstance(entry, dict) and entry.get("relationship") == "ownCity"
    ]
    cities.sort(key=lambda c: c.get("id") or 0)
    return cities


def summarize_city_block(city: dict, data: dict, max_chars: int = 600) -> str:
    """One `== Name (id) coords tradegood=X ==` block: header line (that
    city's own resources), city stats, queue/construction if any."""
    global_data = data.get("updateGlobalData") or {}
    header = global_data.get("headerData") or {}
    now = global_data.get("time")

    name = city.get("name", "?")
    city_id = city.get("id", "?")
    coords = (city.get("coords") or "").strip()
    tradegood = _tradegood_name(city.get("tradegood"))

    lines = [f"== {name} ({city_id}) {coords} tradegood={tradegood} =="]

    header_line = summarize_header(header)
    if header_line:
        lines.append(header_line)

    stats_line = summarize_city_stats(data.get("updateTemplateData") or {})
    if stats_line:
        lines.append(stats_line)

    queue_line = summarize_queue(global_data.get("queueETA"), now)
    if queue_line:
        lines.append(queue_line)

    background = global_data.get("backgroundData")
    if isinstance(background, dict):
        constr_line = summarize_construction(background, now)
        if constr_line:
            lines.append(constr_line)

    text = "\n".join(lines)
    if max_chars is not None and len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------

_FLAG_NAMES = {"--city": "city", "--grep": "grep", "--max": "max", "--file": "file"}


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


def _strip_view_prefix(view: str) -> str:
    """Action Catalog names views like `view:researchAdvisor`; the game's
    view= param wants just `researchAdvisor` — passing the prefix through
    sends an invalid view and gets back an empty response."""
    if view.startswith("view:"):
        return view[len("view:"):]
    return view


def parse_action_args(remaining: list) -> tuple:
    """action name, optional function positional, then key=value params.

    The first positional may also be an Action Catalog name: `action:Name`
    or `action:Name:function` (e.g. `action:IslandScreen:workerPlan` or
    `action:BuildNewBuilding`), which is split into action/function. An
    explicit function positional (no `=`) still wins over one parsed from
    the catalog name.
    """
    if not remaining:
        raise ValueError("action requires an action name")
    action = remaining[0]
    rest = remaining[1:]

    function = None
    if action.startswith("action:"):
        action = action[len("action:"):]
        if ":" in action:
            action, function = action.split(":", 1)

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
    view = _strip_view_prefix(remaining[0])
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
    stats_line = summarize_city_stats(data.get("updateTemplateData") or {})
    if stats_line:
        print(stats_line)
    workers_line = summarize_worker_allocation(data.get("updateTemplateData") or {})
    if workers_line:
        print(workers_line)
    max_chars = int(options.get("max", 2500))
    summary = build_view_summary(data, path, max_chars=max_chars, grep=options.get("grep"))
    if summary:
        print(summary)
    if looks_like_bad_view(data):
        print(
            "HINT: empty response (no changeView, no template data) — "
            "the view name or params are probably wrong (nav strips a "
            "leading 'view:' automatically; check cityId/position too)."
        )
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
    stats_line = summarize_city_stats(data.get("updateTemplateData") or {})
    if stats_line:
        print(stats_line)
    workers_line = summarize_worker_allocation(data.get("updateTemplateData") or {})
    if workers_line:
        print(workers_line)
    max_chars = int(options.get("max", 2500))
    summary = build_view_summary(data, path, max_chars=max_chars, grep=options.get("grep"))
    if summary:
        print(summary)
    print(f"[full response: {path}]")
    return 0


def _fetch_own_cities(server: str, cookie: str, token: str, session_dir: Path, start_city_id: str) -> tuple:
    """nav townHall for `start_city_id` (discovers the account's own cities
    from headerData.cityDropdownMenu), then nav townHall for every other own
    city, persisting the rotating token after each call. Returns
    `(cities, fetched)`: `cities` is own_cities()'s (id-sorted) list, and
    `fetched` maps str(city_id) -> (response_dict, response_path). Raises
    ic.SessionInvalidError if any of the calls fail."""
    state = {"token": token}

    def fetch(city_id: str):
        response = ic.call_nav(
            server, cookie, state["token"], "townHall",
            {"cityId": city_id, "position": "0", "currentCityId": city_id},
        )
        state["token"] = ic.extract_action_request(response)
        ss.write_action_request(session_dir, state["token"])
        path = _save_response(session_dir, f"townHall_{city_id}", response)
        return response_dict(response), path

    data, path = fetch(start_city_id)
    header = (data.get("updateGlobalData") or {}).get("headerData") or {}
    cities = own_cities(header)

    fetched = {start_city_id: (data, path)}
    for city in cities:
        city_id = str(city.get("id"))
        if city_id not in fetched:
            fetched[city_id] = fetch(city_id)
    return cities, fetched


def _cmd_cities(rest: list, session_dir: Path, env_path: Path) -> int:
    """Loop over the account's own cities (headerData.cityDropdownMenu),
    printing one compact block per city instead of the caller driving
    `nav townHall --city ID` by hand for each one."""
    remaining, options = _split_flags(rest)
    del remaining  # cities takes no positionals
    server = _server(env_path)
    start_city_id = str(_city_id(env_path, options.get("city")))

    try:
        cookie = ss.read_cookie(session_dir)
        token = ss.read_action_request(session_dir)
    except ss.SessionError as exc:
        print(f"SESSION_INVALID: {exc}")
        return 2

    try:
        cities, fetched = _fetch_own_cities(server, cookie, token, session_dir, start_city_id)
    except ic.SessionInvalidError as exc:
        print(f"SESSION_INVALID: {exc}")
        return 2

    if not cities:
        print("no own cities found in headerData.cityDropdownMenu")
        return 0

    for city in cities:
        city_id = str(city.get("id"))
        city_data, city_path = fetched[city_id]
        print(summarize_city_block(city, city_data))
        print(f"[full response: {city_path}]")

    return 0


# --------------------------------------------------------------------------
# Structured state (`state` subcommand) — docs/CHORES.md §1
# --------------------------------------------------------------------------


def build_city_state(city: dict, data: dict) -> dict:
    """One `cities[]` entry of the docs/CHORES.md §1 state shape, built
    entirely from a single townHall nav response (updateGlobalData +
    updateTemplateData) — the same response `cities`/`_fetch_own_cities`
    already fetches, no extra call per city.

    Fields left `null` (and why — see the `state` CLI report for the full
    rationale, not repeated per-field here):
    - tavern.wine_level: headerData.wineSpendings gives the current wine
      draw/hour, but the serving ladder (0,12,24,39,54,72,90,108,...) is
      non-linear and only documented up to L7 (Action Catalog note on
      action:CityScreen:assignWinePerTick) — inverting it to a level index
      would be a guess above that. Reading it exactly needs a dedicated
      `nav tavern position=<pos>` call to the rendered <select>, which no
      real saved response exists for yet.
    - alerts-style per-field notes live in build_state(), not here.
    """
    global_data = data.get("updateGlobalData") or {}
    header = global_data.get("headerData") or {}
    template = data.get("updateTemplateData") or {}
    background = global_data.get("backgroundData") or {}
    now = global_data.get("time")

    current = header.get("currentResources") or {}
    maxres = header.get("maxResources") or {}

    tradegood = _tradegood_name(city.get("tradegood"))

    resources = {
        "wood": _round_or_none(current.get("resource")),
        "wine": _round_or_none(current.get("1")),
        "marble": _round_or_none(current.get("2")),
        "crystal": _round_or_none(current.get("3")),
        "sulfur": _round_or_none(current.get("4")),
    }
    max_resources = _round_or_none(maxres.get("resource"))

    wood_prod = _parse_signed_int(_template_value_text(template.get("js_TownHallPopulationGraphWoodProduction")))
    trade_prod = _parse_signed_int(_template_value_text(template.get("js_TownHallPopulationGraphTradeGoodProduction")))
    production_per_hour = {"wood": wood_prod, "tradegood": trade_prod}

    # wine_per_hour / wine_hours_left: only the city whose own tradegood is
    # wine produces it; every city can spend it via its Tavern. wineSpendings
    # is the current hourly draw (0 = Tavern off/no wine, per the Action
    # Catalog note); net_drain > 0 is how fast the cellar empties.
    # Per docs/CHORES.md: wine_per_hour is the Tavern's consumption (≥ 0);
    # wine_net_per_hour is production minus consumption; wine_hours_left is
    # stock / net drain, or WINE_NOT_DRAINING when the cellar isn't shrinking
    # (so a `wine_hours_left < N` chore clause is simply false, not null).
    wine_spendings = _to_float(header.get("wineSpendings"))
    wine_production = trade_prod if (tradegood == "wine" and trade_prod is not None) else 0
    wine_per_hour = None
    wine_net_per_hour = None
    wine_hours_left = None
    if wine_spendings is not None:
        wine_per_hour = int(round(wine_spendings))
        wine_net_per_hour = int(round(wine_production - wine_spendings))
        net_drain = wine_spendings - wine_production
        current_wine = resources["wine"]
        if net_drain <= 0:
            wine_hours_left = WINE_NOT_DRAINING
        elif current_wine is not None:
            wine_hours_left = round(current_wine / net_drain, 1)

    tavern_hit = _find_position(background, "tavern")
    tavern = None
    if tavern_hit is not None:
        idx, slot = tavern_hit
        level = slot.get("level")
        tavern = {
            "position": idx,
            "level": level,
            "wine_level": None,
            # Action Catalog note on assignWinePerTick: "Tavern level caps N"
            # — the serving index can't exceed the building's own level.
            "max_wine_level": level,
        }

    citizens = template.get("CitizenCount")
    if not (isinstance(citizens, (int, float)) and not isinstance(citizens, bool)):
        citizens = None

    workers_raw = {
        "wood": template.get("ResourceWorkerCount"),
        "tradegood": template.get("SpecialWorkerCount"),
        "scientists": template.get("ScientistCount"),
        "priests": template.get("PriestCount"),
    }
    workers = {
        k: (v if isinstance(v, (int, float)) and not isinstance(v, bool) else None)
        for k, v in workers_raw.items()
    }

    construction = []
    try:
        under_idx = int(background.get("underConstruction"))
    except (TypeError, ValueError):
        under_idx = -1
    if under_idx >= 0:
        end = background.get("endUpgradeTime")
        remaining = _remaining_minutes(end, now) if end not in (None, -1) else None
        positions = background.get("position") or []
        name = None
        if 0 <= under_idx < len(positions) and isinstance(positions[under_idx], dict):
            name = positions[under_idx].get("name") or positions[under_idx].get("building")
        construction.append({"building": name, "position": under_idx, "done_in_min": remaining})

    buildings = []
    for idx, slot in enumerate(background.get("position") or []):
        if not isinstance(slot, dict) or slot.get("buildingId") is None:
            continue
        buildings.append(
            {
                "position": idx,
                "name": slot.get("name") or slot.get("building"),
                "level": slot.get("level"),
                "busy": bool(slot.get("isBusy")),
            }
        )

    city_id = city.get("id")

    return {
        "id": str(city_id) if city_id is not None else None,
        "name": city.get("name"),
        "coords": _clean_coords(city.get("coords")),
        "island_id": str(background.get("islandId")) if background.get("islandId") is not None else None,
        "tradegood": tradegood,
        "resources": resources,
        "max_resources": max_resources,
        "production_per_hour": production_per_hour,
        "wine_per_hour": wine_per_hour,
        "wine_net_per_hour": wine_net_per_hour,
        "wine_hours_left": wine_hours_left,
        "tavern": tavern,
        "citizens": citizens,
        "population": _round_or_none(current.get("population")),
        "max_population": _parse_signed_int(_template_value_text(template.get("js_TownHallMaxInhabitants"))),
        "growth_per_hour": _parse_float(_template_value_text(template.get("js_TownHallPopulationGrowthValue"))),
        "happiness": _template_value_text(template.get("js_TownHallHappinessLargeText"))
        or _template_value_text(template.get("js_TownHallHappinessSmallText")),
        "corruption_pct": _parse_percent_int(_template_value_text(template.get("js_TownHallCorruption"))),
        "workers": workers,
        "construction": construction,
        "buildings": buildings,
    }


def build_state(cities: list, fetched: dict, global_data: dict) -> dict:
    """Top-level docs/CHORES.md §1 state dict. `global_data` is the
    updateGlobalData of the discovery call — gold/transporters/alerts/queue
    are account-wide (verified against real captured data: identical
    headerData.gold/income/upkeep/freeTransporters/maxTransporters across
    all 4 own cities' townHall responses), so any one call's headerData is
    the right source for them.

    `alerts.unread_messages` is always `null`: no saved real response (any
    townHall/action call) carries an unread-mail counter — headerData.
    advisors has no "messages" entry (only military/cities/research/
    diplomacy/hasPremiumAccount on this premium account), and
    ingameCounterData was `null` in every capture. A dedicated mail/message
    view would be needed to fill this in.

    `alerts.under_attack` is derived from headerData.advisors.military.
    cssclass containing "alert" (the mechanic docs/CHORES.md §1 describes:
    "normalalert"/"normalactive" for a non-premium account). This account
    is premium, so its classes are "premiumactive"/"premium" instead —
    never "alert" in any of the captures, all of which were made while NOT
    under attack, so the true "under attack" class string is unverified.
    The substring rule is the best-effort, non-guessing interpretation of
    the documented mechanic; it currently evaluates to `False` for this
    account rather than `null`, since the css class IS present and known
    to not contain "alert".
    """
    header = global_data.get("headerData") or {}
    now = global_data.get("time")

    income = _to_float(header.get("income"))
    upkeep = _to_float(header.get("upkeep")) or 0
    gold_per_hour = int(round(income - upkeep)) if income is not None else None

    free_t = header.get("freeTransporters")
    max_t = header.get("maxTransporters")
    transporters = {
        "free": _round_or_none(free_t),
        "max": _round_or_none(max_t),
    }

    military_class = None
    advisors = header.get("advisors")
    if isinstance(advisors, dict):
        military = advisors.get("military")
        if isinstance(military, dict):
            military_class = military.get("cssclass")
    under_attack = "alert" in str(military_class).lower() if military_class is not None else None

    alerts = {"under_attack": under_attack, "unread_messages": None}

    queue = parse_queue_items(global_data.get("queueETA"), now)

    city_states = []
    for city in cities:
        city_id = str(city.get("id"))
        city_data = fetched.get(city_id, (None, None))[0] or {}
        city_states.append(build_city_state(city, city_data))

    return {
        "time": now,
        "gold": _round_or_none(header.get("gold")),
        "gold_per_hour": gold_per_hour,
        "transporters": transporters,
        "alerts": alerts,
        "queue": queue,
        "cities": city_states,
    }


def _render_city_text(city: dict) -> str:
    """Compact multi-line block for one city, capped at ~500 chars."""
    name = city.get("name") or "?"
    city_id = city.get("id") or "?"
    coords = city.get("coords") or ""
    tradegood = city.get("tradegood") or "?"
    lines = [f"== {name} ({city_id}) {coords} tradegood={tradegood} =="]

    res = city.get("resources") or {}
    res_bits = [
        f"{label} {res[label]}"
        for label in ("wood", "wine", "marble", "crystal", "sulfur")
        if res.get(label) is not None
    ]
    if res_bits:
        line = " ".join(res_bits)
        if city.get("max_resources") is not None:
            line += f" (max {city['max_resources']})"
        lines.append(line)

    stat_bits = []
    if city.get("population") is not None:
        stat_bits.append(f"pop {city['population']}/{city.get('max_population', '?')}")
    if city.get("growth_per_hour") is not None:
        stat_bits.append(f"growth {city['growth_per_hour']}")
    if city.get("happiness") is not None:
        stat_bits.append(f"happy {city['happiness']}")
    if city.get("corruption_pct") is not None:
        stat_bits.append(f"corruption {city['corruption_pct']}%")
    if stat_bits:
        lines.append(" | ".join(stat_bits))

    wine_bits = []
    tavern = city.get("tavern")
    if tavern:
        wl = tavern.get("wine_level")
        wine_bits.append(
            f"tavern L{tavern.get('level', '?')}@{tavern.get('position', '?')} "
            f"level {wl if wl is not None else '?'}/{tavern.get('max_wine_level', '?')}"
        )
    if city.get("wine_per_hour") is not None:
        wine_bits.append(f"wine -{city['wine_per_hour']}/h (net {_fmt_signed(city.get('wine_net_per_hour') or 0)}/h)")
    if city.get("wine_hours_left") is not None:
        wine_bits.append("not draining" if city["wine_hours_left"] == WINE_NOT_DRAINING else f"{city['wine_hours_left']}h left")
    if wine_bits:
        lines.append(" ".join(wine_bits))

    workers = city.get("workers") or {}
    w_bits = [f"{k} {workers[k]}" for k in ("wood", "tradegood", "scientists", "priests") if workers.get(k) is not None]
    if w_bits:
        lines.append("workers: " + " ".join(w_bits))

    construction = city.get("construction") or []
    if construction:
        c = construction[0]
        done = c.get("done_in_min")
        bit = f"building: {c.get('building', '?')}@{c.get('position', '?')}"
        if done is not None:
            bit += f" done in {done}m"
        lines.append(bit)
    else:
        lines.append("no construction in progress")

    text = "\n".join(lines)
    if len(text) > 500:
        text = text[:499].rstrip() + "…"
    return text


def render_state_text(state: dict) -> str:
    """Compact text rendering of the full `state` dict (no --json)."""
    lines = []

    header_bits = []
    if state.get("gold") is not None:
        bit = f"gold={state['gold']}"
        if state.get("gold_per_hour") is not None:
            bit += f" ({_fmt_signed(state['gold_per_hour'])}/h)"
        header_bits.append(bit)
    transporters = state.get("transporters") or {}
    if transporters.get("free") is not None or transporters.get("max") is not None:
        header_bits.append(f"transporters {transporters.get('free', '?')}/{transporters.get('max', '?')}")
    alerts = state.get("alerts") or {}
    alert_bits = []
    if alerts.get("under_attack"):
        alert_bits.append("UNDER ATTACK")
    if alerts.get("unread_messages"):
        alert_bits.append(f"{alerts['unread_messages']} unread")
    if alert_bits:
        header_bits.append("ALERTS: " + ", ".join(alert_bits))
    if header_bits:
        lines.append(" | ".join(header_bits))

    queue = state.get("queue") or []
    if queue:
        bits = []
        for item in queue:
            mins = item.get("done_in_min")
            label = item.get("city") or "task"
            bits.append(f"{label} in {mins}m" if mins is not None else str(label))
        lines.append("queue: " + ", ".join(bits))

    for city in state.get("cities") or []:
        lines.append(_render_city_text(city))

    return "\n".join(lines)


def _cmd_state(rest: list, session_dir: Path, env_path: Path) -> int:
    remaining, options = _split_flags(rest)
    as_json = "--json" in remaining
    server = _server(env_path)
    start_city_id = str(_city_id(env_path, options.get("city")))

    try:
        cookie = ss.read_cookie(session_dir)
        token = ss.read_action_request(session_dir)
    except ss.SessionError as exc:
        print(f"SESSION_INVALID: {exc}")
        return 2

    try:
        cities, fetched = _fetch_own_cities(server, cookie, token, session_dir, start_city_id)
    except ic.SessionInvalidError as exc:
        print(f"SESSION_INVALID: {exc}")
        return 2

    if not cities:
        print("no own cities found in headerData.cityDropdownMenu")
        return 0

    discovery_data = fetched[start_city_id][0]
    global_data = discovery_data.get("updateGlobalData") or {}
    state_obj = build_state(cities, fetched, global_data)

    (session_dir / "state.json").write_text(json.dumps(state_obj, indent=2))

    if as_json:
        print(json.dumps(state_obj, indent=2))
    else:
        text = render_state_text(state_obj)
        if text:
            print(text)
    return 0


# --------------------------------------------------------------------------
# Batch (`batch` subcommand)
# --------------------------------------------------------------------------


def _cmd_batch(rest: list, session_dir: Path, env_path: Path) -> int:
    """Read `nav`/`action` command lines from stdin (one per line, args
    exactly as on the CLI, e.g. `action action:IslandScreen:workerPlan
    cityId=1 wood=10`), run them sequentially in this one process, printing
    `## <line>` then that command's normal output. Stops at the first
    failure unless --keep-going; the rotating token is persisted after each
    line (nav/action already do this)."""
    keep_going = "--keep-going" in rest
    any_failed = False

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        print(f"## {line}")
        try:
            tokens = shlex.split(line)
        except ValueError as exc:
            print(f"SESSION_INVALID: bad line: {exc}")
            any_failed = True
            if not keep_going:
                return 1
            continue
        if not tokens:
            continue

        cmd, cmd_rest = tokens[0], tokens[1:]
        if cmd == "nav":
            code = _cmd_nav(cmd_rest, session_dir, env_path)
        elif cmd == "action":
            code = _cmd_action(cmd_rest, session_dir, env_path)
        else:
            print(f"SESSION_INVALID: unsupported batch command: {cmd}")
            code = 2

        if code != 0:
            any_failed = True
            if not keep_going:
                return code

    return 1 if any_failed else 0


# --------------------------------------------------------------------------
# Find (`find` subcommand)
# --------------------------------------------------------------------------


def _walk_json(obj, prefix: str = ""):
    """Yield (dotted.path, leaf_value) for every leaf in a JSON-ish
    structure of nested dicts/lists (list indices become path segments)."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from _walk_json(value, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            yield from _walk_json(value, f"{prefix}.{idx}" if prefix else str(idx))
    else:
        yield prefix, obj


def _find_value_text(value) -> str:
    """Render a leaf value for `find` output: HTML stripped, truncated to
    160 chars."""
    if value is None:
        text = "null"
    elif isinstance(value, bool):
        text = str(value)
    elif isinstance(value, str):
        text = html_to_text(value) if "<" in value else value
        text = text.replace("\n", " ").strip()
    else:
        text = str(value)
    if len(text) > 160:
        text = text[:159] + "…"
    return text


def _cmd_find(rest: list, session_dir: Path, env_path: Path) -> int:
    """Case-insensitive search over a saved raw response (default
    session/responses/last.json): walk the JSON and print matching
    `dotted.path = value` lines, max 40. Replaces ad-hoc `python3 -c`
    digging through session/responses/*.json."""
    del env_path
    remaining, options = _split_flags(rest)
    if not remaining:
        print("SESSION_INVALID: find requires a PATTERN")
        return 2
    pattern_str = remaining[0]

    file_opt = options.get("file")
    path = Path(file_opt) if file_opt else session_dir / "responses" / "last.json"

    try:
        raw = json.loads(path.read_text())
    except OSError as exc:
        print(f"SESSION_INVALID: could not read {path}: {exc}")
        return 2
    except ValueError as exc:
        print(f"SESSION_INVALID: could not parse {path} as JSON: {exc}")
        return 2

    try:
        pattern = re.compile(pattern_str, re.IGNORECASE)
    except re.error as exc:
        print(f"SESSION_INVALID: bad pattern: {exc}")
        return 2

    top = response_dict(raw) if isinstance(raw, list) else raw

    matches = []
    for dotted, value in _walk_json(top):
        line = f"{dotted} = {_find_value_text(value)}"
        if pattern.search(line):
            matches.append(line)
            if len(matches) >= 40:
                break

    if not matches:
        print("no matches")
        return 0
    for line in matches:
        print(line)
    return 0


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main(argv=None, project_root: Path | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(project_root) if project_root is not None else PROJECT_ROOT

    if not args:
        print("usage: game.py <session|set-cookie|nav|action|cities|state|batch|find> ...")
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
        if sub == "cities":
            return _cmd_cities(rest, session_dir, env_path)
        if sub == "state":
            return _cmd_state(rest, session_dir, env_path)
        if sub == "batch":
            return _cmd_batch(rest, session_dir, env_path)
        if sub == "find":
            return _cmd_find(rest, session_dir, env_path)
    except ValueError as exc:
        print(f"SESSION_INVALID: {exc}")
        return 2

    print(f"unknown subcommand: {sub}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
