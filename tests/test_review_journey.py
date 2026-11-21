"""Walk through the member-facing HTTP journey against a real local server."""
import hashlib
import http.cookiejar
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
from spark_web.storage import Store


class ReviewJourneyTests(unittest.TestCase):
    def test_invited_member_login_read_review_and_csrf(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(path=Path(directory) / "fixture.db")
            draft_id = store.save_review_draft("123", "Alpha", "https://talk.nervos.org/t/alpha/123",
                                               "通过", "拟回复正文")
            with store.transaction() as db:
                db.execute("INSERT INTO users VALUES(?,?,?,?,?,?)",
                           ("publisher", store.hash_password("local-fixture"), "operator", "xingtianchunyan",
                            1, server.utcnow()))
                db.execute("INSERT INTO sessions VALUES(?,?,?,?,?)",
                           (hashlib.sha256(b"old-fixture-token").hexdigest(), "publisher", "old-csrf",
                            "9999-01-01", server.utcnow()))
            state = {"meetings": [], "board": {}, "tasks": [], "jobs": [], "templates": {},
                     "characters": [], "links": {}, "knowledge": {}}
            patches = [patch.object(server, "STORE", store), patch.object(server, "TEAM_MODE", True),
                       patch.object(server.legacy, "public_state", return_value=state),
                       patch.object(server.legacy, "timeline", return_value=[]),
                       patch.object(server.project_tracking, "load", return_value={"projects": []}),
                       patch.object(server, "_topic_text_cached", return_value={"posts": [
                           {"post_number": 1, "username": "author", "text": "Alpha 项目提案内容"}]}),
                       patch.object(server, "verify_talk_credentials",
                                    side_effect=lambda name, pwd: pwd == "fixture-pass")]
            for p in patches:
                p.start()
            web_server = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=web_server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{web_server.server_address[1]}"
            client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

            def call(path, data=None, csrf=None, cookie=None):
                headers = {"Content-Type": "application/json"}
                if csrf:
                    headers["X-CSRF-Token"] = csrf
                if cookie:
                    headers["Cookie"] = cookie
                request = urllib.request.Request(base + path,
                                                 data=json.dumps(data).encode() if data is not None else None,
                                                 headers=headers, method="POST" if data is not None else "GET")
                try:
                    with client.open(request, timeout=5) as response:
                        return response.status, json.load(response)
                except urllib.error.HTTPError as error:
                    return error.code, json.load(error)

            try:
                self.assertEqual(call("/api/login/auto", {})[0], 403)
                self.assertEqual(call("/api/drafts?status=pending")[0], 401)
                self.assertEqual(call("/api/drafts?status=pending", cookie="spark_session=old-fixture-token")[0], 401)
                self.assertEqual(call("/api/login", {"username": "Hanssen", "password": "wrong"})[0], 403)
                self.assertEqual(call("/api/login", {"username": "outsider", "password": "fixture-pass"})[0], 403)
                status, login = call("/api/login", {"username": "@Hanssen", "password": "fixture-pass"})
                self.assertEqual(status, 200)
                status, home = call("/api/workbench")
                self.assertEqual(status, 200)
                self.assertEqual((home["session"]["username"], home["session"]["role"]), ("hanssen", "viewer"))
                status, listing = call("/api/drafts?status=pending")
                self.assertEqual(status, 200)
                self.assertEqual(listing["drafts"][0]["id"], draft_id)
                # The draft-only mode does not call the remote proposal fetch.
                status, draft = call(f"/api/drafts/item?id={draft_id}&proposal=0")
                self.assertEqual(status, 200)
                self.assertNotIn("proposal", draft)
                server._topic_text_cached.assert_not_called()
                csrf = login["csrf_token"]
                edit = {"id": draft_id, "revision": draft["revision"], "content": "🚀更新回复"}
                self.assertEqual(call("/api/drafts/edit", edit)[0], 403)
                status, edited = call("/api/drafts/edit", edit, csrf)
                self.assertEqual(status, 200)
                self.assertEqual(call("/api/drafts/edit", edit, csrf)[0], 409)
                status, annotated = call("/api/drafts/comment", {"id": draft_id, "revision": edited["revision"],
                                        "start": 1, "end": 3, "body": "核对措辞"}, csrf)
                self.assertEqual(status, 200)
                self.assertEqual((annotated["comments"][0]["quote"], annotated["comments"][0]["author"]),
                                 ("更新", "hanssen"))
                status, detail = call(f"/api/drafts/item?id={draft_id}")
                self.assertEqual(status, 200)
                self.assertEqual(detail["draft_content"], "🚀更新回复")
                self.assertEqual(detail["comments"], annotated["comments"])
                self.assertIn("Alpha", detail["proposal"]["posts"][0]["text"])
                self.assertEqual(call("/api/drafts/reviewed", {"id": draft_id})[0], 403)
                self.assertEqual(call("/api/drafts/reviewed", {"id": draft_id, "revision": edited["revision"]}, csrf)[0], 200)
                self.assertEqual(call("/api/drafts?status=pending")[1]["drafts"], [])
                self.assertEqual(store.review_draft(draft_id)["reviewed_by"], "hanssen")
                payload = {"topic_id": "123", "raw": "test-only", "confirmation": "正式发布"}
                with patch.object(server, "post_with_password", return_value={"id": 1, "post_number": 2}) as publish:
                    self.assertEqual(call("/api/forum/post", {**payload, "password": "fixture-pass"},
                                          login["csrf_token"])[0], 403)
                    self.assertEqual(call("/api/login", {"username": "xingtianchunyan", "password": "fixture-pass"})[0], 200)
                    csrf = call("/api/workbench")[1]["session"]["csrf_token"]
                    self.assertEqual(call("/api/forum/post", payload, csrf)[0], 400)
                    self.assertEqual(call("/api/forum/post", {**payload, "password": "fixture-pass",
                                                             "username": "other-user"}, csrf)[0], 200)
                    publish.assert_called_once_with("123", "test-only", "xingtianchunyan", "fixture-pass")
            finally:
                web_server.shutdown()
                web_server.server_close()
                thread.join(timeout=3)
                for p in reversed(patches):
                    p.stop()


if __name__ == "__main__":
    unittest.main()
