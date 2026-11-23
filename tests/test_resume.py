import json
import sys
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audio_job import Paused, RATE, job_paths, recognize


class ResumeTests(unittest.TestCase):
    def test_partial_chunk_is_replayed_without_overwriting_saved_text(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with wave.open(str(root / "audio.wav"), "wb") as audio:
                audio.setparams((1, 2, RATE, 0, "NONE", ""))
                audio.writeframes(bytes(70 * RATE * 2))

            stop = threading.Event()
            calls = []

            def recognize_chunk(samples, **kwargs):
                calls.append(len(samples))
                def generate():
                    yield SimpleNamespace(start=0.10, end=0.50, text="保留")
                    if len(calls) == 2:
                        stop.set()
                    yield SimpleNamespace(start=0.60, end=1.00, text="下一句")
                return generate(), SimpleNamespace(language="zh")

            with patch("faster_whisper.WhisperModel") as model:
                model.return_value.transcribe.side_effect = recognize_chunk
                with self.assertRaises(Paused):
                    recognize(root, {}, "small", "zh", lambda *_: None, stop)
                checkpoint = json.loads((root / "state.json").read_text(encoding="utf-8"))
                self.assertEqual(2, len(checkpoint["cues"]))
                cursor = checkpoint["cursor"]
                stop.clear()
                cues, _, _ = recognize(root, checkpoint, "small", "zh", lambda *_: None, stop)
            self.assertEqual(calls[1], calls[2])
            self.assertEqual(70 * RATE, checkpoint["cursor"])
            self.assertEqual(4, len(cues))
            self.assertEqual(round(1000 * cursor / RATE) + 100, cues[2][0])

    def test_replaced_local_source_does_not_reuse_old_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            media = root / "same.wav"
            media.write_bytes(b"first")
            _, before = job_paths(str(media), root, "small", "zh", False)
            media.write_bytes(b"replaced")
            _, after = job_paths(str(media), root, "small", "zh", False)
            self.assertNotEqual(before, after)
