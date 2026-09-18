from unittest.mock import MagicMock, patch

import pytest

import ikariam_client as ic

# Trimmed but structurally faithful to a real captured response
# (har/sample2.har, an IslandScreen/workerPlan response).
SAMPLE_RESPONSE = [
    [
        "updateGlobalData",
        {
            "actionRequest": "2d7b402568f190bc6708586a79eed444",
            "headerData": {
                "currentResources": {
                    "citizens": 18.96,
                    "population": 43.96,
                    "resource": 422,
                    "2": 0,
                    "1": 0,
                    "4": 0,
                    "3": 0,
                }
            },
            "backgroundData": {"name": "Polis", "id": 355955},
        },
    ]
]


def test_build_headers_without_body():
    headers = ic.build_headers("ikariam_session=abc")
    assert headers["Cookie"] == "ikariam_session=abc"
    assert headers["X-Requested-With"] == "XMLHttpRequest"
    assert "Content-Type" not in headers


def test_build_headers_with_body():
    headers = ic.build_headers("ikariam_session=abc", with_body=True)
    assert headers["Content-Type"] == "application/x-www-form-urlencoded; charset=UTF-8"


def test_extract_action_request_finds_token():
    assert ic.extract_action_request(SAMPLE_RESPONSE) == "2d7b402568f190bc6708586a79eed444"


def test_extract_action_request_raises_when_missing():
    with pytest.raises(ic.SessionInvalidError):
        ic.extract_action_request([["someOtherEvent", {}]])


def test_extract_resources_returns_current_resources():
    resources = ic.extract_resources(SAMPLE_RESPONSE)
    assert resources["resource"] == 422
    assert resources["citizens"] == 18.96


def _mock_response(json_body=None, status=200, raise_value_error=False):
    resp = MagicMock()
    resp.status_code = status
    if raise_value_error:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = json_body
    return resp


@patch("ikariam_client.requests.post")
def test_call_nav_builds_expected_request(mock_post):
    mock_post.return_value = _mock_response(SAMPLE_RESPONSE)
    result = ic.call_nav(
        "s401-en.ikariam.gameforge.com", "cookie123", "tok456", "townHall",
        {"cityId": "355955", "position": "0", "currentCityId": "355955"},
    )
    assert result == SAMPLE_RESPONSE
    args, kwargs = mock_post.call_args
    assert args[0] == "https://s401-en.ikariam.gameforge.com/"
    assert kwargs["params"]["view"] == "townHall"
    assert kwargs["params"]["actionRequest"] == "tok456"
    assert kwargs["params"]["ajax"] == "1"
    assert kwargs["headers"]["Cookie"] == "cookie123"


@patch("ikariam_client.requests.post")
def test_call_nav_raises_on_non_200(mock_post):
    mock_post.return_value = _mock_response(status=500)
    with pytest.raises(ic.SessionInvalidError):
        ic.call_nav("server", "cookie", "tok", "townHall", {})


@patch("ikariam_client.requests.post")
def test_call_nav_raises_on_non_json_body(mock_post):
    mock_post.return_value = _mock_response(raise_value_error=True)
    with pytest.raises(ic.SessionInvalidError):
        ic.call_nav("server", "cookie", "tok", "townHall", {})


@patch("ikariam_client.requests.post")
def test_call_action_builds_expected_request(mock_post):
    mock_post.return_value = _mock_response(SAMPLE_RESPONSE)
    result = ic.call_action(
        "s401-en.ikariam.gameforge.com", "cookie123", "tok456",
        "IslandScreen", "workerPlan",
        {"cityId": "355955", "wood": "17"},
    )
    assert result == SAMPLE_RESPONSE
    args, kwargs = mock_post.call_args
    assert args[0] == "https://s401-en.ikariam.gameforge.com/index.php"
    assert kwargs["data"]["action"] == "IslandScreen"
    assert kwargs["data"]["function"] == "workerPlan"
    assert kwargs["data"]["wood"] == "17"
    assert kwargs["headers"]["Content-Type"] == "application/x-www-form-urlencoded; charset=UTF-8"


@patch("ikariam_client.call_nav")
def test_check_session_returns_new_token_and_resources(mock_call_nav):
    mock_call_nav.return_value = SAMPLE_RESPONSE
    token, resources = ic.check_session("server", "cookie", "oldtok", "355955")
    assert token == "2d7b402568f190bc6708586a79eed444"
    assert resources["resource"] == 422
    args, kwargs = mock_call_nav.call_args
    assert args[3] == "townHall"
    assert args[4]["cityId"] == "355955"
