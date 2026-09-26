from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class NotionError(RuntimeError):
    pass


class NotionConflict(NotionError):
    pass


def rich_text(value: str, link: str | None = None) -> list[dict]:
    text: dict[str, Any] = {"content": str(value)}
    if link:
        text["link"] = {"url": link}
    return [{"type": "text", "text": text}]


def prop_title(value: str) -> dict:
    return {"title": rich_text(value)}


def prop_text(value: str) -> dict:
    return {"rich_text": rich_text(value)}


def prop_select(value: str) -> dict:
    return {"select": {"name": value}}


def prop_date(value: str) -> dict:
    return {"date": {"start": value}}


def prop_url(value: str | None) -> dict:
    return {"url": value or None}


def text_block(kind: str, value: str, link: str | None = None) -> dict:
    return {"object": "block", "type": kind, kind: {"rich_text": rich_text(value, link)}}


def paragraph(value: str, link: str | None = None) -> dict:
    return text_block("paragraph", value, link)


def bullet(value: str, link: str | None = None) -> dict:
    return text_block("bulleted_list_item", value, link)


def child_database_block(title: str) -> dict:
    return {"object": "block", "type": "child_database", "child_database": {"title": title}}


def heading(level: int, value: str) -> dict:
    return text_block(f"heading_{level}", value)


def divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


class NotionClient:
    def __init__(self, token: str, version: str = "2026-03-11", timeout: int = 30):
        if not token:
            raise NotionError("NOTION_TOKEN 未配置")
        self.token = token
        self.version = version
        self.timeout = timeout
        self.base = "https://api.notion.com/v1"

    def request(self, method: str, path: str, payload: dict | None = None,
                *, retries: int = 3, retry_creates: bool = False) -> dict:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        url = self.base + path
        for attempt in range(retries):
            req = urllib.request.Request(url, data=data, method=method, headers={
                "Authorization": f"Bearer {self.token}",
                "Notion-Version": self.version,
                "Content-Type": "application/json",
                "User-Agent": "Spark-Program-Workbench/1.0",
            })
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    body = response.read().decode("utf-8")
                    return json.loads(body) if body else {}
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", "replace")
                try:
                    message = json.loads(raw).get("message", raw)
                except Exception:
                    message = raw
                if exc.code == 429 and attempt + 1 < retries:
                    time.sleep(float(exc.headers.get("Retry-After", "1")))
                    continue
                if exc.code >= 500 and attempt + 1 < retries and (method != "POST" or retry_creates):
                    time.sleep(2 ** attempt)
                    continue
                raise NotionError(f"Notion API {exc.code}: {message}") from exc
            except (TimeoutError, urllib.error.URLError) as exc:
                if attempt + 1 < retries and (method != "POST" or retry_creates):
                    time.sleep(2 ** attempt)
                    continue
                raise NotionError(f"Notion 网络请求失败: {exc}") from exc
        raise NotionError("Notion 请求失败")

    def me(self) -> dict:
        return self.request("GET", "/users/me")

    def retrieve_data_source(self, data_source_id: str) -> dict:
        return self.request("GET", f"/data_sources/{data_source_id}")

    def update_data_source(self, data_source_id: str, properties: dict) -> dict:
        """PATCH 内联数据库（child_database 块 id 即 data source id）的列结构。"""
        return self.request("PATCH", f"/data_sources/{data_source_id}", {"properties": properties})

    def query(self, data_source_id: str, filter_body: dict | None = None,
              page_size: int = 100) -> list[dict]:
        payload: dict[str, Any] = {"page_size": page_size}
        if filter_body:
            payload["filter"] = filter_body
        results: list[dict] = []
        while True:
            page = self.request("POST", f"/data_sources/{data_source_id}/query", payload,
                                retry_creates=True)
            results.extend(page.get("results", []))
            if not page.get("has_more"):
                return results
            payload["start_cursor"] = page.get("next_cursor")

    def find_title(self, data_source_id: str, property_name: str, value: str) -> list[dict]:
        return self.query(data_source_id, {"property": property_name, "title": {"equals": value}})

    def create_page(self, data_source_id: str, properties: dict,
                    children: list[dict] | None = None, icon: str | None = None) -> dict:
        payload: dict[str, Any] = {
            "parent": {"type": "data_source_id", "data_source_id": data_source_id},
            "properties": properties,
        }
        if children:
            payload["children"] = children[:100]
        if icon:
            payload["icon"] = {"type": "emoji", "emoji": icon}
        created = self.request("POST", "/pages", payload, retries=1)
        if children and len(children) > 100:
            self.append_children(created["id"], children[100:])
        return created

    def create_database(self, page_id: str, title: str, properties: dict) -> dict:
        """在页面内创建内联数据库（会以 child_database 块出现在页面上）。

        新版 API（2025-09+）下顶层 properties 会被忽略，列结构必须放在
        initial_data_source.properties 里。"""
        payload: dict[str, Any] = {
            "parent": {"type": "page_id", "page_id": page_id},
            "title": rich_text(title),
            "initial_data_source": {"properties": properties},
        }
        return self.request("POST", "/databases", payload, retries=1)

    def database_data_source_id(self, database_id: str) -> str | None:
        """取数据库第一个数据源的 id（新版 API 中 database id ≠ data source id）。"""
        db = self.request("GET", f"/databases/{database_id}")
        sources = db.get("data_sources") or []
        return sources[0].get("id") if sources else None

    def create_child_page(self, parent_page_id: str, title: str,
                          children: list[dict] | None = None) -> dict:
        payload: dict[str, Any] = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "properties": {"title": prop_title(title)["title"]},
        }
        if children:
            payload["children"] = children[:100]
        return self.request("POST", "/pages", payload, retries=1)

    def retrieve_page(self, page_id: str) -> dict:
        return self.request("GET", f"/pages/{page_id}")

    def update_page(self, page_id: str, properties: dict) -> dict:
        return self.request("PATCH", f"/pages/{page_id}", {"properties": properties})

    def archive_page(self, page_id: str) -> dict:
        """归档（删除）页面或数据库行。"""
        return self.request("PATCH", f"/pages/{page_id}", {"in_trash": True})

    def list_children(self, block_id: str, recursive: bool = False) -> list[dict]:
        results: list[dict] = []
        cursor = None
        while True:
            query = "?page_size=100"
            if cursor:
                query += "&start_cursor=" + urllib.parse.quote(cursor)
            page = self.request("GET", f"/blocks/{block_id}/children{query}")
            for block in page.get("results", []):
                if recursive and block.get("has_children"):
                    block["_children"] = self.list_children(block["id"], recursive=True)
                results.append(block)
            if not page.get("has_more"):
                return results
            cursor = page.get("next_cursor")

    def append_children(self, block_id: str, children: list[dict]) -> list[dict]:
        created: list[dict] = []
        for start in range(0, len(children), 100):
            page = self.request("PATCH", f"/blocks/{block_id}/children",
                                {"children": children[start:start + 100]}, retries=1)
            created.extend(page.get("results", []))
        return created


def property_value(page: dict, name: str) -> Any:
    prop = page.get("properties", {}).get(name, {})
    kind = prop.get("type")
    value = prop.get(kind) if kind else None
    if kind in {"title", "rich_text"}:
        return "".join(item.get("plain_text", "") for item in (value or []))
    if kind == "select":
        return (value or {}).get("name")
    if kind == "date":
        return (value or {}).get("start")
    if kind == "url":
        return value or ""
    if kind == "number":
        return value
    if kind == "checkbox":
        return bool(value)
    return value


def _digits(value: Any) -> str:
    import re
    return "".join(re.findall(r"\d+", str(value or "")))


def _is_amount_like(*values: Any) -> bool:
    text = " ".join(str(v or "") for v in values)
    return ("," in text) or any(token in text.upper() for token in ("CKB", "USD", "CNY", "$"))


def values_match(wanted: Any, actual: Any) -> bool:
    """字段等价判断：金额类字段做格式化归一化，其余严格相等。"""
    if str(actual or "") == str(wanted or ""):
        return True
    digits_wanted = _digits(wanted)
    if (digits_wanted and len(digits_wanted) >= 3 and _is_amount_like(wanted, actual)
            and digits_wanted == _digits(actual)):
        return True
    return False


def assert_properties(page: dict, expected: dict[str, Any]) -> None:
    conflicts = {}
    for name, wanted in expected.items():
        actual = property_value(page, name)
        if not values_match(wanted, actual):
            conflicts[name] = {"expected": wanted, "actual": actual}
    if conflicts:
        raise NotionConflict("Notion 字段冲突: " + json.dumps(conflicts, ensure_ascii=False))


WRITABLE_BLOCK_KEYS = {
    "paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item",
    "numbered_list_item", "to_do", "toggle", "quote", "callout", "divider",
    "code", "equation", "bookmark", "embed", "image", "video", "file", "pdf",
}


def _plain_items(body: dict) -> list[dict]:
    """把 rich_text 归一化为可写入的纯文本项：mention/自定义表情等 API 不支持重建
    的类型退化为纯文本（保留 @名字 等字面内容）。"""
    items = []
    for item in body.get("rich_text") or []:
        if item.get("type") == "text":
            text = item.get("text") or {}
            new_item: dict[str, Any] = {"type": "text",
                                        "text": {"content": item.get("plain_text") or text.get("content") or ""}}
            if text.get("link"):
                new_item["text"]["link"] = text["link"]
            items.append(new_item)
        elif item.get("plain_text"):
            items.append({"type": "text", "text": {"content": item["plain_text"]}})
    return items


def cloneable_block(block: dict) -> dict | None:
    kind = block.get("type")
    if kind not in WRITABLE_BLOCK_KEYS:
        return None
    result = {"object": "block", "type": kind, kind: dict(block.get(kind, {}))}
    for key in ("id", "parent", "created_time", "created_by", "last_edited_time",
                "last_edited_by", "has_children", "archived", "in_trash", "icon"):
        result[kind].pop(key, None)
    # 清理值为 None 的键（Notion 校验 icon 等字段不允许显式 null）
    result[kind] = {key: value for key, value in result[kind].items() if value is not None}
    if "rich_text" in result[kind]:
        result[kind]["rich_text"] = _plain_items(result[kind])
    children = [cloneable_block(child) for child in block.get("_children", [])]
    children = [child for child in children if child]
    if children:
        result[kind]["children"] = children
    return result


def _plain_rich_text(items: list[dict] | None) -> str:
    values = []
    for item in items or []:
        if "plain_text" in item:
            values.append(str(item.get("plain_text") or ""))
        else:
            values.append(str((item.get("text") or {}).get("content") or ""))
    return "".join(values)


def block_signature(block: dict) -> dict:
    """Return the stable, writable meaning of a block without Notion metadata."""
    kind = block.get("type", "")
    body = block.get(kind, {}) if kind else {}
    value: dict[str, Any] = {"type": kind}
    if "rich_text" in body:
        value["text"] = _plain_rich_text(body.get("rich_text"))
    if kind == "to_do":
        value["checked"] = bool(body.get("checked"))
    if kind == "code":
        value["language"] = body.get("language")
    if kind == "equation":
        value["expression"] = body.get("expression")
    for key in ("url",):
        if key in body:
            value[key] = body.get(key)
    children = block.get("_children") or body.get("children") or []
    if children:
        value["children"] = [block_signature(child) for child in children]
    return value


def block_signatures(blocks: list[dict]) -> list[dict]:
    return [block_signature(block) for block in blocks]
