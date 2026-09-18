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


def test_rich_text_prop_chunks_text_over_notion_limit():
    long_text = "a" * 2500
    result = nc.rich_text_prop(long_text)
    chunks = result["rich_text"]
    assert len(chunks) == 2
    assert all(len(c["text"]["content"]) <= 1900 for c in chunks)
    assert "".join(c["text"]["content"] for c in chunks) == long_text


def test_rich_text_prop_empty_string_still_produces_one_chunk():
    assert nc.rich_text_prop("") == {"rich_text": [{"type": "text", "text": {"content": ""}}]}


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


@patch("notion_client.requests.get")
def test_get_page_returns_json(mock_get):
    mock_get.return_value = _mock_response({"id": "page-1"})
    result = nc.get_page("tok", "page-1")
    assert result == {"id": "page-1"}
    args, kwargs = mock_get.call_args
    assert args[0] == "https://api.notion.com/v1/pages/page-1"


@patch("notion_client.requests.get")
def test_get_block_children_returns_results(mock_get):
    mock_get.return_value = _mock_response({"results": [{"id": "block-1"}]})
    result = nc.get_block_children("tok", "block-1")
    assert result == [{"id": "block-1"}]
    args, kwargs = mock_get.call_args
    assert args[0] == "https://api.notion.com/v1/blocks/block-1/children"


@patch("notion_client.requests.patch")
def test_update_page_patches_properties(mock_patch):
    mock_patch.return_value = _mock_response({"id": "page-1"})
    nc.update_page("tok", "page-1", {"Name": nc.title_prop("x")})
    args, kwargs = mock_patch.call_args
    assert args[0] == "https://api.notion.com/v1/pages/page-1"
    assert kwargs["json"] == {"properties": {"Name": nc.title_prop("x")}}


@patch("notion_client.requests.patch")
def test_archive_page_sets_archived_true(mock_patch):
    mock_patch.return_value = _mock_response({"id": "page-1", "archived": True})
    nc.archive_page("tok", "page-1")
    args, kwargs = mock_patch.call_args
    assert args[0] == "https://api.notion.com/v1/pages/page-1"
    assert kwargs["json"] == {"archived": True}
