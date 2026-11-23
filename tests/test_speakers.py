import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from speakers import global_ids, label_cues
from transcribe import caption_cues, run_job


class SpeakerTests(unittest.TestCase):
    def test_returning_speaker_keeps_number_across_blocks(self):
        centers = []
        vectors = {9: np.array([1., 0.]), 2: np.array([0., 1.])}
        self.assertEqual(global_ids([9, 2], vectors, centers), {9: 0, 2: 1})
        self.assertEqual(global_ids([0, 1], {0: vectors[2], 1: vectors[9]}, centers), {0: 1, 1: 0})

    def test_word_timing_splits_speakers_in_one_transcription_segment(self):
        turns = [(0, 900, 1), (1000, 1900, 2), (2000, 3000, 1)]
        words = {"0": [[0, 850, "你好。"], [1020, 1880, "早。"], [2000, 2900, "再见。"]]}
        labeled = label_cues([(0, 3000, "你好。早。再见。")], words, turns)
        self.assertEqual([r[2] for r in labeled], ["发言人1：你好。", "发言人2：早。", "发言人1：再见。"])

    def test_unknown_and_mixed_subtitle_preserve_text(self):
        cues = [(50, 1000, "两人对话"), (2000, 3000, "画面提示")]
        self.assertEqual(label_cues(cues, {}, [(0, 530, 1), (540, 1000, 2)]),
                         [(50, 1000, "发言人1 / 发言人2：两人对话"), (2000, 3000, "发言人待确认：画面提示")])

    def test_explicit_webvtt_voice_labels(self):
        vtt = ("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n<v 主持>你好\n\n"
               "00:00:01.000 --> 00:00:02.000\n<v 嘉宾>你好呀\n\n"
               "00:00:02.000 --> 00:00:03.000\n<v 主持>请坐\n")
        self.assertEqual([c[2] for c in caption_cues(vtt, "vtt", False, True)],
                         ["发言人1：你好", "发言人2：你好呀", "发言人1：请坐"])
        self.assertEqual(caption_cues(vtt, "vtt", False, False)[0][2], "你好")

    def test_named_sidecar_exports_without_an_audio_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            media = root / "dialogue.mp4"
            media.touch()
            media.with_suffix(".vtt").write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n<v A>你好\n\n"
                "00:00:01.000 --> 00:00:02.000\n<v B>早上好\n", encoding="utf-8")
            for enabled in (True, False):
                plain, timed, _, _, n = run_job(str(media), root / "out", "base", "zh", False,
                                                lambda *_: None, Event(), speakers=enabled)
                self.assertEqual(n, 2)
                wanted = ["发言人1：你好", "发言人2：早上好"] if enabled else ["你好", "早上好"]
                self.assertEqual(plain.read_text(encoding="utf-8").splitlines(), wanted)
                self.assertEqual(timed.read_text(encoding="utf-8").splitlines()[2::4], wanted)
