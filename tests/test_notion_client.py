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


@patch("notion_client.requests.patch")
def test_append_blocks_with_after_includes_it_in_payload(mock_patch):
    mock_patch.return_value = _mock_response({"results": []})
    blocks = [nc.paragraph_block("hi")]
    nc.append_blocks("tok", "block-1", blocks, after="block-header")
    args, kwargs = mock_patch.call_args
    assert kwargs["json"] == {"children": blocks, "after": "block-header"}


@patch("notion_client.requests.patch")
def test_update_block_patches_block_content(mock_patch):
    mock_patch.return_value = _mock_response({"id": "block-1"})
    payload = {"paragraph": {"rich_text": [{"type": "text", "text": {"content": "new"}}]}}
    nc.update_block("tok", "block-1", payload)
    args, kwargs = mock_patch.call_args
    assert args[0] == "https://api.notion.com/v1/blocks/block-1"
    assert kwargs["json"] == payload


def test_heading3_block():
    assert nc.heading3_block("Cycle 12") == {
        "object": "block",
        "type": "heading_3",
        "heading_3": {"rich_text": [{"type": "text", "text": {"content": "Cycle 12"}}]},
    }


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


@patch("notion_client.requests.post")
def test_query_database_paginates_across_multiple_pages(mock_post):
    page1 = _mock_response({"results": [{"id": "row-1"}], "has_more": True, "next_cursor": "cursor-1"})
    page2 = _mock_response({"results": [{"id": "row-2"}], "has_more": False, "next_cursor": None})
    mock_post.side_effect = [page1, page2]
    result = nc.query_database("tok", "db-1")
    assert result == [{"id": "row-1"}, {"id": "row-2"}]
    assert mock_post.call_count == 2
    first_kwargs = mock_post.call_args_list[0][1]
    assert "start_cursor" not in first_kwargs["json"]
    second_kwargs = mock_post.call_args_list[1][1]
    assert second_kwargs["json"]["start_cursor"] == "cursor-1"


@patch("notion_client.requests.post")
def test_query_database_stops_when_has_more_false(mock_post):
    mock_post.return_value = _mock_response({"results": [{"id": "row-1"}], "has_more": False})
    result = nc.query_database("tok", "db-1")
    assert result == [{"id": "row-1"}]
    assert mock_post.call_count == 1


@patch("notion_client.requests.post")
def test_query_database_passes_sorts(mock_post):
    mock_post.return_value = _mock_response({"results": []})
    sorts = [{"property": "Date", "direction": "descending"}]
    nc.query_database("tok", "db-1", sorts=sorts)
    args, kwargs = mock_post.call_args
    assert kwargs["json"]["sorts"] == sorts


@patch("notion_client.requests.get")
def test_get_block_children_paginates_across_multiple_pages(mock_get):
    page1 = _mock_response({"results": [{"id": "block-1"}], "has_more": True, "next_cursor": "cursor-a"})
    page2 = _mock_response({"results": [{"id": "block-2"}], "has_more": False, "next_cursor": None})
    mock_get.side_effect = [page1, page2]
    result = nc.get_block_children("tok", "parent-1")
    assert result == [{"id": "block-1"}, {"id": "block-2"}]
    assert mock_get.call_count == 2
    first_kwargs = mock_get.call_args_list[0][1]
    assert "start_cursor" not in first_kwargs["params"]
    second_kwargs = mock_get.call_args_list[1][1]
    assert second_kwargs["params"]["start_cursor"] == "cursor-a"


@patch("notion_client.requests.delete")
def test_delete_block_sends_delete_request(mock_delete):
    mock_delete.return_value = _mock_response({"id": "block-1", "archived": True})
    result = nc.delete_block("tok", "block-1")
    assert result == {"id": "block-1", "archived": True}
    args, kwargs = mock_delete.call_args
    assert args[0] == "https://api.notion.com/v1/blocks/block-1"


def test_block_text_extracts_plain_text_any_block_type():
    block = {
        "type": "heading_2",
        "heading_2": {"rich_text": [{"plain_text": "Hello "}, {"plain_text": "World"}]},
    }
    assert nc.block_text(block) == "Hello World"


def test_block_text_falls_back_to_text_content_when_no_plain_text():
    block = {"type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": "hi"}}]}}
    assert nc.block_text(block) == "hi"


def test_block_text_empty_when_no_rich_text():
    assert nc.block_text({"type": "divider", "divider": {}}) == ""


def test_page_title_finds_title_property():
    page = {"properties": {"Name": {"type": "title", "title": [{"plain_text": "Foo"}]}}}
    assert nc.page_title(page) == "Foo"


def test_page_title_empty_when_missing():
    assert nc.page_title({"properties": {}}) == ""


def test_prop_text_handles_select_date_number_rich_text():
    page = {
        "properties": {
            "Kind": {"type": "select", "select": {"name": "action"}},
            "LastVerified": {"type": "date", "date": {"start": "2026-09-01"}},
            "Count": {"type": "number", "number": 5},
            "Notes": {"type": "rich_text", "rich_text": [{"plain_text": "hi"}]},
            "Empty": {"type": "select", "select": None},
        }
    }
    assert nc.prop_text(page, "Kind") == "action"
    assert nc.prop_text(page, "LastVerified") == "2026-09-01"
    assert nc.prop_text(page, "Count") == "5"
    assert nc.prop_text(page, "Notes") == "hi"
    assert nc.prop_text(page, "Empty") == ""
    assert nc.prop_text(page, "Missing") == ""


def test_number_prop():
    assert nc.number_prop(5) == {"number": 5}


def test_paragraph_block_chunks_long_text():
    long_text = "a" * 2500
    block = nc.paragraph_block(long_text)
    chunks = block["paragraph"]["rich_text"]
    assert len(chunks) == 2
    assert "".join(c["text"]["content"] for c in chunks) == long_text


def test_heading2_block_chunks_long_text():
    long_text = "b" * 2000
    block = nc.heading2_block(long_text)
    chunks = block["heading_2"]["rich_text"]
    assert len(chunks) == 2


def test_bulleted_list_item_block():
    assert nc.bulleted_list_item_block("buy wood") == {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "buy wood"}}]},
    }
