import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
