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


def test_chores_schema_has_lifecycle_fields():
    assert bk.CHORES_SCHEMA["Name"] == {"title": {}}
    status_options = {o["name"] for o in bk.CHORES_SCHEMA["Status"]["select"]["options"]}
    assert status_options == {"proposed", "dry-run", "active", "disabled"}
    for field in ("Rule", "Why", "LastResult"):
        assert bk.CHORES_SCHEMA[field] == {"rich_text": {}}
    for field in ("DryRunsOK", "Runs", "Failures"):
        assert "number" in bk.CHORES_SCHEMA[field]
    assert bk.CHORES_SCHEMA["LastRun"] == {"date": {}}


@patch("bootstrap_kb.update_env_file")
@patch("bootstrap_kb.seed_action_catalog", return_value=0)
@patch("bootstrap_kb.ensure_page", return_value="page-id")
@patch("bootstrap_kb.ensure_database")
@patch("bootstrap_kb.load_config")
def test_main_ensures_chores_database_and_persists_id(
    mock_load_config, mock_ensure_db, mock_ensure_page, mock_seed, mock_update_env, tmp_path
):
    config = MagicMock()
    config.notion_token = "tok"
    config.notion_root_page_id = "root-1"
    config.ikariam_server = "s1.example.com"
    mock_load_config.return_value = config
    mock_ensure_db.side_effect = lambda token, root, title, schema: f"db-{title.lower().replace(' ', '-')}"

    with patch("bootstrap_kb.PROJECT_ROOT", tmp_path):
        bk.main()

    chores_calls = [c for c in mock_ensure_db.call_args_list if c.args[2] == "Chores"]
    assert len(chores_calls) == 1
    assert chores_calls[0].args[3] == bk.CHORES_SCHEMA
    env_updates = mock_update_env.call_args.args[1]
    assert env_updates["NOTION_CHORES_DB_ID"] == "db-chores"
