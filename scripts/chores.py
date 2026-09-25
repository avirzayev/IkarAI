"""Chores engine — deterministic routine upkeep, evaluated against
`session/state.json`, so the LLM cycle only runs when there is a
decision to make. See docs/CHORES.md for the full spec.

Usage: python3 scripts/chores.py <cmd> [args...]

    list
    show NAME
    add NAME --why TEXT --rule JSON_STRING | --rule @file.json
    promote NAME
    disable NAME
    run [--dry-run-all] [--state PATH]

Chores live as rows in a lazily-created Notion "Chores" database (id
persisted to NOTION_CHORES_DB_ID). `run` is what `pretick.py` calls
every tick: it evaluates every non-disabled chore's rule against the
current game state, dry-runs proposed/dry-run chores (never touching
the game), executes active chores (subject to hard guardrails), and
writes a machine-readable summary to `session/chores_last.json`.
"""

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import bootstrap_kb as bk
import ikariam_client as ic
import kb
import notion_client as nc
import session_store as ss
from config import load_config, update_env_file


class ChoreError(Exception):
    """Expected, user-facing failure — printed without a traceback."""


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

CHORES_DB_TITLE = "Chores"
KILL_SWITCH_NAME = "KILL SWITCH"

STATUS_PROPOSED = "proposed"
STATUS_DRY_RUN = "dry-run"
STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"
VALID_STATUSES = {STATUS_PROPOSED, STATUS_DRY_RUN, STATUS_ACTIVE, STATUS_DISABLED}

OPS = {"<", "<=", ">", ">=", "==", "!="}
RESOURCES = ("wood", "wine", "marble", "crystal", "sulfur")
WORKER_FIELDS = ("wood", "tradegood", "scientists", "priests")
# rule vocabulary -> the catalogued workerPlan HTTP param name (the game
# calls the tradegood-worker field "luxury", not "tradegood").
WORKER_PARAM_NAME = {"wood": "wood", "tradegood": "luxury", "scientists": "scientists", "priests": "priests"}
# resource -> catalogued loadTransportersWithFreight cargo param name.
CARGO_PARAM_NAME = {
    "wood": "cargo_resource",
    "wine": "cargo_tradegood1",
    "marble": "cargo_tradegood2",
    "crystal": "cargo_tradegood3",
    "sulfur": "cargo_tradegood4",
}
# wine/hour consumed by the tavern at serving level N (observed L0-L7,
# non-linear — see Action Catalog notes on CityScreen:assignWinePerTick).
WINE_SERVING_LADDER = [0, 12, 24, 39, 54, 72, 90, 108]
SHIP_CAPACITY = 500
MAX_EXECUTED_PER_TICK = 5
DRY_RUNS_REQUIRED_TO_PROMOTE = 3
CONSECUTIVE_FAILURES_TO_DISABLE = 2

DAILY_LOG_CHORES_HEADING_PREFIX = "\U0001f527 Chores"  # "🔧 Chores"

# Hard guardrail: never premium/real-money or destructive actions, by name
# OR by anything mentioned in the Action Catalog row's Notes. Matched
# case-insensitively as substrings.
DENY_KEYWORDS = (
    "premium",
    "ambrosia",
    "real money",
    "real-money",
    "diamond",
    "disband",
    "abandon",
    "leave clan",
    "leaveclan",
    "leave the clan",
    "delete account",
)


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------


@dataclass
class Ctx:
    token: str  # Notion token
    config: object
    project_root: Path
    env_path: Path
    session_dir: Path
    now_utc: datetime
    now_local: datetime
    tz: ZoneInfo


# ---------------------------------------------------------------------------
# rule validation
# ---------------------------------------------------------------------------


def _err(msg: str):
    raise ChoreError(msg)


def _check_city_ref(value, scope: str, allow_richest: bool = False) -> None:
    if allow_richest and value == "richest":
        return
    if value == "$city" and scope != "each_city":
        _err("'$city' can only be used when scope is 'each_city'")


def _validate_do(primitive: str, do: dict, scope: str) -> None:
    if primitive == "transport":
        for key in ("resource", "amount", "from", "to"):
            if key not in do:
                _err(f"do.{key} is required for transport")
        if do["resource"] not in RESOURCES:
            _err(f"do.resource must be one of {RESOURCES}, got {do['resource']!r}")
        amount = do["amount"]
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            _err("do.amount must be a positive integer")
        if not isinstance(do["from"], str) or not do["from"]:
            _err("do.from must be a non-empty string ('richest' or a city id)")
        if not isinstance(do["to"], str) or not do["to"]:
            _err("do.to must be a non-empty string ('$city' or a city id)")
        _check_city_ref(do["to"], scope)
        _check_city_ref(do["from"], scope, allow_richest=True)

    elif primitive == "set_tavern_wine":
        for key in ("city", "level"):
            if key not in do:
                _err(f"do.{key} is required for set_tavern_wine")
        _check_city_ref(do["city"], scope)
        level = do["level"]
        if level != "max_affordable" and (not isinstance(level, int) or isinstance(level, bool) or level < 0):
            _err("do.level must be a non-negative integer or 'max_affordable'")

    elif primitive == "set_workers":
        if "city" not in do:
            _err("do.city is required for set_workers")
        _check_city_ref(do["city"], scope)
        present = [f for f in WORKER_FIELDS if f in do]
        if not present:
            _err(f"set_workers requires at least one of {WORKER_FIELDS}")
        max_count = 0
        for field_name in present:
            value = do[field_name]
            if value == "max":
                max_count += 1
            elif not isinstance(value, int) or isinstance(value, bool) or value < 0:
                _err(f"do.{field_name} must be a non-negative integer or 'max'")
        if max_count > 1:
            _err("set_workers allows at most one field set to 'max' (ambiguous otherwise)")

    elif primitive == "catalog_action":
        if not isinstance(do.get("name"), str) or not do["name"]:
            _err("do.name is required for catalog_action")
        params = do.get("params", {})
        if not isinstance(params, dict):
            _err("do.params must be an object")
        for value in params.values():
            if value == "$city":
                _check_city_ref("$city", scope)


def validate_rule(rule) -> dict:
    if not isinstance(rule, dict):
        _err("rule must be a JSON object")

    missing = [k for k in ("scope", "when", "do", "limits") if k not in rule]
    if missing:
        _err(f"rule missing required field(s): {', '.join(missing)}")

    scope = rule["scope"]
    if scope not in ("each_city", "global"):
        _err(f"scope must be 'each_city' or 'global', got {scope!r}")

    when = rule["when"]
    if not isinstance(when, list) or not when:
        _err("when must be a non-empty list of clauses")
    for i, clause in enumerate(when):
        if not isinstance(clause, dict):
            _err(f"when[{i}] must be an object")
        cmissing = [k for k in ("field", "op", "value") if k not in clause]
        if cmissing:
            _err(f"when[{i}] missing: {', '.join(cmissing)}")
        if not isinstance(clause["field"], str) or not clause["field"]:
            _err(f"when[{i}].field must be a non-empty string")
        if clause["op"] not in OPS:
            _err(f"when[{i}].op must be one of {sorted(OPS)}, got {clause['op']!r}")
        if clause["value"] is None or isinstance(clause["value"], (list, dict)):
            _err(f"when[{i}].value must be a string, number, or boolean (not null/list/object)")

    do = rule["do"]
    if not isinstance(do, dict) or "primitive" not in do:
        _err("do must be an object with a 'primitive' key")
    primitive = do["primitive"]
    if primitive not in ("transport", "set_tavern_wine", "set_workers", "catalog_action"):
        _err(
            "do.primitive must be one of transport/set_tavern_wine/set_workers/catalog_action, "
            f"got {primitive!r}"
        )
    _validate_do(primitive, do, scope)

    limits = rule["limits"]
    if not isinstance(limits, dict):
        _err("limits must be an object")
    cooldown = limits.get("cooldown_min")
    if not isinstance(cooldown, int) or isinstance(cooldown, bool) or cooldown < 0:
        _err("limits.cooldown_min must be a non-negative integer")
    max_per_day = limits.get("max_per_day")
    if not isinstance(max_per_day, int) or isinstance(max_per_day, bool) or max_per_day <= 0:
        _err("limits.max_per_day must be a positive integer")
    if primitive == "transport":
        share = limits.get("max_share_of_source")
        if not isinstance(share, (int, float)) or isinstance(share, bool) or not (0 < share <= 1):
            _err("limits.max_share_of_source must be a number in (0, 1] for transport chores")

    return rule


# ---------------------------------------------------------------------------
# rule evaluation
# ---------------------------------------------------------------------------


def _dotted_get(context: dict, path: str):
    cur = context
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _compare(actual, op: str, value) -> bool:
    try:
        if op == "==":
            return actual == value
        if op == "!=":
            return actual != value
        if op == "<":
            return actual < value
        if op == "<=":
            return actual <= value
        if op == ">":
            return actual > value
        if op == ">=":
            return actual >= value
    except TypeError:
        return False
    return False


def _clause_matches(clause: dict, context: dict) -> bool:
    actual = _dotted_get(context, clause["field"])
    if actual is None:
        return False
    return _compare(actual, clause["op"], clause["value"])


def evaluate_rule(rule: dict, state: dict) -> list:
    """Returns a list of {"scope_key": str, "city": dict|None} matches."""
    scope = rule["scope"]
    when = rule["when"]
    matches = []
    if scope == "each_city":
        for city in state.get("cities") or []:
            if all(_clause_matches(c, city) for c in when):
                matches.append({"scope_key": str(city.get("id")), "city": city})
    else:
        if all(_clause_matches(c, state) for c in when):
            matches.append({"scope_key": "global", "city": None})
    return matches


# ---------------------------------------------------------------------------
# guardrails
# ---------------------------------------------------------------------------


def _check_denylist(name: str, notes: str) -> None:
    haystack = f"{name} {notes}".lower()
    for kw in DENY_KEYWORDS:
        if kw in haystack:
            raise ChoreError(f"guardrail: {name!r} denied — matches denylist keyword {kw!r}")


# ---------------------------------------------------------------------------
# primitives — each returns (action, function, params, description, switch_city_id|None)
# ---------------------------------------------------------------------------


def _city_by_id(state: dict, city_id) -> dict | None:
    for city in state.get("cities") or []:
        if str(city.get("id")) == str(city_id):
            return city
    return None


def _richest_city(state: dict, resource: str, exclude_id) -> dict | None:
    best = None
    best_amount = None
    for city in state.get("cities") or []:
        if str(city.get("id")) == str(exclude_id):
            continue
        amount = (city.get("resources") or {}).get(resource)
        if amount is None:
            continue
        if best is None or amount > best_amount:
            best, best_amount = city, amount
    return best


def build_transport(rule: dict, state: dict, match: dict) -> tuple:
    do = rule["do"]
    limits = rule["limits"]
    resource = do["resource"]

    to_id = match["city"]["id"] if do["to"] == "$city" else do["to"]
    to_city = _city_by_id(state, to_id)
    if to_city is None:
        raise ChoreError(f"transport: destination city {to_id!r} not found in state")
    island_id = to_city.get("island_id")
    if not island_id:
        raise ChoreError(f"transport: destination city {to_id} has no island_id in state")

    if do["from"] == "richest":
        from_city = _richest_city(state, resource, to_id)
        if from_city is None:
            raise ChoreError(f"transport: no source city with {resource} stock found (excluding {to_id})")
    else:
        from_city = _city_by_id(state, do["from"])
        if from_city is None:
            raise ChoreError(f"transport: source city {do['from']!r} not found in state")
    from_id = from_city.get("id")
    if str(from_id) == str(to_id):
        raise ChoreError("transport: source and destination city are the same")

    source_stock = (from_city.get("resources") or {}).get(resource)
    if source_stock is None:
        raise ChoreError(f"transport: source city {from_id} has no {resource} stock in state")

    free_ships = (state.get("transporters") or {}).get("free")
    if not free_ships:
        raise ChoreError("transport: no free transporters available")

    share_cap = math.floor(source_stock * limits["max_share_of_source"])
    ship_cap = free_ships * SHIP_CAPACITY
    amount = min(do["amount"], share_cap, ship_cap)
    if amount <= 0:
        raise ChoreError(
            f"transport: clamped amount is 0 (requested={do['amount']}, "
            f"share_cap={share_cap}, ship_cap={ship_cap})"
        )
    ships_used = min(free_ships, math.ceil(amount / SHIP_CAPACITY))

    cargo = {p: 0 for p in CARGO_PARAM_NAME.values()}
    cargo[CARGO_PARAM_NAME[resource]] = amount

    params = {
        "destinationCityId": str(to_id),
        "islandId": str(island_id),
        "oldView": "",
        "position": "",
        "avatar2Name": "",
        "city2Name": "",
        "type": "",
        "activeTab": "",
        "transportDisplayPrice": 0,
        "premiumTransporter": 0,
        "normalTransportersMax": free_ships,
        "transporters": ships_used,
        "capacity": 0,
        "max_capacity": 5,
        "jetPropulsion": 0,
        "currentCityId": str(from_id),
        **cargo,
    }
    desc = f"transport {amount} {resource} from city {from_id} to city {to_id} ({ships_used} ship(s))"
    return "transportOperations", "loadTransportersWithFreight", params, desc, str(from_id)


def _max_affordable_wine_level(city: dict) -> int:
    tavern = city.get("tavern") or {}
    max_level = len(WINE_SERVING_LADDER) - 1
    cap = tavern.get("max_wine_level")
    if isinstance(cap, int):
        max_level = min(max_level, cap)
    stock = (city.get("resources") or {}).get("wine")
    if stock is None:
        raise ChoreError("set_tavern_wine: city has no wine stock in state")
    production = city.get("wine_per_hour") or 0
    best = 0
    for level in range(0, max_level + 1):
        consumption = WINE_SERVING_LADDER[level]
        net = production - consumption
        hours = float("inf") if net >= 0 else stock / (-net)
        if hours >= 12:
            best = level
    return best


def build_set_tavern_wine(rule: dict, state: dict, match: dict) -> tuple:
    do = rule["do"]
    city_id = match["city"]["id"] if do["city"] == "$city" else do["city"]
    city = _city_by_id(state, city_id)
    if city is None:
        raise ChoreError(f"set_tavern_wine: city {city_id!r} not found in state")
    tavern = city.get("tavern") or {}
    position = tavern.get("position")
    if position is None:
        raise ChoreError(f"set_tavern_wine: city {city_id} has no tavern.position in state")

    level = do["level"]
    if level == "max_affordable":
        level = _max_affordable_wine_level(city)
    max_level = tavern.get("max_wine_level")
    if isinstance(max_level, int) and level > max_level:
        level = max_level

    params = {"cityId": str(city_id), "position": str(position), "amount": level, "currentCityId": str(city_id)}
    desc = f"set tavern wine serving level to {level} in city {city_id}"
    return "CityScreen", "assignWinePerTick", params, desc, str(city_id)


def build_set_workers(rule: dict, state: dict, match: dict) -> tuple:
    do = rule["do"]
    city_id = match["city"]["id"] if do["city"] == "$city" else do["city"]
    city = _city_by_id(state, city_id)
    if city is None:
        raise ChoreError(f"set_workers: city {city_id!r} not found in state")
    current = city.get("workers") or {}
    citizens = city.get("citizens")

    free = None
    if citizens is not None and all(current.get(f) is not None for f in WORKER_FIELDS):
        free = math.floor(citizens - sum(current.get(f, 0) for f in WORKER_FIELDS))

    targets = {}
    for field_name in WORKER_FIELDS:
        if field_name not in do:
            continue
        requested = do[field_name]
        if requested == "max":
            if free is None:
                raise ChoreError(f"set_workers: city {city_id} missing citizens/workers data needed for 'max'")
            cur_val = current.get(field_name)
            if cur_val is None:
                raise ChoreError(f"set_workers: city {city_id} missing current {field_name} worker count")
            targets[field_name] = cur_val + max(0, free)
        else:
            targets[field_name] = requested
    if not targets:
        raise ChoreError("set_workers: no worker fields resolved")

    def resolved(field_name):
        if field_name in targets:
            return targets[field_name]
        cur_val = current.get(field_name)
        if cur_val is None:
            raise ChoreError(
                f"set_workers: city {city_id} missing current {field_name} worker count "
                "(needed to preserve it — workerPlan takes new totals, not deltas)"
            )
        return cur_val

    params = {
        "screen": "TownHall",
        "type": "",
        "cityId": str(city_id),
        "currentCityId": str(city_id),
        "backgroundView": "city",
        "templateView": "townHall",
    }
    for field_name in WORKER_FIELDS:
        params[WORKER_PARAM_NAME[field_name]] = resolved(field_name)

    desc = f"set workers in city {city_id}: " + ", ".join(f"{k}={v}" for k, v in targets.items())
    return "IslandScreen", "workerPlan", params, desc, str(city_id)


def build_catalog_action(rule: dict, state: dict, match: dict, catalog_rows: list) -> tuple:
    do = rule["do"]
    name = do["name"]
    row = None
    for r in catalog_rows:
        if nc.page_title(r).strip().lower() == name.strip().lower():
            row = r
            break
    if row is None:
        raise ChoreError(f"catalog_action: no Action Catalog row named {name!r}")
    kind = nc.prop_text(row, "Kind")
    if kind != "action":
        raise ChoreError(f"catalog_action: row {name!r} is kind={kind!r}, only 'action' rows are allowed")
    notes = nc.prop_text(row, "Notes")
    _check_denylist(name, notes)

    action = nc.prop_text(row, "Action")
    function = nc.prop_text(row, "Function")
    if not action:
        raise ChoreError(f"catalog_action: row {name!r} has no Action field")

    city_id = match["city"]["id"] if match["city"] else None
    params = {}
    for key, value in (do.get("params") or {}).items():
        if value == "$city":
            if city_id is None:
                raise ChoreError("catalog_action: $city used but this rule's scope is global")
            params[key] = str(city_id)
        else:
            params[key] = value

    desc = f"catalog_action {name} ({action}:{function}) city={city_id}"
    return action, function, params, desc, (str(city_id) if city_id is not None else None)


def build_primitive(rule: dict, state: dict, match: dict, catalog_rows: list) -> tuple:
    primitive = rule["do"]["primitive"]
    if primitive == "transport":
        result = build_transport(rule, state, match)
    elif primitive == "set_tavern_wine":
        result = build_set_tavern_wine(rule, state, match)
    elif primitive == "set_workers":
        result = build_set_workers(rule, state, match)
    elif primitive == "catalog_action":
        result = build_catalog_action(rule, state, match, catalog_rows)
    else:  # pragma: no cover — validated at add-time
        raise ChoreError(f"unknown primitive {primitive!r}")
    action, function, params, desc, switch_city = result
    _check_denylist(f"{action}:{function}", desc)
    return action, function, params, desc, switch_city


# ---------------------------------------------------------------------------
# game execution
# ---------------------------------------------------------------------------


def _read_session(ctx: Ctx) -> tuple:
    try:
        cookie = ss.read_cookie(ctx.session_dir)
        token = ss.read_action_request(ctx.session_dir)
    except ss.SessionError as exc:
        raise ChoreError(f"game session unavailable: {exc}")
    return cookie, token


def _response_ok(response) -> tuple:
    """(ok, message) from a raw [[name, payload], ...] game response."""
    data = {}
    if isinstance(response, list):
        for item in response:
            if isinstance(item, list) and len(item) >= 2:
                data[item[0]] = item[1]
    if data.get("error"):
        return False, str(data["error"])
    feedback = data.get("provideFeedback")
    if isinstance(feedback, list):
        errors = [
            (f.get("text") or f.get("locakey") or "error")
            for f in feedback
            if isinstance(f, dict) and f.get("type") == 11
        ]
        if errors:
            return False, "; ".join(str(e) for e in errors if e)
        successes = [f.get("text") for f in feedback if isinstance(f, dict) and f.get("type") == 10]
        if successes and any(successes):
            return True, next(s for s in successes if s)
    return True, "ok"


def _plain_call(ctx: Ctx, cookie: str, token: str, action: str, function: str, params: dict):
    response = ic.call_action(ctx.config.ikariam_server, cookie, token, action, function, params)
    new_token = ic.extract_action_request(response)
    ss.write_action_request(ctx.session_dir, new_token)
    return response


def _switch_city_and_call(ctx: Ctx, cookie: str, token: str, city_id: str, action: str, function: str, params: dict):
    """changeCurrentCity's own response carries no usable token (a catalog
    gotcha) — the safe sequence is changeCurrentCity -> a nav call (which
    tolerates the stale token) -> the real action."""
    server = ctx.config.ikariam_server
    ic.call_action(server, cookie, token, "header", "changeCurrentCity", {
        "cityId": city_id, "oldView": "", "currentCityId": city_id,
    })
    nav_response = ic.call_nav(server, cookie, token, "townHall", {
        "cityId": city_id, "position": "0", "currentCityId": city_id,
    })
    fresh_token = ic.extract_action_request(nav_response)
    response = ic.call_action(server, cookie, fresh_token, action, function, params)
    new_token = ic.extract_action_request(response)
    ss.write_action_request(ctx.session_dir, new_token)
    return response


# ---------------------------------------------------------------------------
# runtime (cooldown / max_per_day) — local file, not in Notion
# ---------------------------------------------------------------------------


def _load_runtime(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except ValueError:
        return {}


def _save_runtime(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2))


def _runtime_entry(runtime: dict, chore_name: str) -> dict:
    return runtime.setdefault(chore_name, {"last_fired": {}, "day_counts": {}, "consecutive_failures": 0})


def _limited(entry: dict, scope_key: str, limits: dict, now_utc: datetime, today_local: str) -> str | None:
    cooldown_min = limits["cooldown_min"]
    if cooldown_min:
        last = entry["last_fired"].get(scope_key)
        if last:
            last_dt = datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if now_utc - last_dt < timedelta(minutes=cooldown_min):
                return f"cooldown ({cooldown_min}m)"
    max_per_day = limits["max_per_day"]
    day_bucket = entry["day_counts"].get(today_local, {})
    if day_bucket.get(scope_key, 0) >= max_per_day:
        return f"max_per_day ({max_per_day})"
    return None


def _record_fired(entry: dict, scope_key: str, now_utc: datetime, today_local: str) -> None:
    entry["last_fired"][scope_key] = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    entry["day_counts"].setdefault(today_local, {})
    entry["day_counts"][today_local][scope_key] = entry["day_counts"][today_local].get(scope_key, 0) + 1


# ---------------------------------------------------------------------------
# Notion chores DB helpers
# ---------------------------------------------------------------------------


def _db_id(ctx: Ctx) -> str:
    db_id = ctx.config.notion_chores_db_id
    if not db_id:
        db_id = bk.ensure_database(ctx.token, ctx.config.notion_root_page_id, CHORES_DB_TITLE, bk.CHORES_SCHEMA)
        update_env_file(ctx.env_path, {"NOTION_CHORES_DB_ID": db_id})
        ctx.config.notion_chores_db_id = db_id
    return db_id


def _chore_rows(ctx: Ctx) -> list:
    return nc.query_database(ctx.token, _db_id(ctx))


def _find_chore(rows: list, name: str):
    target = name.strip().lower()
    for row in rows:
        if nc.page_title(row).strip().lower() == target:
            return row
    return None


def _row_status(row: dict) -> str:
    return nc.prop_text(row, "Status")


def _row_number(row: dict, field_name: str) -> int:
    prop = row.get("properties", {}).get(field_name)
    if not prop or prop.get("number") is None:
        return 0
    return prop["number"]


# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------


def _today_local(ctx: Ctx) -> str:
    return ctx.now_local.strftime("%Y-%m-%d")


def _write_daily_log(ctx: Ctx, log_lines: list) -> None:
    if not log_lines:
        return
    heading = f"{DAILY_LOG_CHORES_HEADING_PREFIX} {ctx.now_local.strftime('%H:%M')}"
    title = _today_local(ctx)
    page_id, header_block_id = kb._ensure_daily_log_page(ctx.token, ctx.config.notion_daily_logs_db_id, title)
    children = [nc.heading3_block(heading)] + [nc.paragraph_block(line) for line in log_lines]
    nc.append_blocks(ctx.token, page_id, children, after=header_block_id)

    hourly_log_path = ctx.session_dir / "hourly.log"
    stamp = ctx.now_local.strftime("%Y-%m-%d %H:%M %Z")
    with hourly_log_path.open("a") as f:
        for line in log_lines:
            f.write(f"[{stamp}] {line}\n")


def _write_results(ctx: Ctx, result: dict) -> None:
    (ctx.session_dir / "chores_last.json").write_text(json.dumps(result, indent=2))


# ---------------------------------------------------------------------------
# run — the main runner
# ---------------------------------------------------------------------------


def _load_state(path: Path) -> dict:
    if not path.exists():
        raise ChoreError(f"state file not found: {path}")
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError) as exc:
        raise ChoreError(f"could not read state file {path}: {exc}")


def _handle_failure(
    ctx: Ctx, row: dict, name: str, entry: dict, scope_key: str,
    dry_run_mode: bool, reason: str, result: dict, log_lines: list, preview: bool,
) -> None:
    if preview:
        print(f"[preview] failed: {name}: {reason}")
        result["failures"].append({"chore": name, "scope": scope_key, "reason": reason, "mode": "preview"})
        return

    line = f"failed: {name}: {reason}"
    log_lines.append(line)
    print(line)
    result["failures"].append(
        {"chore": name, "scope": scope_key, "reason": reason, "mode": "dry-run" if dry_run_mode else "active"}
    )

    if dry_run_mode:
        nc.update_page(ctx.token, row["id"], {
            "DryRunsOK": nc.number_prop(0),
            "LastRun": nc.date_prop(_today_local(ctx)),
            "LastResult": nc.rich_text_prop(f"dry-run failed: {reason}"),
        })
        return

    new_failures = _row_number(row, "Failures") + 1
    entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1
    updates = {
        "Failures": nc.number_prop(new_failures),
        "LastRun": nc.date_prop(_today_local(ctx)),
        "LastResult": nc.rich_text_prop(f"failed: {reason}"),
    }
    if entry["consecutive_failures"] >= CONSECUTIVE_FAILURES_TO_DISABLE:
        updates["Status"] = nc.select_prop(STATUS_DISABLED)
        updates["LastResult"] = nc.rich_text_prop(
            f"auto-disabled after {CONSECUTIVE_FAILURES_TO_DISABLE} consecutive failures: {reason}"
        )
        result["disabled"].append(name)
    nc.update_page(ctx.token, row["id"], updates)


def cmd_run(ctx: Ctx, args) -> int:
    state_path = Path(args.state) if args.state else ctx.session_dir / "state.json"
    state = _load_state(state_path)

    rows = _chore_rows(ctx)
    kill_row = _find_chore(rows, KILL_SWITCH_NAME)
    result = {"executed": [], "dry_runs": [], "failures": [], "disabled": [], "pending_review": [], "skipped": []}

    if kill_row is not None and _row_status(kill_row) == STATUS_ACTIVE:
        print(f"KILL SWITCH active — no chores run this tick ({len(rows)} chore row(s) seen)")
        _write_results(ctx, result)
        return 0

    runtime_path = ctx.session_dir / "chores_runtime.json"
    runtime = _load_runtime(runtime_path)
    today_local = _today_local(ctx)

    catalog_rows = None  # fetched lazily, only if a catalog_action chore exists
    log_lines: list = []
    executed_count = 0
    cookie = token = None  # fetched lazily on first real execution

    for row in sorted(rows, key=lambda r: nc.page_title(r).lower()):
        name = nc.page_title(row)
        if name == KILL_SWITCH_NAME:
            continue
        status = _row_status(row)
        if status == STATUS_DISABLED or status not in VALID_STATUSES:
            continue

        try:
            rule = validate_rule(json.loads(nc.prop_text(row, "Rule")))
        except (ValueError, ChoreError) as exc:
            print(f"chore {name}: invalid rule, skipping ({exc})")
            continue

        if status == STATUS_PROPOSED:
            nc.update_page(ctx.token, row["id"], {"Status": nc.select_prop(STATUS_DRY_RUN)})
            status = STATUS_DRY_RUN

        dry_run_mode = bool(args.dry_run_all) or status == STATUS_DRY_RUN

        matches = evaluate_rule(rule, state)
        if not matches:
            continue

        entry = _runtime_entry(runtime, name)
        for match in matches:
            scope_key = match["scope_key"]
            limit_reason = _limited(entry, scope_key, rule["limits"], ctx.now_utc, today_local)
            if limit_reason:
                result["skipped"].append({"chore": name, "scope": scope_key, "reason": limit_reason})
                continue

            if rule["do"]["primitive"] == "catalog_action" and catalog_rows is None:
                catalog_db_id = ctx.config.notion_action_catalog_db_id
                catalog_rows = nc.query_database(ctx.token, catalog_db_id) if catalog_db_id else []

            try:
                action, function, params, desc, switch_city = build_primitive(rule, state, match, catalog_rows or [])
            except ChoreError as exc:
                _handle_failure(ctx, row, name, entry, scope_key, dry_run_mode, str(exc), result, log_lines, bool(args.dry_run_all))
                continue

            if args.dry_run_all:
                line = f"[preview] {name}: would {desc}"
                print(line)
                result["dry_runs"].append({"chore": name, "scope": scope_key, "detail": desc})
                continue

            _record_fired(entry, scope_key, ctx.now_utc, today_local)

            if dry_run_mode:
                new_dry_runs_ok = _row_number(row, "DryRunsOK") + 1
                nc.update_page(ctx.token, row["id"], {
                    "DryRunsOK": nc.number_prop(new_dry_runs_ok),
                    "LastRun": nc.date_prop(today_local),
                    "LastResult": nc.rich_text_prop(f"dry-run OK: {desc}"),
                })
                line = f"dry-run: {name}: would {desc}"
                log_lines.append(line)
                print(line)
                result["dry_runs"].append({"chore": name, "scope": scope_key, "detail": desc})
                continue

            # active — real execution, subject to the per-tick cap
            if executed_count >= MAX_EXECUTED_PER_TICK:
                result["skipped"].append(
                    {"chore": name, "scope": scope_key, "reason": f"tick limit ({MAX_EXECUTED_PER_TICK} executed actions)"}
                )
                continue

            if cookie is None:
                try:
                    cookie, token = _read_session(ctx)
                except ChoreError as exc:
                    _handle_failure(ctx, row, name, entry, scope_key, False, str(exc), result, log_lines, False)
                    continue

            executed_count += 1
            try:
                if switch_city:
                    response = _switch_city_and_call(ctx, cookie, token, switch_city, action, function, params)
                else:
                    response = _plain_call(ctx, cookie, token, action, function, params)
                token = ss.read_action_request(ctx.session_dir)
                ok, message = _response_ok(response)
            except ic.SessionInvalidError as exc:
                ok, message = False, str(exc)

            if ok:
                new_runs = _row_number(row, "Runs") + 1
                nc.update_page(ctx.token, row["id"], {
                    "Runs": nc.number_prop(new_runs),
                    "LastRun": nc.date_prop(today_local),
                    "LastResult": nc.rich_text_prop(f"ok: {desc} — {message}"),
                })
                entry["consecutive_failures"] = 0
                line = f"executed: {name}: {desc} — {message}"
                log_lines.append(line)
                print(line)
                result["executed"].append({"chore": name, "scope": scope_key, "detail": desc, "result": "ok"})
            else:
                _handle_failure(ctx, row, name, entry, scope_key, False, message, result, log_lines, False)

    _save_runtime(runtime_path, runtime)
    _write_daily_log(ctx, log_lines)

    # pending_review reflects *current* status (proposed rows just flipped
    # to dry-run above still count — they were proposed this tick).
    fresh_rows = _chore_rows(ctx)
    for row in fresh_rows:
        name = nc.page_title(row)
        if name == KILL_SWITCH_NAME:
            continue
        status = _row_status(row)
        if status == STATUS_PROPOSED or (status == STATUS_DRY_RUN and _row_number(row, "DryRunsOK") >= DRY_RUNS_REQUIRED_TO_PROMOTE):
            result["pending_review"].append(name)

    _write_results(ctx, result)
    print(
        f"chores: {len(result['executed'])} executed, {len(result['dry_runs'])} dry-run, "
        f"{len(result['failures'])} failed, {len(result['disabled'])} disabled, "
        f"{len(result['pending_review'])} pending review, {len(result['skipped'])} skipped"
    )
    return 0


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def cmd_list(ctx: Ctx, args) -> int:
    rows = _chore_rows(ctx)
    if not rows:
        print("(no chores)")
        return 0
    for row in sorted(rows, key=lambda r: nc.page_title(r).lower()):
        name = nc.page_title(row)
        status = _row_status(row)
        dry = _row_number(row, "DryRunsOK")
        runs = _row_number(row, "Runs")
        fails = _row_number(row, "Failures")
        last_run = nc.prop_text(row, "LastRun")
        last_result = nc.prop_text(row, "LastResult")
        print(f"{name} | {status} | DryRunsOK={dry} Runs={runs} Failures={fails} | {last_run} | {last_result}")
    return 0


def cmd_show(ctx: Ctx, args) -> int:
    rows = _chore_rows(ctx)
    row = _find_chore(rows, args.name)
    if row is None:
        raise ChoreError(f"no chore named {args.name!r}")
    print(f"--- {nc.page_title(row)} ---")
    print(f"Status: {_row_status(row)}")
    print(f"Why: {nc.prop_text(row, 'Why')}")
    print(
        f"DryRunsOK: {_row_number(row, 'DryRunsOK')}  Runs: {_row_number(row, 'Runs')}  "
        f"Failures: {_row_number(row, 'Failures')}"
    )
    print(f"LastRun: {nc.prop_text(row, 'LastRun')}")
    print(f"LastResult: {nc.prop_text(row, 'LastResult')}")
    rule_text = nc.prop_text(row, "Rule")
    try:
        pretty = json.dumps(json.loads(rule_text), indent=2)
    except ValueError:
        pretty = rule_text
    print("Rule:")
    print(pretty)
    return 0


def cmd_add(ctx: Ctx, args) -> int:
    raw = args.rule
    if raw.startswith("@"):
        raw = Path(raw[1:]).read_text()
    try:
        rule = json.loads(raw)
    except ValueError as exc:
        raise ChoreError(f"--rule is not valid JSON: {exc}")
    rule = validate_rule(rule)

    rows = _chore_rows(ctx)
    if _find_chore(rows, args.name):
        raise ChoreError(f"a chore named {args.name!r} already exists")

    properties = {
        "Name": nc.title_prop(args.name),
        "Status": nc.select_prop(STATUS_PROPOSED),
        "Rule": nc.rich_text_prop(json.dumps(rule)),
        "Why": nc.rich_text_prop(args.why),
        "DryRunsOK": nc.number_prop(0),
        "Runs": nc.number_prop(0),
        "Failures": nc.number_prop(0),
        "LastResult": nc.rich_text_prop(""),
    }
    nc.create_page(ctx.token, {"type": "database_id", "database_id": _db_id(ctx)}, properties)
    print(f"OK chore added: {args.name} (proposed)")
    return 0


def cmd_promote(ctx: Ctx, args) -> int:
    rows = _chore_rows(ctx)
    row = _find_chore(rows, args.name)
    if row is None:
        raise ChoreError(f"no chore named {args.name!r}")
    status = _row_status(row)
    if status == STATUS_ACTIVE:
        print(f"{args.name} is already active")
        return 0
    if status != STATUS_DRY_RUN:
        raise ChoreError(f"chore {args.name!r} is {status!r} — must be in dry-run status to promote")
    dry_ok = _row_number(row, "DryRunsOK")
    if dry_ok < DRY_RUNS_REQUIRED_TO_PROMOTE:
        raise ChoreError(
            f"chore {args.name!r} has only {dry_ok}/{DRY_RUNS_REQUIRED_TO_PROMOTE} required "
            "consecutive dry runs — not ready to promote"
        )
    nc.update_page(ctx.token, row["id"], {"Status": nc.select_prop(STATUS_ACTIVE)})
    print(f"OK chore promoted: {args.name} -> active")
    return 0


def cmd_disable(ctx: Ctx, args) -> int:
    rows = _chore_rows(ctx)
    row = _find_chore(rows, args.name)
    if row is None:
        raise ChoreError(f"no chore named {args.name!r}")
    nc.update_page(
        ctx.token, row["id"],
        {"Status": nc.select_prop(STATUS_DISABLED), "LastResult": nc.rich_text_prop("manually disabled")},
    )
    print(f"OK chore disabled: {args.name}")
    return 0


# ---------------------------------------------------------------------------
# argument parsing / entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chores.py", description=__doc__)
    parser.add_argument("--root", default=None, help="project root override (also IKARAI_ROOT env var)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list").set_defaults(func=cmd_list)

    p_show = sub.add_parser("show")
    p_show.add_argument("name")
    p_show.set_defaults(func=cmd_show)

    p_add = sub.add_parser("add")
    p_add.add_argument("name")
    p_add.add_argument("--why", required=True)
    p_add.add_argument("--rule", required=True, help="JSON string, or @file to read the rule from a file")
    p_add.set_defaults(func=cmd_add)

    p_promote = sub.add_parser("promote")
    p_promote.add_argument("name")
    p_promote.set_defaults(func=cmd_promote)

    p_disable = sub.add_parser("disable")
    p_disable.add_argument("name")
    p_disable.set_defaults(func=cmd_disable)

    p_run = sub.add_parser("run")
    p_run.add_argument("--dry-run-all", action="store_true", help="preview every chore as a dry run; no state mutation")
    p_run.add_argument("--state", default=None, help="override path to state.json (default: session/state.json)")
    p_run.set_defaults(func=cmd_run)

    return parser


def main(argv: list | None = None, project_root=None, now: datetime | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = _build_parser()
    args = parser.parse_args(argv)

    if project_root is None:
        if args.root:
            project_root = Path(args.root)
        elif os.environ.get("IKARAI_ROOT"):
            project_root = Path(os.environ["IKARAI_ROOT"])
        else:
            project_root = Path(__file__).resolve().parent.parent
    project_root = Path(project_root)
    env_path = project_root / ".env"
    session_dir = project_root / "session"
    session_dir.mkdir(parents=True, exist_ok=True)

    try:
        config = load_config(env_path)
    except (KeyError, FileNotFoundError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return 1

    tz = ZoneInfo(config.timezone)
    now_utc = now if now is not None else datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_local = now_utc.astimezone(tz)

    ctx = Ctx(
        token=config.notion_token,
        config=config,
        project_root=project_root,
        env_path=env_path,
        session_dir=session_dir,
        now_utc=now_utc,
        now_local=now_local,
        tz=tz,
    )

    try:
        return args.func(ctx, args)
    except requests.HTTPError as e:
        print(f"API error: {e}", file=sys.stderr)
        return 1
    except ChoreError as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
