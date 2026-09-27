# -*- coding: utf-8 -*-
"""notion_preview 数据层单测：注入假上游验证表发现、列序、单元格格式化与错误路径。"""
import email.message
import io
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import spark_web.notion_preview as np
import spark_web.server as server


def _rec(value: dict) -> dict:
    """模拟上游 recordMap 记录：{spaceId, value:{value:{...}}} 双层结构。"""
    return {"spaceId": "s1", "value": {"value": value}}


def _resp(payload: dict):
    body = json.dumps(payload).encode("utf-8")
    msg = email.message.Message()
    msg["Content-Type"] = "application/json"
    return io.BytesIO(body), msg


class _FakeResp:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")
        self.headers = email.message.Message()
        self.headers["Content-Type"] = "application/json"
        self.status = 200

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


PAGE_ID = "e95639bc-0d1b-83f5-952d-811d03c925b3"
COL_ONGOING = "385639bc-0d1b-826e-a95d-077aac42d7c1"
COL_DONE = "5e0639bc-0d1b-8254-b985-075b92de3eb4"
VIEW_ONGOING = "b66639bc-0d1b-8255-a352-0867b3c6b111"
VIEW_DONE = "d85639bc-0d1b-8213-a6f4-883b1d47c2ee"
BLOCK_ONGOING = "865639bc-0d1b-83e3-95dc-81f269947e03"
BLOCK_DONE = "ed9639bc-0d1b-82fe-bd7d-817d608763d8"


def _fake_upstream(request: urllib.request.Request, timeout=None):
    url = request.full_url
    body = json.loads((request.data or b"{}").decode("utf-8"))
    if url.endswith("loadCachedPageChunkV2"):
        page_id = body["page"]["id"]
        children = [
            _rec({"id": "h1", "type": "sub_header", "properties": {"title": [["进行中项目 / Ongoing Projects"]]}}),
            _rec({"id": BLOCK_ONGOING, "type": "collection_view", "collection_id": COL_ONGOING}),
            _rec({"id": "h2", "type": "sub_header", "properties": {"title": [["已完成项目 / Completed Projects"]]}}),
            _rec({"id": BLOCK_DONE, "type": "collection_view", "collection_id": COL_DONE}),
        ]
        return _FakeResp({"recordMap": {
            "block": {page_id: _rec({"id": page_id, "type": "page", "space_id": "s1",
                                     "alive": True, "content": [c["value"]["value"]["id"] for c in children]}),
                      **{c["value"]["value"]["id"]: c for c in children}},
            "collection_view": {VIEW_ONGOING: {}, VIEW_DONE: {}},
        }})
    if url.endswith("syncRecordValuesSpaceInitial"):
        pointers = [r["pointer"] for r in body["requests"]]
        out = {"recordMap": {}}
        for p in pointers:
            table, rid = p["table"], p["id"]
            if table == "collection_view":
                view = {"id": rid, "type": "table", "parent_id": BLOCK_ONGOING if rid == VIEW_ONGOING else BLOCK_DONE,
                        "format": {"table_properties": [
                            {"property": "title", "width": 208, "visible": True},
                            {"property": "k1", "width": 500, "visible": True},
                            {"property": "k2", "width": 120, "visible": False},
                        ],
                            "collection_pointer": {"id": COL_ONGOING if rid == VIEW_ONGOING else COL_DONE,
                                                   "table": "collection"}}}
                out["recordMap"].setdefault("collection_view", {})[rid] = _rec(view)
            elif table == "collection":
                schema = {"title": {"name": "项目名称", "type": "title"},
                          "k1": {"name": "开始日期", "type": "date"},
                          "k2": {"name": "隐藏列", "type": "text"}}
                out["recordMap"].setdefault("collection", {})[rid] = _rec({"id": rid, "schema": schema})
        return _FakeResp(out)
    if "queryCollection" in url:
        view_id = body["collectionView"]["id"]
        row_id = "row-1"
        row = _rec({"id": row_id, "type": "page", "properties": {
            "title": [["Quantum<Purse"]],
            "k1": [["‣", [["d", {"type": "date", "start_date": "2026-05-21", "end_date": "2026-06-01"}]]]],
            "k2": [["https://example.com", [["a", "https://example.com"]]]],
        }})
        return _FakeResp({
            "result": {"reducerResults": {"collection_group_results": {"blockIds": [row_id], "hasMore": False}}},
            "recordMap": {"block": {row_id: row}},
        })
    raise AssertionError(f"unexpected upstream URL: {url}")


CONFIG = {
    "tabs": [
        {"key": "ongoing_projects", "label": "进行中项目表", "hint": "进行中项目",
         "url": "https://example.org/pl"},
        {"key": "completed_projects", "label": "已完成项目表", "hint": "已完成项目",
         "url": "https://example.org/pl"},
    ],
    "parents": [{"page_id": PAGE_ID, "tables": ["ongoing_projects", "completed_projects"]}],
}


class NotionPreviewTests(unittest.TestCase):
    def setUp(self):
        np.invalidate_cache()
        self._patch = patch.object(np, "_urlopen", _fake_upstream)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_discover_tables_pairs_hints_with_headings(self):
        parents = [{"page_id": PAGE_ID,
                    "tables": [{"key": "ongoing_projects", "hint": "进行中项目"},
                               {"key": "completed_projects", "hint": "已完成项目"}]}]
        tables = np.discover_tables(parents)
        self.assertEqual(set(tables), {"ongoing_projects", "completed_projects"})
        self.assertEqual(tables["ongoing_projects"]["view_block_id"], BLOCK_ONGOING)
        self.assertEqual(tables["ongoing_projects"]["collection_id"], COL_ONGOING)
        self.assertEqual(tables["ongoing_projects"]["view_id"], VIEW_ONGOING)

    def test_table_json_uses_view_column_order_and_formats_cells(self):
        result = np.table_json("ongoing_projects", CONFIG)
        self.assertEqual([c["key"] for c in result["columns"]], ["title", "k1", "k2"])
        # 隐藏列 k2 仍在尾部兜底出现；宽度被钳制到 480
        widths = {c["key"]: c["width"] for c in result["columns"]}
        self.assertEqual(widths["k1"], 480)
        row = result["rows"][0]
        self.assertEqual(row["title"], "Quantum&lt;Purse")  # HTML 转义
        self.assertEqual(row["k1"], "2026-05-21 → 2026-06-01")
        self.assertIn('<a href="https://example.com"', row["k2"])
        self.assertEqual(result["page_url"], "https://example.org/pl")

    def test_table_cache_and_refresh(self):
        first = np.table_json("ongoing_projects", CONFIG)
        self.assertIs(first, np.table_json("ongoing_projects", CONFIG))  # 缓存命中返回同一对象
        np.invalidate_cache()
        second = np.table_json("ongoing_projects", CONFIG)
        self.assertIsNot(first, second)

    def test_missing_table_raises(self):
        with self.assertRaises(np.PreviewError):
            np.table_json("no_such_key", CONFIG)

    def test_upstream_http_error_wrapped(self):
        def boom(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 503, "down",
                                         email.message.Message(), None)
        with patch.object(np, "_urlopen", boom):
            with self.assertRaises(np.PreviewError) as ctx:
                np.table_json("ongoing_projects", CONFIG)
        self.assertIn("503", str(ctx.exception))


class _QuietHandler(server.Handler):
    def log_message(self, fmt, *args):
        pass


class PreviewTableEndpointTests(unittest.TestCase):
    """/api/notion/preview-table 端点：带会话时返回表格 JSON。"""

    def _request(self, path, cookies=None):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{httpd.server_address[1]}{path}"
            req = urllib.request.Request(url)
            if cookies:
                req.add_header("Cookie", cookies)
            try:
                with urllib.request.urlopen(req, timeout=10) as response:
                    return response.status, json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read().decode("utf-8") or "{}")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_endpoint_returns_table_json(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "notion_preview.json").write_text(json.dumps(CONFIG, ensure_ascii=False),
                                                         encoding="utf-8")
            with patch.object(server, "ROOT", server.Path(tmp)), \
                    patch.object(server.Handler, "_session", lambda self: {"username": "u", "role": "admin", "csrf_token": "x"}), \
                    patch.object(np, "_urlopen", _fake_upstream):
                status, body = self._request("/api/notion/preview-table?key=ongoing_projects")
        self.assertEqual(status, 200)
        self.assertEqual(body["columns"][0]["name"], "项目名称")
        self.assertEqual(len(body["rows"]), 1)


if __name__ == "__main__":
    unittest.main()
