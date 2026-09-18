import json
from pathlib import Path

from parse_har import bucket_entries, load_har, to_catalog_rows, world_server_entries

HOST = "s401-en.ikariam.gameforge.com"


def _entry(method, url, body_text=None):
    request = {"method": method, "url": url}
    if body_text is not None:
        request["postData"] = {"mimeType": "application/x-www-form-urlencoded", "text": body_text}
    return {"request": request, "response": {"status": 200}}


SAMPLE_ENTRIES = [
    _entry(
        "POST",
        f"https://{HOST}/?view=townHall&cityId=355955&position=0&backgroundView=city&currentCityId=355955&actionRequest=abc123&ajax=1",
    ),
    _entry(
        "POST",
        f"https://{HOST}/?view=townHall&cityId=355955&position=0&backgroundView=city&currentCityId=355955&actionRequest=def456&ajax=1",
    ),
    _entry(
        "POST",
        f"https://{HOST}/index.php",
        body_text=(
            "action=IslandScreen&function=workerPlan&screen=TownHall&type=&cityId=355955"
            "&wood=17&luxury=0&scientists=8&priests=0&backgroundView=city&currentCityId=355955"
            "&templateView=townHall&actionRequest=abc123&ajax=1"
        ),
    ),
    _entry("GET", "https://analytics.google.com/g/collect?v=2"),
]


def test_load_har_reads_entries(tmp_path):
    har_path = tmp_path / "sample.har"
    har_path.write_text(json.dumps({"log": {"entries": SAMPLE_ENTRIES}}))
    assert load_har(har_path) == SAMPLE_ENTRIES


def test_world_server_entries_filters_by_host():
    result = world_server_entries(SAMPLE_ENTRIES, HOST)
    assert len(result) == 3
    assert all(HOST in e["request"]["url"] for e in result)


def test_bucket_entries_dedupes_nav_views_by_view_name():
    navs, actions = bucket_entries(world_server_entries(SAMPLE_ENTRIES, HOST))
    assert list(navs.keys()) == ["townHall"]
    assert set(navs["townHall"]) == {
        "view", "cityId", "position", "backgroundView", "currentCityId", "actionRequest", "ajax",
    }


def test_bucket_entries_extracts_actions_by_action_and_function():
    navs, actions = bucket_entries(world_server_entries(SAMPLE_ENTRIES, HOST))
    assert ("IslandScreen", "workerPlan") in actions
    assert set(actions[("IslandScreen", "workerPlan")]) == {
        "action", "function", "screen", "cityId", "wood", "luxury", "scientists",
        "priests", "backgroundView", "currentCityId", "templateView", "actionRequest", "ajax",
    }


def test_to_catalog_rows_produces_expected_shape():
    navs, actions = bucket_entries(world_server_entries(SAMPLE_ENTRIES, HOST))
    rows = to_catalog_rows(navs, actions)
    names = {row["name"] for row in rows}
    assert names == {"view:townHall", "action:IslandScreen:workerPlan"}
    nav_row = next(r for r in rows if r["name"] == "view:townHall")
    assert nav_row["kind"] == "nav"
    assert nav_row["method"] == "POST"
    assert nav_row["path"] == "/"
    action_row = next(r for r in rows if r["name"] == "action:IslandScreen:workerPlan")
    assert action_row["kind"] == "action"
    assert action_row["path"] == "/index.php"
    assert action_row["action"] == "IslandScreen"
    assert action_row["function"] == "workerPlan"
