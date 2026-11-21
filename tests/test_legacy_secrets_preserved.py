# -*- coding: utf-8 -*-
"""回归测试：save_data 的 load→save 往返不得抹掉密文字段（Talk 绑定 / AI Key）。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from spark_web import legacy


class TestSaveDataPreservesSecrets(unittest.TestCase):
    def test_roundtrip_keeps_secret_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_path = Path(tmp) / "data.json"
            original = {
                "templates": [{"name": "t1"}],
                "nervos_username": "xingtianchunyan",
                "nervos_password": "talk-password",
                "kimi_api_key": "kimi-key",
                "ai_api_key": "ai-key",
            }
            data_path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(legacy, "DATA_PATH", data_path):
                self.assertIsNone(legacy.load_data().get("nervos_username"))
                legacy.save_data(legacy.load_data())
                saved = json.loads(data_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["nervos_username"], "xingtianchunyan")
            self.assertEqual(saved["nervos_password"], "talk-password")
            self.assertEqual(saved["kimi_api_key"], "kimi-key")
            self.assertEqual(saved["ai_api_key"], "ai-key")
            self.assertEqual(saved["templates"], [{"name": "t1"}])

    def test_replace_section_keeps_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_path = Path(tmp) / "data.json"
            data_path.write_text(json.dumps(
                {"meetings": [], "nervos_username": "u1", "nervos_password": "p1"},
                ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(legacy, "DATA_PATH", data_path):
                legacy.replace_section("meetings", [{"id": 1}])
                saved = json.loads(data_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["nervos_username"], "u1")
            self.assertEqual(saved["nervos_password"], "p1")
            self.assertEqual(saved["meetings"], [{"id": 1}])


if __name__ == "__main__":
    unittest.main()
