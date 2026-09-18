# IkarAI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the local scripts and Notion knowledge base that let a
cron-scheduled Claude Code session run the IkarAI empire hourly over
direct HTTP, with no browser automation anywhere in the loop.

**Architecture:** A small set of standalone Python modules (no package,
no framework) under `scripts/` and `har/`, each with a narrow
responsibility: read config, talk to Notion, talk to Ikariam, parse
HAR captures, manage the session cookie/token. A one-time bootstrap
script wires these together to create the Notion KB and seed the
Action Catalog from real captured traffic. The last task registers an
hourly cron cloud agent (via the `schedule` skill) whose prompt tells
Claude Code to follow `RUNBOOK.md` — the hourly cycle is Claude's own
judgment at runtime, not a deterministic script.

**Tech Stack:** Python 3 + `requests` + `pytest`. No web framework, no
ORM, no Notion SDK (thin hand-rolled REST wrapper — the API surface we
need is five endpoints).

**Spec:** `/opt/ideas/games/ikarAI/DESIGN.md`

## Global Constraints

- No browser automation anywhere in this project, at bootstrap or at runtime (spec §2, §7).
- No real-money spending, ever — requests to `intl14-shop.ikariam.gameforge.com` are never issued by any script (spec §9).
- No captcha-solving or anti-bot evasion; on any sign of a challenge, stop and report rather than working around it (spec §2, §9).
- No dry-run mode — scripts execute for real once run (spec §2).
- One account, one world (`s401-en.ikariam.gameforge.com`) — no multi-account support (spec §2).
- Login is never automated. The agent only ever reuses a session cookie you refresh manually; if it's missing or invalid, scripts stop and report rather than attempting to log in (spec §7, §9).
- All project artifacts live under `/opt/ideas/games/ikarAI/` (explicit user instruction — nothing goes in `docs/superpowers/...`).
- `.env` holds secrets and must be `chmod 600` (spec §4, §5).
- Destructive/high-blast-radius in-game actions (disbanding the army, leaving a clan, abandoning a city) require explicit human approval before executing (spec §9) — this is an RUNBOOK-level rule for the runtime agent, not something these bootstrap scripts need to enforce in code.

---

## Task 1: Project scaffolding + config loader

**Files:**
- Create: `/opt/ideas/games/ikarAI/requirements.txt`
- Create: `/opt/ideas/games/ikarAI/conftest.py`
- Create: `/opt/ideas/games/ikarAI/scripts/config.py`
- Test: `/opt/ideas/games/ikarAI/tests/test_config.py`

**Interfaces:**
- Produces: `config.parse_env_file(path: Path) -> dict`, `config.load_config(env_path: Path) -> Config`, `config.update_env_file(path: Path, updates: dict) -> None`, and the `Config` dataclass with fields `ikariam_username, ikariam_password, ikariam_server, ikariam_city_name, notion_token, notion_root_page_id, notion_action_catalog_db_id, notion_daily_logs_db_id, notion_diplomacy_db_id, notion_profile_page_id, notion_strategy_page_id, notion_world_knowledge_page_id` (the last six default to `""`).

- [ ] **Step 1: Create directories and requirements.txt**

```bash
mkdir -p /opt/ideas/games/ikarAI/scripts /opt/ideas/games/ikarAI/tests /opt/ideas/games/ikarAI/session
```

Write `/opt/ideas/games/ikarAI/requirements.txt`:

```
requests>=2.31
pytest>=8.0
```

Then: `cd /opt/ideas/games/ikarAI && pip install -r requirements.txt`

- [ ] **Step 2: Write conftest.py so tests can import scripts/ and har/ as plain modules**

```python
# /opt/ideas/games/ikarAI/conftest.py
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "har"))
```

- [ ] **Step 3: Write the failing test for the config loader**

```python
# /opt/ideas/games/ikarAI/tests/test_config.py
from pathlib import Path

import pytest

from config import Config, load_config, parse_env_file, update_env_file


ENV_CONTENT = """\
IKARIAM_USERNAME=avirzayev@gmail.com
IKARIAM_PASSWORD=aviavi123
IKARIAM_SERVER=s401-en.ikariam.gameforge.com
IKARIAM_CITY_NAME=ClaudeEmpire

NOTION_TOKEN=ntn_testtoken
NOTION_ROOT_PAGE_ID=3dfe84e3-20f5-81b4-9b83-c51021ac8986
"""


def test_parse_env_file_ignores_blank_lines_and_comments(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("# comment\n\nFOO=bar\n")
    assert parse_env_file(env_path) == {"FOO": "bar"}


def test_load_config_reads_required_fields(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(ENV_CONTENT)
    config = load_config(env_path)
    assert config == Config(
        ikariam_username="avirzayev@gmail.com",
        ikariam_password="aviavi123",
        ikariam_server="s401-en.ikariam.gameforge.com",
        ikariam_city_name="ClaudeEmpire",
        notion_token="ntn_testtoken",
        notion_root_page_id="3dfe84e3-20f5-81b4-9b83-c51021ac8986",
    )


def test_load_config_missing_required_key_raises(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("IKARIAM_USERNAME=x\n")
    with pytest.raises(KeyError):
        load_config(env_path)


def test_update_env_file_replaces_existing_key(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("FOO=old\nBAR=keep\n")
    update_env_file(env_path, {"FOO": "new"})
    assert env_path.read_text() == "FOO=new\nBAR=keep\n"


def test_update_env_file_appends_missing_key(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("FOO=old\n")
    update_env_file(env_path, {"BAZ": "added"})
    assert env_path.read_text() == "FOO=old\nBAZ=added\n"
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd /opt/ideas/games/ikarAI && pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'config'`

- [ ] **Step 5: Implement config.py**

```python
# /opt/ideas/games/ikarAI/scripts/config.py
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    ikariam_username: str
    ikariam_password: str
    ikariam_server: str
    ikariam_city_name: str
    notion_token: str
    notion_root_page_id: str
    notion_action_catalog_db_id: str = ""
    notion_daily_logs_db_id: str = ""
    notion_diplomacy_db_id: str = ""
    notion_profile_page_id: str = ""
    notion_strategy_page_id: str = ""
    notion_world_knowledge_page_id: str = ""


def parse_env_file(path: Path) -> dict:
    values = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def load_config(env_path: Path) -> Config:
    values = parse_env_file(env_path)
    return Config(
        ikariam_username=values["IKARIAM_USERNAME"],
        ikariam_password=values.get("IKARIAM_PASSWORD", ""),
        ikariam_server=values["IKARIAM_SERVER"],
        ikariam_city_name=values.get("IKARIAM_CITY_NAME", ""),
        notion_token=values["NOTION_TOKEN"],
        notion_root_page_id=values["NOTION_ROOT_PAGE_ID"],
        notion_action_catalog_db_id=values.get("NOTION_ACTION_CATALOG_DB_ID", ""),
        notion_daily_logs_db_id=values.get("NOTION_DAILY_LOGS_DB_ID", ""),
        notion_diplomacy_db_id=values.get("NOTION_DIPLOMACY_DB_ID", ""),
        notion_profile_page_id=values.get("NOTION_PROFILE_PAGE_ID", ""),
        notion_strategy_page_id=values.get("NOTION_STRATEGY_PAGE_ID", ""),
        notion_world_knowledge_page_id=values.get("NOTION_WORLD_KNOWLEDGE_PAGE_ID", ""),
    )


def update_env_file(path: Path, updates: dict) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    keys_present = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}")
                keys_present.add(key)
                continue
        new_lines.append(line)
    for key, value in updates.items():
        if key not in keys_present:
            new_lines.append(f"{key}={value}")
    path.write_text("\n".join(new_lines) + "\n")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /opt/ideas/games/ikarAI && pytest tests/test_config.py -v`
Expected: PASS (5 tests)

- [ ] **Step 7: Commit**

Note: `/opt` is not a git repository, so there is no commit step for this project — skip `git add`/`git commit` for every task in this plan. (If this ever changes, treat this note as stale and commit normally.)

---

## Task 2: Notion API client wrapper

**Files:**
- Create: `/opt/ideas/games/ikarAI/scripts/notion_client.py`
- Test: `/opt/ideas/games/ikarAI/tests/test_notion_client.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `notion_client.create_page(token, parent, properties, children=None) -> dict`, `create_database(token, parent_page_id, title, properties_schema) -> dict`, `query_database(token, database_id, filter_=None) -> list`, `search(token, query="") -> list`, `append_blocks(token, block_id, children) -> dict`, `title_prop(text) -> dict`, `rich_text_prop(text) -> dict`, `select_prop(name) -> dict`, `date_prop(iso_date) -> dict`, `paragraph_block(text) -> dict`, `heading2_block(text) -> dict`, `normalize_id(notion_id: str) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# /opt/ideas/games/ikarAI/tests/test_notion_client.py
from unittest.mock import MagicMock, patch

import notion_client as nc


def test_title_prop():
    assert nc.title_prop("Hello") == {
        "title": [{"type": "text", "text": {"content": "Hello"}}]
    }


def test_rich_text_prop():
    assert nc.rich_text_prop("Hello") == {
        "rich_text": [{"type": "text", "text": {"content": "Hello"}}]
    }


def test_select_prop():
    assert nc.select_prop("ally") == {"select": {"name": "ally"}}


def test_date_prop():
    assert nc.date_prop("2026-09-18") == {"date": {"start": "2026-09-18"}}


def test_normalize_id_strips_dashes():
    assert nc.normalize_id("3dfe84e3-20f5-81b4-9b83-c51021ac8986") == "3dfe84e320f581b49b83c51021ac8986"


def _mock_response(json_body, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


@patch("notion_client.requests.post")
def test_create_page_posts_expected_payload(mock_post):
    mock_post.return_value = _mock_response({"id": "page-1"})
    result = nc.create_page(
        "tok", {"type": "page_id", "page_id": "root-1"}, {"Name": nc.title_prop("x")}
    )
    assert result == {"id": "page-1"}
    args, kwargs = mock_post.call_args
    assert args[0] == "https://api.notion.com/v1/pages"
    assert kwargs["headers"]["Authorization"] == "Bearer tok"
    assert kwargs["json"]["parent"] == {"type": "page_id", "page_id": "root-1"}
    assert kwargs["json"]["properties"] == {"Name": nc.title_prop("x")}


@patch("notion_client.requests.post")
def test_create_database_posts_expected_payload(mock_post):
    mock_post.return_value = _mock_response({"id": "db-1"})
    schema = {"Name": {"title": {}}}
    result = nc.create_database("tok", "root-1", "Action Catalog", schema)
    assert result == {"id": "db-1"}
    args, kwargs = mock_post.call_args
    assert args[0] == "https://api.notion.com/v1/databases"
    assert kwargs["json"]["parent"] == {"type": "page_id", "page_id": "root-1"}
    assert kwargs["json"]["title"] == [{"type": "text", "text": {"content": "Action Catalog"}}]
    assert kwargs["json"]["properties"] == schema


@patch("notion_client.requests.post")
def test_query_database_returns_results_list(mock_post):
    mock_post.return_value = _mock_response({"results": [{"id": "row-1"}]})
    result = nc.query_database("tok", "db-1")
    assert result == [{"id": "row-1"}]
    args, kwargs = mock_post.call_args
    assert args[0] == "https://api.notion.com/v1/databases/db-1/query"


@patch("notion_client.requests.post")
def test_search_returns_results_list(mock_post):
    mock_post.return_value = _mock_response({"results": [{"id": "obj-1"}]})
    result = nc.search("tok", "Profile")
    assert result == [{"id": "obj-1"}]
    args, kwargs = mock_post.call_args
    assert kwargs["json"] == {"query": "Profile"}


@patch("notion_client.requests.patch")
def test_append_blocks_patches_children(mock_patch):
    mock_patch.return_value = _mock_response({"results": []})
    blocks = [nc.paragraph_block("hi")]
    nc.append_blocks("tok", "block-1", blocks)
    args, kwargs = mock_patch.call_args
    assert args[0] == "https://api.notion.com/v1/blocks/block-1/children"
    assert kwargs["json"] == {"children": blocks}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /opt/ideas/games/ikarAI && pytest tests/test_notion_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'notion_client'`

- [ ] **Step 3: Implement notion_client.py**

```python
# /opt/ideas/games/ikarAI/scripts/notion_client.py
import requests

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def create_page(token: str, parent: dict, properties: dict, children: list | None = None) -> dict:
    payload = {"parent": parent, "properties": properties}
    if children:
        payload["children"] = children
    resp = requests.post(f"{NOTION_API}/pages", headers=_headers(token), json=payload)
    resp.raise_for_status()
    return resp.json()


def create_database(token: str, parent_page_id: str, title: str, properties_schema: dict) -> dict:
    payload = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": properties_schema,
    }
    resp = requests.post(f"{NOTION_API}/databases", headers=_headers(token), json=payload)
    resp.raise_for_status()
    return resp.json()


def query_database(token: str, database_id: str, filter_: dict | None = None) -> list:
    payload = {"filter": filter_} if filter_ else {}
    resp = requests.post(
        f"{NOTION_API}/databases/{database_id}/query", headers=_headers(token), json=payload
    )
    resp.raise_for_status()
    return resp.json()["results"]


def search(token: str, query: str = "") -> list:
    resp = requests.post(f"{NOTION_API}/search", headers=_headers(token), json={"query": query})
    resp.raise_for_status()
    return resp.json()["results"]


def append_blocks(token: str, block_id: str, children: list) -> dict:
    resp = requests.patch(
        f"{NOTION_API}/blocks/{block_id}/children",
        headers=_headers(token),
        json={"children": children},
    )
    resp.raise_for_status()
    return resp.json()


def title_prop(text: str) -> dict:
    return {"title": [{"type": "text", "text": {"content": text}}]}


def rich_text_prop(text: str) -> dict:
    return {"rich_text": [{"type": "text", "text": {"content": text}}]}


def select_prop(name: str) -> dict:
    return {"select": {"name": name}}


def date_prop(iso_date: str) -> dict:
    return {"date": {"start": iso_date}}


def paragraph_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def heading2_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def normalize_id(notion_id: str) -> str:
    return notion_id.replace("-", "")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /opt/ideas/games/ikarAI && pytest tests/test_notion_client.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

Skipped — see Task 1 Step 7 note (`/opt` is not a git repo).

---

## Task 3: HAR parser

**Files:**
- Create: `/opt/ideas/games/ikarAI/har/parse_har.py`
- Test: `/opt/ideas/games/ikarAI/tests/test_parse_har.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `parse_har.load_har(path: Path) -> list`, `parse_har.world_server_entries(entries: list, host: str) -> list`, `parse_har.bucket_entries(entries: list) -> tuple[dict, dict]` (navs, actions), `parse_har.to_catalog_rows(navs: dict, actions: dict) -> list[dict]` where each row has keys `name, kind, method, path, action, function, params` (`params` is a sorted `list[str]`).

- [ ] **Step 1: Write the failing tests using a small synthetic HAR entry list**

These tests don't touch the real (large, sensitive) HAR files — they use a minimal fixture shaped exactly like the real captures, built from the request/response shapes already confirmed against `har/sample2.har` during design.

```python
# /opt/ideas/games/ikarAI/tests/test_parse_har.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /opt/ideas/games/ikarAI && pytest tests/test_parse_har.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'parse_har'`

- [ ] **Step 3: Implement parse_har.py**

```python
# /opt/ideas/games/ikarAI/har/parse_har.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /opt/ideas/games/ikarAI && pytest tests/test_parse_har.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

Skipped — see Task 1 Step 7 note.

---

## Task 4: Notion KB bootstrap script

**Files:**
- Create: `/opt/ideas/games/ikarAI/scripts/bootstrap_kb.py`
- Test: `/opt/ideas/games/ikarAI/tests/test_bootstrap_kb.py`

**Interfaces:**
- Consumes: `config.load_config`, `config.update_env_file` (Task 1); `notion_client.*` (Task 2); `parse_har.load_har`, `parse_har.world_server_entries`, `parse_har.bucket_entries`, `parse_har.to_catalog_rows` (Task 3).
- Produces: `bootstrap_kb.find_child_by_title(token, parent_page_id, title, object_type) -> str | None`, `bootstrap_kb.ensure_database(token, parent_page_id, title, schema) -> str`, `bootstrap_kb.ensure_page(token, parent_page_id, title, children=None) -> str`, `bootstrap_kb.ACTION_CATALOG_SCHEMA`, `bootstrap_kb.DAILY_LOGS_SCHEMA`, `bootstrap_kb.DIPLOMACY_SCHEMA`, `bootstrap_kb.main() -> None`.

- [ ] **Step 1: Write the failing tests for the idempotency helpers**

```python
# /opt/ideas/games/ikarAI/tests/test_bootstrap_kb.py
from unittest.mock import MagicMock, patch

import bootstrap_kb as bk


def _db(id_, title, parent_page_id):
    return {
        "object": "database",
        "id": id_,
        "title": [{"plain_text": title}],
        "parent": {"type": "page_id", "page_id": parent_page_id},
    }


def _page(id_, title, parent_page_id):
    return {
        "object": "page",
        "id": id_,
        "properties": {"title": {"type": "title", "title": [{"plain_text": title}]}},
        "parent": {"type": "page_id", "page_id": parent_page_id},
    }


@patch("bootstrap_kb.nc.search")
def test_find_child_by_title_matches_object_and_parent(mock_search):
    mock_search.return_value = [
        _db("db-1", "Action Catalog", "root-1"),
        _db("db-2", "Action Catalog", "some-other-root"),
        _page("page-1", "Action Catalog", "root-1"),
    ]
    found = bk.find_child_by_title("tok", "root-1", "Action Catalog", "database")
    assert found == "db-1"


@patch("bootstrap_kb.nc.search")
def test_find_child_by_title_returns_none_when_no_match(mock_search):
    mock_search.return_value = []
    assert bk.find_child_by_title("tok", "root-1", "Missing", "page") is None


@patch("bootstrap_kb.nc.create_database")
@patch("bootstrap_kb.find_child_by_title")
def test_ensure_database_skips_creation_when_found(mock_find, mock_create):
    mock_find.return_value = "db-existing"
    result = bk.ensure_database("tok", "root-1", "Action Catalog", {"Name": {"title": {}}})
    assert result == "db-existing"
    mock_create.assert_not_called()


@patch("bootstrap_kb.nc.create_database")
@patch("bootstrap_kb.find_child_by_title")
def test_ensure_database_creates_when_missing(mock_find, mock_create):
    mock_find.return_value = None
    mock_create.return_value = {"id": "db-new"}
    result = bk.ensure_database("tok", "root-1", "Action Catalog", {"Name": {"title": {}}})
    assert result == "db-new"
    mock_create.assert_called_once_with("tok", "root-1", "Action Catalog", {"Name": {"title": {}}})


@patch("bootstrap_kb.nc.create_page")
@patch("bootstrap_kb.find_child_by_title")
def test_ensure_page_creates_when_missing(mock_find, mock_create):
    mock_find.return_value = None
    mock_create.return_value = {"id": "page-new"}
    result = bk.ensure_page("tok", "root-1", "Strategy", children=[{"x": 1}])
    assert result == "page-new"
    args, kwargs = mock_create.call_args
    assert args[0] == "tok"
    assert args[1] == {"type": "page_id", "page_id": "root-1"}
    assert args[3] == [{"x": 1}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /opt/ideas/games/ikarAI && pytest tests/test_bootstrap_kb.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bootstrap_kb'`

- [ ] **Step 3: Implement bootstrap_kb.py**

```python
# /opt/ideas/games/ikarAI/scripts/bootstrap_kb.py
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "har"))

import notion_client as nc
from config import load_config, update_env_file
from parse_har import bucket_entries, load_har, to_catalog_rows, world_server_entries

ACTION_CATALOG_SCHEMA = {
    "Name": {"title": {}},
    "Kind": {"select": {"options": [{"name": "nav"}, {"name": "action"}]}},
    "Method": {"select": {"options": [{"name": "GET"}, {"name": "POST"}]}},
    "Path": {"rich_text": {}},
    "Action": {"rich_text": {}},
    "Function": {"rich_text": {}},
    "Params": {"rich_text": {}},
    "LastVerified": {"date": {}},
    "Notes": {"rich_text": {}},
}

DAILY_LOGS_SCHEMA = {
    "Date": {"title": {}},
    "Summary": {"rich_text": {}},
    "ResourceChanges": {"rich_text": {}},
    "Events": {"rich_text": {}},
    "Decisions": {"rich_text": {}},
    "NextSteps": {"rich_text": {}},
}

DIPLOMACY_SCHEMA = {
    "Name": {"title": {}},
    "Type": {"select": {"options": [{"name": "player"}, {"name": "clan"}]}},
    "Relationship": {
        "select": {
            "options": [
                {"name": "ally"},
                {"name": "enemy"},
                {"name": "neutral"},
                {"name": "unknown"},
            ]
        }
    },
    "History": {"rich_text": {}},
    "LastUpdated": {"date": {}},
}


def find_child_by_title(token: str, parent_page_id: str, title: str, object_type: str) -> str | None:
    results = nc.search(token, query=title)
    for r in results:
        if r["object"] != object_type:
            continue
        parent = r.get("parent", {})
        parent_id = parent.get("page_id") or parent.get("database_id") or ""
        if nc.normalize_id(parent_id) != nc.normalize_id(parent_page_id):
            continue
        if object_type == "database":
            rich = r.get("title", [])
        else:
            title_val = next(
                (v for v in r.get("properties", {}).values() if v.get("type") == "title"),
                {"title": []},
            )
            rich = title_val.get("title", [])
        text = "".join(t["plain_text"] for t in rich)
        if text == title:
            return r["id"]
    return None


def ensure_database(token: str, parent_page_id: str, title: str, schema: dict) -> str:
    existing = find_child_by_title(token, parent_page_id, title, "database")
    if existing:
        return existing
    created = nc.create_database(token, parent_page_id, title, schema)
    return created["id"]


def ensure_page(token: str, parent_page_id: str, title: str, children: list | None = None) -> str:
    existing = find_child_by_title(token, parent_page_id, title, "page")
    if existing:
        return existing
    properties = {"title": nc.title_prop(title)}
    created = nc.create_page(
        token, {"type": "page_id", "page_id": parent_page_id}, properties, children
    )
    return created["id"]


def build_persona_children(config) -> list:
    return [
        nc.heading2_block("Ruler"),
        nc.paragraph_block(f"Avatar name: {config.ikariam_city_name}"),
        nc.paragraph_block(
            "Seeks to be the best in the game and that everyone will know it. "
            "Very patient and knows when he is in a position of power and when "
            "he needs to go backstage and wait for the right moment. Great "
            "politician who can create allies. Doesn't keep things that don't "
            "serve him."
        ),
        nc.heading2_block("Account facts"),
        nc.paragraph_block(f"Server: {config.ikariam_server}"),
        nc.paragraph_block("Capital city: Polis (cityId 355955) on island Stridios (islandId 692)"),
    ]


def build_strategy_children() -> list:
    return [
        nc.heading2_block("Current State"),
        nc.paragraph_block(
            "Freshly founded empire: one city (Polis), Town Hall level 1, no "
            "other buildings constructed yet, no premium account. No "
            "governance chosen, no active campaigns."
        ),
        nc.heading2_block("Active Initiatives"),
        nc.paragraph_block("None yet — the first scheduled run should set initial priorities."),
    ]


def seed_action_catalog(token: str, action_catalog_id: str, har_path: Path, server: str) -> int:
    existing_rows = nc.query_database(token, action_catalog_id)
    existing_names = set()
    for row in existing_rows:
        title_vals = row["properties"]["Name"]["title"]
        if title_vals:
            existing_names.add(title_vals[0]["plain_text"])

    entries = world_server_entries(load_har(har_path), server)
    navs, actions = bucket_entries(entries)
    rows = to_catalog_rows(navs, actions)

    created = 0
    today = date.today().isoformat()
    for row in rows:
        if row["name"] in existing_names:
            continue
        properties = {
            "Name": nc.title_prop(row["name"]),
            "Kind": nc.select_prop(row["kind"]),
            "Method": nc.select_prop(row["method"]),
            "Path": nc.rich_text_prop(row["path"]),
            "Action": nc.rich_text_prop(row["action"]),
            "Function": nc.rich_text_prop(row["function"]),
            "Params": nc.rich_text_prop(", ".join(row["params"])),
            "LastVerified": nc.date_prop(today),
            "Notes": nc.rich_text_prop(""),
        }
        nc.create_page(token, {"type": "database_id", "database_id": action_catalog_id}, properties)
        created += 1
    return created


def main() -> None:
    env_path = PROJECT_ROOT / ".env"
    config = load_config(env_path)
    token = config.notion_token
    root = config.notion_root_page_id

    action_catalog_id = ensure_database(token, root, "Action Catalog", ACTION_CATALOG_SCHEMA)
    daily_logs_id = ensure_database(token, root, "Daily Logs", DAILY_LOGS_SCHEMA)
    diplomacy_id = ensure_database(token, root, "Diplomacy", DIPLOMACY_SCHEMA)
    profile_id = ensure_page(token, root, "Profile & Persona", build_persona_children(config))
    strategy_id = ensure_page(token, root, "Strategy", build_strategy_children())
    world_id = ensure_page(token, root, "World Knowledge", [nc.heading2_block("Learned Mechanics")])

    update_env_file(
        env_path,
        {
            "NOTION_ACTION_CATALOG_DB_ID": action_catalog_id,
            "NOTION_DAILY_LOGS_DB_ID": daily_logs_id,
            "NOTION_DIPLOMACY_DB_ID": diplomacy_id,
            "NOTION_PROFILE_PAGE_ID": profile_id,
            "NOTION_STRATEGY_PAGE_ID": strategy_id,
            "NOTION_WORLD_KNOWLEDGE_PAGE_ID": world_id,
        },
    )

    created = seed_action_catalog(
        token, action_catalog_id, PROJECT_ROOT / "har" / "sample2.har", config.ikariam_server
    )
    print(f"Bootstrap complete. Action Catalog: {created} new rows.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /opt/ideas/games/ikarAI && pytest tests/test_bootstrap_kb.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run the real bootstrap against the live Notion workspace**

The root page `IkarAI Empire` (`3dfe84e3-20f5-81b4-9b83-c51021ac8986`) already exists and `.env` already has `NOTION_TOKEN` and `NOTION_ROOT_PAGE_ID` set (done during design). Run:

```bash
cd /opt/ideas/games/ikarAI && python3 scripts/bootstrap_kb.py
```

Expected output: `Bootstrap complete. Action Catalog: <N> new rows.` where N is the number of distinct nav views + actions found in `har/sample2.har` (28 nav views + 3 actions = 31, based on the exploration done during planning — reconfirm the exact count against the actual file since HAR content is real capture data, not fixed).

- [ ] **Step 6: Verify live in Notion**

```bash
set -a; source /opt/ideas/games/ikarAI/.env; set +a
curl -s -X POST "https://api.notion.com/v1/databases/$NOTION_ACTION_CATALOG_DB_ID/query" \
  -H "Authorization: Bearer $NOTION_TOKEN" -H "Notion-Version: 2022-06-28" \
  -H "Content-Type: application/json" -d '{}' | python3 -c "import json,sys; print(len(json.load(sys.stdin)['results']))"
```

Expected: prints the same row count as Step 5's output. Also open the "IkarAI Empire" page in Notion and confirm Profile & Persona, Strategy, World Knowledge, Daily Logs, Diplomacy, and Action Catalog all exist with sensible content.

- [ ] **Step 7: Commit**

Skipped — see Task 1 Step 7 note.

---

## Task 5: Session store

**Files:**
- Create: `/opt/ideas/games/ikarAI/scripts/session_store.py`
- Test: `/opt/ideas/games/ikarAI/tests/test_session_store.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `session_store.SessionError`, `session_store.read_cookie(session_dir: Path) -> str`, `session_store.read_action_request(session_dir: Path) -> str`, `session_store.write_action_request(session_dir: Path, token: str) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# /opt/ideas/games/ikarAI/tests/test_session_store.py
import pytest

from session_store import SessionError, read_action_request, read_cookie, write_action_request


def test_read_cookie_raises_when_file_missing(tmp_path):
    with pytest.raises(SessionError):
        read_cookie(tmp_path)


def test_read_cookie_raises_when_file_empty(tmp_path):
    (tmp_path / "cookie.txt").write_text("  \n")
    with pytest.raises(SessionError):
        read_cookie(tmp_path)


def test_read_cookie_returns_stripped_content(tmp_path):
    (tmp_path / "cookie.txt").write_text("ikariam_session=abc123\n")
    assert read_cookie(tmp_path) == "ikariam_session=abc123"


def test_read_action_request_raises_when_missing(tmp_path):
    with pytest.raises(SessionError):
        read_action_request(tmp_path)


def test_write_then_read_action_request_roundtrips(tmp_path):
    write_action_request(tmp_path, "token123")
    assert read_action_request(tmp_path) == "token123"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /opt/ideas/games/ikarAI && pytest tests/test_session_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'session_store'`

- [ ] **Step 3: Implement session_store.py**

```python
# /opt/ideas/games/ikarAI/scripts/session_store.py
from pathlib import Path


class SessionError(Exception):
    pass


def read_cookie(session_dir: Path) -> str:
    cookie_path = session_dir / "cookie.txt"
    if not cookie_path.exists():
        raise SessionError(
            "No session cookie found — refresh it manually (see DESIGN.md §7)."
        )
    cookie = cookie_path.read_text().strip()
    if not cookie:
        raise SessionError(
            "Session cookie file is empty — refresh it manually (see DESIGN.md §7)."
        )
    return cookie


def read_action_request(session_dir: Path) -> str:
    token_path = session_dir / "action_request.txt"
    if not token_path.exists():
        raise SessionError(
            "No actionRequest token found — seed it manually (see DESIGN.md §7)."
        )
    token = token_path.read_text().strip()
    if not token:
        raise SessionError(
            "actionRequest token file is empty — seed it manually (see DESIGN.md §7)."
        )
    return token


def write_action_request(session_dir: Path, token: str) -> None:
    (session_dir / "action_request.txt").write_text(token)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /opt/ideas/games/ikarAI && pytest tests/test_session_store.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

Skipped — see Task 1 Step 7 note.

---

## Task 6: Ikariam HTTP client

**Files:**
- Create: `/opt/ideas/games/ikarAI/scripts/ikariam_client.py`
- Test: `/opt/ideas/games/ikarAI/tests/test_ikariam_client.py`

**Interfaces:**
- Consumes: nothing from other tasks directly (takes cookie/token as plain strings — callers get those from `session_store`).
- Produces: `ikariam_client.SessionInvalidError`, `ikariam_client.build_headers(cookie, with_body=False) -> dict`, `ikariam_client.extract_action_request(response_json: list) -> str`, `ikariam_client.extract_resources(response_json: list) -> dict`, `ikariam_client.call_nav(server, cookie, action_request, view, extra_params) -> list`, `ikariam_client.call_action(server, cookie, action_request, action, function, extra_params) -> list`, `ikariam_client.check_session(server, cookie, action_request, city_id) -> tuple[str, dict]`.

This module's request/response shapes are taken directly from real
traffic captured in `har/sample2.har` during design — not guessed.

- [ ] **Step 1: Write the failing tests**

```python
# /opt/ideas/games/ikarAI/tests/test_ikariam_client.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /opt/ideas/games/ikarAI && pytest tests/test_ikariam_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ikariam_client'`

- [ ] **Step 3: Implement ikariam_client.py**

```python
# /opt/ideas/games/ikarAI/scripts/ikariam_client.py
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


def check_session(server: str, cookie: str, action_request: str, city_id: str) -> tuple:
    response = call_nav(
        server, cookie, action_request, "townHall",
        {"cityId": city_id, "position": "0", "currentCityId": city_id},
    )
    new_token = extract_action_request(response)
    resources = extract_resources(response)
    return new_token, resources
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /opt/ideas/games/ikarAI && pytest tests/test_ikariam_client.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Seed the session files and verify live against the real game**

This requires a fresh cookie: log into `https://s401-en.ikariam.gameforge.com/` in your own browser, open DevTools → Network, click any in-game action, right-click the request to `s401-en.ikariam.gameforge.com` → Copy → Copy value of the `Cookie` request header (or "Copy as cURL" and pull the `-H 'Cookie: ...'` line out). Also copy the `actionRequest` query-param or form-field value from that same request.

```bash
echo -n 'PASTE_COOKIE_HEADER_VALUE_HERE' > /opt/ideas/games/ikarAI/session/cookie.txt
echo -n 'PASTE_ACTION_REQUEST_TOKEN_HERE' > /opt/ideas/games/ikarAI/session/action_request.txt
```

Then run a one-off live check:

```bash
cd /opt/ideas/games/ikarAI && python3 -c "
from pathlib import Path
import sys
sys.path.insert(0, 'scripts')
from session_store import read_cookie, read_action_request, write_action_request
from ikariam_client import check_session

session_dir = Path('session')
cookie = read_cookie(session_dir)
token = read_action_request(session_dir)
new_token, resources = check_session('s401-en.ikariam.gameforge.com', cookie, token, '355955')
write_action_request(session_dir, new_token)
print('Session valid. Resources:', resources)
"
```

Expected: prints `Session valid. Resources: {...}` with real numbers. If it raises `SessionInvalidError`, the cookie or token was stale — repeat the copy step with a freshly loaded page. If it consistently fails, note that in the plan's execution log and treat it as a real finding, not something to work around with retries or guessed headers (see Global Constraints — no anti-bot evasion).

- [ ] **Step 6: Commit**

Skipped — see Task 1 Step 7 note.

---

## Task 7: RUNBOOK.md — the hourly cycle instructions

**Files:**
- Create: `/opt/ideas/games/ikarAI/RUNBOOK.md`

**Interfaces:**
- Consumes: all scripts from Tasks 1–6 by name/path; `.env` variable names written by Task 4's bootstrap.
- Produces: the document a scheduled Claude Code session reads and follows each hour (referenced by name from Task 8's cron prompt).

- [ ] **Step 1: Write RUNBOOK.md**

```markdown
# IkarAI Hourly Runbook

You are the ruler of the IkarAI empire in Ikariam. This file is your
instructions for one scheduled wake-up. Read `/opt/ideas/games/ikarAI/DESIGN.md`
first if you haven't internalized it yet — it's the source of truth for
constraints. This file is the step-by-step for what to actually do.

All paths below are relative to `/opt/ideas/games/ikarAI/`.

## 1. Load session state

- Read `.env` for `IKARIAM_SERVER`, `NOTION_TOKEN`, `NOTION_ROOT_PAGE_ID`,
  and the six `NOTION_*_ID` variables written by `scripts/bootstrap_kb.py`.
- Use `scripts/session_store.read_cookie()` and `read_action_request()`
  to load the current session state from `session/`.
- If either raises `SessionError`: **stop this run.** Send a push
  notification saying the Ikariam session needs a manual refresh
  (see DESIGN.md §7) and exit. Do not attempt to log in yourself.

## 2. Verify the session is still valid

- Call `scripts/ikariam_client.check_session(server, cookie, action_request, city_id)`
  (city_id is `355955` for the capital, "Polis" — confirm against
  the Action Catalog / Profile page if a second city has since been founded).
- On success: call `scripts/session_store.write_action_request()` with the
  new token immediately, before doing anything else, so the token is never
  stale even if this run fails partway through.
- On `SessionInvalidError`: stop this run, push-notify that the session
  needs a manual refresh, exit.

## 3. Pull context from Notion

Using `scripts/notion_client.query_database()` / a page read:
- `NOTION_PROFILE_PAGE_ID` — who you are, your persona, red lines.
- `NOTION_STRATEGY_PAGE_ID` — current plan and active initiatives.
- `NOTION_DAILY_LOGS_DB_ID` — the last few days of logs, and whether
  today already has an entry (title = today's ISO date).
- `NOTION_ACTION_CATALOG_DB_ID` — the full catalog of known HTTP actions.
- `NOTION_DIPLOMACY_DB_ID` — known players/clans and relationship status.

## 4. Fetch current game state

Use already-documented "nav" entries from the Action Catalog (e.g.
`view:townHall`) via `scripts/ikariam_client.call_nav()` to see current
resources, buildings, and any pending events. `check_session`'s
returned `resources` dict already gives you a resource snapshot for
free — use it as your first data point before deciding whether more
navigation calls are worth the request budget this hour.

## 5. Decide

Compare state against Strategy. Use your judgment — this step is not
scripted. Guidelines:

- **Nothing to do this hour is the expected common case.** If so, skip
  to step 7 without touching Ikariam again.
- **Known action needed:** look it up in the Action Catalog, call it via
  `scripts/ikariam_client.call_action()`, update the stored
  `action_request` token from the response immediately after.
- **Real-money purchase would help:** never execute it. Log it as
  "considered, declined — policy" in today's Daily Log and move on.
  This is a flat rule, not a judgment call.
- **Destructive/high-blast-radius action** (disbanding the army, leaving
  a clan, abandoning a city): do not execute. Push-notify and describe
  what you'd do and why; wait for explicit approval before a future run
  executes it.
- **Unfamiliar situation, no catalog entry fits:** research the general
  mechanic (web search) but do not guess-execute an unverified HTTP call
  against the live account. Log it in today's Daily Log as
  blocked/needs-bootstrap. If it's time-sensitive (e.g., under attack),
  push-notify.
- **You executed something and learned its real request shape** (e.g.
  you had to adjust params from what the catalog said): update that
  Action Catalog row's `Notes` and `LastVerified` via
  `scripts/notion_client`. If it's a genuinely new action, add a new row
  following the same property shape `scripts/bootstrap_kb.py` uses
  (`Name`, `Kind`, `Method`, `Path`, `Action`, `Function`, `Params`,
  `LastVerified`, `Notes`).

## 6. Update Diplomacy if relevant

If this hour's events involved another player or clan (attack, message,
alliance offer), update or create their row in `NOTION_DIPLOMACY_DB_ID`.

## 7. Update today's Daily Log

If today's date has no entry in `NOTION_DAILY_LOGS_DB_ID` yet, create
one. Otherwise append to it (Notion pages are appended via
`scripts/notion_client.append_blocks()`, or by updating the
`Summary`/`Events`/`Decisions`/`NextSteps` rich-text properties).
Capture: what changed, resources gained/lost, decisions made and why
(in character — this is where the persona shows), what's next.

## 8. Exit

That's the full cycle. There's no cleanup step — exiting the process is
the end of the run.
```

- [ ] **Step 2: Commit**

Skipped — see Task 1 Step 7 note.

---

## Task 8: Configure the hourly cron schedule

**Ruling (supersedes the original steps below):** A cron-scheduled
*cloud* agent (via the `schedule` skill) cannot work for this project —
cloud routines run in an isolated sandbox with no access to this
machine's local files, `.env`, or `session/` cookie, which this entire
project's state model depends on. Discovered when the `schedule` skill
was actually invoked during execution; DESIGN.md §3/§10 updated to
match. This task instead sets up a **local** cron job that invokes
`claude -p` headlessly on this machine. Original steps struck through
for the record; the replacement section below is what's actually built.

~~**Files:**~~
~~- None created — this task registers a cloud cron agent via the `schedule` skill; there's no local file artifact beyond what already exists.~~

~~**Interfaces:**~~
~~- Consumes: `RUNBOOK.md` (Task 7) by reference in the cron prompt.~~

~~- [ ] **Step 1: Register the hourly cron agent**~~

~~Invoke the `schedule` skill (not `CronCreate` directly — the skill~~
~~handles the tool's exact schema) to create a routine:~~

~~- Schedule: hourly (cron expression `0 * * * *`).~~
~~- Prompt: `Follow /opt/ideas/games/ikarAI/RUNBOOK.md for this hour's IkarAI cycle. If the session cookie is invalid or missing, stop and notify rather than attempting to log in yourself.`~~
~~- Working directory / context: this needs read/write access to `/opt/ideas/games/ikarAI/` and network access for both the Notion API and `s401-en.ikariam.gameforge.com`.~~

~~- [ ] **Step 2: Trigger one run manually and verify end-to-end**~~

~~Before trusting the hourly cadence, trigger the new routine once~~
~~on-demand (the `schedule` skill/its listing supports a manual "run now").~~
~~Confirm afterward:~~

~~- A new or updated row appears in `NOTION_DAILY_LOGS_DB_ID` for today.~~
~~- `session/action_request.txt` has a different value than before the run~~
  ~~(proof the token-refresh chain worked).~~
~~- No request was made to `intl14-shop.ikariam.gameforge.com` (check~~
  ~~nothing purchase-related happened if you have any way to audit the~~
  ~~run's tool calls/logs).~~

~~- [ ] **Step 3: Confirm the schedule persists**~~

~~List scheduled routines (via the `schedule` skill) and confirm the new~~
~~hourly IkarAI routine shows up with the correct cron expression and is~~
~~enabled.~~

### Replacement: local cron job

**Files:**
- Create: `/opt/ideas/games/ikarAI/scripts/run_hourly_cycle.sh`

- [ ] **Step 1: Write the wrapper script cron will invoke**

```bash
#!/usr/bin/env bash
# /opt/ideas/games/ikarAI/scripts/run_hourly_cycle.sh
set -euo pipefail
cd /opt/ideas/games/ikarAI
exec claude -p "Follow /opt/ideas/games/ikarAI/RUNBOOK.md for this hour's IkarAI cycle. If the session cookie is invalid or missing, stop and notify rather than attempting to log in yourself." \
  --allowedTools "Bash,Read,Write,Edit" \
  >> /opt/ideas/games/ikarAI/session/hourly.log 2>&1
```

Make it executable: `chmod +x /opt/ideas/games/ikarAI/scripts/run_hourly_cycle.sh`

- [ ] **Step 2: Install the hourly crontab entry**

```bash
(crontab -l 2>/dev/null; echo "0 * * * * /opt/ideas/games/ikarAI/scripts/run_hourly_cycle.sh") | crontab -
```

Verify: `crontab -l` shows the new line.

- [ ] **Step 3: Trigger one run manually and verify end-to-end**

```bash
/opt/ideas/games/ikarAI/scripts/run_hourly_cycle.sh
```

Confirm afterward:
- `session/hourly.log` has fresh output from the run.
- A new or updated row appears in `NOTION_DAILY_LOGS_DB_ID` for today.
- `session/action_request.txt` has a different value than before the run
  (proof the token-refresh chain worked).
- No request was made to `intl14-shop.ikariam.gameforge.com`.

- [ ] **Step 4: Commit**

Commit `scripts/run_hourly_cycle.sh` (the crontab entry itself is
machine state, not a repo file, and isn't committed).

---

## Plan self-review notes

- **Spec coverage:** §3 architecture → Tasks 1–8 overall. §4 file layout → Task 1 (scaffolding), Task 3 (har/), matches exactly. §5 credentials → Task 1 config loader reads `.env` as already populated during design. §6 Notion KB structure → Task 4 (all six objects, matching schemas). §7 bootstrap → Tasks 3 (parser) + 4 (orchestration) + 6 Step 5 (session seeding) + manual-login note carried into RUNBOOK.md step 1. §8 hourly cycle → Task 7 (RUNBOOK.md, since the decision logic is Claude's runtime judgment, not deterministic code — this is a legitimate divergence from "tasks produce code," documented rather than hidden). §9 safety guardrails → encoded as explicit rules in RUNBOOK.md step 5 (real-money hard block, destructive-action approval gate, unknown-action rule) since these are judgment calls at runtime, not enforceable in the thin HTTP client without over-building it. §10 scheduling → Task 8. §11 known risks → not a task; they're accepted risks, correctly left undone.
- **Placeholder scan:** no TBD/TODO; every code block is complete and real; RUNBOOK.md has no unfilled brackets.
- **Type consistency:** `Config` field names match between Task 1's dataclass and Task 4's `config.notion_root_page_id` etc. usage. `to_catalog_rows` row dict keys (`name, kind, method, path, action, function, params`) match what Task 4's `seed_action_catalog` reads. `ikariam_client.check_session` signature `(server, cookie, action_request, city_id)` matches its Task 6 test and its RUNBOOK.md usage description.
