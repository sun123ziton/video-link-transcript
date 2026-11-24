import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from transcribe import export_cues, run_job


class ChineseScriptTests(unittest.TestCase):
    def test_caption_export_formats_both_files(self):
        with tempfile.TemporaryDirectory() as temp, patch("completed_history.DATABASE", Path(temp) / "history.db"):
            root = Path(temp)
            media = root / "course.mp4"
            media.touch()
            media.with_suffix(".srt").write_text("1\n00:00:01,000 --> 00:00:02,100\n發展软件 API\n", encoding="utf-8")
            for setting, words in (({}, "发展软件 API"), ({"chinese_script": "traditional"}, "發展軟件 API"),
                                   ({"chinese_script": "original"}, "發展软件 API")):
                plain, timed, *_ = run_job(str(media), root, "small", None, False, lambda *_: None,
                                          Event(), speakers=False, **setting)
                self.assertEqual(plain.read_text(encoding="utf-8"), words + "\n")
                self.assertIn("00:00:01,000 --> 00:00:02,100\n" + words, timed.read_text(encoding="utf-8"))

    def test_non_chinese_and_speaker_timing(self):
        cues = [(150, 930, "发言人1：電腦與網絡")]
        self.assertEqual(export_cues(cues, "zh-Hant", "simplified"), [(150, 930, "发言人1：电脑与网络")])
        self.assertEqual(export_cues(cues, "ja", "simplified"), cues)
        japanese = [(0, 200, "発言の記録")]
        self.assertEqual(export_cues(japanese, "und", "simplified"), japanese)
