from __future__ import annotations

import json
import unittest
import urllib.parse
from unittest.mock import patch

from spark_web import forum


class _FakeResp:
    def __init__(self, value):
        self._value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._value


class _FakeOpener:
    """按 URL 路由返回假响应，并记录请求序列。"""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def open(self, request, timeout=30):
        self.requests.append(request)
        value = self.routes[request.full_url]
        return _FakeResp(value() if callable(value) else value)


class ForumPostTests(unittest.TestCase):
    def fixture(self, login, post=None):
        return _FakeOpener({
            f"{forum.TALK_ORIGIN}/session/csrf": json.dumps({"csrf": "csrf-1"}).encode(),
            f"{forum.TALK_ORIGIN}/session": json.dumps(login).encode(),
            f"{forum.TALK_ORIGIN}/posts.json": json.dumps(post or {}).encode(),
        })

    def test_password_login_flow_posts_successfully_without_key(self):
        opener = self.fixture({"user": {"username": "member"}}, {"id": 42, "post_number": 3})
        with patch("urllib.request.build_opener", return_value=opener):
            result = forum.post_with_password("10338", "你好世界", "Member", "pw!")
        self.assertEqual(result["id"], 42)
        self.assertEqual([r.full_url.removeprefix(forum.TALK_ORIGIN) for r in opener.requests],
                         ["/session/csrf", "/session", "/session/csrf", "/posts.json"])
        login = urllib.parse.parse_qs(opener.requests[1].data.decode())
        self.assertEqual(login["password"], ["pw!"])
        self.assertEqual(login["login"], ["Member"])
        self.assertEqual(json.loads(opener.requests[3].data), {"topic_id": 10338, "raw": "你好世界"})
        self.assertNotIn("User-api-key", opener.requests[3].headers)

    def test_rejected_or_mismatched_login_never_posts(self):
        for response in ({"error": "second factor required"}, {"user": {"username": "different"}}, {}):
            with self.subTest(response=response):
                opener = self.fixture(response)
                with patch("urllib.request.build_opener", return_value=opener), self.assertRaises(PermissionError):
                    forum.post_with_password("10338", "x", "member", "test-only")
                self.assertEqual(len(opener.requests), 2)

    def test_unconfirmed_post_is_not_retried(self):
        opener = self.fixture({"user": {"username": "member"}}, {"errors": ["not posted"]})
        with patch("urllib.request.build_opener", return_value=opener), self.assertRaisesRegex(RuntimeError, "原帖核对"):
            forum.post_with_password("10338", "raw", "member", "pw")
        self.assertEqual(len(opener.requests), 4)



if __name__ == "__main__":
    unittest.main()
