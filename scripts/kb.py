"""Compact one-line-command CLI over the IkarAI Notion knowledge base.

Usage: python3 scripts/kb.py <group> <cmd> [args...]

Every command prints compact plain text (never raw JSON) — see RUNBOOK.md
for the workflow this replaces (ad-hoc `python3 -c` scripts + raw page
dumps). Run `python3 scripts/kb.py --help` (or `<group> --help`) for the
full command reference.
"""

import argparse
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import bootstrap_kb as bk
import notion_client as nc
import session_store
from config import load_config, update_env_file


class KbError(Exception):
    """Expected, user-facing failure — printed without a traceback."""


@dataclass
class Ctx:
    token: str
    config: object
    project_root: Path
    env_path: Path
    session_dir: Path
    now_utc: datetime
    now_local: datetime
    tz: ZoneInfo


# ---------------------------------------------------------------------------
# small shared helpers
# ---------------------------------------------------------------------------


def _default_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _short_id(notion_id: str) -> str:
    return nc.normalize_id(notion_id)


def _find_exact(rows: list, name: str):
    target = name.strip().lower()
    for row in rows:
        if nc.page_title(row).strip().lower() == target:
            return row
    return None


def _blocks_to_text(blocks: list) -> str:
    lines = []
    for b in blocks:
        btype = b.get("type")
        text = nc.block_text(b)
        if btype == "heading_1":
            lines.append(f"# {text}")
        elif btype == "heading_2":
            lines.append(f"## {text}")
        elif btype == "heading_3":
            lines.append(f"### {text}")
        elif btype == "bulleted_list_item":
            lines.append(f"- {text}")
        else:
            lines.append(text)
    return "\n".join(lines)


def _page_full_text(token: str, page_id: str) -> str:
    if not page_id:
        return "(not configured)"
    blocks = nc.get_block_children(token, page_id)
    text = _blocks_to_text(blocks)
    return text if text.strip() else "(empty)"


def _split_paragraphs(text: str) -> list:
    return [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def _markdown_to_blocks(text: str) -> list:
    """`## ` -> heading_2, `### ` -> heading_3, `- ` -> bulleted_list_item,
    blank-line-separated runs of plain lines -> paragraph blocks."""
    blocks = []
    for chunk in re.split(r"\n\s*\n", text.strip()):
        para_lines = []

        def _flush():
            if para_lines:
                blocks.append(nc.paragraph_block("\n".join(para_lines)))
                para_lines.clear()

        for raw_line in chunk.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("### "):
                _flush()
                blocks.append(nc.heading3_block(line[4:].strip()))
            elif line.startswith("## "):
                _flush()
                blocks.append(nc.heading2_block(line[3:].strip()))
            elif line.startswith("- "):
                _flush()
                blocks.append(nc.bulleted_list_item_block(line[2:].strip()))
            else:
                para_lines.append(line)
        _flush()
    return blocks


def _last_cycle_entry(token: str, daily_logs_db_id: str) -> str | None:
    if not daily_logs_db_id:
        return None
    rows = nc.query_database(
        token, daily_logs_db_id, sorts=[{"property": "Date", "direction": "descending"}]
    )
    if not rows:
        return None
    page = rows[0]
    title = nc.page_title(page)
    blocks = nc.get_block_children(token, page["id"])
    if not blocks:
        return f"{title}\n(no entries)"
    header = nc.block_text(blocks[0])
    lines = [title, header]
    idx = None
    for i, b in enumerate(blocks[1:], start=1):
        if b.get("type") == "heading_3":
            idx = i
            break
    if idx is None:
        lines.append("(no cycle entries yet)")
        return "\n".join(lines)
    lines.append(nc.block_text(blocks[idx]))
    for b in blocks[idx + 1 :]:
        if b.get("type") == "heading_3":
            break
        text = nc.block_text(b)
        if text:
            lines.append(text)
    return "\n".join(lines)


def _ensure_daily_log_page(token: str, db_id: str, title: str) -> tuple:
    rows = nc.query_database(
        token, db_id, filter_={"property": "Date", "title": {"equals": title}}
    )
    if rows:
        page_id = rows[0]["id"]
    else:
        header = nc.paragraph_block("⏰ Next scheduled run: (not yet decided)")
        page = nc.create_page(
            token, {"type": "database_id", "database_id": db_id}, {"Date": nc.title_prop(title)}, [header]
        )
        page_id = page["id"]
    blocks = nc.get_block_children(token, page_id)
    header_block_id = blocks[0]["id"] if blocks else None
    return page_id, header_block_id


def _print_http_error(e: requests.HTTPError) -> None:
    resp = e.response
    status = resp.status_code if resp is not None else "?"
    message = status
    if resp is not None:
        try:
            body = resp.json()
            message = body.get("message", body)
        except ValueError:
            message = resp.text
    print(f"Notion API error {status}: {message}", file=sys.stderr)


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------


LAST_CYCLE_CHAR_CAP = 2500
WORLD_KNOWLEDGE_HEADINGS_CAP = 40


def _capped_strategy(text: str) -> str:
    """The Strategy page used to be an append-only changelog (it reached
    ~120K chars). Never dump that into context: show only the newest tail
    and tell the agent to condense it — `strategy set` archives the rest."""
    if len(text) <= STRATEGY_CHAR_CAP:
        return text
    tail = text[-STRATEGY_CHAR_CAP:]
    return (
        f"!! STRATEGY PAGE IS {len(text):,} CHARS — over the {STRATEGY_CHAR_CAP} cap. "
        "Only its newest part is shown below. THIS CYCLE, condense it into the "
        "current plan (not a changelog) with `kb.py strategy set`; the full old "
        "page is archived automatically.\n… " + tail
    )


def _append_in_batches(token: str, parent_id: str, blocks: list, batch: int = 100) -> None:
    """Notion accepts at most 100 children per append request."""
    for i in range(0, len(blocks), batch):
        nc.append_blocks(token, parent_id, blocks[i : i + batch])


def cmd_context(ctx: Ctx, args) -> int:
    config = ctx.config
    lines = []
    lines.append(f"today: {ctx.now_local.strftime('%Y-%m-%d')}  time: {ctx.now_local.strftime('%H:%M %Z')}")

    lines.append("")
    lines.append("=== PROFILE ===")
    lines.append(_page_full_text(ctx.token, config.notion_profile_page_id))

    lines.append("")
    lines.append("=== STRATEGY ===")
    lines.append(_capped_strategy(_page_full_text(ctx.token, config.notion_strategy_page_id)))

    lines.append("")
    lines.append("=== HUMAN REQUIRED ===")
    human_rows = nc.query_database(ctx.token, config.notion_human_required_db_id) if config.notion_human_required_db_id else []
    if not human_rows:
        lines.append("(none open)")
    else:
        for row in human_rows:
            title = nc.page_title(row)
            created = nc.prop_text(row, "CreatedAt")
            blocks = nc.get_block_children(ctx.token, row["id"])
            if len(blocks) > 2:
                reply = " ".join(t for t in (nc.block_text(b) for b in blocks[2:]) if t)
                lines.append(f"{_short_id(row['id'])} | {title} | {created} | ANSWERED: {reply}")
            else:
                lines.append(f"{_short_id(row['id'])} | {title} | {created} | open")

    lines.append("")
    lines.append("=== LAST CYCLE ===")
    last = _last_cycle_entry(ctx.token, config.notion_daily_logs_db_id)
    if last and len(last) > LAST_CYCLE_CHAR_CAP:
        last = last[:LAST_CYCLE_CHAR_CAP] + " … (truncated; `kb.py log last` for all of it)"
    lines.append(last if last else "(no daily logs yet)")

    lines.append("")
    lines.append("=== ACTION CATALOG INDEX ===")
    catalog_rows = (
        nc.query_database(ctx.token, config.notion_action_catalog_db_id)
        if config.notion_action_catalog_db_id
        else []
    )
    by_kind: dict = {}
    for row in catalog_rows:
        kind = nc.prop_text(row, "Kind") or "?"
        by_kind.setdefault(kind, []).append(nc.page_title(row))
    if not by_kind:
        lines.append("(empty)")
    else:
        for kind in sorted(by_kind):
            lines.append(f"{kind}: {', '.join(sorted(by_kind[kind]))}")

    lines.append("")
    lines.append("=== DIPLOMACY ===")
    dip_rows = (
        nc.query_database(ctx.token, config.notion_diplomacy_db_id)
        if config.notion_diplomacy_db_id
        else []
    )
    if not dip_rows:
        lines.append("(none)")
    else:
        for row in dip_rows:
            lines.append(
                f"{nc.page_title(row)} | {nc.prop_text(row, 'Type')} | "
                f"{nc.prop_text(row, 'Relationship')} | {nc.prop_text(row, 'LastUpdated')}"
            )

    lines.append("")
    lines.append("=== WORLD KNOWLEDGE ===")
    wk_blocks = (
        nc.get_block_children(ctx.token, config.notion_world_knowledge_page_id)
        if config.notion_world_knowledge_page_id
        else []
    )
    headings = [nc.block_text(b) for b in wk_blocks if b.get("type") in ("heading_1", "heading_2", "heading_3")]
    if len(headings) > WORLD_KNOWLEDGE_HEADINGS_CAP:
        omitted = len(headings) - WORLD_KNOWLEDGE_HEADINGS_CAP
        headings = [f"(+{omitted} older)"] + headings[-WORLD_KNOWLEDGE_HEADINGS_CAP:]
    lines.append(", ".join(headings) if headings else "(empty)")
    lines.append("(use `kb.py knowledge search <regex>` for details)")

    print("\n".join(lines))
    return 0


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------


def cmd_catalog_list(ctx: Ctx, args) -> int:
    filter_ = {"property": "Kind", "select": {"equals": args.kind}} if args.kind else None
    rows = nc.query_database(ctx.token, ctx.config.notion_action_catalog_db_id, filter_=filter_)
    if not rows:
        print("(empty)")
        return 0
    for row in sorted(rows, key=lambda r: nc.page_title(r).lower()):
        print(
            f"{nc.page_title(row)} | {nc.prop_text(row, 'Kind')} | "
            f"{nc.prop_text(row, 'Method')} | {nc.prop_text(row, 'LastVerified')}"
        )
    return 0


NOTES_CHAR_CAP = 1500


def _print_catalog_row_full(row: dict, full: bool = False) -> None:
    print(f"--- {nc.page_title(row)} ---")
    for field in ("Kind", "Method", "Path", "Action", "Function", "Params", "LastVerified"):
        print(f"{field}: {nc.prop_text(row, field)}")
    notes = nc.prop_text(row, "Notes")
    if full or len(notes) <= NOTES_CHAR_CAP:
        print(f"Notes: {notes}")
    else:
        print(
            f"Notes ({len(notes):,} chars, over the {NOTES_CHAR_CAP} cap — newest part shown; "
            f"condense with `catalog upsert NAME --notes`, old notes are archived): "
            f"… {notes[-NOTES_CHAR_CAP:]}"
        )


def _archive_text(ctx: "Ctx", config_attr: str, env_key: str, page_title: str, heading: str, text: str) -> None:
    """Append `text` under a timestamped heading on an archive page nobody
    reads in-cycle, creating the page lazily and persisting its id."""
    archive_id = getattr(ctx.config, config_attr)
    if not archive_id:
        archive_id = bk.ensure_page(ctx.token, ctx.config.notion_root_page_id, page_title)
        update_env_file(ctx.env_path, {env_key: archive_id})
        setattr(ctx.config, config_attr, archive_id)
    chunks = [text[i : i + 1900] for i in range(0, len(text), 1900)]
    stamp = ctx.now_local.strftime("%Y-%m-%d %H:%M %Z")
    _append_in_batches(
        ctx.token,
        archive_id,
        [nc.heading3_block(f"{heading} — {stamp}")] + [nc.paragraph_block(c) for c in chunks],
    )


def cmd_catalog_show(ctx: Ctx, args) -> int:
    rows = nc.query_database(ctx.token, ctx.config.notion_action_catalog_db_id)
    for name in args.names:
        match = _find_exact(rows, name)
        if match:
            _print_catalog_row_full(match, full=args.full)
            continue
        needle = name.strip().lower()
        substrings = [r for r in rows if needle in nc.page_title(r).strip().lower()][:5]
        if substrings:
            print(f"No exact match for '{name}'. Close matches:")
            for r in substrings:
                print(f"  {nc.page_title(r)}")
        else:
            print(f"No match for '{name}'")
    return 0


def _check_notes_cap(notes: str, flag: str) -> None:
    if len(notes) > NOTES_CHAR_CAP:
        raise KbError(
            f"{flag} would make Notes {len(notes)} chars, over the {NOTES_CHAR_CAP} cap — "
            "rewrite them condensed (what works, required params, gotchas) with --notes; "
            "the old notes are archived automatically."
        )


def cmd_catalog_upsert(ctx: Ctx, args) -> int:
    db_id = ctx.config.notion_action_catalog_db_id
    rows = nc.query_database(ctx.token, db_id)
    match = _find_exact(rows, args.name)
    today = ctx.now_local.strftime("%Y-%m-%d")

    if match:
        properties = {"LastVerified": nc.date_prop(today)}
        if args.kind:
            properties["Kind"] = nc.select_prop(args.kind)
        if args.method:
            properties["Method"] = nc.select_prop(args.method)
        if args.path is not None:
            properties["Path"] = nc.rich_text_prop(args.path)
        if args.action is not None:
            properties["Action"] = nc.rich_text_prop(args.action)
        if args.function is not None:
            properties["Function"] = nc.rich_text_prop(args.function)
        if args.params is not None:
            properties["Params"] = nc.rich_text_prop(args.params)
        current = nc.prop_text(match, "Notes")
        if args.notes is not None:
            _check_notes_cap(args.notes, "--notes")
            if current.strip() and current != args.notes:
                _archive_text(
                    ctx, "notion_catalog_archive_page_id", "NOTION_CATALOG_ARCHIVE_PAGE_ID",
                    "Catalog Notes Archive", args.name, current,
                )
            properties["Notes"] = nc.rich_text_prop(args.notes)
        elif args.append_notes is not None:
            new_notes = (current + "\n" if current else "") + f"[{today}] {args.append_notes}"
            _check_notes_cap(new_notes, "--append-notes")
            properties["Notes"] = nc.rich_text_prop(new_notes)
        nc.update_page(ctx.token, match["id"], properties)
        kind_label = args.kind or nc.prop_text(match, "Kind")
        print(f"OK catalog updated: {kind_label}:{args.name}")
        return 0

    if not args.kind or not args.method:
        raise KbError(f"no existing row named '{args.name}' — creating one requires --kind and --method")
    _check_notes_cap(args.notes or "", "--notes")
    properties = {
        "Name": nc.title_prop(args.name),
        "Kind": nc.select_prop(args.kind),
        "Method": nc.select_prop(args.method),
        "Path": nc.rich_text_prop(args.path or ""),
        "Action": nc.rich_text_prop(args.action or ""),
        "Function": nc.rich_text_prop(args.function or ""),
        "Params": nc.rich_text_prop(args.params or ""),
        "LastVerified": nc.date_prop(today),
        "Notes": nc.rich_text_prop(args.notes or ""),
    }
    nc.create_page(ctx.token, {"type": "database_id", "database_id": db_id}, properties)
    print(f"OK catalog created: {args.kind}:{args.name}")
    return 0


# ---------------------------------------------------------------------------
# strategy
# ---------------------------------------------------------------------------

STRATEGY_CHAR_CAP = 3500


def cmd_strategy_show(ctx: Ctx, args) -> int:
    print(_page_full_text(ctx.token, ctx.config.notion_strategy_page_id))
    return 0


def cmd_strategy_set(ctx: Ctx, args) -> int:
    if args.file and args.text:
        raise KbError("pass either TEXT or --file, not both")
    if args.file:
        text = Path(args.file).read_text()
    elif args.text == "-":
        text = sys.stdin.read()
    elif args.text:
        text = args.text
    else:
        raise KbError("strategy set requires TEXT, --file PATH, or '-' for stdin")

    text = text.strip()
    if not text:
        raise KbError("strategy text is empty")
    if len(text) > STRATEGY_CHAR_CAP:
        raise KbError(
            f"strategy text is {len(text)} chars, over the {STRATEGY_CHAR_CAP} cap — "
            "condense it and move detail to World Knowledge."
        )

    strategy_id = ctx.config.notion_strategy_page_id
    old_blocks = nc.get_block_children(ctx.token, strategy_id)
    old_text = _blocks_to_text(old_blocks)

    archive_id = ctx.config.notion_strategy_archive_page_id
    if not archive_id:
        archive_id = bk.ensure_page(ctx.token, ctx.config.notion_root_page_id, "Strategy Archive")
        update_env_file(ctx.env_path, {"NOTION_STRATEGY_ARCHIVE_PAGE_ID": archive_id})
        ctx.config.notion_strategy_archive_page_id = archive_id

    if old_text.strip():
        timestamp = ctx.now_local.strftime("%Y-%m-%d %H:%M %Z")
        chunks = [old_text[i : i + 1900] for i in range(0, len(old_text), 1900)]
        _append_in_batches(
            ctx.token,
            archive_id,
            [nc.heading3_block(timestamp)] + [nc.paragraph_block(c) for c in chunks],
        )

    for block in old_blocks:
        nc.delete_block(ctx.token, block["id"])

    new_blocks = _markdown_to_blocks(text)
    _append_in_batches(ctx.token, strategy_id, new_blocks)
    print(f"OK strategy updated: {len(text)} chars, old version archived")
    return 0


# ---------------------------------------------------------------------------
# knowledge
# ---------------------------------------------------------------------------


def cmd_knowledge_headings(ctx: Ctx, args) -> int:
    blocks = nc.get_block_children(ctx.token, ctx.config.notion_world_knowledge_page_id)
    prefixes = {"heading_1": "#", "heading_2": "##", "heading_3": "###"}
    found = False
    for b in blocks:
        prefix = prefixes.get(b.get("type"))
        if prefix:
            found = True
            print(f"{prefix} {nc.block_text(b)}")
    if not found:
        print("(empty)")
    return 0


def cmd_knowledge_search(ctx: Ctx, args) -> int:
    blocks = nc.get_block_children(ctx.token, ctx.config.notion_world_knowledge_page_id)
    try:
        regex = re.compile(args.regex, re.IGNORECASE)
    except re.error as e:
        raise KbError(f"invalid regex '{args.regex}': {e}")
    current_heading = "(no heading)"
    out_lines = []
    for b in blocks:
        btype = b.get("type")
        text = nc.block_text(b)
        if btype in ("heading_1", "heading_2", "heading_3"):
            current_heading = text
            continue
        if text and regex.search(text):
            out_lines.append(f"[{current_heading}] {text}")
    result = "\n".join(out_lines) if out_lines else "(no matches)"
    if len(result) > 3000:
        result = result[:3000] + "\n... (truncated)"
    print(result)
    return 0


def cmd_knowledge_add(ctx: Ctx, args) -> int:
    page_id = ctx.config.notion_world_knowledge_page_id
    blocks = nc.get_block_children(ctx.token, page_id)
    para_blocks = [nc.paragraph_block(p) for p in _split_paragraphs(args.text)]

    heading_idx = None
    for i, b in enumerate(blocks):
        if b.get("type") in ("heading_1", "heading_2", "heading_3") and nc.block_text(b).strip().lower() == args.heading.strip().lower():
            heading_idx = i
            break

    if heading_idx is None:
        children = [nc.heading2_block(args.heading)] + para_blocks
        nc.append_blocks(ctx.token, page_id, children)
        print(f"OK knowledge added: new heading '{args.heading}'")
        return 0

    end_idx = len(blocks) - 1
    for j in range(heading_idx + 1, len(blocks)):
        if blocks[j].get("type") in ("heading_1", "heading_2", "heading_3"):
            end_idx = j - 1
            break
    anchor_id = blocks[end_idx]["id"]
    nc.append_blocks(ctx.token, page_id, para_blocks, after=anchor_id)
    print(f"OK knowledge added under '{args.heading}'")
    return 0


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------


def cmd_log_last(ctx: Ctx, args) -> int:
    text = _last_cycle_entry(ctx.token, ctx.config.notion_daily_logs_db_id)
    print(text if text else "(no daily logs yet)")
    return 0


def cmd_log_add(ctx: Ctx, args) -> int:
    text = sys.stdin.read() if args.text == "-" else args.text
    text = text.strip()
    if not text:
        raise KbError("log entry text is empty")

    title = ctx.now_local.strftime("%Y-%m-%d")
    page_id, header_block_id = _ensure_daily_log_page(ctx.token, ctx.config.notion_daily_logs_db_id, title)
    time_str = ctx.now_local.strftime("%H:%M %Z")
    children = [nc.heading3_block(time_str)] + [nc.paragraph_block(p) for p in _split_paragraphs(text)]
    nc.append_blocks(ctx.token, page_id, children, after=header_block_id)
    print(f"OK log added: {title} {time_str}")
    return 0


# ---------------------------------------------------------------------------
# wake
# ---------------------------------------------------------------------------


def cmd_wake(ctx: Ctx, args) -> int:
    value = args.value
    if value.startswith("+"):
        try:
            minutes = int(value[1:])
        except ValueError:
            raise KbError(f"invalid wake offset '{value}' — expected e.g. +30")
        wake_utc = ctx.now_utc + timedelta(minutes=minutes)
    else:
        try:
            wake_utc = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            raise KbError(f"invalid ISO UTC timestamp '{value}' — expected e.g. 2026-09-25T14:30:00Z")

    iso = wake_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    session_store.write_next_wake(ctx.session_dir, iso)

    wake_local = wake_utc.astimezone(ctx.tz)
    local_str = wake_local.strftime("%H:%M %Z")
    utc_str = wake_utc.strftime("%H:%M")
    header_text = f"⏰ Next scheduled run: {local_str} ({utc_str} UTC)"

    title = ctx.now_local.strftime("%Y-%m-%d")
    _, header_block_id = _ensure_daily_log_page(ctx.token, ctx.config.notion_daily_logs_db_id, title)
    if header_block_id:
        nc.update_block(ctx.token, header_block_id, {"paragraph": nc.paragraph_block(header_text)["paragraph"]})

    print(f"OK wake set: {iso} ({local_str} local / {utc_str} UTC)")
    return 0


# ---------------------------------------------------------------------------
# human
# ---------------------------------------------------------------------------


def cmd_human_list(ctx: Ctx, args) -> int:
    rows = nc.query_database(ctx.token, ctx.config.notion_human_required_db_id)
    if not rows:
        print("(no human-required rows)")
        return 0
    for row in rows:
        title = nc.page_title(row)
        created = nc.prop_text(row, "CreatedAt")
        blocks = nc.get_block_children(ctx.token, row["id"])
        if len(blocks) > 2:
            reply = " ".join(t for t in (nc.block_text(b) for b in blocks[2:]) if t)
            print(f"{_short_id(row['id'])} | {title} | {created} | ANSWERED: {reply}")
        else:
            print(f"{_short_id(row['id'])} | {title} | {created} | open")
    return 0


def cmd_human_create(ctx: Ctx, args) -> int:
    db_id = ctx.config.notion_human_required_db_id
    rows = nc.query_database(ctx.token, db_id)
    for row in rows:
        if nc.page_title(row) == args.title:
            print(f"EXISTS {_short_id(row['id'])}")
            return 0

    today = ctx.now_local.strftime("%Y-%m-%d")
    properties = {"Name": nc.title_prop(args.title), "CreatedAt": nc.date_prop(today)}
    children = [nc.heading2_block("Question"), nc.paragraph_block(args.question)]
    page = nc.create_page(ctx.token, {"type": "database_id", "database_id": db_id}, properties, children)
    print(f"OK human created: {_short_id(page['id'])}")
    return 0


def cmd_human_resolve(ctx: Ctx, args) -> int:
    nc.archive_page(ctx.token, args.page_id)
    print(f"OK human resolved: {args.page_id}")
    return 0


# ---------------------------------------------------------------------------
# diplomacy
# ---------------------------------------------------------------------------


def cmd_diplomacy_list(ctx: Ctx, args) -> int:
    rows = nc.query_database(ctx.token, ctx.config.notion_diplomacy_db_id)
    if not rows:
        print("(no diplomacy rows)")
        return 0
    for row in rows:
        history = nc.prop_text(row, "History")
        if len(history) > 120:
            history = history[:120] + "..."
        print(
            f"{nc.page_title(row)} | {nc.prop_text(row, 'Type')} | "
            f"{nc.prop_text(row, 'Relationship')} | {nc.prop_text(row, 'LastUpdated')} | {history}"
        )
    return 0


def cmd_diplomacy_upsert(ctx: Ctx, args) -> int:
    db_id = ctx.config.notion_diplomacy_db_id
    rows = nc.query_database(ctx.token, db_id)
    match = _find_exact(rows, args.name)
    today = ctx.now_local.strftime("%Y-%m-%d")

    properties = {"LastUpdated": nc.date_prop(today)}
    if args.type:
        properties["Type"] = nc.select_prop(args.type)
    if args.relationship:
        properties["Relationship"] = nc.select_prop(args.relationship)
    if args.history_append:
        current = nc.prop_text(match, "History") if match else ""
        new_history = (current + "\n" if current else "") + f"[{today}] {args.history_append}"
        properties["History"] = nc.rich_text_prop(new_history)

    if match:
        nc.update_page(ctx.token, match["id"], properties)
        print(f"OK diplomacy updated: {args.name}")
        return 0

    properties["Name"] = nc.title_prop(args.name)
    properties.setdefault("Type", nc.select_prop(args.type or "player"))
    properties.setdefault("Relationship", nc.select_prop(args.relationship or "unknown"))
    properties.setdefault("History", nc.rich_text_prop(""))
    nc.create_page(ctx.token, {"type": "database_id", "database_id": db_id}, properties)
    print(f"OK diplomacy created: {args.name}")
    return 0


# ---------------------------------------------------------------------------
# argument parsing / entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kb.py", description=__doc__)
    parser.add_argument("--root", default=None, help="project root override (also IKARAI_ROOT env var)")
    sub = parser.add_subparsers(dest="group", required=True)

    p_context = sub.add_parser("context", help="the one call at cycle start")
    p_context.set_defaults(func=cmd_context)

    p_catalog = sub.add_parser("catalog")
    catalog_sub = p_catalog.add_subparsers(dest="cmd", required=True)

    p_list = catalog_sub.add_parser("list")
    p_list.add_argument("--kind", choices=["nav", "action"])
    p_list.set_defaults(func=cmd_catalog_list)

    p_show = catalog_sub.add_parser("show")
    p_show.add_argument("names", nargs="+")
    p_show.add_argument("--full", action="store_true", help="print Notes even past the cap")
    p_show.set_defaults(func=cmd_catalog_show)

    p_upsert = catalog_sub.add_parser("upsert")
    p_upsert.add_argument("name")
    p_upsert.add_argument("--kind", choices=["nav", "action"])
    p_upsert.add_argument("--method", choices=["GET", "POST"])
    p_upsert.add_argument("--path")
    p_upsert.add_argument("--action")
    p_upsert.add_argument("--function")
    p_upsert.add_argument("--params")
    notes_group = p_upsert.add_mutually_exclusive_group()
    notes_group.add_argument("--notes")
    notes_group.add_argument("--append-notes")
    p_upsert.set_defaults(func=cmd_catalog_upsert)

    p_strategy = sub.add_parser("strategy")
    strategy_sub = p_strategy.add_subparsers(dest="cmd", required=True)
    strategy_sub.add_parser("show").set_defaults(func=cmd_strategy_show)
    p_strat_set = strategy_sub.add_parser("set")
    p_strat_set.add_argument("text", nargs="?")
    p_strat_set.add_argument("--file")
    p_strat_set.set_defaults(func=cmd_strategy_set)

    p_knowledge = sub.add_parser("knowledge")
    knowledge_sub = p_knowledge.add_subparsers(dest="cmd", required=True)
    knowledge_sub.add_parser("headings").set_defaults(func=cmd_knowledge_headings)
    p_k_search = knowledge_sub.add_parser("search")
    p_k_search.add_argument("regex")
    p_k_search.set_defaults(func=cmd_knowledge_search)
    p_k_add = knowledge_sub.add_parser("add")
    p_k_add.add_argument("--heading", required=True)
    p_k_add.add_argument("text")
    p_k_add.set_defaults(func=cmd_knowledge_add)

    p_log = sub.add_parser("log")
    log_sub = p_log.add_subparsers(dest="cmd", required=True)
    log_sub.add_parser("last").set_defaults(func=cmd_log_last)
    p_log_add = log_sub.add_parser("add")
    p_log_add.add_argument("text")
    p_log_add.set_defaults(func=cmd_log_add)

    p_wake = sub.add_parser("wake")
    p_wake.add_argument("value", help="ISO UTC timestamp (2026-09-25T14:30:00Z) or +MINUTES")
    p_wake.set_defaults(func=cmd_wake)

    p_human = sub.add_parser("human")
    human_sub = p_human.add_subparsers(dest="cmd", required=True)
    human_sub.add_parser("list").set_defaults(func=cmd_human_list)
    p_h_create = human_sub.add_parser("create")
    p_h_create.add_argument("title")
    p_h_create.add_argument("question")
    p_h_create.set_defaults(func=cmd_human_create)
    p_h_resolve = human_sub.add_parser("resolve")
    p_h_resolve.add_argument("page_id")
    p_h_resolve.set_defaults(func=cmd_human_resolve)

    p_diplomacy = sub.add_parser("diplomacy")
    diplomacy_sub = p_diplomacy.add_subparsers(dest="cmd", required=True)
    diplomacy_sub.add_parser("list").set_defaults(func=cmd_diplomacy_list)
    p_d_upsert = diplomacy_sub.add_parser("upsert")
    p_d_upsert.add_argument("name")
    p_d_upsert.add_argument("--type", choices=["player", "clan"])
    p_d_upsert.add_argument("--relationship", choices=["ally", "enemy", "neutral", "unknown"])
    p_d_upsert.add_argument("--history-append")
    p_d_upsert.set_defaults(func=cmd_diplomacy_upsert)

    return parser


def main(argv: list | None = None, project_root=None, now: datetime | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = _build_parser()
    args = parser.parse_args(argv)

    if project_root is None:
        if args.root:
            project_root = Path(args.root)
        elif os.environ.get("IKARAI_ROOT"):
            project_root = Path(os.environ["IKARAI_ROOT"])
        else:
            project_root = _default_project_root()
    project_root = Path(project_root)
    env_path = project_root / ".env"
    session_dir = project_root / "session"

    try:
        config = load_config(env_path)
    except (KeyError, FileNotFoundError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return 1

    tz = ZoneInfo(config.timezone)
    now_utc = now if now is not None else datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_local = now_utc.astimezone(tz)

    ctx = Ctx(
        token=config.notion_token,
        config=config,
        project_root=project_root,
        env_path=env_path,
        session_dir=session_dir,
        now_utc=now_utc,
        now_local=now_local,
        tz=tz,
    )

    try:
        return args.func(ctx, args)
    except requests.HTTPError as e:
        _print_http_error(e)
        return 1
    except KbError as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
