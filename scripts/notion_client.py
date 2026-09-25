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


def query_database(
    token: str,
    database_id: str,
    filter_: dict | None = None,
    sorts: list | None = None,
) -> list:
    # Notion paginates at 100 rows/page. Looping over has_more/next_cursor
    # avoids silently truncating databases that have grown past that.
    results = []
    start_cursor = None
    while True:
        payload = {"page_size": 100}
        if filter_:
            payload["filter"] = filter_
        if sorts:
            payload["sorts"] = sorts
        if start_cursor:
            payload["start_cursor"] = start_cursor
        resp = requests.post(
            f"{NOTION_API}/databases/{database_id}/query", headers=_headers(token), json=payload
        )
        resp.raise_for_status()
        data = resp.json()
        results.extend(data["results"])
        if data.get("has_more") and data.get("next_cursor"):
            start_cursor = data["next_cursor"]
        else:
            break
    return results


def search(token: str, query: str = "") -> list:
    resp = requests.post(f"{NOTION_API}/search", headers=_headers(token), json={"query": query})
    resp.raise_for_status()
    return resp.json()["results"]


def append_blocks(token: str, block_id: str, children: list, after: str | None = None) -> dict:
    payload = {"children": children}
    if after:
        payload["after"] = after
    resp = requests.patch(
        f"{NOTION_API}/blocks/{block_id}/children",
        headers=_headers(token),
        json=payload,
    )
    resp.raise_for_status()
    return resp.json()


def update_block(token: str, block_id: str, block_payload: dict) -> dict:
    resp = requests.patch(
        f"{NOTION_API}/blocks/{block_id}", headers=_headers(token), json=block_payload
    )
    resp.raise_for_status()
    return resp.json()


def delete_block(token: str, block_id: str) -> dict:
    resp = requests.delete(f"{NOTION_API}/blocks/{block_id}", headers=_headers(token))
    resp.raise_for_status()
    return resp.json()


def get_page(token: str, page_id: str) -> dict:
    resp = requests.get(f"{NOTION_API}/pages/{page_id}", headers=_headers(token))
    resp.raise_for_status()
    return resp.json()


def get_block_children(token: str, block_id: str) -> list:
    # Same truncation risk as query_database — page through has_more.
    results = []
    start_cursor = None
    while True:
        params = {"page_size": 100}
        if start_cursor:
            params["start_cursor"] = start_cursor
        resp = requests.get(
            f"{NOTION_API}/blocks/{block_id}/children",
            headers=_headers(token),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        results.extend(data["results"])
        if data.get("has_more") and data.get("next_cursor"):
            start_cursor = data["next_cursor"]
        else:
            break
    return results


def update_page(token: str, page_id: str, properties: dict) -> dict:
    resp = requests.patch(
        f"{NOTION_API}/pages/{page_id}", headers=_headers(token), json={"properties": properties}
    )
    resp.raise_for_status()
    return resp.json()


def archive_page(token: str, page_id: str) -> dict:
    resp = requests.patch(
        f"{NOTION_API}/pages/{page_id}", headers=_headers(token), json={"archived": True}
    )
    resp.raise_for_status()
    return resp.json()


def title_prop(text: str) -> dict:
    return {"title": [{"type": "text", "text": {"content": text}}]}


def _chunk_rich_text(text: str) -> list:
    # Notion rejects any single text object over 2000 chars; chunk so
    # accumulating fields/blocks don't start failing once they grow past it.
    chunk_size = 1900
    chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)] or [""]
    return [{"type": "text", "text": {"content": c}} for c in chunks]


def rich_text_prop(text: str) -> dict:
    return {"rich_text": _chunk_rich_text(text)}


def select_prop(name: str) -> dict:
    return {"select": {"name": name}}


def date_prop(iso_date: str) -> dict:
    return {"date": {"start": iso_date}}


def number_prop(n) -> dict:
    return {"number": n}


def paragraph_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": _chunk_rich_text(text)},
    }


def heading2_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": _chunk_rich_text(text)},
    }


def heading3_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "heading_3",
        "heading_3": {"rich_text": _chunk_rich_text(text)},
    }


def bulleted_list_item_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": _chunk_rich_text(text)},
    }


def block_text(block: dict) -> str:
    """Plain text of a block's rich_text, whatever the block's type."""
    block_type = block.get("type", "")
    content = block.get(block_type, {})
    rich = content.get("rich_text", [])
    parts = []
    for t in rich:
        if "plain_text" in t:
            parts.append(t["plain_text"])
        else:
            parts.append(t.get("text", {}).get("content", ""))
    return "".join(parts)


def page_title(page: dict) -> str:
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in prop.get("title", []))
    return ""


def prop_text(page: dict, name: str) -> str:
    """Plain-string rendering of a page property, whatever its type."""
    prop = page.get("properties", {}).get(name)
    if not prop:
        return ""
    ptype = prop.get("type")
    if ptype == "title":
        return "".join(t.get("plain_text", "") for t in prop.get("title", []))
    if ptype == "rich_text":
        return "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))
    if ptype == "select":
        sel = prop.get("select")
        return sel["name"] if sel else ""
    if ptype == "date":
        d = prop.get("date")
        return d["start"] if d else ""
    if ptype == "number":
        n = prop.get("number")
        return "" if n is None else str(n)
    return ""


def normalize_id(notion_id: str) -> str:
    return notion_id.replace("-", "")
