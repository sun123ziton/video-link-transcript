import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from completed_history import completed_exports
from transcribe import save_result


class HistoryTests(unittest.TestCase):
    def test_previous_and_new_exports_survive_directory_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("completed_history.DATABASE", root / "config" / "history.sqlite3"):
                old = root / "previous" / "昨晚的课-ab12cd34"
                old.mkdir(parents=True)
                for ext in ("srt", "txt"):
                    (old / f"transcript.{ext}").touch()
                partial = old.parent / "未完成"
                partial.mkdir()
                (partial / "transcript.txt").touch()
                title = "今天的课" * 20
                plain, _ = save_result(root / "new", title, [(0, 1000, "你好")])
                rows, warning = completed_exports([old.parent, plain.parent.parent])
                self.assertEqual(warning, "")
                self.assertEqual({r.folder: r.title for r in rows}, {old: "昨晚的课", plain.parent: title})
                self.assertEqual(completed_exports([root / "another"]), (rows, ""))

    def test_index_failure_keeps_output_findable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("completed_history.connect", side_effect=sqlite3.OperationalError("read-only")):
                plain, timed = save_result(root, "test", [(0, 1000, "你好")])
                rows, warning = completed_exports([root])
                self.assertTrue(plain.is_file() and timed.is_file())
                self.assertEqual([row.folder for row in rows], [plain.parent])
                self.assertTrue(warning)
