import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import spark_web.server as server


class _QuietHandler(server.Handler):
    def log_message(self, fmt, *args):  # noqa: D102 - 测试期间保持输出干净
        pass


class NotionPreviewEndpointTests(unittest.TestCase):
    def _request(self, root, path="/api/notion/preview"):
        with patch.object(server, "ROOT", root), patch.object(server, "TEAM_MODE", False):
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{httpd.server_address[1]}{path}"
                try:
                    with urllib.request.urlopen(url, timeout=10) as response:
                        return response.status, json.loads(response.read().decode("utf-8"))
                except urllib.error.HTTPError as exc:
                    return exc.code, json.loads(exc.read().decode("utf-8"))
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)

    def test_preview_returns_config_file(self):
        config = {"tabs": [{"key": "fund_allocations", "label": "资金分配情况表",
                            "url": "https://www.notion.so/demo-fund"}],
                  "parents": [{"page_id": "p1", "title": "资金池状态", "tables": ["fund_allocations"]}]}
        with tempfile.TemporaryDirectory() as tmp:
            root_path = Path(tmp)
            (root_path / "notion_preview.json").write_text(
                json.dumps(config, ensure_ascii=False), encoding="utf-8")
            status, body = self._request(root_path)
        self.assertEqual(status, 200)
        self.assertEqual(body, config)

    def test_preview_missing_config_returns_404_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            status, body = self._request(Path(tmp))
        self.assertEqual(status, 404)
        self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()
