from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spark_web import legacy
import spark_web.server as server


class ForumBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_path = Path(self.tmp.name) / "spark_task_data.json"
        self.env_path = Path(self.tmp.name) / ".env"

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip_and_secret_preservation(self):
        self.data_path.write_text(json.dumps(
            {"templates": {"t": "x"}, "nervos_password": "keep-me"}), encoding="utf-8")
        with patch("spark_web.legacy.DATA_PATH", self.data_path):
            self.assertIsNone(legacy.forum_binding())
            legacy.set_forum_binding("xingtianchunyan")
            self.assertEqual(legacy.forum_binding(), "xingtianchunyan")
        raw = json.loads(self.data_path.read_text(encoding="utf-8"))
        self.assertEqual(raw["nervos_password"], "keep-me")
        self.assertEqual(raw["nervos_username"], "xingtianchunyan")
        self.assertEqual(raw["templates"], {"t": "x"})

    def test_empty_username_rejected(self):
        with patch("spark_web.legacy.DATA_PATH", self.data_path):
            with self.assertRaises(ValueError):
                legacy.set_forum_binding("   ")

    def test_corrupt_file_treated_as_unbound(self):
        self.data_path.write_text("not-json", encoding="utf-8")
        with patch("spark_web.legacy.DATA_PATH", self.data_path):
            self.assertIsNone(legacy.forum_binding())
            legacy.set_forum_binding("zz_tovarishch")
            self.assertEqual(legacy.forum_binding(), "zz_tovarishch")

    def test_forum_settings_only_reports_presence_of_legacy_password(self):
        self.data_path.write_text(json.dumps({"nervos_username": "member", "nervos_password": "legacy"}),
                                  encoding="utf-8")
        with patch("spark_web.legacy.DATA_PATH", self.data_path), patch.object(server, "_hf_token", return_value=None):
            self.assertEqual(server._forum_settings_payload(),
                             {"talk_username": "member", "talk_password_saved": True, "hf_token_configured": False})


if __name__ == "__main__":
    unittest.main()
