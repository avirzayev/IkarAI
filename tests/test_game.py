import json
from unittest.mock import patch

import pytest

import game
import ikariam_client as ic
import session_store as ss

# ---------------------------------------------------------------------------
# Fixtures trimmed from real captured responses (har/sample2.har), cookies /
# tokens replaced with fakes.
# ---------------------------------------------------------------------------

REAL_HEADER_DATA = {
    "ambrosia": 0,
    "gold": 1648.621119364182,
    "freeTransporters": 0,
    "maxTransporters": 0,
    "income": 170.6686944886351,
    "upkeep": 0,
    "currentResources": {
        "citizens": 18.963188276515012,
        "population": 43.96318827651501,
        "resource": 422,
        "2": 0,
        "1": 0,
        "4": 0,
        "3": 0,
    },
    "maxResources": {
        "resource": 2500,
        "0": 2500,
        "1": 2500,
        "2": 2500,
        "3": 2500,
        "4": 2500,
    },
    "resourceProduction": 0.019125000000000003,
    "tradegoodProduction": 0,
    "producedTradegood": 1,
    "maxActionPoints": 3,
}

REAL_TEMPLATE_DATA = {
    "js_TownHallOccupiedSpace": {"text": "43"},
    "js_TownHallMaxInhabitants": {"text": "60"},
    "js_TownHallActionPointsAvailable": {"text": 3},
    "js_TownHallIncomeGoldValue": {"text": "18", "class": "green"},
    "js_TownHallHappinessSmallText": {"text": "happy"},
    "CitizenCount": 18,
    "js_TownHallIncomeGold": {"class": "incomegold_positive"},
    "js_TownHallNoticeBox": {"class": "invisible", "text": "", "notices": []},
    "new_js_params": '{"citizens":43.9631882765,"cityIcons":{"selectIconPopupQuestion":' + ("x" * 300) + '"}}',
}

REAL_CHANGE_VIEW_HTML = (
    '<div class="dynamic">\n'
    '    <h3 class="header">Info</h3>\n'
    '    <div class="content">\n'
    '        <table class="cityinfo">\n'
    '            <tr class="name alt"><td class="nameValue"><span>Name: </span></td>'
    '<td colspan="3"><span id="js_selectedCityName">Stonemorph</span>'
    '(<span id="js_selectedCityLevel">26</span>)</td></tr>\n'
    '            <tr class="owner alt"><td class="nameValue"><span>Player:</span></td>'
    '<td colspan="2"><a href="">Orpheus</a></td></tr>\n'
    '        </table>\n'
    '    </div>\n'
    '</div>\n'
    '<script>var x = 1;</script>'
)

REAL_POSITION_LIST = [
    {
        "buildingId": 0, "name": "Town Hall", "level": 1, "building": "townHall",
        "groundId": 0, "isBusy": False,
    },
    {"buildingId": None, "building": "buildingGround shore", "groundId": 1},
    {"buildingId": None, "building": "buildingGround land", "groundId": 2},
]

# Trimmed from a real captured updateTemplateData for the townHall view
# (session/responses/session.json), tokens/coordinates not present there.
REAL_TOWNHALL_STATS_TEMPLATE = {
    "js_TownHallPopulationGrowthValue": {"text": "0.09 ", "class": "green"},
    "js_TownHallIncomeGoldValue": {"text": "-90", "class": "red"},
    "js_TownHallCorruption": {"text": "3%", "class": "red"},
    "js_TownHallHappinessLargeValue": {"text": "1"},
    "js_TownHallHappinessLargeText": {"text": "neutral"},
    "js_TownHallSatisfactionOverviewWineBoniTavernBonusValue": {"text": "+99"},
    "js_TownHallSatisfactionOverviewWineBoniServeBonusValue": {"text": "+494"},
    "CitizenCount": 106,
    "ResourceWorkerCount": 548,
    "SpecialWorkerCount": 100,
    "ScientistCount": 58,
    "PriestCount": 0,
}

# Trimmed from a real captured headerData.cityDropdownMenu (session.json);
# ids/coords are the player's own test-server cities, no secrets.
REAL_CITY_DROPDOWN_MENU = {
    "city_355955": {"id": 355955, "name": "Kernel", "coords": "[53:67] ", "tradegood": 1, "relationship": "ownCity"},
    "city_356174": {"id": 356174, "name": "Daemon", "coords": "[53:65] ", "tradegood": 2, "relationship": "ownCity"},
    "additionalInfo": "cityCoords",
    "selectedCity": "city_355955",
}


def sample_response(action_request="2d7b402568f190bc6708586a79eed444"):
    return [
        [
            "updateGlobalData",
            {
                "actionRequest": action_request,
                "time": 1789734801,
                "headerData": dict(REAL_HEADER_DATA),
                "backgroundData": {
                    "name": "Polis",
                    "id": 355955,
                    "underConstruction": -1,
                    "endUpgradeTime": -1,
                    "position": REAL_POSITION_LIST,
                },
                "queueETA": '{"version":"abc","items":[]}',
            },
        ],
        ["changeView", ["townHall", REAL_CHANGE_VIEW_HTML]],
        ["updateTemplateData", dict(REAL_TEMPLATE_DATA)],
        ["provideFeedback", [{"location": 5, "text": "", "type": 10}]],
    ]


# ---------------------------------------------------------------------------
# summarize_header
# ---------------------------------------------------------------------------


def test_summarize_header_formats_real_data():
    line = game.summarize_header(REAL_HEADER_DATA)
    assert "gold=1649" in line
    assert "wood 422/2500" in line
    assert "wine 0" in line
    assert "citizens 19" in line
    assert "pop 44" in line
    assert "AP 3" in line


def test_summarize_header_skips_missing_fields():
    header = {"gold": 100}
    line = game.summarize_header(header)
    assert line == "gold=100"


def test_summarize_header_empty_dict_returns_empty_string():
    assert game.summarize_header({}) == ""


def test_summarize_header_shows_transporters_when_present():
    header = {"freeTransporters": 1, "maxTransporters": 1}
    line = game.summarize_header(header)
    assert "transporters 1/1" in line


def test_summarize_header_omits_zero_production_rate():
    header = {"currentResources": {"resource": 100}, "resourceProduction": 0.001}
    line = game.summarize_header(header)
    assert "(+" not in line


def test_summarize_header_rounds_string_gold():
    # Real API responses sometimes serialize gold as a numeric string, e.g.
    # headerData.gold == "29479.83975277733" — must still round to an int.
    header = {"gold": "29479.83975277733"}
    line = game.summarize_header(header)
    assert line == "gold=29480"


def test_summarize_header_rounds_income_upkeep_with_string_gold():
    header = {"gold": "30456.49176993326", "income": 2219.7474135170983, "upkeep": -9}
    line = game.summarize_header(header)
    assert "gold=30456" in line
    assert "(+2229/h)" in line
    assert "." not in line


# ---------------------------------------------------------------------------
# _fmt_num
# ---------------------------------------------------------------------------


def test_fmt_num_rounds_numeric_string():
    assert game._fmt_num("29479.83975277733") == "29480"


def test_fmt_num_rounds_float():
    assert game._fmt_num(1648.621119364182) == "1649"


def test_fmt_num_passes_through_int():
    assert game._fmt_num(422) == "422"


def test_fmt_num_none_is_unknown():
    assert game._fmt_num(None) == "?"


# ---------------------------------------------------------------------------
# html_to_text
# ---------------------------------------------------------------------------


def test_html_to_text_strips_tags_and_scripts():
    text = game.html_to_text(REAL_CHANGE_VIEW_HTML)
    assert "<" not in text
    assert "Stonemorph" in text
    assert "26" in text
    assert "var x = 1" not in text


def test_html_to_text_unescapes_entities():
    assert game.html_to_text("<p>Tom &amp; Jerry</p>") == "Tom & Jerry"


def test_html_to_text_empty_input():
    assert game.html_to_text("") == ""
    assert game.html_to_text(None) == ""


def test_html_to_text_collapses_whitespace():
    text = game.html_to_text("<p>a   b\t\tc</p>")
    assert text == "a b c"


# ---------------------------------------------------------------------------
# summarize_template_data
# ---------------------------------------------------------------------------


def test_summarize_template_data_extracts_text_fields():
    lines = game.summarize_template_data(REAL_TEMPLATE_DATA)
    joined = "\n".join(lines)
    assert "js_TownHallOccupiedSpace: 43" in joined
    assert "js_TownHallActionPointsAvailable: 3" in joined
    assert "CitizenCount: 18" in joined


def test_summarize_template_data_skips_class_only_entries():
    lines = game.summarize_template_data(REAL_TEMPLATE_DATA)
    assert not any("js_TownHallIncomeGold:" in ln for ln in lines)


def test_summarize_template_data_skips_empty_text():
    lines = game.summarize_template_data(REAL_TEMPLATE_DATA)
    assert not any("js_TownHallNoticeBox:" in ln for ln in lines)


def test_summarize_template_data_skips_pure_zero_noise():
    lines = game.summarize_template_data({"js_Something": {"text": 0}})
    assert lines == []


def test_summarize_template_data_skips_huge_blob_values():
    lines = game.summarize_template_data(REAL_TEMPLATE_DATA)
    assert not any("new_js_params" in ln for ln in lines)


def test_summarize_template_data_skips_json_blob_with_embedded_spaces():
    # Real new_js_params values are long JSON blobs that happen to contain
    # human-readable text (e.g. popup questions), so a naive "no spaces"
    # blob check misses them — this pins the fix.
    blob = '{"citizens":1,"cityIcons":{"question":"' + ("this has spaces " * 20) + '"}}'
    lines = game.summarize_template_data({"js_blob": {"text": blob}})
    assert lines == []


def test_summarize_template_data_drops_noise_keys():
    template = {
        "js_chatWindow": {"text": "hello"},
        "js_friendsList": {"text": "someone"},
        "js_advisorTip": {"text": "tip"},
        "js_ingameAdBanner": {"text": "ad"},
        "js_cityDropdownMenu": {"text": "x"},
        "js_cityLeftMenuThing": {"text": "y"},
        "js_TownHallOccupiedSpace": {"text": "43"},
    }
    lines = game.summarize_template_data(template)
    assert len(lines) == 1
    assert "js_TownHallOccupiedSpace: 43" in lines[0]


def test_summarize_template_data_truncates_long_values():
    template = {"js_long": {"text": "word " * 60}}
    lines = game.summarize_template_data(template)
    assert len(lines[0]) <= 160 + len("js_long: ") + 1
    assert lines[0].endswith("…")


# ---------------------------------------------------------------------------
# build_view_summary: grep + max truncation
# ---------------------------------------------------------------------------


def test_build_view_summary_grep_filters_lines():
    data = {
        "changeView": ["townHall", REAL_CHANGE_VIEW_HTML],
        "updateTemplateData": REAL_TEMPLATE_DATA,
    }
    summary = game.build_view_summary(data, "/tmp/x.json", grep="stonemorph")
    assert "Stonemorph" in summary
    assert "CitizenCount" not in summary


def test_build_view_summary_caps_length_with_truncation_marker():
    data = {"updateTemplateData": {f"js_key{i}": {"text": f"value{i}"} for i in range(200)}}
    summary = game.build_view_summary(data, "/tmp/full.json", max_chars=100)
    assert len(summary) > 100  # marker pushes it past the raw cap
    assert summary.endswith("… [truncated, full response: /tmp/full.json]")
    assert len(summary.split("\n… [truncated")[0]) <= 100


def test_build_view_summary_no_truncation_when_under_cap():
    data = {"updateTemplateData": {"js_key": {"text": "short"}}}
    summary = game.build_view_summary(data, "/tmp/full.json", max_chars=2500)
    assert "truncated" not in summary


# ---------------------------------------------------------------------------
# status_lines / queue / construction / positions
# ---------------------------------------------------------------------------


def test_status_lines_includes_header_and_positions():
    global_data = {
        "time": 1000,
        "headerData": REAL_HEADER_DATA,
        "backgroundData": {"underConstruction": -1, "endUpgradeTime": -1, "position": REAL_POSITION_LIST},
        "queueETA": '{"version":"x","items":[]}',
    }
    lines = game.status_lines(global_data)
    assert any("gold=" in ln for ln in lines)
    assert any("0:Town Hall L1" in ln for ln in lines)


def test_summarize_city_positions_marks_under_construction():
    background = {"underConstruction": 0, "position": REAL_POSITION_LIST}
    line = game.summarize_city_positions(background)
    assert "0:Town Hall L1 (constructing)" in line


def test_summarize_city_positions_prefers_name_over_building():
    # Real slots carry both `name` ("Town Hall", display) and `building`
    # ("townhall", internal slug) — prefer the display name.
    background = {"position": REAL_POSITION_LIST}
    line = game.summarize_city_positions(background)
    assert "Town Hall" in line
    assert "townHall" not in line


def test_summarize_city_positions_falls_back_to_building_without_name():
    # Shape with no `name` field at all still renders something sane.
    background = {"position": [{"buildingId": 5, "building": "shipyard", "level": 2}]}
    line = game.summarize_city_positions(background)
    assert "0:shipyard L2" in line


def test_summarize_city_positions_marks_busy_and_upgradable():
    background = {
        "position": [
            {"buildingId": 5, "name": "Shipyard", "level": 2, "isBusy": True, "canUpgrade": False},
            {"buildingId": 3, "name": "Trading Port", "level": 3, "isBusy": False, "canUpgrade": True},
        ]
    }
    line = game.summarize_city_positions(background)
    assert "0:Shipyard L2 (busy)" in line
    assert "1:Trading Port L3 (upgradable)" in line


def test_summarize_city_positions_skips_empty_slots():
    background = {"position": REAL_POSITION_LIST}
    line = game.summarize_city_positions(background)
    assert "buildingGround" not in line


def test_summarize_construction_computes_remaining_minutes():
    background = {"underConstruction": 0, "endUpgradeTime": 1700, "position": REAL_POSITION_LIST}
    line = game.summarize_construction(background, now=1400)
    assert "Town Hall@0" in line
    assert "5m" in line


def test_summarize_queue_reports_items():
    # Real queueETA items are empire-wide: {"timestamp": ..., "cityName": ...}
    # — not a per-task {"type"/"time"} pair.
    queue = json.dumps({"version": "x", "items": [{"timestamp": 1660, "cityName": "Kernel"}]})
    line = game.summarize_queue(queue, now=1600)
    assert line == "queue: Kernel in 1m"


def test_summarize_queue_multiple_cities():
    queue = json.dumps(
        {
            "version": "x",
            "items": [
                {"timestamp": 1660, "cityName": "Kernel"},
                {"timestamp": 2200, "cityName": "Async"},
            ],
        }
    )
    line = game.summarize_queue(queue, now=1600)
    assert line == "queue: Kernel in 1m, Async in 10m"


def test_summarize_queue_returns_none_when_empty():
    queue = json.dumps({"version": "x", "items": []})
    assert game.summarize_queue(queue, now=100) is None


# ---------------------------------------------------------------------------
# summarize_city_stats
# ---------------------------------------------------------------------------


def test_summarize_city_stats_extracts_all_real_fields():
    line = game.summarize_city_stats(REAL_TOWNHALL_STATS_TEMPLATE)
    assert line.startswith("city stats: ")
    assert "growth 0.09" in line
    assert "happy neutral (1)" in line
    assert "corruption 3%" in line
    assert "wine +593" in line  # +99 tavern + +494 serve
    assert "income -90" in line
    assert "citizens 106" in line
    assert "wood 548" in line
    assert "special 100" in line
    assert "sci 58" in line
    assert "priests 0" in line


def test_summarize_city_stats_returns_none_when_no_stat_keys_present():
    assert game.summarize_city_stats({"js_someOtherThing": {"text": "x"}}) is None


def test_summarize_city_stats_handles_partial_fields():
    line = game.summarize_city_stats({"js_TownHallIncomeGoldValue": {"text": "540"}})
    assert line == "city stats: income 540"


def test_summarize_city_stats_non_dict_returns_none():
    assert game.summarize_city_stats("") is None
    assert game.summarize_city_stats(None) is None


def test_summarize_city_stats_survives_in_nav_output_even_when_truncated(tmp_path, capsys):
    # The dedicated line must show up even when max_chars would truncate the
    # generic template dump before reaching these keys.
    _setup_session(tmp_path)
    response = sample_response()
    response[2] = ["updateTemplateData", dict(REAL_TEMPLATE_DATA, **REAL_TOWNHALL_STATS_TEMPLATE)]
    with patch("game.ic.call_nav", return_value=response):
        game.main(["nav", "townHall", "--max", "5"], project_root=tmp_path)
    out = capsys.readouterr().out
    stats_lines = [ln for ln in out.splitlines() if ln.startswith("city stats:")]
    assert len(stats_lines) == 1
    assert "citizens 106" in stats_lines[0]


# ---------------------------------------------------------------------------
# own_cities / _tradegood_name / summarize_city_block
# ---------------------------------------------------------------------------


def test_own_cities_filters_and_sorts():
    cities = game.own_cities({"cityDropdownMenu": REAL_CITY_DROPDOWN_MENU})
    assert [c["id"] for c in cities] == [355955, 356174]
    assert all(c["relationship"] == "ownCity" for c in cities)


def test_own_cities_ignores_non_dict_entries():
    # additionalInfo/selectedCity are plain strings, not city dicts.
    cities = game.own_cities({"cityDropdownMenu": REAL_CITY_DROPDOWN_MENU})
    names = {c["name"] for c in cities}
    assert "cityCoords" not in names


def test_own_cities_missing_menu_returns_empty():
    assert game.own_cities({}) == []
    assert game.own_cities(None) == []


def test_tradegood_name_maps_known_codes():
    assert game._tradegood_name(1) == "wine"
    assert game._tradegood_name("2") == "marble"
    assert game._tradegood_name(3) == "crystal"
    assert game._tradegood_name(4) == "sulfur"


def test_tradegood_name_unknown_code_passthrough():
    assert game._tradegood_name(9) == "9"
    assert game._tradegood_name(None) == "?"


def test_summarize_city_block_includes_title_header_and_stats():
    city = {"id": 355955, "name": "Kernel", "coords": "[53:67] ", "tradegood": 1}
    data = {
        "updateGlobalData": {
            "time": 1600,
            "headerData": REAL_HEADER_DATA,
            "queueETA": json.dumps({"items": [{"timestamp": 1660, "cityName": "Kernel"}]}),
            "backgroundData": {"underConstruction": -1, "endUpgradeTime": -1},
        },
        "updateTemplateData": REAL_TOWNHALL_STATS_TEMPLATE,
    }
    block = game.summarize_city_block(city, data)
    assert block.startswith("== Kernel (355955) [53:67] tradegood=wine ==")
    assert "gold=" in block
    assert "city stats:" in block
    assert "Kernel in 1m" in block


def test_summarize_city_block_caps_length():
    city = {"id": 1, "name": "Big", "coords": "[1:1]", "tradegood": 1}
    data = {
        "updateGlobalData": {"time": 100, "headerData": REAL_HEADER_DATA},
        "updateTemplateData": REAL_TOWNHALL_STATS_TEMPLATE,
    }
    block = game.summarize_city_block(city, data, max_chars=50)
    assert len(block) <= 50
    assert block.endswith("…")


# ---------------------------------------------------------------------------
# arg parsing
# ---------------------------------------------------------------------------


def test_parse_kv_args_builds_dict():
    assert game.parse_kv_args(["cityId=355955", "wood=17"]) == {"cityId": "355955", "wood": "17"}


def test_parse_kv_args_raises_on_missing_equals():
    with pytest.raises(ValueError):
        game.parse_kv_args(["notkv"])


def test_parse_action_args_with_function_and_params():
    action, function, params = game.parse_action_args(["IslandScreen", "workerPlan", "wood=17"])
    assert action == "IslandScreen"
    assert function == "workerPlan"
    assert params == {"wood": "17"}


def test_parse_action_args_without_function_positional():
    action, function, params = game.parse_action_args(["BuildNewBuilding", "position=12", "building=6"])
    assert action == "BuildNewBuilding"
    assert function is None
    assert params == {"position": "12", "building": "6"}


def test_parse_action_args_requires_action_name():
    with pytest.raises(ValueError):
        game.parse_action_args([])


def test_parse_action_args_splits_catalog_name_with_function():
    # Action Catalog names look like action:IslandScreen:workerPlan.
    action, function, params = game.parse_action_args(["action:IslandScreen:workerPlan", "wood=17"])
    assert action == "IslandScreen"
    assert function == "workerPlan"
    assert params == {"wood": "17"}


def test_parse_action_args_splits_catalog_name_without_function():
    action, function, params = game.parse_action_args(["action:BuildNewBuilding", "position=12"])
    assert action == "BuildNewBuilding"
    assert function is None
    assert params == {"position": "12"}


def test_parse_action_args_explicit_function_overrides_catalog_function():
    action, function, params = game.parse_action_args(
        ["action:IslandScreen:workerPlan", "otherFunction", "wood=17"]
    )
    assert action == "IslandScreen"
    assert function == "otherFunction"
    assert params == {"wood": "17"}


# ---------------------------------------------------------------------------
# _strip_view_prefix / looks_like_bad_view
# ---------------------------------------------------------------------------


def test_strip_view_prefix_removes_leading_view_colon():
    assert game._strip_view_prefix("view:researchAdvisor") == "researchAdvisor"


def test_strip_view_prefix_leaves_plain_view_name_alone():
    assert game._strip_view_prefix("townHall") == "townHall"


def test_looks_like_bad_view_true_for_empty_template_and_no_changeview():
    # Matches the real captured session/responses/view_researchAdvisor.json /
    # view_townHall.json / view_transport.json shape: updateTemplateData is
    # "" and there is no changeView key at all.
    data = {"updateTemplateData": ""}
    assert game.looks_like_bad_view(data) is True


def test_looks_like_bad_view_false_with_changeview():
    data = {"updateTemplateData": "", "changeView": ["townHall", "<div>html</div>"]}
    assert game.looks_like_bad_view(data) is False


def test_looks_like_bad_view_false_with_template_data():
    data = {"updateTemplateData": {"js_key": {"text": "value"}}}
    assert game.looks_like_bad_view(data) is False


def test_split_flags_extracts_options():
    remaining, options = game._split_flags(["townHall", "wood=1", "--city", "999", "--grep", "gold"])
    assert remaining == ["townHall", "wood=1"]
    assert options == {"city": "999", "grep": "gold"}


# ---------------------------------------------------------------------------
# main(): end-to-end CLI behavior with mocked HTTP
# ---------------------------------------------------------------------------


def _setup_session(tmp_path):
    (tmp_path / ".env").write_text("IKARIAM_SERVER=s401-en.ikariam.gameforge.com\n")
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    ss.write_cookie(session_dir, "ikariam_session=fakecookie")
    ss.write_action_request(session_dir, "oldtoken000000000000000000000000")
    return session_dir


def test_nav_persists_token_before_printing(tmp_path, capsys):
    session_dir = _setup_session(tmp_path)
    response = sample_response(action_request="newtoken111111111111111111111111")

    write_order = []
    real_write = ss.write_action_request

    def spy_write(sdir, token):
        write_order.append(("write", token))
        return real_write(sdir, token)

    with patch("game.ic.call_nav", return_value=response) as mock_call_nav:
        with patch("game.ss.write_action_request", side_effect=spy_write):
            with patch("builtins.print", side_effect=lambda *a, **k: write_order.append(("print", a))):
                exit_code = game.main(["nav", "townHall"], project_root=tmp_path)

    assert exit_code == 0
    assert mock_call_nav.called
    # token must be written before any print call
    first_print_idx = next(i for i, (kind, _) in enumerate(write_order) if kind == "print")
    write_idx = next(i for i, (kind, _) in enumerate(write_order) if kind == "write")
    assert write_idx < first_print_idx
    assert ss.read_action_request(session_dir) == "newtoken111111111111111111111111"


def test_nav_prints_status_block_and_response_path(tmp_path, capsys):
    _setup_session(tmp_path)
    response = sample_response()
    with patch("game.ic.call_nav", return_value=response):
        exit_code = game.main(["nav", "townHall"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "gold=" in out
    assert "[full response:" in out
    saved = tmp_path / "session" / "responses" / "townHall.json"
    assert saved.exists()
    assert json.loads(saved.read_text()) == response
    last = tmp_path / "session" / "responses" / "last.json"
    assert json.loads(last.read_text()) == response


def test_nav_defaults_city_params(tmp_path):
    _setup_session(tmp_path)
    response = sample_response()
    with patch("game.ic.call_nav", return_value=response) as mock_call_nav:
        game.main(["nav", "townHall"], project_root=tmp_path)
    args, kwargs = mock_call_nav.call_args
    extra_params = args[4]
    assert extra_params["cityId"] == game.DEFAULT_CITY_ID
    assert extra_params["currentCityId"] == game.DEFAULT_CITY_ID


def test_nav_city_override_flag(tmp_path):
    _setup_session(tmp_path)
    response = sample_response()
    with patch("game.ic.call_nav", return_value=response) as mock_call_nav:
        game.main(["nav", "townHall", "--city", "999"], project_root=tmp_path)
    args, kwargs = mock_call_nav.call_args
    assert args[4]["cityId"] == "999"


def test_action_prints_feedback_first(tmp_path, capsys):
    _setup_session(tmp_path)
    response = sample_response()
    response[3] = ["provideFeedback", [{"location": 1, "text": "Not enough resources", "type": 2}]]
    with patch("game.ic.call_action", return_value=response):
        game.main(["action", "BuildNewBuilding", "position=12"], project_root=tmp_path)
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].startswith("FEEDBACK:")
    assert "Not enough resources" in lines[0]


def test_action_optional_function_positional(tmp_path):
    _setup_session(tmp_path)
    response = sample_response()
    with patch("game.ic.call_action", return_value=response) as mock_call_action:
        game.main(["action", "IslandScreen", "workerPlan", "wood=17"], project_root=tmp_path)
    args, kwargs = mock_call_action.call_args
    assert args[3] == "IslandScreen"
    assert args[4] == "workerPlan"
    assert args[5] == {"wood": "17"}


def test_action_without_function_treats_second_positional_as_param(tmp_path):
    _setup_session(tmp_path)
    response = sample_response()
    with patch("game.ic.call_action", return_value=response) as mock_call_action:
        game.main(["action", "BuildNewBuilding", "position=12", "building=6"], project_root=tmp_path)
    args, kwargs = mock_call_action.call_args
    assert args[3] == "BuildNewBuilding"
    assert args[4] == ""
    assert args[5] == {"position": "12", "building": "6"}


def test_action_persists_token_before_printing(tmp_path):
    session_dir = _setup_session(tmp_path)
    response = sample_response(action_request="actiontoken2222222222222222222222")
    with patch("game.ic.call_action", return_value=response):
        game.main(["action", "BuildNewBuilding", "position=12"], project_root=tmp_path)
    assert ss.read_action_request(session_dir) == "actiontoken2222222222222222222222"


def test_set_cookie_writes_cookie_and_token_without_echoing(tmp_path, capsys, monkeypatch):
    (tmp_path / ".env").write_text("IKARIAM_SERVER=s401-en.ikariam.gameforge.com\n")
    session_dir = tmp_path / "session"
    session_dir.mkdir()

    with patch("game.ic.bootstrap_action_request", return_value="bootstraptoken0000000000000000000"):
        exit_code = game.main(["set-cookie", "--cookie", "ikariam_session=supersecret"], project_root=tmp_path)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert out.strip() == "COOKIE_SET"
    assert "supersecret" not in out
    assert "bootstraptoken" not in out
    assert ss.read_cookie(session_dir) == "ikariam_session=supersecret"
    assert ss.read_action_request(session_dir) == "bootstraptoken0000000000000000000"


def test_set_cookie_reads_from_stdin(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("IKARIAM_SERVER=s401-en.ikariam.gameforge.com\n")
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("ikariam_session=fromstdin\n"))

    with patch("game.ic.bootstrap_action_request", return_value="tok"):
        game.main(["set-cookie"], project_root=tmp_path)

    assert ss.read_cookie(session_dir) == "ikariam_session=fromstdin"


def test_session_invalid_missing_cookie_exit_code(tmp_path, capsys):
    (tmp_path / ".env").write_text("IKARIAM_SERVER=s401-en.ikariam.gameforge.com\n")
    (tmp_path / "session").mkdir()
    exit_code = game.main(["session"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert exit_code == 2
    assert out.startswith("SESSION_INVALID:")


def test_session_invalid_on_call_nav_error(tmp_path, capsys):
    _setup_session(tmp_path)
    with patch("game.ic.call_nav", side_effect=ic.SessionInvalidError("expired")):
        exit_code = game.main(["session"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert exit_code == 2
    assert "SESSION_INVALID: expired" in out


def test_session_ok_prints_status_block(tmp_path, capsys):
    _setup_session(tmp_path)
    response = sample_response()
    with patch("game.ic.call_nav", return_value=response):
        exit_code = game.main(["session"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert exit_code == 0
    lines = out.splitlines()
    assert lines[0] == "SESSION_OK"
    assert any("gold=" in ln for ln in lines)


def test_nav_invalid_view_argument_name_exits_2(tmp_path, capsys):
    _setup_session(tmp_path)
    exit_code = game.main(["nav"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert exit_code == 2
    assert out.startswith("SESSION_INVALID:")


def test_nav_strips_view_colon_prefix_before_calling(tmp_path):
    _setup_session(tmp_path)
    response = sample_response()
    with patch("game.ic.call_nav", return_value=response) as mock_call_nav:
        game.main(["nav", "view:researchAdvisor"], project_root=tmp_path)
    args, kwargs = mock_call_nav.call_args
    assert args[3] == "researchAdvisor"
    # saved under the stripped name, not the raw "view:researchAdvisor"
    assert (tmp_path / "session" / "responses" / "researchAdvisor.json").exists()


def test_nav_prints_hint_on_empty_response_with_no_changeview(tmp_path, capsys):
    # Real shape captured from a bad view= call: updateTemplateData is "",
    # no changeView key.
    _setup_session(tmp_path)
    response = [
        [
            "updateGlobalData",
            {
                "actionRequest": "2d7b402568f190bc6708586a79eed444",
                "time": 1000,
                "headerData": {},
                "backgroundData": {},
                "queueETA": "",
            },
        ],
        ["updateTemplateData", ""],
    ]
    with patch("game.ic.call_nav", return_value=response):
        game.main(["nav", "view:researchAdvisor"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert "HINT:" in out
    assert "view name" in out


def test_nav_no_hint_when_response_has_content(tmp_path, capsys):
    _setup_session(tmp_path)
    response = sample_response()
    with patch("game.ic.call_nav", return_value=response):
        game.main(["nav", "townHall"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert "HINT:" not in out


def test_action_accepts_catalog_name_end_to_end(tmp_path):
    _setup_session(tmp_path)
    response = sample_response()
    with patch("game.ic.call_action", return_value=response) as mock_call_action:
        game.main(["action", "action:IslandScreen:workerPlan", "wood=17"], project_root=tmp_path)
    args, kwargs = mock_call_action.call_args
    assert args[3] == "IslandScreen"
    assert args[4] == "workerPlan"
    assert args[5] == {"wood": "17"}


# ---------------------------------------------------------------------------
# `cities` subcommand
# ---------------------------------------------------------------------------


def _city_response(city_id, name, action_request, extra_template=None):
    header = dict(REAL_HEADER_DATA)
    if city_id == 355955:
        header["cityDropdownMenu"] = REAL_CITY_DROPDOWN_MENU
    template = dict(REAL_TOWNHALL_STATS_TEMPLATE)
    if extra_template:
        template.update(extra_template)
    return [
        [
            "updateGlobalData",
            {
                "actionRequest": action_request,
                "time": 1789734801,
                "headerData": header,
                "backgroundData": {
                    "name": name,
                    "id": city_id,
                    "underConstruction": -1,
                    "endUpgradeTime": -1,
                    "position": REAL_POSITION_LIST,
                },
                "queueETA": '{"version":"abc","items":[]}',
            },
        ],
        ["updateTemplateData", template],
    ]


def test_cities_prints_one_block_per_own_city(tmp_path, capsys):
    session_dir = _setup_session(tmp_path)
    responses = [
        _city_response(355955, "Kernel", "token1111111111111111111111111111"),
        _city_response(356174, "Daemon", "token2222222222222222222222222222"),
    ]
    with patch("game.ic.call_nav", side_effect=responses) as mock_call_nav:
        exit_code = game.main(["cities"], project_root=tmp_path)
    out = capsys.readouterr().out

    assert exit_code == 0
    assert mock_call_nav.call_count == 2
    assert "== Kernel (355955)" in out
    assert "== Daemon (356174)" in out
    assert "tradegood=wine" in out
    assert "tradegood=marble" in out
    # token from the last call persisted
    assert ss.read_action_request(session_dir) == "token2222222222222222222222222222"
    # each city's raw response saved under townHall_<id>
    assert (tmp_path / "session" / "responses" / "townHall_355955.json").exists()
    assert (tmp_path / "session" / "responses" / "townHall_356174.json").exists()


def test_cities_does_not_refetch_the_discovery_city(tmp_path):
    # start_city_id (355955, the default) is also one of the own cities —
    # it should not be fetched twice.
    _setup_session(tmp_path)
    responses = [
        _city_response(355955, "Kernel", "token1111111111111111111111111111"),
        _city_response(356174, "Daemon", "token2222222222222222222222222222"),
    ]
    with patch("game.ic.call_nav", side_effect=responses) as mock_call_nav:
        game.main(["cities"], project_root=tmp_path)
    assert mock_call_nav.call_count == 2  # one discovery+Kernel call, one Daemon call


def test_cities_no_own_cities_found(tmp_path, capsys):
    _setup_session(tmp_path)
    response = _city_response(355955, "Kernel", "token1111111111111111111111111111")
    # strip the dropdown menu so no own cities are found
    response[0][1]["headerData"] = dict(REAL_HEADER_DATA)
    with patch("game.ic.call_nav", return_value=response):
        exit_code = game.main(["cities"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "no own cities found" in out


def test_cities_session_invalid_on_discovery_call(tmp_path, capsys):
    _setup_session(tmp_path)
    with patch("game.ic.call_nav", side_effect=ic.SessionInvalidError("expired")):
        exit_code = game.main(["cities"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert exit_code == 2
    assert "SESSION_INVALID: expired" in out


# ---------------------------------------------------------------------------
# `state` — small parsing/formatting helpers
# (values below are trimmed from real session/responses/townHall_355955.json
#  — Kernel, the account's capital — tokens were never present in that data)
# ---------------------------------------------------------------------------


def test_round_or_none_handles_numeric_string():
    assert game._round_or_none("29479.83975277733") == 29480


def test_round_or_none_none_for_bool_and_none():
    assert game._round_or_none(True) is None
    assert game._round_or_none(None) is None


def test_parse_signed_int_strips_commas_and_plus():
    assert game._parse_signed_int("+1,658") == 1658
    assert game._parse_signed_int("-4,932") == -4932
    assert game._parse_signed_int("308") == 308


def test_parse_signed_int_none_for_empty_or_missing():
    assert game._parse_signed_int("") is None
    assert game._parse_signed_int(None) is None
    assert game._parse_signed_int("not a number") is None


def test_parse_float_strips_whitespace():
    assert game._parse_float("0.09 ") == 0.09


def test_parse_float_none_for_empty_or_missing():
    assert game._parse_float("") is None
    assert game._parse_float(None) is None


def test_parse_percent_int_strips_percent_sign():
    assert game._parse_percent_int("3%") == 3
    assert game._parse_percent_int("28 %") == 28


def test_parse_percent_int_none_for_missing():
    assert game._parse_percent_int(None) is None


def test_clean_coords_strips_brackets_and_space():
    assert game._clean_coords("[53:67] ") == "53:67"


def test_clean_coords_none_passthrough():
    assert game._clean_coords(None) is None


def test_find_position_locates_by_building_slug():
    background = {"position": [{"building": "townhall"}, {"building": "tavern", "level": 8}]}
    idx, slot = game._find_position(background, "tavern")
    assert idx == 1
    assert slot["level"] == 8


def test_find_position_returns_none_when_absent():
    assert game._find_position({"position": [{"building": "townhall"}]}, "tavern") is None
    assert game._find_position({}, "tavern") is None
    assert game._find_position(None, "tavern") is None


def test_parse_queue_items_structured():
    queue = json.dumps({"items": [{"timestamp": 1660, "cityName": "Kernel"}]})
    assert game.parse_queue_items(queue, now=1600) == [{"city": "Kernel", "done_in_min": 1}]


def test_parse_queue_items_empty():
    assert game.parse_queue_items("", now=100) == []
    assert game.parse_queue_items(json.dumps({"items": []}), now=100) == []


# ---------------------------------------------------------------------------
# `summarize_worker_allocation`
# ---------------------------------------------------------------------------


def test_summarize_worker_allocation_extracts_counts_and_production():
    template = dict(
        REAL_TOWNHALL_STATS_TEMPLATE,
        js_TownHallPopulationGraphWoodProduction={"text": "+1,658"},
        js_TownHallPopulationGraphTradeGoodProduction={"text": "+308"},
    )
    line = game.summarize_worker_allocation(template)
    assert line == "workers: wood 548 (+1658/h) tradegood 100 (+308/h)"


def test_summarize_worker_allocation_omits_production_when_absent():
    line = game.summarize_worker_allocation(REAL_TOWNHALL_STATS_TEMPLATE)
    assert line == "workers: wood 548 tradegood 100"


def test_summarize_worker_allocation_returns_none_when_no_worker_keys():
    assert game.summarize_worker_allocation({"js_other": {"text": "x"}}) is None


def test_summarize_worker_allocation_non_dict_returns_none():
    assert game.summarize_worker_allocation(None) is None
    assert game.summarize_worker_allocation("") is None


def test_nav_prints_worker_allocation_line_when_present(tmp_path, capsys):
    _setup_session(tmp_path)
    response = sample_response()
    response[2] = ["updateTemplateData", dict(REAL_TEMPLATE_DATA, **REAL_TOWNHALL_STATS_TEMPLATE)]
    with patch("game.ic.call_nav", return_value=response):
        game.main(["nav", "townHall"], project_root=tmp_path)
    out = capsys.readouterr().out
    workers_lines = [ln for ln in out.splitlines() if ln.startswith("workers:")]
    assert len(workers_lines) == 1
    assert "wood 548" in workers_lines[0]


# ---------------------------------------------------------------------------
# `build_city_state` / `build_state` / `render_state_text`
# (fixtures trimmed from real session/responses/townHall_355955.json — Kernel)
# ---------------------------------------------------------------------------

REAL_KERNEL_CITY = {"id": 355955, "name": "Kernel", "coords": "[53:67] ", "tradegood": 1}

REAL_KERNEL_HEADER = {
    "gold": "30456.49176993326",
    "income": 2219.5790986118864,
    "upkeep": -9,
    "freeTransporters": 8,
    "maxTransporters": 8,
    "currentResources": {
        "citizens": 106.32801247939938,
        "population": 812.3280124793994,
        "resource": 70578,
        "1": 26826,
        "2": 1911,
        "3": 2081,
        "4": 1017,
    },
    "maxResources": {"resource": 73920, "1": 73920, "2": 73920, "3": 73920, "4": 73920},
    "wineSpendings": 129,
    "producedTradegood": 1,
    "advisors": {"military": {"cssclass": "premiumactive"}, "hasPremiumAccount": True},
}

REAL_KERNEL_TEMPLATE = {
    "CitizenCount": 106,
    "ResourceWorkerCount": 548,
    "SpecialWorkerCount": 100,
    "ScientistCount": 58,
    "PriestCount": 0,
    "js_TownHallCorruption": {"text": "3%"},
    "js_TownHallHappinessLargeText": {"text": "neutral"},
    "js_TownHallPopulationGrowthValue": {"text": "0.09 "},
    "js_TownHallMaxInhabitants": {"text": "818"},
    "js_TownHallPopulationGraphWoodProduction": {"text": "+1,658"},
    "js_TownHallPopulationGraphTradeGoodProduction": {"text": "+308"},
}

REAL_KERNEL_POSITIONS = [
    {"buildingId": 0, "name": "Town Hall", "level": 11, "building": "townhall", "isBusy": False},
    {"buildingId": 9, "name": "Tavern", "level": 8, "building": "tavern", "isBusy": False, "canUpgrade": True},
]

REAL_KERNEL_BACKGROUND = {
    "name": "Kernel",
    "id": 355955,
    "islandId": 692,
    "underConstruction": -1,
    "endUpgradeTime": -1,
    "position": REAL_KERNEL_POSITIONS,
}


def _kernel_data(header=None, background=None, template=None, time=1790339344, queue_eta=""):
    return {
        "updateGlobalData": {
            "time": time,
            "headerData": header if header is not None else dict(REAL_KERNEL_HEADER),
            "backgroundData": background if background is not None else dict(REAL_KERNEL_BACKGROUND),
            "queueETA": queue_eta,
        },
        "updateTemplateData": template if template is not None else dict(REAL_KERNEL_TEMPLATE),
    }


def test_build_city_state_extracts_real_fields():
    state = game.build_city_state(REAL_KERNEL_CITY, _kernel_data())
    assert state["id"] == "355955"
    assert state["name"] == "Kernel"
    assert state["coords"] == "53:67"
    assert state["island_id"] == "692"
    assert state["tradegood"] == "wine"
    assert state["resources"] == {"wood": 70578, "wine": 26826, "marble": 1911, "crystal": 2081, "sulfur": 1017}
    assert state["max_resources"] == 73920
    assert state["production_per_hour"] == {"wood": 1658, "tradegood": 308}
    assert state["citizens"] == 106
    assert state["population"] == 812
    assert state["max_population"] == 818
    assert state["growth_per_hour"] == 0.09
    assert state["happiness"] == "neutral"
    assert state["corruption_pct"] == 3
    assert state["workers"] == {"wood": 548, "tradegood": 100, "scientists": 58, "priests": 0}
    assert state["construction"] == []
    assert {"position": 1, "name": "Tavern", "level": 8, "busy": False} in state["buildings"]


def test_build_city_state_wine_per_hour_is_consumption_and_net_is_separate():
    # Kernel's own tradegood is wine: production 308/h, tavern draw 129/h.
    state = game.build_city_state(REAL_KERNEL_CITY, _kernel_data())
    assert state["wine_per_hour"] == 129
    assert state["wine_net_per_hour"] == 179  # 308 - 129
    # net production is positive (cellar filling, not draining) -> not applicable
    assert state["wine_hours_left"] == game.WINE_NOT_DRAINING


def test_build_city_state_wine_hours_left_when_actually_draining():
    # A city whose own tradegood is NOT wine (no wine production) but whose
    # Tavern is drawing 500/h against a 26,826 stock. (`tradegood` is the
    # CITY's own resource — from cityDropdownMenu — not headerData at all.)
    marble_city = dict(REAL_KERNEL_CITY, tradegood=2)
    header = dict(REAL_KERNEL_HEADER, wineSpendings=500)
    state = game.build_city_state(marble_city, _kernel_data(header=header))
    assert state["wine_per_hour"] == 500
    assert state["wine_net_per_hour"] == -500
    assert state["wine_hours_left"] == round(26826 / 500, 1)


def test_build_city_state_tavern_fields_from_background_position():
    state = game.build_city_state(REAL_KERNEL_CITY, _kernel_data())
    assert state["tavern"] == {"position": 1, "level": 8, "wine_level": None, "max_wine_level": 8}


def test_build_city_state_tavern_none_when_not_built():
    background = dict(REAL_KERNEL_BACKGROUND, position=[REAL_KERNEL_POSITIONS[0]])
    state = game.build_city_state(REAL_KERNEL_CITY, _kernel_data(background=background))
    assert state["tavern"] is None
    assert state["wine_hours_left"] == game.WINE_NOT_DRAINING  # governed by wineSpendings, not the tavern


def test_build_city_state_construction_in_progress():
    background = dict(REAL_KERNEL_BACKGROUND, underConstruction=1, endUpgradeTime=1790344391)
    state = game.build_city_state(REAL_KERNEL_CITY, _kernel_data(background=background, time=1790339391))
    assert state["construction"] == [{"building": "Tavern", "position": 1, "done_in_min": 83}]


def test_build_city_state_no_construction_is_empty_list():
    state = game.build_city_state(REAL_KERNEL_CITY, _kernel_data())
    assert state["construction"] == []


def test_build_city_state_missing_fields_are_null_not_guessed():
    data = {
        "updateGlobalData": {"time": 1, "headerData": {}, "backgroundData": {}, "queueETA": ""},
        "updateTemplateData": {},
    }
    state = game.build_city_state({"id": 1, "name": "X", "coords": None, "tradegood": None}, data)
    assert state["citizens"] is None
    assert state["population"] is None
    assert state["max_population"] is None
    assert state["growth_per_hour"] is None
    assert state["happiness"] is None
    assert state["corruption_pct"] is None
    assert state["tavern"] is None
    assert state["wine_per_hour"] is None
    assert state["wine_hours_left"] is None
    assert state["max_resources"] is None
    assert state["island_id"] is None
    assert state["workers"] == {"wood": None, "tradegood": None, "scientists": None, "priests": None}
    assert state["buildings"] == []
    assert state["construction"] == []


def test_build_state_top_level_fields_from_discovery_headerdata():
    fetched = {"355955": (_kernel_data(), "path")}
    global_data = _kernel_data()["updateGlobalData"]
    state = game.build_state([REAL_KERNEL_CITY], fetched, global_data)
    assert state["gold"] == 30456
    assert state["gold_per_hour"] == 2229
    assert state["transporters"] == {"free": 8, "max": 8}
    assert state["alerts"] == {"under_attack": False, "unread_messages": None}
    assert state["queue"] == []
    assert len(state["cities"]) == 1
    assert state["cities"][0]["name"] == "Kernel"


def test_build_state_under_attack_true_when_alert_class_seen():
    header = dict(REAL_KERNEL_HEADER, advisors={"military": {"cssclass": "normalalert"}})
    global_data = {"time": 1, "headerData": header, "backgroundData": {}, "queueETA": ""}
    state = game.build_state([], {}, global_data)
    assert state["alerts"]["under_attack"] is True


def test_build_state_under_attack_null_when_no_advisor_data():
    global_data = {"time": 1, "headerData": {}, "backgroundData": {}, "queueETA": ""}
    state = game.build_state([], {}, global_data)
    assert state["alerts"]["under_attack"] is None


def test_build_state_unread_messages_always_null():
    # No saved real response carries an unread-mail counter (see
    # build_state()'s docstring) — always null, never guessed.
    global_data = _kernel_data()["updateGlobalData"]
    state = game.build_state([], {}, global_data)
    assert state["alerts"]["unread_messages"] is None


def test_render_state_text_includes_header_and_city_block():
    fetched = {"355955": (_kernel_data(), "path")}
    global_data = _kernel_data()["updateGlobalData"]
    state = game.build_state([REAL_KERNEL_CITY], fetched, global_data)
    text = game.render_state_text(state)
    assert "gold=30456" in text
    assert "== Kernel (355955) 53:67 tradegood=wine ==" in text
    assert "workers: wood 548 tradegood 100 scientists 58 priests 0" in text


def test_render_city_text_truncates_at_500_chars():
    city = {"name": "X", "id": 1, "coords": "1:1", "tradegood": "wine", "happiness": "z" * 600}
    text = game._render_city_text(city)
    assert len(text) <= 500
    assert text.endswith("…")


# ---------------------------------------------------------------------------
# `state` subcommand: end-to-end CLI behavior with mocked HTTP
# ---------------------------------------------------------------------------


def test_state_json_output_written_to_file_and_printed(tmp_path, capsys):
    session_dir = _setup_session(tmp_path)
    responses = [
        _city_response(355955, "Kernel", "statetoken1111111111111111111111"),
        _city_response(356174, "Daemon", "statetoken2222222222222222222222"),
    ]
    with patch("game.ic.call_nav", side_effect=responses):
        exit_code = game.main(["state", "--json"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert exit_code == 0
    payload = json.loads(out)
    assert [c["name"] for c in payload["cities"]] == ["Kernel", "Daemon"]
    assert payload["gold"] == 1649  # REAL_HEADER_DATA.gold rounds to 1649
    saved = json.loads((session_dir / "state.json").read_text())
    assert saved == payload


def test_state_text_output_by_default(tmp_path, capsys):
    _setup_session(tmp_path)
    responses = [
        _city_response(355955, "Kernel", "statetoken1111111111111111111111"),
        _city_response(356174, "Daemon", "statetoken2222222222222222222222"),
    ]
    with patch("game.ic.call_nav", side_effect=responses):
        exit_code = game.main(["state"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "== Kernel (355955)" in out
    assert "gold=" in out
    # --json output (raw JSON) must not leak into the default text rendering
    assert not out.strip().startswith("{")


def test_state_no_own_cities_found(tmp_path, capsys):
    _setup_session(tmp_path)
    response = _city_response(355955, "Kernel", "statetoken1111111111111111111111")
    response[0][1]["headerData"] = dict(REAL_HEADER_DATA)  # no cityDropdownMenu
    with patch("game.ic.call_nav", return_value=response):
        exit_code = game.main(["state"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "no own cities found" in out


def test_state_session_invalid_on_discovery_call(tmp_path, capsys):
    _setup_session(tmp_path)
    with patch("game.ic.call_nav", side_effect=ic.SessionInvalidError("expired")):
        exit_code = game.main(["state"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert exit_code == 2
    assert "SESSION_INVALID: expired" in out


def test_state_city_override_flag_used_as_discovery_city(tmp_path):
    _setup_session(tmp_path)
    response = _city_response(356174, "Daemon", "statetoken1111111111111111111111")
    # give the override city its own dropdown menu (normally only 355955 has one)
    response[0][1]["headerData"]["cityDropdownMenu"] = REAL_CITY_DROPDOWN_MENU
    with patch("game.ic.call_nav", return_value=response) as mock_call_nav:
        game.main(["state", "--city", "356174"], project_root=tmp_path)
    args, kwargs = mock_call_nav.call_args_list[0]
    assert args[4]["cityId"] == "356174"


# ---------------------------------------------------------------------------
# `batch` subcommand
# ---------------------------------------------------------------------------


def test_batch_runs_nav_and_action_lines_in_order(tmp_path, capsys, monkeypatch):
    session_dir = _setup_session(tmp_path)
    nav_response = sample_response(action_request="batchtoken1111111111111111111111")
    action_response = sample_response(action_request="batchtoken2222222222222222222222")
    monkeypatch.setattr(
        "sys.stdin", __import__("io").StringIO("nav townHall\naction BuildNewBuilding position=12\n")
    )
    with patch("game.ic.call_nav", return_value=nav_response) as mock_nav, patch(
        "game.ic.call_action", return_value=action_response
    ) as mock_action:
        exit_code = game.main(["batch"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert mock_nav.called
    assert mock_action.called
    assert "## nav townHall" in out
    assert "## action BuildNewBuilding position=12" in out
    assert ss.read_action_request(session_dir) == "batchtoken2222222222222222222222"


def test_batch_stops_at_first_failure_by_default(tmp_path, monkeypatch):
    _setup_session(tmp_path)
    monkeypatch.setattr(
        "sys.stdin", __import__("io").StringIO("nav townHall\naction BuildNewBuilding position=12\n")
    )
    with patch("game.ic.call_nav", side_effect=ic.SessionInvalidError("expired")), patch(
        "game.ic.call_action"
    ) as mock_action:
        exit_code = game.main(["batch"], project_root=tmp_path)
    assert exit_code != 0
    assert not mock_action.called


def test_batch_keep_going_runs_all_lines_despite_failure(tmp_path, monkeypatch):
    _setup_session(tmp_path)
    ok_response = sample_response()
    monkeypatch.setattr(
        "sys.stdin", __import__("io").StringIO("nav townHall\naction BuildNewBuilding position=12\n")
    )
    with patch("game.ic.call_nav", side_effect=ic.SessionInvalidError("expired")), patch(
        "game.ic.call_action", return_value=ok_response
    ) as mock_action:
        exit_code = game.main(["batch", "--keep-going"], project_root=tmp_path)
    assert mock_action.called
    assert exit_code != 0  # still non-zero: something in the batch failed


def test_batch_all_lines_succeed_exits_zero(tmp_path, monkeypatch):
    _setup_session(tmp_path)
    response = sample_response()
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("nav townHall\nnav townHall\n"))
    with patch("game.ic.call_nav", return_value=response) as mock_nav:
        exit_code = game.main(["batch"], project_root=tmp_path)
    assert exit_code == 0
    assert mock_nav.call_count == 2


def test_batch_skips_blank_and_comment_lines(tmp_path, capsys, monkeypatch):
    _setup_session(tmp_path)
    response = sample_response()
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("\n# a comment\nnav townHall\n"))
    with patch("game.ic.call_nav", return_value=response) as mock_nav:
        exit_code = game.main(["batch"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert mock_nav.call_count == 1
    assert "## # a comment" not in out


def test_batch_unsupported_command_fails(tmp_path, monkeypatch):
    _setup_session(tmp_path)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("bogus foo\n"))
    exit_code = game.main(["batch"], project_root=tmp_path)
    assert exit_code != 0


# ---------------------------------------------------------------------------
# `find` subcommand
# ---------------------------------------------------------------------------


def test_find_matches_path_or_value_case_insensitive(tmp_path, capsys):
    session_dir = _setup_session(tmp_path)
    responses_dir = session_dir / "responses"
    responses_dir.mkdir(exist_ok=True)
    raw = [["updateGlobalData", {"headerData": {"gold": 100, "wineSpendings": 129}}]]
    (responses_dir / "last.json").write_text(json.dumps(raw))
    exit_code = game.main(["find", "wine"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "updateGlobalData.headerData.wineSpendings = 129" in out


def test_find_uses_custom_file(tmp_path, capsys):
    _setup_session(tmp_path)
    custom = tmp_path / "custom.json"
    custom.write_text(json.dumps([["x", {"a": "hello world"}]]))
    exit_code = game.main(["find", "hello", "--file", str(custom)], project_root=tmp_path)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "x.a = hello world" in out


def test_find_no_matches_reports_cleanly(tmp_path, capsys):
    session_dir = _setup_session(tmp_path)
    responses_dir = session_dir / "responses"
    responses_dir.mkdir(exist_ok=True)
    (responses_dir / "last.json").write_text(json.dumps([["x", {"a": 1}]]))
    exit_code = game.main(["find", "zzz_not_found_anywhere"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "no matches" in out


def test_find_missing_file_reports_session_invalid(tmp_path, capsys):
    _setup_session(tmp_path)
    exit_code = game.main(["find", "x", "--file", str(tmp_path / "nope.json")], project_root=tmp_path)
    out = capsys.readouterr().out
    assert exit_code == 2
    assert out.startswith("SESSION_INVALID:")


def test_find_requires_pattern(tmp_path, capsys):
    exit_code = game.main(["find"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert exit_code == 2
    assert out.startswith("SESSION_INVALID:")


def test_find_strips_html_and_matches_inner_text(tmp_path, capsys):
    session_dir = _setup_session(tmp_path)
    responses_dir = session_dir / "responses"
    responses_dir.mkdir(exist_ok=True)
    raw = [["x", {"html": "<p>hello there</p>"}]]
    (responses_dir / "last.json").write_text(json.dumps(raw))
    exit_code = game.main(["find", "hello"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "<p>" not in out
    assert "hello there" in out


def test_find_caps_at_40_matches(tmp_path, capsys):
    session_dir = _setup_session(tmp_path)
    responses_dir = session_dir / "responses"
    responses_dir.mkdir(exist_ok=True)
    raw = [["x", {f"k{i}": "wine" for i in range(60)}]]
    (responses_dir / "last.json").write_text(json.dumps(raw))
    exit_code = game.main(["find", "wine"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert exit_code == 0
    lines = [ln for ln in out.splitlines() if "wine" in ln]
    assert len(lines) == 40


def test_find_default_file_is_last_json(tmp_path, capsys):
    session_dir = _setup_session(tmp_path)
    responses_dir = session_dir / "responses"
    responses_dir.mkdir(exist_ok=True)
    (responses_dir / "last.json").write_text(json.dumps([["x", {"marker": "findme"}]]))
    (responses_dir / "other.json").write_text(json.dumps([["x", {"marker": "not this one"}]]))
    exit_code = game.main(["find", "findme"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "findme" in out
    assert "not this one" not in out
