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
    assert any("0:townHall L1" in ln for ln in lines)


def test_summarize_city_positions_marks_under_construction():
    background = {"underConstruction": 0, "position": REAL_POSITION_LIST}
    line = game.summarize_city_positions(background)
    assert "0:townHall L1 (constructing)" in line


def test_summarize_city_positions_skips_empty_slots():
    background = {"position": REAL_POSITION_LIST}
    line = game.summarize_city_positions(background)
    assert "buildingGround" not in line


def test_summarize_construction_computes_remaining_minutes():
    background = {"underConstruction": 0, "endUpgradeTime": 1700, "position": REAL_POSITION_LIST}
    line = game.summarize_construction(background, now=1400)
    assert "townHall@0" in line
    assert "5m" in line


def test_summarize_queue_reports_items():
    queue = json.dumps({"version": "x", "items": [{"type": "build", "time": 1660}]})
    line = game.summarize_queue(queue, now=1600)
    assert line == "queue: build 1m"


def test_summarize_queue_returns_none_when_empty():
    queue = json.dumps({"version": "x", "items": []})
    assert game.summarize_queue(queue, now=100) is None


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
