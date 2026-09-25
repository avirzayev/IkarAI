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
    notion_human_required_db_id: str = ""
    notion_strategy_archive_page_id: str = ""
    notion_token_usage_db_id: str = ""
    notion_catalog_archive_page_id: str = ""
    timezone: str = "UTC"


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
        notion_human_required_db_id=values.get("NOTION_HUMAN_REQUIRED_DB_ID", ""),
        notion_strategy_archive_page_id=values.get("NOTION_STRATEGY_ARCHIVE_PAGE_ID", ""),
        notion_token_usage_db_id=values.get("NOTION_TOKEN_USAGE_DB_ID", ""),
        notion_catalog_archive_page_id=values.get("NOTION_CATALOG_ARCHIVE_PAGE_ID", ""),
        timezone=values.get("TIMEZONE", "UTC"),
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
