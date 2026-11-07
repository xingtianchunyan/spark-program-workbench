from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from spark_web import legacy


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps({"nervos-network": {"usd": 0.01}}).encode("utf-8")


class LegacyDraftTests(unittest.TestCase):
    def setUp(self):
        legacy._CKBRATE_CACHE = (0.0, None, None)

    def tearDown(self):
        legacy._CKBRATE_CACHE = (0.0, None, None)

    @patch("urllib.request.urlopen", return_value=_Response())
    def test_ckb_rate_initializes_and_reuses_global_cache(self, urlopen):
        self.assertEqual(legacy.get_ckb_usd_rate(), (0.01, 100.0))
        self.assertEqual(legacy.get_ckb_usd_rate(), (0.01, 100.0))
        self.assertEqual(urlopen.call_count, 1)

    def test_same_title_topics_remain_distinct_by_topic_id(self):
        first = legacy.topic_id_from_url("https://talk.nervos.org/t/corven/10001")
        second = legacy.topic_id_from_url("https://talk.nervos.org/t/corven/20002")
        self.assertEqual(first, "10001")
        self.assertEqual(second, "20002")
        self.assertNotEqual(first, second)

    def test_timeline_includes_category_only_classified_posts(self):
        light = {"id": 11062, "title": "Spark Program | Light dApp Kit",
                 "url": "https://talk.nervos.org/t/light/11062", "created_at": "2026-10-28"}
        board = {"all_posts": {}, "classified": {"spark_program": {"pre_review": [light]}}}
        self.assertEqual([post["id"] for post in legacy.timeline(board)], [11062])

    def test_whitelist_cache_retains_target_for_related_posts(self):
        post = {"id": 10101, "author": "builder", "tags": [], "title": "开发心得"}
        board = {"all_posts": {"spark": [post]}, "classified": {"submitted": [post], "spark_program": {}}}
        result = legacy.apply_whitelist(board, {"10101": "completion"})
        self.assertEqual(result["board_whitelist"]["10101"], "completion")
        self.assertEqual(result["classified"]["completion"][0]["id"], 10101)

    def test_project_name_matching_handles_spacing_and_alias_words(self):
        posts = [
            {"id": 11079, "title": "Spark Program | TapFiber: Fiber Payment Intent",
             "url": "https://talk.nervos.org/t/tapfiber/11079"},
            {"id": 11041, "title": "Spark Program | MeterNet: Pay-per-MB Wi-Fi Hotspots Metered by Fiber Payments",
             "url": "https://talk.nervos.org/t/meternet/11041"},
            {"id": 11062, "title": "Spark Program | Light dApp Kit",
             "url": "https://talk.nervos.org/t/light/11062"},
        ]
        board = {"all_posts": {}, "classified": {"pending": posts, "spark_program": {}}}
        with patch.object(legacy, "load_cache", return_value=board):
            self.assertTrue(legacy.find_post_by_project_name("Tap Fiber").endswith("/11079"))
            self.assertTrue(legacy.find_post_by_project_name("Fiber WiFi").endswith("/11041"))
            self.assertTrue(legacy.find_post_by_project_name("Light DApp Kit").endswith("/11062"))


class TemplateEditTests(unittest.TestCase):
    def test_add_edit_and_rename_preserve_unrelated_templates(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(legacy, "DATA_PATH", Path(directory) / "data.json"), \
             patch.object(legacy, "tracker_module", return_value=SimpleNamespace(TEMPLATES={
                 "内置": {"desc": "原说明", "template": "原文"}})):
            legacy.save_template("内置", "内置", "改说明", "改文")
            self.assertEqual(legacy.merged_templates()["内置"]["desc"], "改说明")
            legacy.save_template("新模板", "", "新说明", "新正文")
            legacy.save_template("重命名", "新模板", "新说明", "新正文")
            self.assertEqual(legacy.merged_templates()["内置"]["template"], "改文")
            self.assertEqual(legacy.merged_templates()["重命名"]["template"], "新正文")
            self.assertNotIn("新模板", legacy.merged_templates())
            with self.assertRaisesRegex(ValueError, "已存在"):
                legacy.save_template("内置", "", "", "覆盖")
            with self.assertRaisesRegex(ValueError, "不能为空"):
                legacy.save_template("无内容", "", "", " ")

if __name__ == "__main__":
    unittest.main()
