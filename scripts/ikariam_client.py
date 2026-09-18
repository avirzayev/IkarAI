import re

import requests

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)


class SessionInvalidError(Exception):
    pass


def build_headers(cookie: str, with_body: bool = False) -> dict:
    headers = {
        "Cookie": cookie,
        "User-Agent": USER_AGENT,
        "X-Requested-With": "XMLHttpRequest",
    }
    if with_body:
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
    return headers


def extract_action_request(response_json: list) -> str:
    for name, payload in response_json:
        if name == "updateGlobalData":
            return payload["actionRequest"]
    raise SessionInvalidError("Response had no updateGlobalData entry — session likely expired.")


def extract_resources(response_json: list) -> dict:
    for name, payload in response_json:
        if name == "updateGlobalData":
            return payload["headerData"]["currentResources"]
    raise SessionInvalidError("Response had no updateGlobalData entry — session likely expired.")


def call_nav(server: str, cookie: str, action_request: str, view: str, extra_params: dict) -> list:
    params = {"view": view, "backgroundView": "city", "actionRequest": action_request, "ajax": "1"}
    params.update(extra_params)
    resp = requests.post(f"https://{server}/", params=params, headers=build_headers(cookie))
    if resp.status_code != 200:
        raise SessionInvalidError(f"Unexpected status {resp.status_code} from view={view}")
    try:
        return resp.json()
    except ValueError as exc:
        raise SessionInvalidError(f"Non-JSON response from view={view} — session likely expired.") from exc


def call_action(
    server: str, cookie: str, action_request: str, action: str, function: str, extra_params: dict
) -> list:
    body = {"action": action, "actionRequest": action_request, "ajax": "1"}
    if function:
        body["function"] = function
    body.update(extra_params)
    resp = requests.post(
        f"https://{server}/index.php", data=body, headers=build_headers(cookie, with_body=True)
    )
    if resp.status_code != 200:
        raise SessionInvalidError(f"Unexpected status {resp.status_code} from action={action}")
    try:
        return resp.json()
    except ValueError as exc:
        raise SessionInvalidError(f"Non-JSON response from action={action} — session likely expired.") from exc


def bootstrap_action_request(server: str, cookie: str) -> str:
    """Derive a fresh actionRequest token from a plain page load — only the
    cookie is needed. Lets a human refresh a session by supplying just the
    Cookie header value, without also hunting for actionRequest in DevTools."""
    resp = requests.get(f"https://{server}/", headers=build_headers(cookie))
    if resp.status_code != 200:
        raise SessionInvalidError(f"Unexpected status {resp.status_code} bootstrapping actionRequest")
    match = re.search(r'actionRequest["\s:=]+([a-f0-9]{32})', resp.text)
    if not match:
        raise SessionInvalidError(
            "Could not find an actionRequest token in the page — cookie is likely invalid."
        )
    return match.group(1)


def check_session(server: str, cookie: str, action_request: str, city_id: str) -> tuple:
    response = call_nav(
        server, cookie, action_request, "townHall",
        {"cityId": city_id, "position": "0", "currentCityId": city_id},
    )
    new_token = extract_action_request(response)
    resources = extract_resources(response)
    return new_token, resources
