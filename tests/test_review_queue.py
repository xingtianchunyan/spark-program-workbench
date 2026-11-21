from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spark_web.storage import ReviewConflict, Store
import spark_web.server as server


class ReviewDraftStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(path=Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_and_list_pending(self):
        draft_id = self.store.save_review_draft("10338", "Wallet", "https://talk.nervos.org/t/x/10338",
                                                "预审", "draft body")
        self.assertGreater(draft_id, 0)
        rows = self.store.review_drafts("pending")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["topic_id"], "10338")
        self.assertEqual(rows[0]["template"], "预审")

    def test_same_topic_template_overwrites(self):
        first = self.store.save_review_draft("10338", "Wallet", "url", "预审", "v1")
        second = self.store.save_review_draft("10338", "Wallet", "url", "预审", "v2")
        self.assertEqual(first, second)
        rows = self.store.review_drafts("pending")
        self.assertEqual(len(rows), 1)
        full = self.store.review_draft(first)
        self.assertEqual(full["draft_content"], "v2")

    def test_mark_reviewed_and_reopen(self):
        draft_id = self.store.save_review_draft("10338", "Wallet", "url", "预审", "v1")
        self.store.mark_review_draft(draft_id, "committee")
        self.assertEqual(self.store.review_drafts("pending"), [])
        reviewed = self.store.review_drafts("reviewed")
        self.assertEqual(reviewed[0]["reviewed_by"], "committee")
        self.store.reopen_review_draft(draft_id)
        self.assertEqual(len(self.store.review_drafts("pending")), 1)

    def test_delete(self):
        draft_id = self.store.save_review_draft("10338", "Wallet", "url", "预审", "v1")
        self.store.delete_review_draft(draft_id)
        self.assertIsNone(self.store.review_draft(draft_id))

    def test_review_comment_survives_edits_moves_and_restart(self):
        draft_id = self.store.save_review_draft("123", "例", "url", "模板", "🚀开头\r\n中文锚点\n结束")
        initial = self.store.review_document(draft_id)
        self.assertNotIn("\r", initial["draft_content"])
        start = initial["draft_content"].index("中文锚点")
        result = self.store.comment_on_review_draft(draft_id, initial["revision"], start, start + 4, " 建议改写 ", "hanssen")
        self.assertEqual(result["comments"][0]["quote"], "中文锚点")
        edited = self.store.edit_review_draft(draft_id, initial["revision"], "添加\n" + initial["draft_content"])
        self.assertEqual(edited["comments"][0]["start_offset"], start + 3)
        detached = self.store.edit_review_draft(draft_id, edited["revision"], "只保留其它正文")
        self.assertIsNone(detached["comments"][0]["start_offset"])
        self.assertEqual(detached["comments"][0]["body"], "建议改写")
        self.assertEqual(Store(path=self.store.path).review_document(draft_id), detached)
        self.store.delete_review_draft(draft_id)
        self.assertEqual(self.store.query("SELECT * FROM review_draft_comments"), [])

    def test_stale_revision_and_reviewed_draft_cannot_be_changed(self):
        draft_id = self.store.save_review_draft("123", "例", "url", "模板", "原稿")
        initial = self.store.review_document(draft_id)
        edited = self.store.edit_review_draft(draft_id, initial["revision"], "第二版")
        with self.assertRaises(ReviewConflict):
            self.store.edit_review_draft(draft_id, initial["revision"], "不能覆盖第二版")
        with self.assertRaises(ReviewConflict):
            self.store.comment_on_review_draft(draft_id, initial["revision"], 0, 1, "过期批注", "hanssen")
        with self.assertRaises(ReviewConflict):
            self.store.mark_review_draft(draft_id, "someone", revision=initial["revision"])
        self.store.mark_review_draft(draft_id, "hanssen", revision=edited["revision"])
        with self.assertRaises(ReviewConflict):
            self.store.edit_review_draft(draft_id, edited["revision"], "不能改")

    def test_generated_candidate_cannot_overwrite_annotated_draft(self):
        draft_id = self.store.save_review_draft("123", "例", "url", "模板", "原稿")
        initial = self.store.review_document(draft_id)
        self.store.comment_on_review_draft(draft_id, initial["revision"], 0, 2, "保留", "hanssen")
        fresh = self.store.save_review_draft("123", "例", "url", "模板", "重新生成")
        self.assertNotEqual(fresh, draft_id)
        self.assertEqual(self.store.review_document(draft_id)["draft_content"], "原稿")
        self.assertEqual(self.store.review_document(draft_id)["comments"][0]["body"], "保留")


class CacheReviewDraftTests(unittest.TestCase):
    def test_cache_review_draft_from_plan(self):
        plan = {"topic_id": "10338", "template_name": "预审",
                "post": {"title": "Wallet Behaviour", "full_url": "https://talk.nervos.org/t/10338"}}
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(path=Path(tmp) / "t.db")
            with patch.object(server, "STORE", store):
                draft_id = server._cache_review_draft(plan, "  draft content  ", "analysis")
                self.assertIsNotNone(draft_id)
                draft = store.review_draft(draft_id)
                self.assertEqual(draft["draft_content"], "draft content")
                self.assertEqual(draft["analysis"], "analysis")
                # 空内容不入队
                self.assertIsNone(server._cache_review_draft(plan, "   "))

    def test_topic_text_cached_uses_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            with patch("spark_web.config.CACHE_PATH", cache_path), \
                 patch("spark_web.server.legacy.topic_text",
                       return_value={"id": "10338", "posts": []}) as fetch:
                first = server._topic_text_cached("10338")
                second = server._topic_text_cached("10338")
                self.assertEqual(first, second)
                self.assertEqual(fetch.call_count, 1)
                # 缓存已持久化
                self.assertIn("topic_text_cache", json.loads(cache_path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
