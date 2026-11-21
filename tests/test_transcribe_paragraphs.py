from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from spark_web.config import ROOT

_spec = importlib.util.spec_from_file_location("transcribe_tool", ROOT / "tools" / "transcribe.py")
transcribe_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(transcribe_tool)


class ParagraphGroupingTests(unittest.TestCase):
    def test_merge_short_gaps_split_long_pauses(self):
        segments = [
            {"start": 0.0, "end": 2.0, "text": "大家好"},
            {"start": 2.5, "end": 4.0, "text": "这里是委员会"},
            {"start": 3.9, "end": 5.0, "text": ""},          # 空文本跳过
            {"start": 10.0, "end": 12.0, "text": "今天讨论拨款"},
        ]
        paragraphs = transcribe_tool.group_paragraphs(segments, gap=1.5)
        self.assertEqual(len(paragraphs), 2)
        self.assertEqual(paragraphs[0]["text"], "大家好这里是委员会")
        self.assertEqual(paragraphs[0]["start"], 0.0)
        self.assertEqual(paragraphs[1]["text"], "今天讨论拨款")
        self.assertEqual(paragraphs[1]["start"], 10.0)

    def test_empty_input(self):
        self.assertEqual(transcribe_tool.group_paragraphs([]), [])

    def test_timestamp_format(self):
        self.assertEqual(transcribe_tool.fmt_ts(0), "00:00")
        self.assertEqual(transcribe_tool.fmt_ts(75.4), "01:15")

    def test_paragraph_text_has_timestamp_marker(self):
        segments = [{"start": 65.0, "end": 70.0, "text": "决议通过"}]
        paragraphs = transcribe_tool.group_paragraphs(segments)
        line = f"【{transcribe_tool.fmt_ts(paragraphs[0]['start'])}】{paragraphs[0]['text']}"
        self.assertEqual(line, "【01:05】决议通过")

    def test_speaker_change_forces_new_paragraph(self):
        segments = [
            {"start": 0.0, "end": 2.0, "text": "我们同意这个提案", "speaker_label": "发言人A"},
            {"start": 2.5, "end": 4.0, "text": "补充一点风险", "speaker_label": "发言人B"},
            {"start": 4.5, "end": 6.0, "text": "同意补充", "speaker_label": "发言人A"},
        ]
        paragraphs = transcribe_tool.group_paragraphs(segments, gap=10.0)
        self.assertEqual(len(paragraphs), 3)
        self.assertEqual(paragraphs[0]["speaker_label"], "发言人A")
        self.assertEqual(paragraphs[1]["speaker_label"], "发言人B")
        self.assertEqual(paragraphs[2]["speaker_label"], "发言人A")

    def test_speaker_label_carried_through(self):
        segments = [
            {"start": 0.0, "end": 2.0, "text": "第一句", "speaker_label": "发言人A"},
            {"start": 2.3, "end": 4.0, "text": "第二句", "speaker_label": "发言人A"},
        ]
        paragraphs = transcribe_tool.group_paragraphs(segments, gap=1.5)
        self.assertEqual(len(paragraphs), 1)
        self.assertEqual(paragraphs[0]["text"], "第一句第二句")
        self.assertEqual(paragraphs[0]["speaker_label"], "发言人A")


if __name__ == "__main__":
    unittest.main()
