from datetime import datetime, timezone
from unittest.mock import patch

import kb
import notion_client as nc


# ---------------------------------------------------------------------------
# fixtures / builders
# ---------------------------------------------------------------------------


def _write_env(tmp_path, **overrides):
    values = {
        "IKARIAM_USERNAME": "u@example.com",
        "IKARIAM_SERVER": "s1.example.com",
        "NOTION_TOKEN": "ntn_test",
        "NOTION_ROOT_PAGE_ID": "root-page-id",
        "NOTION_ACTION_CATALOG_DB_ID": "catalog-db",
        "NOTION_DAILY_LOGS_DB_ID": "logs-db",
        "NOTION_DIPLOMACY_DB_ID": "diplomacy-db",
        "NOTION_PROFILE_PAGE_ID": "profile-page",
        "NOTION_STRATEGY_PAGE_ID": "strategy-page",
        "NOTION_WORLD_KNOWLEDGE_PAGE_ID": "world-page",
        "NOTION_HUMAN_REQUIRED_DB_ID": "human-db",
        "NOTION_STRATEGY_ARCHIVE_PAGE_ID": "archive-page",
        "TIMEZONE": "Asia/Jerusalem",
    }
    values.update(overrides)
    env_path = tmp_path / ".env"
    env_path.write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
    (tmp_path / "session").mkdir(exist_ok=True)
    return env_path


def _title_prop_val(text):
    return {"type": "title", "title": [{"plain_text": text, "text": {"content": text}}]}


def _rich_text_prop_val(text):
    return {"type": "rich_text", "rich_text": [{"plain_text": text, "text": {"content": text}}]}


def _select_prop_val(name):
    return {"type": "select", "select": {"name": name} if name else None}


def _date_prop_val(value):
    return {"type": "date", "date": {"start": value} if value else None}


def _page(id_, properties):
    return {"object": "page", "id": id_, "properties": properties}


def _block(id_, type_, text):
    return {"object": "block", "id": id_, "type": type_, type_: {"rich_text": [{"plain_text": text}]}}


def _catalog_row(id_, name, kind, method, path="", action="", function="", params="", last_verified="2026-09-01", notes=""):
    return _page(
        id_,
        {
            "Name": _title_prop_val(name),
            "Kind": _select_prop_val(kind),
            "Method": _select_prop_val(method),
            "Path": _rich_text_prop_val(path),
            "Action": _rich_text_prop_val(action),
            "Function": _rich_text_prop_val(function),
            "Params": _rich_text_prop_val(params),
            "LastVerified": _date_prop_val(last_verified),
            "Notes": _rich_text_prop_val(notes),
        },
    )


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------


@patch("kb.nc.get_block_children")
@patch("kb.nc.query_database")
def test_context_output_sections_and_catalog_notes_excluded(mock_query, mock_children, tmp_path, capsys):
    _write_env(tmp_path)

    def query_side_effect(token, db_id, filter_=None, sorts=None):
        if db_id == "human-db":
            return [_page("h1", {"Name": _title_prop_val("Session expired"), "CreatedAt": _date_prop_val("2026-09-20")})]
        if db_id == "catalog-db":
            return [
                _catalog_row("c1", "BuildUnits", "action", "POST", notes="super secret internal note"),
                _catalog_row("c2", "ViewTownHall", "nav", "GET"),
            ]
        if db_id == "diplomacy-db":
            return [
                _page(
                    "d1",
                    {
                        "Name": _title_prop_val("Rival1"),
                        "Type": _select_prop_val("player"),
                        "Relationship": _select_prop_val("enemy"),
                        "LastUpdated": _date_prop_val("2026-09-21"),
                    },
                )
            ]
        if db_id == "logs-db":
            return [_page("log1", {"Date": _title_prop_val("2026-09-24")})]
        return []

    mock_query.side_effect = query_side_effect

    def children_side_effect(token, block_id):
        if block_id == "profile-page":
            return [_block("b1", "paragraph", "Ruler bio text")]
        if block_id == "strategy-page":
            return [_block("b2", "heading_2", "Current State"), _block("b3", "paragraph", "One city.")]
        if block_id == "h1":
            return [_block("hb1", "heading_2", "Question"), _block("hb2", "paragraph", "Full context")]
        if block_id == "log1":
            return [
                _block("hdr", "paragraph", "Next run: soon"),
                _block("h3-1", "heading_3", "19:00 IDT"),
                _block("p1", "paragraph", "Did stuff."),
            ]
        if block_id == "world-page":
            return [_block("w1", "heading_2", "Buildings"), _block("w2", "paragraph", "Town hall info.")]
        return []

    mock_children.side_effect = children_side_effect

    now = datetime(2026, 9, 25, 16, 30, tzinfo=timezone.utc)
    rc = kb.main(["context"], project_root=tmp_path, now=now)
    out = capsys.readouterr().out

    assert rc == 0
    assert "today: 2026-09-25" in out
    assert "=== PROFILE ===" in out and "Ruler bio text" in out
    assert "=== STRATEGY ===" in out and "## Current State" in out
    assert "=== HUMAN REQUIRED ===" in out and "Session expired" in out
    assert "=== LAST CYCLE ===" in out and "19:00 IDT" in out and "Did stuff." in out
    assert "=== ACTION CATALOG INDEX ===" in out
    assert "action: BuildUnits" in out
    assert "nav: ViewTownHall" in out
    assert "super secret internal note" not in out
    assert "=== DIPLOMACY ===" in out and "Rival1 | player | enemy | 2026-09-21" in out
    assert "=== WORLD KNOWLEDGE ===" in out and "Buildings" in out
    assert "knowledge search" in out


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------


@patch("kb.nc.query_database")
def test_catalog_show_exact_match_prints_full_row(mock_query, tmp_path, capsys):
    _write_env(tmp_path)
    mock_query.return_value = [_catalog_row("c1", "BuildUnits", "action", "POST", path="/a", notes="n1")]
    rc = kb.main(["catalog", "show", "buildunits"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "--- BuildUnits ---" in out
    assert "Notes: n1" in out
    assert "Path: /a" in out


@patch("kb.nc.query_database")
def test_catalog_show_fuzzy_fallback_lists_up_to_5_substring_matches(mock_query, tmp_path, capsys):
    _write_env(tmp_path)
    rows = [_catalog_row(f"c{i}", f"BuildThing{i}", "action", "POST") for i in range(7)]
    mock_query.return_value = rows
    rc = kb.main(["catalog", "show", "buildthing"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "No exact match for 'buildthing'" in out
    assert out.count("BuildThing") == 5


@patch("kb.nc.query_database")
def test_catalog_show_no_match_at_all(mock_query, tmp_path, capsys):
    _write_env(tmp_path)
    mock_query.return_value = [_catalog_row("c1", "SomethingElse", "action", "POST")]
    rc = kb.main(["catalog", "show", "zzz"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "No match for 'zzz'" in out


@patch("kb.nc.update_page")
@patch("kb.nc.query_database")
def test_catalog_upsert_updates_existing_row_and_stamps_last_verified(mock_query, mock_update, tmp_path, capsys):
    _write_env(tmp_path)
    mock_query.return_value = [_catalog_row("c1", "BuildUnits", "action", "POST", notes="old notes")]
    now = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)
    rc = kb.main(["catalog", "upsert", "BuildUnits", "--append-notes", "works with cityId param"], project_root=tmp_path, now=now)
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK catalog updated: action:BuildUnits" in out
    args, kwargs = mock_update.call_args
    assert args[1] == "c1"
    props = args[2]
    assert props["LastVerified"] == nc.date_prop("2026-09-25")
    assert "old notes" in props["Notes"]["rich_text"][0]["text"]["content"]
    assert "works with cityId param" in props["Notes"]["rich_text"][0]["text"]["content"]


@patch("kb.nc.create_page")
@patch("kb.nc.query_database")
def test_catalog_upsert_creates_new_row_requires_kind_and_method(mock_query, mock_create, tmp_path, capsys):
    _write_env(tmp_path)
    mock_query.return_value = []
    rc = kb.main(["catalog", "upsert", "NewAction"], project_root=tmp_path)
    err = capsys.readouterr().err
    assert rc == 1
    assert "--kind" in err and "--method" in err
    mock_create.assert_not_called()


@patch("kb.nc.create_page")
@patch("kb.nc.query_database")
def test_catalog_upsert_creates_new_row_when_kind_and_method_given(mock_query, mock_create, tmp_path, capsys):
    _write_env(tmp_path)
    mock_query.return_value = []
    rc = kb.main(
        ["catalog", "upsert", "NewAction", "--kind", "action", "--method", "POST", "--path", "/x"],
        project_root=tmp_path,
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK catalog created: action:NewAction" in out
    mock_create.assert_called_once()


# ---------------------------------------------------------------------------
# strategy
# ---------------------------------------------------------------------------


@patch("kb.nc.get_block_children")
def test_strategy_set_rejects_text_over_cap(mock_children, tmp_path, capsys):
    _write_env(tmp_path)
    rc = kb.main(["strategy", "set", "x" * 3501], project_root=tmp_path)
    err = capsys.readouterr().err
    assert rc == 1
    assert "3501" in err
    assert "3500" in err
    mock_children.assert_not_called()


@patch("kb.nc.append_blocks")
@patch("kb.nc.delete_block")
@patch("kb.nc.get_block_children")
def test_strategy_set_archives_old_content_before_deleting_and_replacing(mock_children, mock_delete, mock_append, tmp_path, capsys):
    _write_env(tmp_path)
    mock_children.return_value = [_block("s1", "heading_2", "Old Heading"), _block("s2", "paragraph", "Old text.")]

    call_order = []

    def append_side_effect(token, block_id, children, after=None):
        call_order.append(("append", block_id))
        return {}

    def delete_side_effect(token, block_id):
        call_order.append(("delete", block_id))
        return {}

    mock_append.side_effect = append_side_effect
    mock_delete.side_effect = delete_side_effect

    now = datetime(2026, 9, 25, 16, 0, tzinfo=timezone.utc)
    rc = kb.main(["strategy", "set", "## New Heading\n\nNew text."], project_root=tmp_path, now=now)
    out = capsys.readouterr().out

    assert rc == 0
    assert "OK strategy updated" in out
    assert call_order == [
        ("append", "archive-page"),
        ("delete", "s1"),
        ("delete", "s2"),
        ("append", "strategy-page"),
    ]


@patch("kb.update_env_file")
@patch("kb.bk.ensure_page")
@patch("kb.nc.append_blocks")
@patch("kb.nc.delete_block")
@patch("kb.nc.get_block_children")
def test_strategy_set_creates_archive_page_lazily_when_missing(
    mock_children, mock_delete, mock_append, mock_ensure_page, mock_update_env, tmp_path
):
    _write_env(tmp_path, NOTION_STRATEGY_ARCHIVE_PAGE_ID="")
    mock_children.return_value = [_block("s1", "paragraph", "Old.")]
    mock_ensure_page.return_value = "new-archive-id"

    rc = kb.main(["strategy", "set", "New content."], project_root=tmp_path)
    assert rc == 0
    mock_ensure_page.assert_called_once_with("ntn_test", "root-page-id", "Strategy Archive")
    mock_update_env.assert_called_once()
    args, kwargs = mock_update_env.call_args
    assert args[1] == {"NOTION_STRATEGY_ARCHIVE_PAGE_ID": "new-archive-id"}
    # archived under the newly created page
    first_append_target = mock_append.call_args_list[0][0][1]
    assert first_append_target == "new-archive-id"


def test_markdown_to_blocks_converts_headings_bullets_and_paragraphs():
    text = (
        "## Heading Two\n\n"
        "Some paragraph text\nsecond line.\n\n"
        "- item one\n- item two\n\n"
        "### Sub heading\n\n"
        "Final para."
    )
    blocks = kb._markdown_to_blocks(text)
    types = [b["type"] for b in blocks]
    assert types == [
        "heading_2",
        "paragraph",
        "bulleted_list_item",
        "bulleted_list_item",
        "heading_3",
        "paragraph",
    ]
    assert blocks[0]["heading_2"]["rich_text"][0]["text"]["content"] == "Heading Two"
    assert blocks[1]["paragraph"]["rich_text"][0]["text"]["content"] == "Some paragraph text\nsecond line."
    assert blocks[2]["bulleted_list_item"]["rich_text"][0]["text"]["content"] == "item one"
    assert blocks[3]["bulleted_list_item"]["rich_text"][0]["text"]["content"] == "item two"
    assert blocks[4]["heading_3"]["rich_text"][0]["text"]["content"] == "Sub heading"
    assert blocks[5]["paragraph"]["rich_text"][0]["text"]["content"] == "Final para."


# ---------------------------------------------------------------------------
# knowledge
# ---------------------------------------------------------------------------


@patch("kb.nc.get_block_children")
def test_knowledge_headings_lists_all_levels(mock_children, tmp_path, capsys):
    _write_env(tmp_path)
    mock_children.return_value = [
        _block("h1", "heading_1", "Top"),
        _block("h2", "heading_2", "Mid"),
        _block("p1", "paragraph", "text"),
        _block("h3", "heading_3", "Low"),
    ]
    rc = kb.main(["knowledge", "headings"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "# Top" in out
    assert "## Mid" in out
    assert "### Low" in out


@patch("kb.nc.get_block_children")
def test_knowledge_search_attributes_matches_to_heading_section(mock_children, tmp_path, capsys):
    _write_env(tmp_path)
    mock_children.return_value = [
        _block("h1", "heading_2", "Buildings"),
        _block("p1", "paragraph", "Warehouse increases storage capacity."),
        _block("h2", "heading_2", "Research"),
        _block("p2", "paragraph", "Carpentry reduces building costs, storage unaffected."),
    ]
    rc = kb.main(["knowledge", "search", "storage"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "[Buildings] Warehouse increases storage capacity." in out
    assert "[Research] Carpentry reduces building costs, storage unaffected." in out


@patch("kb.nc.append_blocks")
@patch("kb.nc.get_block_children")
def test_knowledge_add_appends_after_existing_section(mock_children, mock_append, tmp_path, capsys):
    _write_env(tmp_path)
    mock_children.return_value = [
        _block("h1", "heading_2", "Buildings"),
        _block("p1", "paragraph", "Warehouse info."),
        _block("h2", "heading_2", "Research"),
        _block("p2", "paragraph", "Research info."),
    ]
    rc = kb.main(["knowledge", "add", "--heading", "Buildings", "New fact about warehouses."], project_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK knowledge added under 'Buildings'" in out
    args, kwargs = mock_append.call_args
    assert kwargs["after"] == "p1"
    assert args[2][0]["paragraph"]["rich_text"][0]["text"]["content"] == "New fact about warehouses."


@patch("kb.nc.append_blocks")
@patch("kb.nc.get_block_children")
def test_knowledge_add_creates_new_heading_when_not_found(mock_children, mock_append, tmp_path, capsys):
    _write_env(tmp_path)
    mock_children.return_value = [_block("h1", "heading_2", "Buildings")]
    rc = kb.main(["knowledge", "add", "--heading", "Governance", "New fact."], project_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK knowledge added: new heading 'Governance'" in out
    args, kwargs = mock_append.call_args
    assert "after" not in kwargs or kwargs.get("after") is None
    assert args[2][0]["type"] == "heading_2"
    assert args[2][0]["heading_2"]["rich_text"][0]["text"]["content"] == "Governance"


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------


@patch("kb.nc.append_blocks")
@patch("kb.nc.get_block_children")
@patch("kb.nc.create_page")
@patch("kb.nc.query_database")
def test_log_add_creates_page_when_missing_and_inserts_after_header(
    mock_query, mock_create, mock_children, mock_append, tmp_path, capsys
):
    _write_env(tmp_path)
    mock_query.return_value = []
    mock_create.return_value = {"id": "new-log-page"}
    mock_children.return_value = [_block("hdr-block", "paragraph", "⏰ Next scheduled run: (not yet decided)")]

    now = datetime(2026, 9, 25, 16, 7, tzinfo=timezone.utc)  # 19:07 IDT
    rc = kb.main(["log", "add", "Did some stuff.\n\nSecond paragraph."], project_root=tmp_path, now=now)
    out = capsys.readouterr().out
    assert rc == 0

    create_args, _ = mock_create.call_args
    assert create_args[1] == {"type": "database_id", "database_id": "logs-db"}
    assert create_args[2] == {"Date": nc.title_prop("2026-09-25")}
    header_children = create_args[3]
    assert (
        header_children[0]["paragraph"]["rich_text"][0]["text"]["content"]
        == "⏰ Next scheduled run: (not yet decided)"
    )

    append_args, append_kwargs = mock_append.call_args
    assert append_args[1] == "new-log-page"
    assert append_kwargs["after"] == "hdr-block"
    children = append_args[2]
    assert children[0]["type"] == "heading_3"
    assert children[0]["heading_3"]["rich_text"][0]["text"]["content"] == "19:07 IDT"
    assert children[1]["paragraph"]["rich_text"][0]["text"]["content"] == "Did some stuff."
    assert children[2]["paragraph"]["rich_text"][0]["text"]["content"] == "Second paragraph."
    assert "OK log added: 2026-09-25 19:07 IDT" in out


@patch("kb.nc.append_blocks")
@patch("kb.nc.get_block_children")
@patch("kb.nc.query_database")
def test_log_add_uses_existing_page_and_does_not_create(mock_query, mock_children, mock_append, tmp_path):
    _write_env(tmp_path)
    mock_query.return_value = [_page("existing-log", {"Date": _title_prop_val("2026-09-25")})]
    mock_children.return_value = [_block("hdr-block", "paragraph", "header")]

    now = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)
    with patch("kb.nc.create_page") as mock_create:
        rc = kb.main(["log", "add", "More stuff."], project_root=tmp_path, now=now)
        mock_create.assert_not_called()
    assert rc == 0
    append_args, append_kwargs = mock_append.call_args
    assert append_args[1] == "existing-log"
    assert append_kwargs["after"] == "hdr-block"


@patch("kb.nc.get_block_children")
@patch("kb.nc.query_database")
def test_log_last_returns_no_logs_message_when_empty(mock_query, mock_children, tmp_path, capsys):
    _write_env(tmp_path)
    mock_query.return_value = []
    rc = kb.main(["log", "last"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "no daily logs yet" in out


# ---------------------------------------------------------------------------
# wake
# ---------------------------------------------------------------------------


@patch("kb.nc.update_block")
@patch("kb.nc.get_block_children")
@patch("kb.nc.query_database")
@patch("kb.session_store.write_next_wake")
def test_wake_relative_minutes_writes_file_and_updates_header(
    mock_write_wake, mock_query, mock_children, mock_update_block, tmp_path, capsys
):
    _write_env(tmp_path)
    mock_query.return_value = [_page("log-today", {"Date": _title_prop_val("2026-09-25")})]
    mock_children.return_value = [_block("hdr-block", "paragraph", "old header")]

    now = datetime(2026, 9, 25, 16, 0, tzinfo=timezone.utc)  # 19:00 IDT
    rc = kb.main(["wake", "+30"], project_root=tmp_path, now=now)
    out = capsys.readouterr().out
    assert rc == 0

    mock_write_wake.assert_called_once()
    wake_args = mock_write_wake.call_args[0]
    assert wake_args[0] == tmp_path / "session"
    assert wake_args[1] == "2026-09-25T16:30:00Z"

    update_args = mock_update_block.call_args[0]
    assert update_args[1] == "hdr-block"
    text = update_args[2]["paragraph"]["rich_text"][0]["text"]["content"]
    assert text == "⏰ Next scheduled run: 19:30 IDT (16:30 UTC)"
    assert "2026-09-25T16:30:00Z" in out


@patch("kb.nc.update_block")
@patch("kb.nc.get_block_children")
@patch("kb.nc.query_database")
@patch("kb.session_store.write_next_wake")
def test_wake_absolute_iso_timestamp(mock_write_wake, mock_query, mock_children, mock_update_block, tmp_path):
    _write_env(tmp_path)
    mock_query.return_value = [_page("log-today", {"Date": _title_prop_val("2026-09-25")})]
    mock_children.return_value = [_block("hdr-block", "paragraph", "old header")]

    now = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    rc = kb.main(["wake", "2026-09-26T05:00:00Z"], project_root=tmp_path, now=now)
    assert rc == 0
    wake_args = mock_write_wake.call_args[0]
    assert wake_args[1] == "2026-09-26T05:00:00Z"


def test_wake_invalid_value_errors(tmp_path, capsys):
    _write_env(tmp_path)
    rc = kb.main(["wake", "not-a-date"], project_root=tmp_path)
    err = capsys.readouterr().err
    assert rc == 1
    assert "invalid" in err.lower()


# ---------------------------------------------------------------------------
# human
# ---------------------------------------------------------------------------


@patch("kb.nc.create_page")
@patch("kb.nc.query_database")
def test_human_create_dedupes_existing_row_with_same_title(mock_query, mock_create, tmp_path, capsys):
    _write_env(tmp_path)
    title = "Ikariam session expired — manual cookie refresh needed"
    mock_query.return_value = [_page("existing-id", {"Name": _title_prop_val(title), "CreatedAt": _date_prop_val("2026-09-20")})]
    rc = kb.main(["human", "create", title, "please help"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip() == f"EXISTS {nc.normalize_id('existing-id')}"
    mock_create.assert_not_called()


@patch("kb.nc.create_page")
@patch("kb.nc.query_database")
def test_human_create_creates_when_no_duplicate(mock_query, mock_create, tmp_path, capsys):
    _write_env(tmp_path)
    mock_query.return_value = []
    mock_create.return_value = {"id": "new-id"}
    rc = kb.main(["human", "create", "Title", "Question text"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK human created:" in out
    mock_create.assert_called_once()
    args, kwargs = mock_create.call_args
    children = args[3]
    assert children[0]["type"] == "heading_2"
    assert children[1]["paragraph"]["rich_text"][0]["text"]["content"] == "Question text"


@patch("kb.nc.get_block_children")
@patch("kb.nc.query_database")
def test_human_list_marks_answered_rows_with_reply_text(mock_query, mock_children, tmp_path, capsys):
    _write_env(tmp_path)
    mock_query.return_value = [
        _page("open-1", {"Name": _title_prop_val("Open Q"), "CreatedAt": _date_prop_val("2026-09-20")}),
        _page("answered-1", {"Name": _title_prop_val("Answered Q"), "CreatedAt": _date_prop_val("2026-09-18")}),
    ]

    def children_side_effect(token, page_id):
        if page_id == "open-1":
            return [_block("q1", "heading_2", "Question"), _block("q2", "paragraph", "the question")]
        return [
            _block("q1", "heading_2", "Question"),
            _block("q2", "paragraph", "the question"),
            _block("r1", "paragraph", "human's reply"),
        ]

    mock_children.side_effect = children_side_effect
    rc = kb.main(["human", "list"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "Open Q" in out and "| open" in out
    assert "Answered Q" in out and "ANSWERED: human's reply" in out


@patch("kb.nc.archive_page")
def test_human_resolve_archives_row(mock_archive, tmp_path, capsys):
    _write_env(tmp_path)
    rc = kb.main(["human", "resolve", "some-page-id"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    mock_archive.assert_called_once_with("ntn_test", "some-page-id")
    assert "OK human resolved" in out


# ---------------------------------------------------------------------------
# diplomacy
# ---------------------------------------------------------------------------


@patch("kb.nc.query_database")
def test_diplomacy_list_truncates_history_to_120_chars(mock_query, tmp_path, capsys):
    _write_env(tmp_path)
    long_history = "x" * 200
    mock_query.return_value = [
        _page(
            "d1",
            {
                "Name": _title_prop_val("Rival1"),
                "Type": _select_prop_val("player"),
                "Relationship": _select_prop_val("enemy"),
                "LastUpdated": _date_prop_val("2026-09-21"),
                "History": _rich_text_prop_val(long_history),
            },
        )
    ]
    rc = kb.main(["diplomacy", "list"], project_root=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    line = [l for l in out.splitlines() if "Rival1" in l][0]
    history_field = line.split(" | ")[-1]
    assert len(history_field) <= 124  # 120 + "..."
    assert history_field.startswith("x" * 120)


@patch("kb.nc.update_page")
@patch("kb.nc.query_database")
def test_diplomacy_upsert_appends_history_with_date_prefix(mock_query, mock_update, tmp_path, capsys):
    _write_env(tmp_path)
    mock_query.return_value = [
        _page(
            "d1",
            {
                "Name": _title_prop_val("Rival1"),
                "Type": _select_prop_val("player"),
                "Relationship": _select_prop_val("neutral"),
                "LastUpdated": _date_prop_val("2026-09-01"),
                "History": _rich_text_prop_val("previous entry"),
            },
        )
    ]
    now = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)
    rc = kb.main(
        ["diplomacy", "upsert", "Rival1", "--relationship", "enemy", "--history-append", "declared war"],
        project_root=tmp_path,
        now=now,
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK diplomacy updated: Rival1" in out
    args, kwargs = mock_update.call_args
    props = args[2]
    history_text = props["History"]["rich_text"][0]["text"]["content"]
    assert "previous entry" in history_text
    assert "[2026-09-25] declared war" in history_text
    assert props["Relationship"] == nc.select_prop("enemy")


def test_capped_strategy_passes_small_text_through():
    assert kb._capped_strategy("## Plan\nGrow wood.") == "## Plan\nGrow wood."


def test_capped_strategy_shows_only_newest_tail_with_condense_notice():
    text = "OLD-CYCLE-1 " + "x" * 10000 + " NEWEST-UPDATE"
    out = kb._capped_strategy(text)
    assert "OLD-CYCLE-1" not in out
    assert out.endswith("NEWEST-UPDATE")
    assert "strategy set" in out and "10,026 CHARS" in out
    assert len(out) < kb.STRATEGY_CHAR_CAP + 400


@patch("kb.nc.append_blocks")
@patch("kb.nc.delete_block")
@patch("kb.nc.get_block_children")
def test_strategy_set_archives_huge_page_in_batches_of_100(mock_children, mock_delete, mock_append, tmp_path):
    _write_env(tmp_path, NOTION_STRATEGY_ARCHIVE_PAGE_ID="archive-1")
    old = [_block(f"b{i}", "paragraph", "y" * 1000) for i in range(250)]
    mock_children.return_value = old
    assert kb.main(["strategy", "set", "## Plan\nCondensed."], project_root=tmp_path) == 0
    archive_calls = [c for c in mock_append.call_args_list if c.args[1] == "archive-1"]
    assert len(archive_calls) >= 2
    for c in archive_calls:
        blocks = c.args[2]
        assert len(blocks) <= 100
        for b in blocks:
            for rt in b[b["type"]]["rich_text"]:
                assert len(rt["text"]["content"]) <= 2000
    assert mock_delete.call_count == 250
