import tempfile
import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import spark_web.server as server
import spark_web.storage as storage
from spark_web.storage import Store


class _Tracker:
    @staticmethod
    def fetch_nervos_talk_projects():
        def post(topic_id, title):
            return {"id": int(topic_id), "title": title,
                    "url": f"https://talk.nervos.org/t/x/{topic_id}", "posts_count": 9}
        return {"classified": {
            "in_progress": [post("10653", "NNCBN")],
            "completion": [post("10212", "Dular"), post("10385", "CKB Builder Lab")],
            "closure": [], "submitted": [], "pending": [], "rejection": [],
            "spark_program": {"pre_review": []},
        }, "all_posts": {}, "errors": []}


class _Automation:
    def __init__(self, store):
        self.store = store
        self.resumed = []
        self.completed = []
        self.reconciled = 0

    def reconcile_notion(self, actor):
        self.reconciled += 1
        return {"matched_projects": [], "verified_jobs": [], "conflicts": []}

    def execute_new_project(self, payload, actor):
        self.resumed.append(str(payload["topic_id"]))
        key = self.store.queue_job(str(payload["topic_id"]), "in_progress", payload)
        self.store.set_step(key, "fund_pool", "verified", ["fund"])
        self.store.set_step(key, "project_list", "verified", ["project"])

    def read_ongoing_base(self, topic_id):
        return {"topic_id": str(topic_id), "project_name": "project", "team": "team",
                "start_date": "2026-01-01", "wallet": "ckb1...", "total_funding": "$1",
                "funding_amount": "$1", "expected_completion": "2026-02-01"}

    def execute_completion(self, payload, actor):
        self.completed.append(str(payload["topic_id"]))


class RefreshTests(unittest.TestCase):
    def test_whitelisted_applicant_article_does_not_seed_or_queue_notion_workflow(self):
        article = {"id": 10101, "title": "UGMP 开发心得", "url": "https://talk.nervos.org/t/x/10101",
                   "author": "builder", "tags": ["spark-program"], "posts_count": 3}
        class Tracker:
            @staticmethod
            def fetch_nervos_talk_projects():
                return {"all_posts": {"spark": [article]}, "classified": {"completion": [article],
                        "in_progress": [], "closure": [], "spark_program": {}}, "errors": []}
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "test.db")
            with patch.object(server, "STORE", store), patch.object(server, "tracker_module", return_value=Tracker), \
                 patch.object(server, "NOTION_TOKEN", ""), patch.object(server.legacy, "save_cache"), \
                 patch.object(server.legacy, "load_data", return_value={"board_whitelist": {"10101": "completion"}}), \
                 patch.object(server, "fetch_topic", side_effect=AssertionError("related post must not be fetched for automation")):
                result = server.refresh_board("test")
            self.assertEqual(store.query("SELECT * FROM projects"), [])
            self.assertEqual(store.query("SELECT * FROM sync_jobs"), [])
            self.assertEqual(result["transitions"], [])

    def test_timeline_refresh_http_route_does_not_reconcile_notion(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with patch.object(server, "TEAM_MODE", False), \
                 patch.object(server, "tracker_module", return_value=_Tracker), \
                 patch.object(server, "automation", side_effect=AssertionError("Notion must not run")), \
                 patch.object(server.legacy, "save_cache") as save_cache, \
                 patch.object(server.legacy, "load_data", return_value={}), \
                 patch.object(server.STORE, "audit"):
                request = urllib.request.Request(url + "/api/timeline/refresh", data=b"{}",
                                                 method="POST", headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=5) as response:
                    result = json.load(response)
                self.assertEqual(response.status, 200)
                self.assertEqual(len(result["timeline"]), 3)
                save_cache.assert_called_once()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_refresh_resumes_partial_project_and_migrates_existing_completions(self):
        original_data_path = storage.DATA_PATH
        with tempfile.TemporaryDirectory() as tmp:
            storage.DATA_PATH = Path(tmp) / "missing.json"
            store = Store(Path(tmp) / "test.db")
            for topic_id, title in (("10653", "NNCBN"), ("10212", "Dular"), ("10385", "CKB Builder Lab")):
                store.upsert_project(topic_id, title, f"https://talk.nervos.org/t/x/{topic_id}", "in_progress", 2)
            key = store.queue_job("10653", "in_progress", {"topic_id": "10653", "project_name": "NNCBN"})
            store.set_step(key, "fund_pool", "verified", ["fund"])
            store.set_step(key, "project_list", "failed", [], "interrupted")
            runner = _Automation(store)
            try:
                with patch.object(server, "STORE", store), patch.object(server, "tracker_module", return_value=_Tracker), \
                     patch.object(server, "automation", return_value=runner), patch.object(server, "AUTO_NOTION", True), \
                     patch.object(server, "NOTION_TOKEN", "secret"), patch.object(server, "fetch_topic", return_value={}), \
                     patch.object(server, "extract_completion", side_effect=lambda topic, base, status: {**base, "completion_date": "2026-09-20", "status": status}), \
                     patch.object(server.legacy, "save_cache"), patch.object(server.legacy, "load_data", return_value={}):
                    result = server.refresh_board("test")
            finally:
                storage.DATA_PATH = original_data_path
        self.assertEqual(runner.resumed, ["10653"])
        self.assertEqual(set(runner.completed), {"10212", "10385"})
        self.assertEqual(set(result["transitions"]), {"10212", "10385"})
        self.assertFalse(result["errors"])
        self.assertEqual(runner.reconciled, 1)

    def _run_refresh(self, store, runner):
        patches = [
            patch.object(server, "STORE", store),
            patch.object(server, "tracker_module", return_value=_Tracker),
            patch.object(server, "automation", return_value=runner),
            patch.object(server, "AUTO_NOTION", True),
            patch.object(server, "NOTION_TOKEN", "secret"),
            patch.object(server, "fetch_topic", return_value={}),
            patch.object(server, "extract_weekly_update", return_value=None),
            patch.object(server, "extract_completion",
                         side_effect=lambda topic, base, status: {**base, "completion_date": "2026-09-20",
                                                                  "status": status}),
            patch.object(server.legacy, "save_cache"),
            patch.object(server.legacy, "load_data", return_value={}),
        ]
        with patches[0]:
            for item in patches[1:]:
                item.start()
            try:
                return server.refresh_board("test")
            finally:
                for item in reversed(patches):
                    item.stop()

    def test_refresh_skips_settled_completion_and_closure_jobs(self):
        """已有 verified/ignored 状态作业的 topic，刷新不得再为其排队同类迁移作业
        （completion/closure 共用同一入口，按 bucket workflow 匹配）。"""
        original_data_path = storage.DATA_PATH
        with tempfile.TemporaryDirectory() as tmp:
            storage.DATA_PATH = Path(tmp) / "missing.json"
            store = Store(Path(tmp) / "test.db")
            for topic_id, title in (("10212", "Dular"), ("10385", "CKB Builder Lab")):
                store.upsert_project(topic_id, title, f"https://talk.nervos.org/t/x/{topic_id}",
                                     "in_progress", 2)
            key = store.queue_job("10212", "completion")
            store.set_step(key, "completion", "verified", ["page"])
            key = store.queue_job("10385", "completion")
            store.set_job_status(key, "ignored", "用户手动忽略")
            runner = _Automation(store)
            try:
                result = self._run_refresh(store, runner)
                queued = store.query("""SELECT * FROM sync_jobs
                                        WHERE workflow IN ('completion','closure') AND status='queued'""")
            finally:
                storage.DATA_PATH = original_data_path
        self.assertEqual(runner.completed, [])
        self.assertEqual(result["transitions"], [])
        self.assertFalse(result["errors"])
        # 没有新排队任何 completion/closure 作业
        self.assertEqual(queued, [])

    def test_refresh_does_not_resume_ignored_in_progress_job(self):
        """被用户忽略的入库作业（哪怕还有失败步骤）刷新时不得恢复执行，防止重复建页。"""
        original_data_path = storage.DATA_PATH
        with tempfile.TemporaryDirectory() as tmp:
            storage.DATA_PATH = Path(tmp) / "missing.json"
            store = Store(Path(tmp) / "test.db")
            store.upsert_project("10653", "NNCBN", "https://talk.nervos.org/t/x/10653",
                                 "in_progress", 2)
            key = store.queue_job("10653", "in_progress", {"topic_id": "10653", "project_name": "NNCBN"})
            store.set_step(key, "project_list", "failed", [], "interrupted")
            store.set_job_status(key, "ignored", "用户手动忽略")
            runner = _Automation(store)
            try:
                result = self._run_refresh(store, runner)
            finally:
                storage.DATA_PATH = original_data_path
        self.assertEqual(runner.resumed, [])
        self.assertFalse(result["new_projects"])
        self.assertFalse(result["errors"])

    def test_refresh_stops_all_writes_when_identity_reconcile_fails(self):
        class BrokenAutomation(_Automation):
            def reconcile_notion(self, actor):
                raise RuntimeError("schema unavailable")

        original_data_path = storage.DATA_PATH
        with tempfile.TemporaryDirectory() as tmp:
            storage.DATA_PATH = Path(tmp) / "missing.json"
            store = Store(Path(tmp) / "test.db")
            store.upsert_project("10653", "NNCBN", "https://talk.nervos.org/t/x/10653",
                                 "in_progress", 2)
            key = store.queue_job("10653", "in_progress", {"topic_id": "10653",
                                                             "project_name": "NNCBN"})
            store.set_step(key, "project_list", "failed", [], "interrupted")
            runner = BrokenAutomation(store)
            try:
                result = self._run_refresh(store, runner)
            finally:
                storage.DATA_PATH = original_data_path
        self.assertEqual(result["status"], "partial")
        self.assertTrue(any("已停止本轮自动写入" in error for error in result["errors"]))
        self.assertEqual(runner.resumed, [])
        self.assertEqual(runner.completed, [])


if __name__ == "__main__":
    unittest.main()
