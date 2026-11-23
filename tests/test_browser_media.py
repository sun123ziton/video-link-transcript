import io
import sys
import tempfile
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import Error, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import browser_media
from transcribe import online_source


class BrowserMediaTests(unittest.TestCase):
    def test_retry_only_recoverable_errors(self):
        for reason in ("Unsupported URL", "HTTP Error 401", "Please sign in", "HTTP Error 403"):
            self.assertTrue(browser_media.browser_retry(reason))
        for reason in ("DRM, sign in required", "DNS lookup failed", "disk full"):
            self.assertFalse(browser_media.browser_retry(reason))

    def test_blob_requires_one_player_and_one_manifest(self):
        url = "https://media.example/file.m3u8?sig=abc"
        choice = dict(drm=False, live=False, src="blob:x", manifests=[url], players=1)
        self.assertEqual(browser_media.chosen_media(choice), url)
        for change in (dict(drm=True), dict(live=True), dict(players=2), dict(manifests=[])):
            with self.assertRaises(ValueError):
                browser_media.chosen_media({**choice, **change})

    def test_login_redirect_embedded_player_and_cookie_protected_file(self):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", ""))
            audio.writeframes(bytes(16000 * 2 * 12))
        payload = buf.getvalue()
        unauthenticated = []

        class Site(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                logged_in = self.headers.get("Cookie") == "fixture=ok"
                content_type = "text/html; charset=utf-8"
                if self.path == "/share" and not logged_in:
                    unauthenticated.append(True)
                    self.send_response(302)
                    self.send_header("Location", "/login")
                    self.end_headers()
                    return
                if self.path == "/login":
                    body = b"<a href='/enter'>Login</a>"
                elif self.path == "/enter":
                    self.send_response(302)
                    self.send_header("Set-Cookie", "fixture=ok; HttpOnly; Path=/")
                    self.send_header("Location", "/share")
                    self.end_headers()
                    return
                elif self.path == "/share" and logged_in:
                    body = b"<title>Lesson</title><iframe src='/player'></iframe>"
                elif self.path == "/player" and logged_in:
                    body = b"<audio src='/lesson.wav'></audio><button onclick='document.querySelector(\"audio\").play()'>Play</button>"
                elif self.path == "/lesson.wav" and logged_in:
                    body, content_type = payload, "audio/wav"
                else:
                    self.send_error(403)
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        with sync_playwright() as pw, tempfile.TemporaryDirectory() as temp:
            for channel in ("msedge", "chrome", "chromium"):
                try:
                    context = pw.chromium.launch_persistent_context(temp + "/profile", channel=channel)
                    break
                except Error as exc:
                    if not any(hint in str(exc).lower() for hint in ("not found", "doesn't exist")):
                        raise
            else:
                self.skipTest("Browser integration requires Edge, Chrome or Playwright Chromium")
            site = ThreadingHTTPServer(("127.0.0.1", 0), Site)
            server = threading.Thread(target=site.serve_forever, daemon=True)
            server.start()
            self.addCleanup(site.server_close)
            self.addCleanup(site.shutdown)
            url = f"http://127.0.0.1:{site.server_port}/share"
            try:
                page = context.pages[0]
                def report(stage, pct):
                    if stage.startswith("请在浏览器里登录"):
                        page.get_by_text("Login").click()
                        player = page.frame_locator("iframe")
                        player.get_by_text("Play", exact=True).click()
                        frame = page.frames[-1]
                        frame.wait_for_function("document.querySelector('audio').currentTime > 0")
                        frame.evaluate(browser_media.PLAYER_PICKER)
                        player.get_by_text("转写当前播放").click()

                folder = Path(temp)
                state = {}
                with patch.object(browser_media, "open_player", side_effect=lambda *args: args[-1](page)) as opened:
                    media, captions = online_source(url, folder, state, None, True, report, threading.Event(), False)
                opened.assert_called_once()
                self.assertTrue(unauthenticated)
                self.assertEqual(media.read_bytes(), payload)
                self.assertIsNone(captions)
                self.assertEqual(state["title"], "Lesson")
                self.assertNotIn("fixture=ok", (folder / "state.json").read_text())
            finally:
                context.close()
