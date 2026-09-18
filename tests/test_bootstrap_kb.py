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
