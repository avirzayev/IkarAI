import copy
import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

import chores
import notion_client as nc


# ---------------------------------------------------------------------------
# fixtures / builders
# ---------------------------------------------------------------------------


def _write_env(tmp_path, **overrides):
    values = {
        "IKARIAM_USERNAME": "u@example.com",
        "IKARIAM_SERVER": "s1.example.com",
        "NOTION_TOKEN": "ntn_test",
        "NOTION_ROOT_PAGE_ID": "root-page-id",
        "NOTION_ACTION_CATALOG_DB_ID": "catalog-db",
        "NOTION_DAILY_LOGS_DB_ID": "logs-db",
        "NOTION_HUMAN_REQUIRED_DB_ID": "human-db",
        "NOTION_CHORES_DB_ID": "chores-db",
        "TIMEZONE": "UTC",
    }
    values.update(overrides)
    env_path = tmp_path / ".env"
    env_path.write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
    (tmp_path / "session").mkdir(exist_ok=True)
    return env_path


def _title_prop_val(text):
    return {"type": "title", "title": [{"plain_text": text, "text": {"content": text}}]}


def _rich_text_prop_val(text):
    return {"type": "rich_text", "rich_text": [{"plain_text": text, "text": {"content": text}}]}


def _select_prop_val(name):
    return {"type": "select", "select": {"name": name} if name else None}


def _date_prop_val(value):
    return {"type": "date", "date": {"start": value} if value else None}


def _number_prop_val(value):
    return {"type": "number", "number": value}


def _page(id_, properties):
    return {"object": "page", "id": id_, "properties": properties}


def _block(id_, type_, text):
    return {"object": "block", "id": id_, "type": type_, type_: {"rich_text": [{"plain_text": text}]}}


def _chore_row(id_, name, status, rule, why="", dry_ok=0, runs=0, failures=0, last_run=None, last_result=""):
    return _page(id_, {
        "Name": _title_prop_val(name),
        "Status": _select_prop_val(status),
        "Rule": _rich_text_prop_val(json.dumps(rule)),
        "Why": _rich_text_prop_val(why),
        "DryRunsOK": _number_prop_val(dry_ok),
        "Runs": _number_prop_val(runs),
        "Failures": _number_prop_val(failures),
        "LastRun": _date_prop_val(last_run),
        "LastResult": _rich_text_prop_val(last_result),
    })


def _catalog_row(id_, name, kind, action, function, notes=""):
    return _page(id_, {
        "Name": _title_prop_val(name),
        "Kind": _select_prop_val(kind),
        "Method": _select_prop_val("POST"),
        "Path": _rich_text_prop_val("/index.php"),
        "Action": _rich_text_prop_val(action),
        "Function": _rich_text_prop_val(function),
        "Params": _rich_text_prop_val(""),
        "LastVerified": _date_prop_val("2026-09-25"),
        "Notes": _rich_text_prop_val(notes),
    })


@pytest.fixture(autouse=True)
def _stub_daily_log():
    """Most `run` tests don't assert on Daily Log writes — stub them out so a
    dry-run/execution match doesn't fall through to a real Notion HTTP call.
    Tests that DO assert on logging re-patch these explicitly."""
    with patch("chores.kb._ensure_daily_log_page", return_value=("log-page", "hdr-block")), \
         patch("chores.nc.append_blocks"):
        yield


WINE_RULE = {
    "scope": "each_city",
    "when": [
        {"field": "wine_hours_left", "op": "<", "value": 6},
        {"field": "tavern.wine_level", "op": ">", "value": 0},
    ],
    "do": {"primitive": "transport", "resource": "wine", "amount": 1500, "from": "richest", "to": "$city"},
    "limits": {"cooldown_min": 120, "max_per_day": 6, "max_share_of_source": 0.3},
}


def state_fixture(**overrides):
    """A realistic session/state.json fixture following docs/CHORES.md §1."""
    state = {
        "time": 1790336336,
        "gold": 30456,
        "gold_per_hour": 2229,
        "transporters": {"free": 8, "max": 8},
        "alerts": {"under_attack": False, "unread_messages": 0},
        "queue": [{"city": "Async", "done_in_min": 108}],
        "cities": [
            {
                "id": "355955",
                "name": "Kernel",
                "coords": "53:67",
                "island_id": "692",
                "tradegood": "wine",
                "resources": {"wood": 69474, "wine": 2000, "marble": 11, "crystal": 181, "sulfur": 1017},
                "max_resources": 73920,
                "production_per_hour": {"wood": 548, "tradegood": 120},
                "wine_per_hour": 44,
                "wine_hours_left": 4.5,
                "tavern": {"position": 7, "level": 8, "wine_level": 6, "max_wine_level": 8},
                "citizens": 182,
                "population": 812,
                "max_population": 900,
                "growth_per_hour": 0.09,
                "happiness": "neutral",
                "corruption_pct": 3,
                "workers": {"wood": 100, "tradegood": 50, "scientists": 20, "priests": 0},
                "construction": [{"building": "Shipyard", "position": 1, "done_in_min": 14}],
                "buildings": [{"position": 0, "name": "Town Hall", "level": 11, "busy": False}],
            },
            {
                "id": "356174",
                "name": "Daemon",
                "coords": "54:68",
                "island_id": "693",
                "tradegood": "marble",
                "resources": {"wood": 40000, "wine": 30000, "marble": 5000, "crystal": 500, "sulfur": 800},
                "max_resources": 60000,
                "production_per_hour": {"wood": 300, "tradegood": 80},
                "wine_per_hour": 10,
                "wine_hours_left": 3000.0,
                "tavern": {"position": 5, "level": 4, "wine_level": 2, "max_wine_level": 4},
                "citizens": 150,
                "population": 600,
                "max_population": 700,
                "growth_per_hour": 0.05,
                "happiness": "content",
                "corruption_pct": 1,
                "workers": {"wood": 80, "tradegood": 40, "scientists": 10, "priests": 0},
                "construction": [],
                "buildings": [],
            },
        ],
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# rule validation
# ---------------------------------------------------------------------------


def test_validate_rule_accepts_wine_resupply_sample():
    assert chores.validate_rule(copy.deepcopy(WINE_RULE))["scope"] == "each_city"


@pytest.mark.parametrize("mutate,fragment", [
    (lambda r: r.pop("scope"), "missing"),
    (lambda r: r.__setitem__("scope", "nonsense"), "scope"),
    (lambda r: r.__setitem__("when", []), "when"),
    (lambda r: r["when"].append({"field": "x", "op": "weird", "value": 1}), "op"),
    (lambda r: r["when"].append({"field": "x", "op": "<", "value": None}), "value"),
    (lambda r: r["do"].__setitem__("primitive", "nonsense"), "primitive"),
])
def test_validate_rule_rejects_malformed_rules(mutate, fragment):
    rule = copy.deepcopy(WINE_RULE)
    mutate(rule)
    with pytest.raises(chores.ChoreError, match=fragment):
        chores.validate_rule(rule)


def test_validate_rule_requires_max_share_of_source_for_transport():
    rule = copy.deepcopy(WINE_RULE)
    del rule["limits"]["max_share_of_source"]
    with pytest.raises(chores.ChoreError, match="max_share_of_source"):
        chores.validate_rule(rule)


def test_validate_rule_rejects_city_ref_in_global_scope():
    rule = {
        "scope": "global",
        "when": [{"field": "gold", "op": ">", "value": 1000}],
        "do": {"primitive": "set_tavern_wine", "city": "$city", "level": 3},
        "limits": {"cooldown_min": 60, "max_per_day": 1},
    }
    with pytest.raises(chores.ChoreError, match="\\$city"):
        chores.validate_rule(rule)


def test_validate_rule_set_workers_requires_at_least_one_field():
    rule = {
        "scope": "each_city",
        "when": [{"field": "citizens", "op": ">", "value": 0}],
        "do": {"primitive": "set_workers", "city": "$city"},
        "limits": {"cooldown_min": 0, "max_per_day": 1},
    }
    with pytest.raises(chores.ChoreError, match="at least one"):
        chores.validate_rule(rule)


def test_validate_rule_set_workers_rejects_multiple_max_fields():
    rule = {
        "scope": "each_city",
        "when": [{"field": "citizens", "op": ">", "value": 0}],
        "do": {"primitive": "set_workers", "city": "$city", "wood": "max", "scientists": "max"},
        "limits": {"cooldown_min": 0, "max_per_day": 1},
    }
    with pytest.raises(chores.ChoreError, match="one field"):
        chores.validate_rule(rule)


def test_validate_rule_catalog_action_requires_name():
    rule = {
        "scope": "global",
        "when": [{"field": "gold", "op": ">", "value": 0}],
        "do": {"primitive": "catalog_action", "params": {}},
        "limits": {"cooldown_min": 0, "max_per_day": 1},
    }
    with pytest.raises(chores.ChoreError, match="do.name"):
        chores.validate_rule(rule)


# ---------------------------------------------------------------------------
# rule evaluation
# ---------------------------------------------------------------------------


def test_evaluate_rule_each_city_matches_only_qualifying_cities():
    state = state_fixture()
    matches = chores.evaluate_rule(WINE_RULE, state)
    assert [m["scope_key"] for m in matches] == ["355955"]
    assert matches[0]["city"]["name"] == "Kernel"


def test_evaluate_rule_null_field_makes_clause_false():
    state = state_fixture()
    state["cities"][0]["wine_hours_left"] = None
    matches = chores.evaluate_rule(WINE_RULE, state)
    assert matches == []


def test_evaluate_rule_dotted_path_into_missing_parent_is_null():
    rule = copy.deepcopy(WINE_RULE)
    rule["when"] = [{"field": "tavern.nonexistent.deep", "op": "==", "value": 1}]
    state = state_fixture()
    assert chores.evaluate_rule(rule, state) == []


def test_evaluate_rule_global_scope_reads_top_level_state():
    rule = {
        "scope": "global",
        "when": [{"field": "gold", "op": ">", "value": 10000}],
        "do": {"primitive": "catalog_action", "name": "x", "params": {}},
        "limits": {"cooldown_min": 0, "max_per_day": 1},
    }
    state = state_fixture()
    matches = chores.evaluate_rule(rule, state)
    assert len(matches) == 1
    assert matches[0]["scope_key"] == "global"
    assert matches[0]["city"] is None


def test_evaluate_rule_and_semantics_requires_all_clauses():
    rule = copy.deepcopy(WINE_RULE)
    state = state_fixture()
    state["cities"][0]["tavern"]["wine_level"] = 0  # second clause now fails
    assert chores.evaluate_rule(rule, state) == []


# ---------------------------------------------------------------------------
# primitive: transport
# ---------------------------------------------------------------------------


def test_build_transport_params_match_catalog_documented_shape():
    state = state_fixture()
    match = {"scope_key": "355955", "city": state["cities"][0]}
    action, function, params, desc, switch_city = chores.build_transport(WINE_RULE, state, match)

    assert action == "transportOperations"
    assert function == "loadTransportersWithFreight"
    # Catalog notes: destinationCityId, islandId, currentCityId,
    # transporters=<used>, normalTransportersMax=<available>,
    # cargo_resource=<wood>, cargo_tradegood1..4=<wine/marble/crystal/sulfur>.
    assert params["destinationCityId"] == "355955"
    assert params["islandId"] == "692"  # destination's island
    assert params["currentCityId"] == "356174"  # source (origin) city — must be current
    assert switch_city == "356174"
    assert params["normalTransportersMax"] == 8
    assert params["cargo_tradegood1"] == 1500  # wine slot
    assert params["cargo_resource"] == 0
    assert params["cargo_tradegood2"] == 0 and params["cargo_tradegood3"] == 0 and params["cargo_tradegood4"] == 0
    assert params["transporters"] == 3  # ceil(1500/500)
    # fixed boilerplate documented in the catalog
    assert params["premiumTransporter"] == 0
    assert params["jetPropulsion"] == 0
    assert params["max_capacity"] == 5
    assert "1500" in desc and "wine" in desc


def test_build_transport_clamps_to_max_share_of_source():
    rule = copy.deepcopy(WINE_RULE)
    rule["do"]["amount"] = 100000  # far more than any clamp allows
    state = state_fixture()
    state["transporters"]["free"] = 100  # ship capacity should not be the binding constraint here
    match = {"scope_key": "355955", "city": state["cities"][0]}
    _, _, params, _, _ = chores.build_transport(rule, state, match)
    # richest source is Daemon (356174) with 30000 wine; share cap = 0.3*30000=9000
    assert params["cargo_tradegood1"] == 9000


def test_build_transport_clamps_to_free_transporter_capacity():
    rule = copy.deepcopy(WINE_RULE)
    rule["do"]["amount"] = 100000
    rule["limits"]["max_share_of_source"] = 1.0
    state = state_fixture()
    state["transporters"]["free"] = 1  # only 500 capacity
    match = {"scope_key": "355955", "city": state["cities"][0]}
    _, _, params, _, _ = chores.build_transport(rule, state, match)
    assert params["cargo_tradegood1"] == 500
    assert params["transporters"] == 1


def test_build_transport_fails_chore_when_no_free_transporters():
    state = state_fixture()
    state["transporters"]["free"] = 0
    match = {"scope_key": "355955", "city": state["cities"][0]}
    with pytest.raises(chores.ChoreError, match="no free transporters"):
        chores.build_transport(WINE_RULE, state, match)


def test_build_transport_fails_chore_when_destination_island_id_null():
    state = state_fixture()
    state["cities"][0]["island_id"] = None
    match = {"scope_key": "355955", "city": state["cities"][0]}
    with pytest.raises(chores.ChoreError, match="island_id"):
        chores.build_transport(WINE_RULE, state, match)


def test_build_transport_richest_excludes_destination_city():
    state = state_fixture()
    # Only two cities; make destination (Kernel) also the richest by wine —
    # richest must still pick Daemon since Kernel is excluded.
    state["cities"][0]["resources"]["wine"] = 999999
    match = {"scope_key": "355955", "city": state["cities"][0]}
    _, _, params, _, switch_city = chores.build_transport(WINE_RULE, state, match)
    assert switch_city == "356174"


def test_build_transport_explicit_from_city_id():
    rule = copy.deepcopy(WINE_RULE)
    rule["do"]["from"] = "356174"
    state = state_fixture()
    match = {"scope_key": "355955", "city": state["cities"][0]}
    _, _, params, _, switch_city = chores.build_transport(rule, state, match)
    assert switch_city == "356174"


# ---------------------------------------------------------------------------
# primitive: set_tavern_wine
# ---------------------------------------------------------------------------


def test_build_set_tavern_wine_fixed_level():
    rule = {
        "scope": "each_city",
        "when": [{"field": "wine_hours_left", "op": ">", "value": 0}],
        "do": {"primitive": "set_tavern_wine", "city": "$city", "level": 3},
        "limits": {"cooldown_min": 0, "max_per_day": 1},
    }
    state = state_fixture()
    match = {"scope_key": "355955", "city": state["cities"][0]}
    action, function, params, desc, switch_city = chores.build_set_tavern_wine(rule, state, match)
    assert action == "CityScreen" and function == "assignWinePerTick"
    assert params == {"cityId": "355955", "position": "7", "amount": 3, "currentCityId": "355955"}
    assert switch_city == "355955"


def test_build_set_tavern_wine_fails_when_tavern_position_missing():
    rule = {
        "scope": "each_city",
        "when": [{"field": "wine_hours_left", "op": ">", "value": 0}],
        "do": {"primitive": "set_tavern_wine", "city": "$city", "level": 3},
        "limits": {"cooldown_min": 0, "max_per_day": 1},
    }
    state = state_fixture()
    state["cities"][0]["tavern"]["position"] = None
    match = {"scope_key": "355955", "city": state["cities"][0]}
    with pytest.raises(chores.ChoreError, match="tavern.position"):
        chores.build_set_tavern_wine(rule, state, match)


def test_build_set_tavern_wine_max_affordable_picks_highest_sustainable_level():
    rule = {
        "scope": "each_city",
        "when": [{"field": "wine_hours_left", "op": ">", "value": 0}],
        "do": {"primitive": "set_tavern_wine", "city": "$city", "level": "max_affordable"},
        "limits": {"cooldown_min": 0, "max_per_day": 1},
    }
    state = state_fixture()
    city = state["cities"][0]
    city["resources"]["wine"] = 2000
    city["wine_per_hour"] = 44
    # ladder: [0,12,24,39,54,72,90,108]; net at L3=44-39=5>=0 -> infinite hours (affordable)
    # net at L4=44-54=-10 -> 2000/10=200h >=12 (affordable); L5=44-72=-28 -> 2000/28=71h (affordable)
    # L6=44-90=-46 -> 2000/46=43h (affordable); L7=44-108=-64 -> 2000/64=31h (affordable, still >=12)
    # max_wine_level caps it at 8 (ladder only goes to 7 anyway)
    match = {"scope_key": "355955", "city": city}
    _, _, params, _, _ = chores.build_set_tavern_wine(rule, state, match)
    assert params["amount"] == 7


def test_build_set_tavern_wine_max_affordable_low_stock_picks_low_level():
    rule = {
        "scope": "each_city",
        "when": [{"field": "wine_hours_left", "op": ">", "value": 0}],
        "do": {"primitive": "set_tavern_wine", "city": "$city", "level": "max_affordable"},
        "limits": {"cooldown_min": 0, "max_per_day": 1},
    }
    state = state_fixture()
    city = state["cities"][0]
    city["resources"]["wine"] = 50
    city["wine_per_hour"] = 0
    match = {"scope_key": "355955", "city": city}
    _, _, params, _, _ = chores.build_set_tavern_wine(rule, state, match)
    # L1 consumption=12/h -> 50/12=4.2h < 12h -> not affordable; L0 always affordable (0 consumption)
    assert params["amount"] == 0


def test_build_set_tavern_wine_clamps_explicit_level_to_max_wine_level():
    rule = {
        "scope": "each_city",
        "when": [{"field": "wine_hours_left", "op": ">", "value": 0}],
        "do": {"primitive": "set_tavern_wine", "city": "$city", "level": 99},
        "limits": {"cooldown_min": 0, "max_per_day": 1},
    }
    state = state_fixture()
    match = {"scope_key": "355955", "city": state["cities"][0]}
    _, _, params, _, _ = chores.build_set_tavern_wine(rule, state, match)
    assert params["amount"] == 8  # city's max_wine_level


# ---------------------------------------------------------------------------
# primitive: set_workers
# ---------------------------------------------------------------------------


def test_build_set_workers_int_targets_send_full_new_totals():
    rule = {
        "scope": "each_city",
        "when": [{"field": "citizens", "op": ">", "value": 0}],
        "do": {"primitive": "set_workers", "city": "$city", "wood": 120},
        "limits": {"cooldown_min": 0, "max_per_day": 1},
    }
    state = state_fixture()
    match = {"scope_key": "355955", "city": state["cities"][0]}
    action, function, params, desc, switch_city = chores.build_set_workers(rule, state, match)
    assert action == "IslandScreen" and function == "workerPlan"
    # rule field "tradegood" maps to HTTP param "luxury"; unset fields keep current totals
    assert params["wood"] == 120
    assert params["luxury"] == 50  # unchanged, current tradegood count
    assert params["scientists"] == 20
    assert params["priests"] == 0
    assert params["screen"] == "TownHall"
    assert switch_city == "355955"


def test_build_set_workers_max_assigns_free_citizens():
    rule = {
        "scope": "each_city",
        "when": [{"field": "citizens", "op": ">", "value": 0}],
        "do": {"primitive": "set_workers", "city": "$city", "scientists": "max"},
        "limits": {"cooldown_min": 0, "max_per_day": 1},
    }
    state = state_fixture()
    city = state["cities"][0]
    # citizens=182, assigned=100+50+20+0=170 -> free=12
    match = {"scope_key": "355955", "city": city}
    _, _, params, _, _ = chores.build_set_workers(rule, state, match)
    assert params["scientists"] == 20 + 12


def test_build_set_workers_fails_when_current_count_missing_for_unset_field():
    rule = {
        "scope": "each_city",
        "when": [{"field": "citizens", "op": ">", "value": 0}],
        "do": {"primitive": "set_workers", "city": "$city", "wood": 10},
        "limits": {"cooldown_min": 0, "max_per_day": 1},
    }
    state = state_fixture()
    state["cities"][0]["workers"]["priests"] = None
    match = {"scope_key": "355955", "city": state["cities"][0]}
    with pytest.raises(chores.ChoreError, match="priests"):
        chores.build_set_workers(rule, state, match)


# ---------------------------------------------------------------------------
# primitive: catalog_action
# ---------------------------------------------------------------------------


def test_build_catalog_action_substitutes_city_and_passes_params():
    rule = {
        "scope": "each_city",
        "when": [{"field": "citizens", "op": ">", "value": 0}],
        "do": {"primitive": "catalog_action", "name": "action:CityScreen:rename", "params": {"cityId": "$city", "name": "NewName"}},
        "limits": {"cooldown_min": 0, "max_per_day": 1},
    }
    state = state_fixture()
    match = {"scope_key": "355955", "city": state["cities"][0]}
    catalog_rows = [_catalog_row("c1", "action:CityScreen:rename", "action", "CityScreen", "rename")]
    action, function, params, desc, switch_city = chores.build_catalog_action(rule, state, match, catalog_rows)
    assert action == "CityScreen" and function == "rename"
    assert params == {"cityId": "355955", "name": "NewName"}
    assert switch_city == "355955"


def test_build_catalog_action_unknown_row_fails_chore():
    rule = {
        "scope": "global",
        "when": [{"field": "gold", "op": ">", "value": 0}],
        "do": {"primitive": "catalog_action", "name": "action:Nonexistent", "params": {}},
        "limits": {"cooldown_min": 0, "max_per_day": 1},
    }
    state = state_fixture()
    match = {"scope_key": "global", "city": None}
    with pytest.raises(chores.ChoreError, match="no Action Catalog row"):
        chores.build_catalog_action(rule, state, match, [])


def test_build_catalog_action_rejects_nav_kind_rows():
    rule = {
        "scope": "global",
        "when": [{"field": "gold", "op": ">", "value": 0}],
        "do": {"primitive": "catalog_action", "name": "view:townHall", "params": {}},
        "limits": {"cooldown_min": 0, "max_per_day": 1},
    }
    state = state_fixture()
    match = {"scope_key": "global", "city": None}
    catalog_rows = [_catalog_row("c1", "view:townHall", "nav", "", "")]
    with pytest.raises(chores.ChoreError, match="kind="):
        chores.build_catalog_action(rule, state, match, catalog_rows)


# ---------------------------------------------------------------------------
# guardrails: denylist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,notes", [
    ("action:Premium:buyGold", ""),
    ("action:Something", "this uses Ambrosia currency"),
    ("action:Clan:leaveClan", ""),
    ("action:Empire:disbandArmy", ""),
    ("action:City:abandonCity", ""),
])
def test_catalog_action_denies_by_name_or_notes_keywords(name, notes):
    rule = {
        "scope": "global",
        "when": [{"field": "gold", "op": ">", "value": 0}],
        "do": {"primitive": "catalog_action", "name": name, "params": {}},
        "limits": {"cooldown_min": 0, "max_per_day": 1},
    }
    state = state_fixture()
    match = {"scope_key": "global", "city": None}
    catalog_rows = [_catalog_row("c1", name, "action", "Something", "doit", notes=notes)]
    with pytest.raises(chores.ChoreError, match="guardrail"):
        chores.build_catalog_action(rule, state, match, catalog_rows)


def test_build_primitive_denylist_applies_uniformly_via_dispatcher():
    rule = {
        "scope": "global",
        "when": [{"field": "gold", "op": ">", "value": 0}],
        "do": {"primitive": "catalog_action", "name": "action:X", "params": {}},
        "limits": {"cooldown_min": 0, "max_per_day": 1},
    }
    state = state_fixture()
    match = {"scope_key": "global", "city": None}
    catalog_rows = [_catalog_row("c1", "action:X", "action", "X", "premiumBuy", notes="")]
    with pytest.raises(chores.ChoreError, match="guardrail"):
        chores.build_primitive(rule, state, match, catalog_rows)


# ---------------------------------------------------------------------------
# limits: cooldown / max_per_day (via _limited helper)
# ---------------------------------------------------------------------------


def test_limited_enforces_cooldown():
    entry = chores._runtime_entry({}, "wine-resupply")
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    chores._record_fired(entry, "355955", now, "2026-09-25")
    limits = {"cooldown_min": 120, "max_per_day": 6}
    soon = now.replace(minute=30)
    assert chores._limited(entry, "355955", limits, soon, "2026-09-25") == "cooldown (120m)"
    later = now.replace(hour=14, minute=1)
    assert chores._limited(entry, "355955", limits, later, "2026-09-25") is None


def test_limited_enforces_max_per_day():
    entry = chores._runtime_entry({}, "wine-resupply")
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    limits = {"cooldown_min": 0, "max_per_day": 2}
    chores._record_fired(entry, "355955", now, "2026-09-25")
    chores._record_fired(entry, "355955", now, "2026-09-25")
    assert chores._limited(entry, "355955", limits, now, "2026-09-25") == "max_per_day (2)"
    # a different city (scope key) is unaffected
    assert chores._limited(entry, "356174", limits, now, "2026-09-25") is None
    # a new day resets the count
    assert chores._limited(entry, "355955", limits, now, "2026-09-26") is None


# ---------------------------------------------------------------------------
# CLI: full run() lifecycle, mocked HTTP
# ---------------------------------------------------------------------------


def _write_state(tmp_path, state):
    (tmp_path / "session" / "state.json").write_text(json.dumps(state))


@patch("chores.nc.update_page")
@patch("chores.nc.query_database")
def test_run_flips_proposed_to_dry_run_and_records_dry_run_ok(mock_query, mock_update, tmp_path):
    _write_env(tmp_path)
    _write_state(tmp_path, state_fixture())
    row = _chore_row("row1", "wine-resupply", "proposed", WINE_RULE)
    mock_query.return_value = [row]

    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    rc = chores.main(["run"], project_root=tmp_path, now=now)
    assert rc == 0

    update_calls = mock_update.call_args_list
    # first call flips Status proposed -> dry-run
    assert update_calls[0].args[1] == "row1"
    assert update_calls[0].args[2] == {"Status": nc.select_prop("dry-run")}
    # second call records the dry run
    assert update_calls[1].args[2]["DryRunsOK"] == nc.number_prop(1)

    result = json.loads((tmp_path / "session" / "chores_last.json").read_text())
    assert len(result["dry_runs"]) == 1
    assert result["dry_runs"][0]["chore"] == "wine-resupply"
    assert result["executed"] == []


@patch("chores.ic.call_action")
@patch("chores.nc.update_page")
@patch("chores.nc.query_database")
def test_dry_run_never_calls_the_game(mock_query, mock_update, mock_call_action, tmp_path):
    _write_env(tmp_path)
    _write_state(tmp_path, state_fixture())
    row = _chore_row("row1", "wine-resupply", "dry-run", WINE_RULE, dry_ok=1)
    mock_query.return_value = [row]

    rc = chores.main(["run"], project_root=tmp_path, now=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc))
    assert rc == 0
    mock_call_action.assert_not_called()


@patch("chores.ss.write_action_request")
@patch("chores.ss.read_action_request", return_value="tok0")
@patch("chores.ss.read_cookie", return_value="cookie")
@patch("chores.ic.call_nav")
@patch("chores.ic.call_action")
@patch("chores.nc.append_blocks")
@patch("chores.kb._ensure_daily_log_page", return_value=("log-page", "hdr-block"))
@patch("chores.nc.update_page")
@patch("chores.nc.query_database")
def test_active_chore_executes_switches_city_and_logs(
    mock_query, mock_update, mock_ensure_log, mock_append, mock_call_action, mock_call_nav,
    mock_read_cookie, mock_read_token, mock_write_token, tmp_path,
):
    _write_env(tmp_path)
    _write_state(tmp_path, state_fixture())
    row = _chore_row("row1", "wine-resupply", "active", WINE_RULE, dry_ok=3)
    mock_query.return_value = [row]

    # changeCurrentCity response (no updateGlobalData, per catalog gotcha)
    mock_call_action.side_effect = [
        [["custom", ["reload"]]],  # changeCurrentCity
        [["updateGlobalData", {"actionRequest": "tok2"}],
         ["provideFeedback", [{"location": 2, "text": "Your order has been carried out.", "type": 10}]]],
    ]
    mock_call_nav.return_value = [["updateGlobalData", {"actionRequest": "tok1"}]]

    rc = chores.main(["run"], project_root=tmp_path, now=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc))
    assert rc == 0

    # changeCurrentCity -> nav(townHall) -> real action, in that order
    assert mock_call_action.call_args_list[0].args[3] == "header"
    assert mock_call_action.call_args_list[1].args[3] == "transportOperations"
    mock_call_nav.assert_called_once()

    result = json.loads((tmp_path / "session" / "chores_last.json").read_text())
    assert len(result["executed"]) == 1
    assert result["executed"][0]["result"] == "ok"

    # Runs incremented on the chore row
    runs_updates = [c for c in mock_update.call_args_list if "Runs" in c.args[2]]
    assert runs_updates and runs_updates[0].args[2]["Runs"] == nc.number_prop(1)

    # logged to the Daily Log under a Chores heading
    append_children = mock_append.call_args.args[2]
    assert append_children[0]["type"] == "heading_3"
    assert "Chores" in append_children[0]["heading_3"]["rich_text"][0]["text"]["content"]


@patch("chores.ss.write_action_request")
@patch("chores.ss.read_action_request", return_value="tok0")
@patch("chores.ss.read_cookie", return_value="cookie")
@patch("chores.ic.call_nav")
@patch("chores.ic.call_action")
@patch("chores.nc.append_blocks")
@patch("chores.kb._ensure_daily_log_page", return_value=("log-page", "hdr-block"))
@patch("chores.nc.update_page")
@patch("chores.nc.query_database")
def test_two_consecutive_failures_auto_disable(
    mock_query, mock_update, mock_ensure_log, mock_append, mock_call_action, mock_call_nav,
    mock_read_cookie, mock_read_token, mock_write_token, tmp_path,
):
    _write_env(tmp_path)
    state = state_fixture()
    # make the rule always match without needing a real transport (use set_tavern_wine so a
    # single call_action failure is easy to model deterministically)
    rule = {
        "scope": "each_city",
        "when": [{"field": "wine_hours_left", "op": ">", "value": 0}],
        "do": {"primitive": "set_tavern_wine", "city": "$city", "level": 3},
        "limits": {"cooldown_min": 0, "max_per_day": 100},
    }
    _write_state(tmp_path, state)
    row = _chore_row("row1", "wine-serve", "active", rule)
    mock_query.return_value = [row]
    mock_call_nav.return_value = [["updateGlobalData", {"actionRequest": "tok1"}]]
    failing_action_response = [
        ["updateGlobalData", {"actionRequest": "tok2"}],
        ["provideFeedback", [{"location": 5, "text": "Access denied", "type": 11}]],
    ]
    # two matching cities (Kernel, Daemon), each attempting changeCurrentCity -> action
    mock_call_action.side_effect = [
        [["custom", ["reload"]]], failing_action_response,
        [["custom", ["reload"]]], failing_action_response,
    ]

    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    rc = chores.main(["run"], project_root=tmp_path, now=now)
    assert rc == 0

    # both cities match and both fail -> 2 consecutive failures -> auto-disabled
    disable_updates = [c for c in mock_update.call_args_list if c.args[2].get("Status") == nc.select_prop("disabled")]
    assert disable_updates, "expected the chore to be auto-disabled after 2 consecutive failures"
    result = json.loads((tmp_path / "session" / "chores_last.json").read_text())
    assert "wine-serve" in result["disabled"]
    assert len(result["failures"]) == 2


@patch("chores.nc.update_page")
@patch("chores.nc.query_database")
def test_kill_switch_stops_all_chores(mock_query, mock_update, tmp_path):
    _write_env(tmp_path)
    _write_state(tmp_path, state_fixture())
    kill_row = _chore_row("kill1", chores.KILL_SWITCH_NAME, "active", {
        "scope": "global", "when": [{"field": "gold", "op": ">", "value": 0}],
        "do": {"primitive": "catalog_action", "name": "x", "params": {}},
        "limits": {"cooldown_min": 0, "max_per_day": 1},
    })
    chore_row = _chore_row("row1", "wine-resupply", "dry-run", WINE_RULE, dry_ok=1)
    mock_query.return_value = [kill_row, chore_row]

    rc = chores.main(["run"], project_root=tmp_path, now=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc))
    assert rc == 0
    mock_update.assert_not_called()
    result = json.loads((tmp_path / "session" / "chores_last.json").read_text())
    assert result == {"executed": [], "dry_runs": [], "failures": [], "disabled": [], "pending_review": [], "skipped": []}


@patch("chores.nc.update_page")
@patch("chores.nc.query_database")
def test_max_5_executed_actions_per_tick(mock_query, mock_update, tmp_path):
    _write_env(tmp_path)
    # 7 cities all matching an each_city rule that would execute for real
    state = state_fixture()
    base_city = state["cities"][0]
    state["cities"] = []
    for i in range(7):
        c = copy.deepcopy(base_city)
        c["id"] = f"city{i}"
        c["tavern"]["position"] = 1
        state["cities"].append(c)
    _write_state(tmp_path, state)

    rule = {
        "scope": "each_city",
        "when": [{"field": "wine_hours_left", "op": ">", "value": 0}],
        "do": {"primitive": "set_tavern_wine", "city": "$city", "level": 3},
        "limits": {"cooldown_min": 0, "max_per_day": 100},
    }
    row = _chore_row("row1", "wine-serve", "active", rule)
    mock_query.return_value = [row]

    with patch("chores.ss.read_cookie", return_value="cookie"), \
         patch("chores.ss.read_action_request", return_value="tok0"), \
         patch("chores.ss.write_action_request"), \
         patch("chores.ic.call_nav", return_value=[["updateGlobalData", {"actionRequest": "tok1"}]]), \
         patch("chores.ic.call_action") as mock_call_action, \
         patch("chores.kb._ensure_daily_log_page", return_value=("log-page", "hdr-block")), \
         patch("chores.nc.append_blocks"):
        mock_call_action.side_effect = [
            [["custom", ["reload"]]] if i % 2 == 0 else
            [["updateGlobalData", {"actionRequest": f"tok{i}"}],
             ["provideFeedback", [{"location": 2, "text": "ok", "type": 10}]]]
            for i in range(14)
        ]
        rc = chores.main(["run"], project_root=tmp_path, now=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc))
        assert rc == 0

    result = json.loads((tmp_path / "session" / "chores_last.json").read_text())
    assert len(result["executed"]) == 5
    assert any(s["reason"].startswith("tick limit") for s in result["skipped"])


@patch("chores.nc.update_page")
@patch("chores.nc.query_database")
def test_disabled_chore_is_skipped_entirely(mock_query, mock_update, tmp_path):
    _write_env(tmp_path)
    _write_state(tmp_path, state_fixture())
    row = _chore_row("row1", "wine-resupply", "disabled", WINE_RULE)
    mock_query.return_value = [row]
    rc = chores.main(["run"], project_root=tmp_path, now=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc))
    assert rc == 0
    mock_update.assert_not_called()
    result = json.loads((tmp_path / "session" / "chores_last.json").read_text())
    assert result["dry_runs"] == [] and result["executed"] == []


@patch("chores.nc.query_database")
def test_run_dry_run_all_preview_makes_no_notion_writes(mock_query, tmp_path):
    _write_env(tmp_path)
    _write_state(tmp_path, state_fixture())
    row = _chore_row("row1", "wine-resupply", "active", WINE_RULE, dry_ok=3)
    mock_query.return_value = [row]
    with patch("chores.nc.update_page") as mock_update, patch("chores.ic.call_action") as mock_call_action:
        rc = chores.main(["run", "--dry-run-all"], project_root=tmp_path, now=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc))
        assert rc == 0
        mock_update.assert_not_called()
        mock_call_action.assert_not_called()
    result = json.loads((tmp_path / "session" / "chores_last.json").read_text())
    assert len(result["dry_runs"]) == 1


# ---------------------------------------------------------------------------
# CLI: add / promote / disable / list / show
# ---------------------------------------------------------------------------


@patch("chores.nc.create_page")
@patch("chores.nc.query_database")
def test_add_validates_and_creates_proposed_row(mock_query, mock_create, tmp_path, capsys):
    _write_env(tmp_path)
    mock_query.return_value = []
    rc = chores.main(
        ["add", "wine-resupply", "--why", "keep taverns stocked", "--rule", json.dumps(WINE_RULE)],
        project_root=tmp_path,
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK chore added" in out
    args, kwargs = mock_create.call_args
    assert args[2]["Status"] == nc.select_prop("proposed")


@patch("chores.nc.create_page")
@patch("chores.nc.query_database")
def test_add_rejects_invalid_rule_json(mock_query, mock_create, tmp_path, capsys):
    _write_env(tmp_path)
    mock_query.return_value = []
    rc = chores.main(["add", "bad", "--why", "x", "--rule", "{not json"], project_root=tmp_path)
    err = capsys.readouterr().err
    assert rc == 1
    assert "not valid JSON" in err
    mock_create.assert_not_called()


@patch("chores.nc.create_page")
@patch("chores.nc.query_database")
def test_add_rejects_duplicate_name(mock_query, mock_create, tmp_path, capsys):
    _write_env(tmp_path)
    mock_query.return_value = [_chore_row("row1", "wine-resupply", "active", WINE_RULE)]
    rc = chores.main(["add", "wine-resupply", "--why", "x", "--rule", json.dumps(WINE_RULE)], project_root=tmp_path)
    err = capsys.readouterr().err
    assert rc == 1
    assert "already exists" in err
    mock_create.assert_not_called()


def test_add_accepts_rule_from_file(tmp_path, capsys):
    _write_env(tmp_path)
    rule_path = tmp_path / "rule.json"
    rule_path.write_text(json.dumps(WINE_RULE))
    with patch("chores.nc.query_database", return_value=[]), patch("chores.nc.create_page") as mock_create:
        rc = chores.main(
            ["add", "wine-resupply", "--why", "x", "--rule", f"@{rule_path}"], project_root=tmp_path,
        )
        assert rc == 0
        mock_create.assert_called_once()


@patch("chores.nc.update_page")
@patch("chores.nc.query_database")
def test_promote_requires_three_dry_runs_ok(mock_query, mock_update, tmp_path, capsys):
    _write_env(tmp_path)
    mock_query.return_value = [_chore_row("row1", "wine-resupply", "dry-run", WINE_RULE, dry_ok=2)]
    rc = chores.main(["promote", "wine-resupply"], project_root=tmp_path)
    err = capsys.readouterr().err
    assert rc == 1
    assert "2/3" in err
    mock_update.assert_not_called()


@patch("chores.nc.update_page")
@patch("chores.nc.query_database")
def test_promote_succeeds_at_three_dry_runs_ok(mock_query, mock_update, tmp_path, capsys):
    _write_env(tmp_path)
    mock_query.return_value = [_chore_row("row1", "wine-resupply", "dry-run", WINE_RULE, dry_ok=3)]
    rc = chores.main(["promote", "wine-resupply"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "-> active" in out
    mock_update.assert_called_once_with("ntn_test", "row1", {"Status": nc.select_prop("active")})


@patch("chores.nc.update_page")
@patch("chores.nc.query_database")
def test_disable_sets_status(mock_query, mock_update, tmp_path, capsys):
    _write_env(tmp_path)
    mock_query.return_value = [_chore_row("row1", "wine-resupply", "active", WINE_RULE)]
    rc = chores.main(["disable", "wine-resupply"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK chore disabled" in out
    args, kwargs = mock_update.call_args
    assert args[2]["Status"] == nc.select_prop("disabled")


@patch("chores.nc.query_database")
def test_list_prints_one_line_per_chore(mock_query, tmp_path, capsys):
    _write_env(tmp_path)
    mock_query.return_value = [_chore_row("row1", "wine-resupply", "active", WINE_RULE, dry_ok=3, runs=5, failures=1)]
    rc = chores.main(["list"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "wine-resupply | active | DryRunsOK=3 Runs=5 Failures=1" in out


@patch("chores.nc.query_database")
def test_show_prints_full_rule(mock_query, tmp_path, capsys):
    _write_env(tmp_path)
    mock_query.return_value = [_chore_row("row1", "wine-resupply", "active", WINE_RULE, why="keep taverns stocked")]
    rc = chores.main(["show", "wine-resupply"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "--- wine-resupply ---" in out
    assert "keep taverns stocked" in out
    assert '"primitive": "transport"' in out


@patch("chores.nc.query_database")
def test_show_unknown_chore_errors(mock_query, tmp_path, capsys):
    _write_env(tmp_path)
    mock_query.return_value = []
    rc = chores.main(["show", "nonexistent"], project_root=tmp_path)
    err = capsys.readouterr().err
    assert rc == 1
    assert "no chore named" in err


# ---------------------------------------------------------------------------
# Chores DB lazily created / persisted
# ---------------------------------------------------------------------------


@patch("chores.update_env_file")
@patch("chores.bk.ensure_database", return_value="new-chores-db")
@patch("chores.nc.query_database")
def test_chores_db_created_lazily_when_missing(mock_query, mock_ensure, mock_update_env, tmp_path):
    _write_env(tmp_path, NOTION_CHORES_DB_ID="")
    mock_query.return_value = []
    rc = chores.main(["list"], project_root=tmp_path)
    assert rc == 0
    mock_ensure.assert_called_once_with("ntn_test", "root-page-id", "Chores", chores.bk.CHORES_SCHEMA)
    mock_update_env.assert_called_once()
    args, kwargs = mock_update_env.call_args
    assert args[1] == {"NOTION_CHORES_DB_ID": "new-chores-db"}


# ---------------------------------------------------------------------------
# state file handling
# ---------------------------------------------------------------------------


@patch("chores.nc.query_database")
def test_run_errors_clearly_when_state_file_missing(mock_query, tmp_path, capsys):
    _write_env(tmp_path)
    mock_query.return_value = []
    rc = chores.main(["run"], project_root=tmp_path)
    err = capsys.readouterr().err
    assert rc == 1
    assert "state file not found" in err


@patch("chores.nc.update_page")
@patch("chores.nc.query_database")
def test_run_respects_state_path_override(mock_query, mock_update, tmp_path):
    _write_env(tmp_path)
    custom_path = tmp_path / "custom_state.json"
    custom_path.write_text(json.dumps(state_fixture()))
    row = _chore_row("row1", "wine-resupply", "dry-run", WINE_RULE, dry_ok=1)
    mock_query.return_value = [row]
    rc = chores.main(["run", "--state", str(custom_path)], project_root=tmp_path)
    assert rc == 0
