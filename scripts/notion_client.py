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


def query_database(token: str, database_id: str, filter_: dict | None = None) -> list:
    payload = {"filter": filter_} if filter_ else {}
    resp = requests.post(
        f"{NOTION_API}/databases/{database_id}/query", headers=_headers(token), json=payload
    )
    resp.raise_for_status()
    return resp.json()["results"]


def search(token: str, query: str = "") -> list:
    resp = requests.post(f"{NOTION_API}/search", headers=_headers(token), json={"query": query})
    resp.raise_for_status()
    return resp.json()["results"]


def append_blocks(token: str, block_id: str, children: list) -> dict:
    resp = requests.patch(
        f"{NOTION_API}/blocks/{block_id}/children",
        headers=_headers(token),
        json={"children": children},
    )
    resp.raise_for_status()
    return resp.json()


def title_prop(text: str) -> dict:
    return {"title": [{"type": "text", "text": {"content": text}}]}


def rich_text_prop(text: str) -> dict:
    return {"rich_text": [{"type": "text", "text": {"content": text}}]}


def select_prop(name: str) -> dict:
    return {"select": {"name": name}}


def date_prop(iso_date: str) -> dict:
    return {"date": {"start": iso_date}}


def paragraph_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def heading2_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def normalize_id(notion_id: str) -> str:
    return notion_id.replace("-", "")
