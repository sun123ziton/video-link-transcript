"""Interactive playback in a dedicated, local browser profile."""

import base64
import hashlib
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from filelock import FileLock, Timeout
from playwright.sync_api import Error, sync_playwright

from audio_job import check_pause


def open_player(profile, report, stop, wait):
    profile.parent.mkdir(parents=True, exist_ok=True)
    channels = ("msedge", "chrome", None) if sys.platform == "win32" else ("chrome", None)
    report("正在打开视频浏览器", None)
    try:
        lock = FileLock(str(profile) + ".lock").acquire(timeout=0)
    except Timeout as exc:
        raise ValueError("另一个任务正在使用视频浏览器，请先完成该窗口的登录或播放。") from exc
    with lock, sync_playwright() as pw:
        for channel in channels:
            check_pause(stop)
            try:
                browser = pw.chromium.launch_persistent_context(
                    str(profile), channel=channel, headless=False, viewport=None)
                break
            except Error as exc:
                if "not found" not in str(exc).lower() and "doesn't exist" not in str(exc).lower():
                    raise ValueError("视频浏览器启动失败，请关闭工具之前弹出的视频窗口后重试。") from exc
        else:
            raise ValueError("未找到可用浏览器。请安装 Edge、Chrome 或执行 python -m playwright install chromium。")
        try:
            page = browser.pages[0] if browser.pages else browser.new_page()
            browser.add_init_script("performance.setResourceTimingBufferSize(2000)")
            page.bring_to_front()
            return wait(page)
        finally:
            browser.close()


def browser_retry(error):
    message = str(error).lower()
    if any(word in message for word in ("drm", "copyright", "geo-restricted")):
        return False
    return any(word in message for word in (
        "unsupported url", "no video formats", "no video found", "no formats found",
        "login", "log in", "sign in", "sign-in", "logged in", "authentication",
        "http error 401", "http error 403", "use --cookies", "require cookies", "登录",
    ))


# The button is present only in the frame with one actively playing element.
# Bind the choice to the element at click time, including on pages with ads.
PLAYER_PICKER = r"""() => {
    const active = () => [...document.querySelectorAll('video,audio')].filter(
        v => !v.paused && !v.ended && v.readyState >= 2 && v.currentSrc);
    let button = document.querySelector('button[data-transcript-picker]');
    if (!button && active().length === 1) {
        button = document.createElement('button');
        button.type = 'button';
        button.dataset.transcriptPicker = '';
        button.textContent = '转写当前播放';
        button.style.cssText = 'all:initial;position:fixed;right:12px;top:8px;z-index:2147483647;'
          + 'padding:9px 14px;background:#163d78;color:white;border-radius:6px;'
          + 'font:600 15px system-ui;cursor:pointer';
        button.addEventListener('click', e => {
            if (!e.isTrusted) return;
            const playing = active();
            if (playing.length !== 1) return;
            const v = playing[0];
            const manifests = performance.getEntriesByType('resource').map(r => r.name)
                .filter(u => /\.(?:m3u8|mpd)(?:[?#]|$)/i.test(u));
            window.__transcriptChoice = {src:v.currentSrc, drm:!!v.mediaKeys ||
                !!v.dataset.transcriptEncrypted, live:!Number.isFinite(v.duration),
                manifests:[...new Set(manifests)], players:document.querySelectorAll('video,audio').length};
        });
        (document.fullscreenElement || document.body).append(button);
    }
    if (button) button.style.display = active().length === 1 ? 'block' : 'none';
    return window.__transcriptChoice || null;
}"""


def chosen_media(choice):
    if choice["drm"]:
        raise ValueError("这个播放器使用 DRM 保护。请使用平台提供的字幕或可下载文件。")
    if choice["live"]:
        raise ValueError("当前播放没有固定时长。请在直播结束后使用回放链接。")
    src = choice["src"]
    if src.startswith("blob:") and choice["players"] == 1 and len(choice["manifests"]) == 1:
        src = choice["manifests"][0]
    if urlsplit(src).scheme not in ("https", "http") or not urlsplit(src).hostname:
        raise ValueError("无法确认这个播放器对应的媒体。可将平台提供下载的音视频文件拖入工具。")
    return src


def browser_file(frame, url, folder, report, stop):
    """Read a same-origin media response inside the logged-in page, without exporting cookies."""
    response = frame.evaluate(r"""async url => {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 30000);
        try {
            const r = await fetch(url, {credentials:'same-origin', mode:'same-origin', signal:controller.signal});
            const type = r.headers.get('Content-Type') || '';
            if (r.status !== 200 || !/^(audio\/|video\/|application\/octet-stream)/i.test(type)
                || /mpegurl|dash\+xml/i.test(type)) { controller.abort(); return null; }
            window.__transcriptStream = {reader:r.body.getReader(), controller};
            return {size:Number(r.headers.get('Content-Length'))};
        } catch { return null; } finally { clearTimeout(timer); }
    }""", url)
    if response is None:
        return None
    partial = folder / "browser-file.part"
    target = folder / "browser-file.media"
    try:
        report("从已登录的播放器读取媒体", None)
        last = time.monotonic()
        with partial.open("wb") as out:
            while True:
                check_pause(stop)
                chunk = frame.evaluate("""async () => {
                    const {reader, controller} = window.__transcriptStream;
                    const timer = setTimeout(() => controller.abort(), 30000);
                    try {
                        let data = '';
                        while (data.length < 262144) {
                            const {done, value} = await reader.read();
                            if (done) break;
                            for (let i = 0; i < value.length; i += 8192)
                                data += String.fromCharCode(...value.subarray(i, i + 8192));
                        }
                        return btoa(data);
                    } finally { clearTimeout(timer); }
                }""")
                if not chunk:
                    break
                out.write(base64.b64decode(chunk))
                if time.monotonic() - last >= 0.4:
                    size = response["size"]
                    report("从已登录的播放器读取媒体", min(100, out.tell() * 100 / size) if size else None)
                    last = time.monotonic()
        if not partial.stat().st_size:
            raise ValueError("播放器返回空媒体，请重新登录后再试。")
        partial.replace(target)
        return target
    finally:
        partial.unlink(missing_ok=True)


def wait_for_choice(page, source, folder, report, stop, timeout=300):
    context = page.context
    tabs = [page]
    context.on("page", lambda tab: tabs.append(tab))
    context.add_init_script("""document.addEventListener('encrypted', e => {
        if (e.target instanceof HTMLMediaElement) e.target.dataset.transcriptEncrypted = '1';
    }, true);""")
    page.goto(source, wait_until="commit", timeout=45000)
    report("请在浏览器里登录（如需要），播放目标视频，再点「转写当前播放」", None)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        check_pause(stop)
        opened = [tab for tab in tabs if not tab.is_closed()]
        if not opened:
            raise ValueError("视频浏览器已关闭。点「开始 / 继续」可以重试。")
        for tab in reversed(opened):
            for frame in tab.frames:
                if not frame.url.startswith(("https://", "http://")):
                    continue
                try:
                    choice = frame.evaluate(PLAYER_PICKER)
                except Error:
                    continue  # Login and redirects can replace the frame.
                if not choice:
                    continue
                url = chosen_media(choice)
                title = tab.title() or "视频"
                origin = frame.evaluate("location.origin")
                agent = frame.evaluate("navigator.userAgent")
                same_origin = frame.evaluate("url => new URL(url).origin === location.origin", url)
                if same_origin and not urlsplit(url).path.lower().endswith((".m3u8", ".mpd")):
                    media = browser_file(frame, url, folder, report, stop)
                    if media:
                        return media, title, {}
                return url, title, {"Referer": frame.url if same_origin else origin + "/", "User-Agent": agent}
        try:
            opened[-1].wait_for_timeout(400)
        except Error:
            if not opened[-1].is_closed():
                raise
    raise ValueError("等待播放器超时。请重新开始，登录后播放目标视频并点「转写当前播放」，或拖入平台提供的本地文件。")


def resolve_shared_page(source, folder, report, stop):
    parts = urlsplit(source)
    site = (parts.scheme + "://" + parts.netloc.lower()).encode()
    key = hashlib.sha256(site).hexdigest()[:24]
    profile = Path.home() / ".video-link-transcript" / "sites" / key
    return open_player(profile, report, stop, lambda page: wait_for_choice(page, source, folder, report, stop))
