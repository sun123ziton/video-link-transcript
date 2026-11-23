"""Resolve a Panda College replay through the site's normal, interactive player."""

import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from filelock import FileLock, Timeout

from audio_job import check_pause


def is_replay(url):
    parts = urlsplit(url)
    return (parts.scheme == "https" and parts.netloc.lower() == "fclive.pandacollege.cn"
            and parts.path.rstrip("/") == "/st-live/playback" and bool(parse_qs(parts.query).get("param")))


def select_media(current_src, resources):
    def is_url(url):
        return urlsplit(url).scheme in ("http", "https") and bool(urlsplit(url).hostname)

    if is_url(current_src):
        return current_src
    if not current_src.startswith("blob:"):
        return None
    manifests = [url for url in resources if is_url(url) and urlsplit(url).path.lower().endswith(".m3u8")]
    for url in reversed(manifests):
        if urlsplit(url).path.lower().endswith("/playlist_eof.m3u8"):
            return url
    # Multiple manifests may belong to different renditions or players.
    unique = list(dict.fromkeys(manifests))
    return unique[0] if len(unique) == 1 else None


def _wait_for_player(page, source, report, stop, timeout):
    from playwright.sync_api import Error

    source_id = parse_qs(urlsplit(source).query)["param"]
    page.goto(source, wait_until="commit", timeout=45000)
    deadline = time.monotonic() + timeout
    last_stage = None
    while time.monotonic() < deadline:
        check_pause(stop)
        if page.is_closed():
            raise ValueError("课程浏览器已关闭。点「开始 / 继续」可以重新打开。")
        stage = "请在课程浏览器中完成登录并打开回放"
        if is_replay(page.url) and parse_qs(urlsplit(page.url).query)["param"] == source_id:
            try:
                state = page.evaluate("""() => {
                    const v = document.querySelector('#playback-player video, video#playback-player');
                    const visible = document.body?.innerText || '';
                    return {
                        ready: !!v && Number.isFinite(v.duration) && v.duration > 0,
                        src: v?.currentSrc || '',
                        urls: v ? performance.getEntriesByType('resource').map(r => r.name)
                                   .filter(u => /\\.m3u8(?:[?#]|$)/i.test(u)) : [],
                        login: /微信登录|扫码登录|扫描二维码登录|其他方式登录/.test(visible),
                        denied: visible.includes('无观看权限'),
                        title: document.title,
                        agent: navigator.userAgent
                    };
                }""")
            except Error:
                # Normal QR login can replace the document between polls.
                if page.is_closed():
                    raise ValueError("课程浏览器已关闭，请重新开始。") from None
                state = None
            if state:
                if state["login"]:
                    stage = "请在弹出的课程浏览器里扫码登录"
                elif state["denied"]:
                    stage = "平台提示无观看权限，请在课程浏览器中确认账号"
                else:
                    stage = "等待回放加载；若有播放按钮，请点击播放"
                    if state["ready"]:
                        media = select_media(state["src"], state["urls"])
                        if media:
                            return media, state["title"] or "课程回放", state["agent"]
        if stage != last_stage:
            report(stage, None)
            last_stage = stage
        try:
            page.wait_for_timeout(500)
        except Error:
            if page.is_closed():
                raise ValueError("课程浏览器已关闭，请重新开始。") from None
            raise
    raise ValueError("等待课程回放超时。请重试，并在弹出的浏览器中登录、确认有观看权限后播放。")


def resolve_replay(source, report, stop):
    from playwright.sync_api import Error, sync_playwright

    if not is_replay(source):
        raise ValueError("请输入有效的课程回放分享链接。")
    profile = Path.home() / ".video-link-transcript" / "pandacollege-browser"
    profile.parent.mkdir(parents=True, exist_ok=True)
    channels = ("msedge", "chrome", None) if sys.platform == "win32" else ("chrome", None)
    report("正在打开课程浏览器", None)
    try:
        lock = FileLock(str(profile) + ".lock").acquire(timeout=0)
    except Timeout as exc:
        raise ValueError("另一个任务正在使用课程浏览器，请先完成该窗口的登录或播放。") from exc
    with lock, sync_playwright() as pw:
        for channel in channels:
            check_pause(stop)
            try:
                browser = pw.chromium.launch_persistent_context(
                    str(profile), channel=channel, headless=False, viewport=None)
                break
            except Error as exc:
                if "not found" not in str(exc).lower() and "doesn't exist" not in str(exc).lower():
                    raise ValueError("课程浏览器启动失败，请关闭工具之前弹出的课程窗口后重试。") from exc
        else:
            raise ValueError("未找到可用浏览器。请安装 Edge、Chrome 或执行 python -m playwright install chromium。")
        try:
            page = browser.pages[0] if browser.pages else browser.new_page()
            page.add_init_script("performance.setResourceTimingBufferSize(2000)")
            page.bring_to_front()
            return _wait_for_player(page, source, report, stop, timeout=300)
        finally:
            browser.close()
