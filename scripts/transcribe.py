"""Generate a transcript from a single online video."""

import argparse
import html
import json
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

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


def extract_captions(ydl, info, language, temp):
    for index, (_, lang, automatic, fmt) in enumerate(tracks(info, language)):
        try:
            ext = fmt["ext"]
            data = fmt.get("data")
            if data is None:
                path = temp / f"captions-{index}.{ext}"
                download = {"http_headers": info.get("http_headers") or {}, **fmt}
                ydl.dl(str(path), download, subtitle=True)
                data = path.read_text(encoding="utf-8-sig")
            cues = list(read_cues(data, ext, automatic))
            if cues:
                return cues, lang, "平台自动字幕" if automatic else "平台字幕"
        except (DownloadError, OSError, ValueError, TypeError, KeyError) as exc:
            print(f"字幕轨 {lang} 读取失败：{exc}", file=sys.stderr)
    return None


def transcribe_audio(ydl, info, language, model):
    from faster_whisper import WhisperModel

    print("正在下载音轨并在本机转写…", file=sys.stderr)
    ydl.process_info(dict(info))
    media = ydl.prepare_filename(info)
    try:
        recognizer = WhisperModel(model, device="cpu", compute_type="int8", local_files_only=True)
    except (OSError, FileNotFoundError, RuntimeError, ValueError):
        recognizer = WhisperModel(model, device="cpu", compute_type="int8")
    segments, details = recognizer.transcribe(
        media, language=base_lang(language) or None, vad_filter=True,
        vad_parameters={"threshold": 0.30, "min_silence_duration_ms": 650},
        word_timestamps=True,
    )
    cues = []
    for item in segments:
        if item.text.strip():
            cues.append((round(item.start * 1000), round(item.end * 1000), item.text.strip()))
    if not cues:
        raise ValueError("没有识别到可转写的人声。")
    return cues, details.language, f"本地语音识别（{Path(model).name}）"


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


def main():
    parser = argparse.ArgumentParser(description="视频分享链接转逐字稿")
    parser.add_argument("url", help="单个视频的 HTTP(S) 链接")
    parser.add_argument("--language", help="视频原声语言，例如 zh 或 en")
    parser.add_argument("--from-audio", action="store_true", help="直接按原声转写")
    parser.add_argument("--model", default="small", help="本地 Whisper 模型路径或名称")
    parser.add_argument("--output-dir", type=Path, default=Path("transcripts"))
    args = parser.parse_args()
    url = urlsplit(args.url)
    if url.scheme not in ("https", "http") or not url.hostname:
        parser.error("请输入完整的 HTTP(S) 视频链接。")
    with tempfile.TemporaryDirectory(prefix="video-transcript-") as tmp:
        temp = Path(tmp)
        options = {
            "quiet": True, "noprogress": True, "noplaylist": True,
            "playlistend": 1, "extract_flat": "in_playlist", "socket_timeout": 30,
            "writesubtitles": not args.from_audio, "writeautomaticsub": not args.from_audio,
            "format": "bestaudio/best", "outtmpl": str(temp / "media.%(ext)s"),
        }
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(args.url, download=False)
            ydl.params.update(writesubtitles=False, writeautomaticsub=False)
            if not info or info.get("_type") in ("playlist", "multi_video") or info.get("is_live"):
                raise ValueError("请提供单个已发布视频的链接。")
            result = None if args.from_audio else extract_captions(ydl, info, args.language, temp)
            cues, lang, source = result or transcribe_audio(ydl, info, args.language, args.model)
    plain, timed = save_result(args.output_dir, info.get("title") or "video", cues)
    print(f"来源：{source}；语言：{lang}；共 {len(cues)} 段\n逐字稿：{plain}\n时间轴：{timed}")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if not stream.isatty():
            stream.reconfigure(encoding="utf-8")
    try:
        main()
    except Exception as error:
        sys.exit(f"生成失败：{error}")
