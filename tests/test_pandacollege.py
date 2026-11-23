import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pandacollege import is_replay, select_media


class ReplayTests(unittest.TestCase):
    def test_only_exact_replay_pages_open_the_browser(self):
        root = "https://fclive.pandacollege.cn"
        self.assertTrue(is_replay(root + "/st-live/playback?param=example"))
        self.assertFalse(is_replay(root + "/st-live/live?param=example"))
        self.assertFalse(is_replay(root + "/st-live/playback"))
        self.assertFalse(is_replay(root + ".example.org/st-live/playback?param=example"))

    def test_blob_player_preserves_signed_vod_manifest(self):
        replay = "https://media.example/lesson/playlist_eof.m3u8?signature=x%2Fy"
        other = "https://media.example/another.m3u8"
        self.assertEqual(select_media("blob:https://example/video", [replay, other]), replay)
        self.assertIsNone(select_media("", [replay]))
        self.assertIsNone(select_media("blob:x", [other, "https://media.example/third.m3u8"]))
        direct = "https://media.example/lesson.mp4"
        self.assertEqual(select_media(direct, [replay]), direct)
