"""Record one cycle's token usage in Notion.

Invoked by run_hourly_cycle.sh after `claude -p --output-format json`
exits, with the path to that JSON result. Appends a human-readable
summary to session/hourly.log (via stdout) and adds a row to the
"Token Usage" Notion database — created lazily under the KB root the
first time, with its id persisted to .env.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "har"))

import notion_client as nc
from config import load_config, update_env_file

TOKEN_USAGE_SCHEMA = {
    "Run": {"title": {}},
    "Date": {"date": {}},
    "Status": {"select": {"options": [{"name": "ok"}, {"name": "error"}]}},
    "Total Tokens": {"number": {"format": "number_with_commas"}},
    "Turns": {"number": {"format": "number"}},
    "Input": {"number": {"format": "number_with_commas"}},
    "Cache Read": {"number": {"format": "number_with_commas"}},
    "Cache Write": {"number": {"format": "number_with_commas"}},
    "Output": {"number": {"format": "number_with_commas"}},
    "Thinking": {"number": {"format": "number_with_commas"}},
    "Cost USD": {"number": {"format": "dollar"}},
    "Duration (min)": {"number": {"format": "number"}},
    "Model": {"rich_text": {}},
    "Summary": {"rich_text": {}},
}


def parse_result(raw: str, exit_code: int = 0) -> dict:
    """Turn `claude -p --output-format json` stdout into flat usage stats.

    Tolerates non-JSON output (e.g. an auth error printed as text) so a
    broken run still gets a row instead of silently vanishing."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {
            "status": "error", "turns": 0, "input": 0, "cache_read": 0, "cache_write": 0,
            "output": 0, "thinking": 0, "total": 0, "cost": 0.0, "duration_min": 0.0,
            "model": "", "summary": (raw or "").strip()[:1900] or f"no output (exit {exit_code})",
        }
    usage = data.get("usage") or {}
    inp = usage.get("input_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    cache_write = usage.get("cache_creation_input_tokens", 0)
    output = usage.get("output_tokens", 0)
    thinking = (usage.get("output_tokens_details") or {}).get("thinking_tokens", 0)
    failed = exit_code != 0 or data.get("is_error") or data.get("subtype") != "success"
    return {
        "status": "error" if failed else "ok",
        "turns": data.get("num_turns", 0),
        "input": inp,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "output": output,
        "thinking": thinking,
        "total": inp + cache_read + cache_write + output,
        "cost": round(data.get("total_cost_usd") or 0.0, 4),
        "duration_min": round((data.get("duration_ms") or 0) / 60000, 1),
        "model": ", ".join((data.get("modelUsage") or {}).keys()),
        "summary": str(data.get("result") or data.get("subtype") or "")[:1900],
    }


def format_line(stats: dict) -> str:
    return (
        f"[usage] {stats['status']} | total {stats['total']:,} tokens | turns {stats['turns']} | "
        f"in {stats['input']:,} cache-read {stats['cache_read']:,} cache-write {stats['cache_write']:,} "
        f"out {stats['output']:,} (thinking {stats['thinking']:,}) | ${stats['cost']:.2f} | "
        f"{stats['duration_min']} min | {stats['model']}"
    )


def build_properties(stats: dict, now: datetime) -> dict:
    def num(n):
        return {"number": n}

    return {
        "Run": nc.title_prop(now.strftime("%Y-%m-%d %H:%M %Z")),
        "Date": nc.date_prop(now.isoformat(timespec="seconds")),
        "Status": nc.select_prop(stats["status"]),
        "Total Tokens": num(stats["total"]),
        "Turns": num(stats["turns"]),
        "Input": num(stats["input"]),
        "Cache Read": num(stats["cache_read"]),
        "Cache Write": num(stats["cache_write"]),
        "Output": num(stats["output"]),
        "Thinking": num(stats["thinking"]),
        "Cost USD": num(stats["cost"]),
        "Duration (min)": num(stats["duration_min"]),
        "Model": nc.rich_text_prop(stats["model"]),
        "Summary": nc.rich_text_prop(stats["summary"]),
    }


def ensure_usage_db(env_path: Path, config) -> str:
    if config.notion_token_usage_db_id:
        return config.notion_token_usage_db_id
    from bootstrap_kb import ensure_database

    db_id = ensure_database(
        config.notion_token, config.notion_root_page_id, "Token Usage", TOKEN_USAGE_SCHEMA
    )
    update_env_file(env_path, {"NOTION_TOKEN_USAGE_DB_ID": db_id})
    return db_id


def main(argv: list | None = None, project_root: Path = PROJECT_ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_json", type=Path)
    parser.add_argument("--exit-code", type=int, default=0)
    args = parser.parse_args(argv)

    raw = args.result_json.read_text() if args.result_json.exists() else ""
    stats = parse_result(raw, args.exit_code)
    if stats["summary"] and stats["status"] == "ok":
        print(stats["summary"])
    print(format_line(stats))

    env_path = project_root / ".env"
    config = load_config(env_path)
    now = datetime.now(ZoneInfo(config.timezone or "UTC"))
    try:
        db_id = ensure_usage_db(env_path, config)
        nc.create_page(
            config.notion_token,
            {"type": "database_id", "database_id": db_id},
            build_properties(stats, now),
        )
    except Exception as exc:  # logging must never break the cycle
        print(f"[usage] failed to write Notion row: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
