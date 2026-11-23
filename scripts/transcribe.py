"""Generate a transcript from a single online video."""

import argparse
import html
import json
import re
import sys
import threading
import time
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from filelock import FileLock, Timeout
from audio_job import Paused, check_pause, decode_audio, job_paths, recognize, save_json
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, sanitize_filename


TIMESTAMP = r"(?:\d+:)?\d{2}:\d{2}[.,]\d{3}"
TIMING = re.compile(rf"^({TIMESTAMP})\s+-->\s+({TIMESTAMP})(?:\s|$)")
MARKUP = re.compile(r"</?(?:c|v|b|i|u|ruby|rt|lang|font)(?:[.\s][^>]*)?>|<" + TIMESTAMP + r">", re.I)


def milliseconds(stamp):
    total = 0
    for part in stamp[:-4].split(":"):
        total = total * 60 + int(part)
    return total * 1000 + int(stamp[-3:])


def timestamp(ms):
    seconds, fraction = divmod(ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02},{fraction:03}"


def read_cues(data, ext, automatic):
    if ext == "json3":
        for event in json.loads(data).get("events", []):
            words = "".join(s.get("utf8", "") for s in event.get("segs", []))
            start = event.get("tStartMs", 0)
            end = start + event.get("dDurationMs", 0)
            if words.strip() and end > start:
                yield start, end, " ".join(words.split())
        return

    previous = []
    previous_end = -1
    for block in re.split(r"\n[ \t]*\n", data.replace("\r\n", "\n").lstrip("\ufeff")):
        lines = block.strip().splitlines()
        if not lines or lines[0].startswith(("NOTE", "STYLE", "REGION", "WEBVTT")):
            continue
        for index, line in enumerate(lines[:2]):
            match = TIMING.match(line)
            if match:
                break
        else:
            continue
        start, end = map(milliseconds, match.groups())
        rows = [html.unescape(MARKUP.sub("", row)).strip() for row in lines[index + 1:]]
        rows = [row for row in rows if row]
        if not rows or end <= start:
            continue
        fresh = rows
        if automatic and start <= previous_end + 100 and len(rows) > 1:
            for count in range(min(len(previous), len(rows)), 0, -1):
                if previous[-count:] == rows[:count]:
                    fresh = rows[count:]
                    break
        previous, previous_end = rows, end
        if fresh:
            yield start, end, " ".join(fresh)


def base_lang(code):
    return (code or "").lower().removeprefix("ai-").split("-")[0]


def tracks(info, language):
    choices = []
    automatic_tracks = info.get("automatic_captions") or {}
    original = next((k for k in automatic_tracks if k.endswith("-orig")), None)
    wanted = base_lang(language or info.get("language") or original)
    for key in ("subtitles", "automatic_captions"):
        for lang, formats in (info.get(key) or {}).items():
            if lang == "live_chat" or (wanted and base_lang(lang) != wanted):
                continue
            automatic = key == "automatic_captions" or lang.startswith("ai-")
            for fmt in formats:
                ext = fmt.get("ext")
                if ext not in ("json3", "srt", "vtt"):
                    continue
                if "tlang" in parse_qs(urlsplit(fmt.get("url") or "").query):
                    continue
                rank = (automatic, not lang.endswith("-orig"), ("json3", "srt", "vtt").index(ext))
                choices.append((rank, lang, automatic, fmt))
    return sorted(choices, key=lambda item: item[0])


def labeled(cues):
    return bool(cues) and all(re.match(r"^发言人\d+\s*[:：]", text) for _, _, text in cues)


def caption_cues(data, ext, automatic, speakers):
    if speakers and ext == "vtt" and "<v" in data:
        names = {}
        def voice(match):
            name = html.unescape(match.group(1)).strip()
            return f"发言人{names.setdefault(name, len(names) + 1)}："
        marked = re.sub(r"<v(?:\.[^\s>]+)?\s+([^>]+)>", voice, data)
        cues = list(read_cues(marked, ext, automatic))
        if labeled(cues):
            return cues
    return list(read_cues(data, ext, automatic))


def extract_captions(ydl, info, language, temp, speakers):
    for index, (_, lang, automatic, fmt) in enumerate(tracks(info, language)):
        try:
            ext = fmt["ext"]
            data = fmt.get("data")
            if data is None:
                path = temp / f"captions-{index}.{ext}"
                download = {"http_headers": info.get("http_headers") or {}, **fmt}
                ydl.dl(str(path), download, subtitle=True)
                data = path.read_text(encoding="utf-8-sig")
            cues = caption_cues(data, ext, automatic, speakers)
            if cues:
                return cues, lang, "平台自动字幕" if automatic else "平台字幕"
        except (DownloadError, OSError, ValueError, TypeError, KeyError) as exc:
            print(f"字幕轨 {lang} 读取失败：{exc}", file=sys.stderr)
    return None


def save_result(output, title, cues):
    output.mkdir(parents=True, exist_ok=True)
    prefix = sanitize_filename(title, restricted=False).strip(" .")[:60] or "video"
    folder = Path(tempfile.mkdtemp(prefix=prefix + "-", dir=output))
    plain = folder / "transcript.txt"
    timed = folder / "transcript.srt"
    try:
        plain.write_text("".join(f"{words}\n" for _, _, words in cues), encoding="utf-8")
        with timed.open("w", encoding="utf-8") as handle:
            for index, (start, end, words) in enumerate(cues, 1):
                handle.write(f"{index}\n{timestamp(start)} --> {timestamp(end)}\n{words}\n\n")
    except BaseException:
        plain.unlink(missing_ok=True)
        timed.unlink(missing_ok=True)
        folder.rmdir()
        raise
    return plain.resolve(), timed.resolve()


def online_source(source, folder, state, language, from_audio, report, stop, speakers, browser=False):
    result = state.get("captions")
    if result and (not speakers or labeled(result[0])):
        return None, result
    previous = state.get("media")
    if previous and (folder / previous).is_file():
        return folder / previous, result
    last_update = 0
    stage = "读取平台字幕"

    def progress(event):
        nonlocal last_update
        check_pause(stop)
        now = time.monotonic()
        if now - last_update < 0.4:
            return
        last_update = now
        total = event.get("total_bytes") or event.get("total_bytes_estimate")
        pct = min(100, 100 * event.get("downloaded_bytes", 0) / total) if total else None
        report(stage, pct)

    options = {
        "quiet": True, "noprogress": True, "noplaylist": True,
        "playlistend": 1, "extract_flat": "in_playlist", "socket_timeout": 30,
        "writesubtitles": not from_audio, "writeautomaticsub": not from_audio,
        "format": "bestaudio/best", "outtmpl": str(folder / "media.%(ext)s"),
        "progress_hooks": [progress], "skip_unavailable_fragments": False,
    }
    from browser_media import browser_retry, resolve_shared_page
    from pandacollege import is_replay, resolve_replay

    def read_media(url, title=None):
        nonlocal result, stage
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
            check_pause(stop)
            if not info or info.get("_type") in ("playlist", "multi_video") or info.get("is_live"):
                raise ValueError("请提供单个已发布视频的链接。")
            state["title"] = title or info.get("title") or "video"
            ydl.params.update(writesubtitles=False, writeautomaticsub=False)
            if not from_audio and result is None:
                report("读取平台字幕", None)
                result = extract_captions(ydl, info, language, folder, speakers)
                if result:
                    if not speakers or labeled(result[0]):
                        return None, result
                    state["captions"] = result
                    save_json(folder / "state.json", state)
            check_pause(stop)
            stage = "下载媒体"
            report(stage, None)
            ydl.process_info(dict(info))
            media = Path(ydl.prepare_filename(info))
            if not media.is_file():
                raise ValueError("下载结束后没有找到媒体文件。")
            state["media"] = media.name
            save_json(folder / "state.json", state)
            return media, result

    report("解析视频链接", None)
    if is_replay(source):
        media_url, title, agent = resolve_replay(source, report, stop)
        options["http_headers"] = {"Referer": source, "User-Agent": agent}
        return read_media(media_url, title)
    if not browser:
        try:
            return read_media(source)
        except DownloadError as exc:
            check_pause(stop)
            if not browser_retry(exc):
                raise
    # A share page may contain several items. Don't reuse a guessed item's
    # subtitles or append to its partially downloaded file after user selection.
    result = None
    if state.pop("captions", None) is not None:
        save_json(folder / "state.json", state)
    location, title, headers = resolve_shared_page(source, folder, report, stop)
    if isinstance(location, Path):
        state.update(title=title, media=location.name)
        save_json(folder / "state.json", state)
        return location, None
    options.update(http_headers=headers, continuedl=False, overwrites=True,
                   outtmpl=str(folder / "browser-media.%(ext)s"))
    try:
        return read_media(location, title)
    except DownloadError as exc:
        raise ValueError("播放器已打开，但下载器未能取得完整媒体。可将平台提供下载的文件拖入工具。") from exc


def local_captions(media, language, stop, speakers):
    # Prefer an explicitly named language track, then the matching plain sidecar.
    stems = [media.stem]
    if language and re.fullmatch(r"[A-Za-z0-9-]+", language):
        stems.insert(0, f"{media.stem}.{language}")
    for stem in stems:
        for ext in ("srt", "vtt"):
            path = media.with_name(f"{stem}.{ext}")
            if path.is_file():
                try:
                    cues = caption_cues(path.read_text(encoding="utf-8-sig"), ext, False, speakers)
                except UnicodeDecodeError:
                    continue
                if cues:
                    return cues, language or "未标注", "本地字幕"
    import av

    aliases = {"eng": "en", "zho": "zh", "chi": "zh", "jpn": "ja", "kor": "ko",
               "fra": "fr", "fre": "fr", "deu": "de", "ger": "de", "spa": "es"}
    with av.open(str(media)) as container:
        for stream in container.streams.subtitles:
            tag = base_lang(stream.metadata.get("language"))
            tag = aliases.get(tag, tag)
            if language and tag != base_lang(language):
                continue
            if stream.codec_context.name not in ("mov_text", "ass", "ssa", "subrip", "webvtt", "text"):
                continue
            if stream.disposition & av.stream.Disposition.forced:
                continue
            cues = []
            origin = round((container.start_time or 0) / 1000)
            container.seek(0)
            for packet in container.demux(stream):
                check_pause(stop)
                if packet.pts is None or not packet.duration:
                    continue
                start = max(0, round(packet.pts * packet.time_base * 1000) - origin)
                end = round((packet.pts + packet.duration) * packet.time_base * 1000) - origin
                for part in packet.decode():
                    words = getattr(part, "dialogue", b"").decode("utf-8", errors="replace")
                    words = re.sub(r"\{\\[^}]*\}", "", words).replace(r"\N", " ").replace(r"\n", " ").strip()
                    if words and end > start:
                        cues.append((start, end, words))
            if cues:
                return cues, tag or "未标注", "内嵌字幕"
    return None


def run_job(source, output, model, language, from_audio, report, stop, speakers=True, browser=False):
    source = source.strip().strip('"')
    local, folder = job_paths(source, output, model, language, from_audio, browser=browser)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        lock = FileLock(str(folder / "run.lock")).acquire(timeout=0)
    except Timeout as exc:
        raise ValueError("同一任务已经在另一个窗口运行。") from exc
    with lock:
        checkpoint = folder / "state.json"
        state = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {}
        state["request"] = dict(source=str(local or source), model=model, language=language or "",
                                from_audio=from_audio, speakers=speakers, browser=browser)
        state.setdefault("title", local.stem if local else (urlsplit(source).hostname or "视频"))
        save_json(checkpoint, state)
        media = local
        audio = folder / "audio.wav"
        result = state.get("captions")
        if not audio.is_file():
            if state.get("cursor"):
                raise ValueError("断点所需的音频已丢失。请另选输出目录重新开始。")
            if local:
                state["title"] = local.stem
                if not from_audio:
                    report("检查本地字幕", None)
                    result = local_captions(local, language, stop, speakers)
            else:
                media, result = online_source(source, folder, state, language, from_audio, report, stop, speakers, browser)
            check_pause(stop)
            if result is None or (speakers and not labeled(result[0])):
                if result:
                    state["captions"] = result
                save_json(checkpoint, state)
                decode_audio(media, audio, report, stop)
        if audio.is_file() and state.get("media"):
            (folder / state.pop("media")).unlink(missing_ok=True)
            save_json(checkpoint, state)
        if result is None:
            result = recognize(folder, state, model, base_lang(language) or None, report, stop)
        cues, lang, origin = result
        if speakers and not labeled(cues):
            from speakers import diarize, label_cues
            turns = diarize(folder, report, stop)
            check_pause(stop)
            cues = label_cues(cues, state.get("words", {}), turns)
            origin += " · 本地发言人分析"
        plain, timed = save_result(output, state["title"], cues)
        # This directory is private to a single hashed job. Only remove our files.
        checkpoint.unlink(missing_ok=True)
        audio.unlink(missing_ok=True)
        (folder / "speakers.json").unlink(missing_ok=True)
        for path in folder.glob("captions-*"):
            if path.is_file():
                path.unlink(missing_ok=True)
        report("已完成", 100)
        return plain, timed, lang, origin, len(cues)


def main():
    parser = argparse.ArgumentParser(description="视频链接或本地音视频转逐字稿")
    parser.add_argument("source", help="单个视频链接，或本地视频、音频文件路径")
    parser.add_argument("--language", help="视频原声语言，例如 zh 或 en")
    parser.add_argument("--from-audio", action="store_true", help="直接按原声转写")
    parser.add_argument("--browser", action="store_true", help="直接用浏览器登录并选择播放器")
    parser.add_argument("--no-speakers", action="store_true", help="跳过发言人分析")
    parser.add_argument("--model", default="small", help="本地 Whisper 模型路径或名称")
    parser.add_argument("--output-dir", type=Path, default=Path("transcripts"))
    args = parser.parse_args()
    last = None

    def report(stage, pct):
        nonlocal last
        mark = (stage, None if pct is None else int(pct) // 5 * 5)
        if mark != last:
            print(stage if pct is None else f"{stage} {mark[1]}%", file=sys.stderr, flush=True)
            last = mark

    result = run_job(args.source, args.output_dir, args.model, args.language,
                     args.from_audio, report, threading.Event(), speakers=not args.no_speakers, browser=args.browser)
    plain, timed, lang, origin, count = result
    print(f"来源：{origin}；语言：{lang}；共 {count} 段\n逐字稿：{plain}\n时间轴：{timed}")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if not stream.isatty():
            stream.reconfigure(encoding="utf-8")
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("已中断。同样的输入、设置和输出目录可继续已保存的进度。")
    except Exception as error:
        sys.exit(f"生成失败：{error}")
