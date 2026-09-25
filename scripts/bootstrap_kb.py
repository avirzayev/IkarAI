import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "har"))

import notion_client as nc
from config import load_config, update_env_file
from log_usage import TOKEN_USAGE_SCHEMA
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

HUMAN_REQUIRED_SCHEMA = {
    "Name": {"title": {}},
    "CreatedAt": {"date": {}},
}

CHORES_SCHEMA = {
    "Name": {"title": {}},
    "Status": {
        "select": {
            "options": [
                {"name": "proposed"},
                {"name": "dry-run"},
                {"name": "active"},
                {"name": "disabled"},
            ]
        }
    },
    "Rule": {"rich_text": {}},
    "Why": {"rich_text": {}},
    "DryRunsOK": {"number": {"format": "number"}},
    "Runs": {"number": {"format": "number"}},
    "Failures": {"number": {"format": "number"}},
    "LastRun": {"date": {}},
    "LastResult": {"rich_text": {}},
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
    human_required_id = ensure_database(token, root, "Human Required", HUMAN_REQUIRED_SCHEMA)
    token_usage_id = ensure_database(token, root, "Token Usage", TOKEN_USAGE_SCHEMA)
    chores_id = ensure_database(token, root, "Chores", CHORES_SCHEMA)
    profile_id = ensure_page(token, root, "Profile & Persona", build_persona_children(config))
    strategy_id = ensure_page(token, root, "Strategy", build_strategy_children())
    world_id = ensure_page(token, root, "World Knowledge", [nc.heading2_block("Learned Mechanics")])
    strategy_archive_id = ensure_page(token, root, "Strategy Archive")

    update_env_file(
        env_path,
        {
            "NOTION_ACTION_CATALOG_DB_ID": action_catalog_id,
            "NOTION_DAILY_LOGS_DB_ID": daily_logs_id,
            "NOTION_DIPLOMACY_DB_ID": diplomacy_id,
            "NOTION_HUMAN_REQUIRED_DB_ID": human_required_id,
            "NOTION_PROFILE_PAGE_ID": profile_id,
            "NOTION_STRATEGY_PAGE_ID": strategy_id,
            "NOTION_WORLD_KNOWLEDGE_PAGE_ID": world_id,
            "NOTION_STRATEGY_ARCHIVE_PAGE_ID": strategy_archive_id,
            "NOTION_TOKEN_USAGE_DB_ID": token_usage_id,
            "NOTION_CHORES_DB_ID": chores_id,
        },
    )

    created = seed_action_catalog(
        token, action_catalog_id, PROJECT_ROOT / "har" / "sample2.har", config.ikariam_server
    )
    print(f"Bootstrap complete. Action Catalog: {created} new rows.")


if __name__ == "__main__":
    main()
