"""Notion 反向代理端点 /notion-view/ 的单测：注入假上游验证头剥除、HTML 改写、透传与 3xx 重写。"""
import email.message
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import spark_web.server as server


UPSTREAM = "https://evergreen-stream-df9.notion.site"


class _FakeResp:
    """模拟 urllib 响应：.status / .headers / .read()；4xx/5xx 由 urlopen 以 HTTPError 抛出。"""

    def __init__(self, status, headers, body):
        self.status = status
        self.code = status
        self.headers = email.message.Message()
        for key, value in headers:
            self.headers[key] = value
        self._body = body

    def read(self):
        return self._body


class _FakeHTTPError(urllib.error.HTTPError):
    def __init__(self, status, headers, body):
        msg = email.message.Message()
        for key, value in headers:
            msg[key] = value
        super().__init__(UPSTREAM, status, "error", msg, None)
        self._body = body
        self.headers = msg

    def read(self):
        return self._body


class _QuietHandler(server.Handler):
    def log_message(self, fmt, *args):  # noqa: D102 - 测试期间保持输出干净
        pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102 - 不跟随重定向，直接断言 3xx
        return None


_CLIENT = urllib.request.build_opener(_NoRedirect)


class NotionProxyTests(unittest.TestCase):
    def _request(self, path, fake_urlopen, method="GET", body=None, headers=None):
        with patch.object(server, "_notion_urlopen", fake_urlopen), \
                patch.object(server, "TEAM_MODE", False):
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{httpd.server_address[1]}{path}"
                req = urllib.request.Request(url, method=method, data=body, headers=headers or {})
                try:
                    with _CLIENT.open(req, timeout=10) as response:
                        return response.status, dict(response.headers), response.read()
                except urllib.error.HTTPError as exc:
                    return exc.code, dict(exc.headers), exc.read()
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)

    def test_strips_frame_headers_and_rewrites_html(self):
        html = ('<!doctype html><html><head>'
                '<link rel="stylesheet" href="/_next/static/css/app.css">'
                '<script src="/_assets/ClientFramework-a1b2.js"></script>'
                '<script src="/_next/static/chunk.js"></script>'
                '<meta property="og:image" content="/images/cover.png">'
                '<div style="background:url(/_notion/x.png)"></div>'
                '<script>window.__asset="\\"/_next/data.json\\""</script>'
                '<a href="https://example.com/_next/keep">外部链接</a>'
                '<script src="/other/not-rewritten.js"></script>'
                '</head><body><h1>项目列表</h1></body></html>').encode("utf-8")
        seen = {}

        def fake_urlopen(request, timeout=None):
            seen["url"] = request.full_url
            seen["ua"] = request.get_header("User-agent", "")
            seen["encoding"] = request.get_header("Accept-encoding", "")
            return _FakeResp(200, [
                ("Content-Type", "text/html; charset=utf-8"),
                ("Cache-Control", "max-age=60"),
                ("X-Frame-Options", "SAMEORIGIN"),
                ("Content-Security-Policy", "frame-ancestors 'self'"),
                ("Set-Cookie", "notion_browser_id=abc; Secure"),
            ], html)

        status, headers, body = self._request("/notion-view/Project-List-x?p=1", fake_urlopen)
        self.assertEqual(status, 200)
        lowered = {key.lower(): value for key, value in headers.items()}
        self.assertNotIn("x-frame-options", lowered)
        self.assertNotIn("content-security-policy", lowered)
        self.assertNotIn("set-cookie", lowered)
        self.assertEqual(lowered.get("content-type"), "text/html; charset=utf-8")
        self.assertEqual(lowered.get("cache-control"), "max-age=60")
        text = body.decode("utf-8")
        self.assertIn(f'href="{UPSTREAM}/_next/static/css/app.css"', text)
        self.assertIn(f'src="{UPSTREAM}/_assets/ClientFramework-a1b2.js"', text)
        self.assertIn(f'src="{UPSTREAM}/_next/static/chunk.js"', text)
        self.assertIn(f'content="{UPSTREAM}/images/cover.png"', text)
        self.assertIn(f'url({UPSTREAM}/_notion/x.png)', text)
        self.assertIn(f'\\"{UPSTREAM}/_next/', text)  # JSON 转义引用同样被改写
        self.assertIn('href="https://example.com/_next/keep"', text)  # 已含域名不重复替换
        self.assertIn('src="/other/not-rewritten.js"', text)  # 非资源前缀不替换
        self.assertIn("<h1>项目列表</h1>", text)
        # query string 透传 + 浏览器 UA + identity 编码
        self.assertEqual(seen["url"], f"{UPSTREAM}/Project-List-x?p=1")
        self.assertIn("Mozilla/5.0", seen["ua"])
        self.assertEqual(seen["encoding"], "identity")

    def test_non_html_passthrough(self):
        payload = b"\x89PNG\r\n\x1a\nhref=\"/_next/x\""  # 含形似引用也不能被改写

        def fake_urlopen(request, timeout=None):
            return _FakeResp(200, [("Content-Type", "image/png"),
                                   ("Cache-Control", "max-age=3600"),
                                   ("X-Frame-Options", "SAMEORIGIN")], payload)

        status, headers, body = self._request("/notion-view/image.png", fake_urlopen)
        self.assertEqual(status, 200)
        self.assertEqual(body, payload)
        lowered = {key.lower(): value for key, value in headers.items()}
        self.assertEqual(lowered.get("content-type"), "image/png")
        self.assertNotIn("x-frame-options", lowered)

    def test_same_domain_redirect_rewritten(self):
        def fake_urlopen(request, timeout=None):
            return _FakeResp(302, [("Location", f"{UPSTREAM}/Fund-Pool-146639bc?source=redirect")], b"")

        status, headers, _ = self._request("/notion-view/Old-Page", fake_urlopen)
        self.assertEqual(status, 302)
        lowered = {key.lower(): value for key, value in headers.items()}
        self.assertEqual(lowered.get("location"), "/notion-view/Fund-Pool-146639bc?source=redirect")

    def test_external_redirect_passthrough(self):
        def fake_urlopen(request, timeout=None):
            return _FakeResp(302, [("Location", "https://app.notion.com/login")], b"")

        status, headers, _ = self._request("/notion-view/Old-Page", fake_urlopen)
        self.assertEqual(status, 302)
        lowered = {key.lower(): value for key, value in headers.items()}
        self.assertEqual(lowered.get("location"), "https://app.notion.com/login")

    def test_upstream_error_forwarded(self):
        def fake_urlopen(request, timeout=None):
            raise _FakeHTTPError(404, [("Content-Type", "text/html; charset=utf-8"),
                                       ("X-Frame-Options", "SAMEORIGIN")], b"<h1>Not Found</h1>")

        status, headers, body = self._request("/notion-view/Missing", fake_urlopen)
        self.assertEqual(status, 404)
        self.assertEqual(body, b"<h1>Not Found</h1>")
        lowered = {key.lower(): value for key, value in headers.items()}
        self.assertNotIn("x-frame-options", lowered)

    def test_upstream_unreachable_returns_marked_502(self):
        def fake_urlopen(request, timeout=None):
            raise urllib.error.URLError("timed out")

        status, headers, body = self._request("/notion-view/Project-List-x", fake_urlopen)
        self.assertEqual(status, 502)
        lowered = {key.lower(): value for key, value in headers.items()}
        self.assertNotIn("x-frame-options", lowered)
        self.assertNotIn("content-security-policy", lowered)
        self.assertIn(b"data-spark-proxy-error", body)  # 前端据此显示回退说明

    def test_prefix_get_passthrough_strips_headers(self):
        """白名单前缀 GET 透传：同路径+query 直达上游，剥帧限制类头，非 HTML 不改写。"""
        payload = b"console.log(1);href=\"/_next/x\""  # 形似引用也不许被改写
        seen = {}

        def fake_urlopen(request, timeout=None):
            seen["url"] = request.full_url
            seen["method"] = request.get_method()
            return _FakeResp(200, [
                ("Content-Type", "application/javascript"),
                ("Cache-Control", "max-age=31536000"),
                ("X-Frame-Options", "SAMEORIGIN"),
                ("Content-Security-Policy", "script-src 'self'"),
                ("Content-Security-Policy-Report-Only", "script-src 'self'"),
                ("Set-Cookie", "notion_browser_id=abc; Secure"),
            ], payload)

        status, headers, body = self._request("/_assets/ClientFramework-a1b2.js?v=1", fake_urlopen)
        self.assertEqual(status, 200)
        self.assertEqual(body, payload)
        lowered = {key.lower(): value for key, value in headers.items()}
        self.assertEqual(lowered.get("content-type"), "application/javascript")
        self.assertEqual(lowered.get("cache-control"), "max-age=31536000")
        self.assertNotIn("x-frame-options", lowered)
        self.assertNotIn("content-security-policy", lowered)
        self.assertNotIn("content-security-policy-report-only", lowered)
        self.assertNotIn("set-cookie", lowered)
        self.assertEqual(seen["url"], f"{UPSTREAM}/_assets/ClientFramework-a1b2.js?v=1")
        self.assertEqual(seen["method"], "GET")

    def test_prefix_post_body_and_content_type_forwarded(self):
        """loadCachedPageChunkV2 类 XHR：POST 请求体与 Content-Type 必须原样转发。"""
        req_body = b'{"page":{"id":"a1b2-c3d4"},"limit":50}'
        resp_body = b'{"recordMap":{}}'
        seen = {}

        def fake_urlopen(request, timeout=None):
            seen["url"] = request.full_url
            seen["method"] = request.get_method()
            seen["body"] = request.data
            seen["content_type"] = request.get_header("Content-type")
            return _FakeResp(200, [("Content-Type", "application/json")], resp_body)

        status, headers, body = self._request(
            "/api/v3/loadCachedPageChunkV2?src=app", fake_urlopen,
            method="POST", body=req_body,
            headers={"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertEqual(body, resp_body)
        self.assertEqual(seen["url"], f"{UPSTREAM}/api/v3/loadCachedPageChunkV2?src=app")
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["body"], req_body)
        self.assertEqual(seen["content_type"], "application/json")

    def test_statsig_and_print_prefixes_proxied(self):
        """白名单内其余前缀（/statsig/、/print.）同样透传，query 保留。"""
        seen = {}

        def fake_urlopen(request, timeout=None):
            seen["url"] = request.full_url
            return _FakeResp(204, [], b"")

        status, _, _ = self._request("/statsig/v1/rgstr?k=abc", fake_urlopen)
        self.assertEqual(status, 204)
        self.assertEqual(seen["url"], f"{UPSTREAM}/statsig/v1/rgstr?k=abc")

        def fake_urlopen2(request, timeout=None):
            seen["url2"] = request.full_url
            return _FakeResp(200, [("Content-Type", "text/css")], b"body{}")

        status, _, _ = self._request("/print.a1b2.css", fake_urlopen2)
        self.assertEqual(status, 200)
        self.assertEqual(seen["url2"], f"{UPSTREAM}/print.a1b2.css")

    def test_prefix_upstream_error_forwarded(self):
        def fake_urlopen(request, timeout=None):
            raise _FakeHTTPError(503, [("Content-Type", "text/html; charset=utf-8"),
                                       ("X-Frame-Options", "SAMEORIGIN")], b"<h1>upstream down</h1>")

        status, headers, body = self._request("/_next/static/chunk.js", fake_urlopen)
        self.assertEqual(status, 503)
        self.assertEqual(body, b"<h1>upstream down</h1>")
        lowered = {key.lower(): value for key, value in headers.items()}
        self.assertNotIn("x-frame-options", lowered)

    def test_non_whitelist_prefix_not_proxied(self):
        """非白名单路径（/api/v4/x、/evil）不得落入代理，走工作台原 404 逻辑。"""
        called = []

        def fake_urlopen(request, timeout=None):
            called.append(request.full_url)
            return _FakeResp(200, [], b"")

        status, _, body = self._request("/api/v4/getAppConfig", fake_urlopen)
        self.assertEqual(status, 404)
        self.assertEqual(called, [])

        status, _, body = self._request("/evil/_assets/x", fake_urlopen)
        self.assertEqual(status, 404)
        self.assertEqual(called, [])
        self.assertIn(b"not found", body)

    def test_prefix_post_non_whitelist_not_proxied(self):
        """POST 到非白名单前缀同样不进代理（且不能绕过会话校验落到业务接口之外）。"""
        called = []

        def fake_urlopen(request, timeout=None):
            called.append(request.full_url)
            return _FakeResp(200, [], b"")

        status, _, _ = self._request("/api/v4/loadCachedPageChunkV2", fake_urlopen,
                                     method="POST", body=b"{}", headers={"Content-Type": "application/json"})
        self.assertEqual(status, 404)
        self.assertEqual(called, [])

    def test_rewrite_location_static(self):
        self.assertEqual(server.Handler._rewrite_location(f"{UPSTREAM}/A-B?x=1"),
                         "/notion-view/A-B?x=1")
        self.assertEqual(server.Handler._rewrite_location("/Relative-Path"),
                         "/notion-view/Relative-Path")
        self.assertEqual(server.Handler._rewrite_location("https://app.notion.com/p/abc"),
                         "https://app.notion.com/p/abc")
        self.assertEqual(server.Handler._rewrite_location("https://other.example/keep"),
                         "https://other.example/keep")


if __name__ == "__main__":
    unittest.main()
