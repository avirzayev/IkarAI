import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def load_har(path: Path) -> list:
    data = json.loads(Path(path).read_text())
    return data["log"]["entries"]


def world_server_entries(entries: list, host: str) -> list:
    return [e for e in entries if host in e["request"]["url"]]


def bucket_entries(entries: list) -> tuple:
    navs: dict = {}
    actions: dict = {}
    for entry in entries:
        req = entry["request"]
        if req["method"] != "POST":
            continue
        parsed = urlparse(req["url"])
        qs = parse_qs(parsed.query)
        if parsed.path == "/":
            view = qs.get("view", [""])[0]
            if view and view not in navs:
                navs[view] = list(qs.keys())
        elif parsed.path == "/index.php":
            body = req.get("postData", {}).get("text", "")
            body_qs = parse_qs(body)
            action = body_qs.get("action", [""])[0]
            function = body_qs.get("function", [""])[0]
            if action and (action, function) not in actions:
                actions[(action, function)] = list(body_qs.keys())
    return navs, actions


def to_catalog_rows(navs: dict, actions: dict) -> list:
    rows = []
    for view, params in navs.items():
        rows.append(
            {
                "name": f"view:{view}",
                "kind": "nav",
                "method": "POST",
                "path": "/",
                "action": "",
                "function": "",
                "params": sorted(params),
            }
        )
    for (action, function), params in actions.items():
        name = f"action:{action}:{function}" if function else f"action:{action}"
        rows.append(
            {
                "name": name,
                "kind": "action",
                "method": "POST",
                "path": "/index.php",
                "action": action,
                "function": function,
                "params": sorted(params),
            }
        )
    return rows
