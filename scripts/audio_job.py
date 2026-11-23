"""On-disk checkpoints and bounded-memory local speech recognition."""

import hashlib
import json
import os
import tempfile
import wave
from pathlib import Path
from urllib.parse import urlsplit

RATE = 16000
CHUNK_SECONDS = 60


class Paused(Exception):
    pass


def check_pause(stop):
    if stop.is_set():
        raise Paused("已暂停")


def save_json(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as handle:
        temp = Path(handle.name)
        try:
            json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            handle.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def job_paths(source, output, model, language, from_audio, browser=False):
    url = urlsplit(source) if source.startswith(("https://", "http://")) else None
    if url and url.hostname:
        local = None
        identity = [source, "browser"] if browser else source
    else:
        local = Path(source).expanduser().resolve()
        if not local.is_file():
            raise ValueError("请选择存在的音视频文件，或粘贴完整的视频链接。")
        stat = local.stat()
        identity = [os.path.normcase(str(local)), stat.st_size, stat.st_mtime_ns]
    key = json.dumps([identity, model, language or "", bool(from_audio)], ensure_ascii=False)
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    return local, output.resolve() / ".video-link-transcript" / digest


def decode_audio(media, target, report, stop):
    import av

    temp = target.with_suffix(".part")
    report("准备音频", None)
    try:
        with av.open(str(media)) as container, wave.open(str(temp), "wb") as out:
            if not container.streams.audio:
                raise ValueError("这个文件没有音轨。")
            out.setparams((1, 2, RATE, 0, "NONE", ""))
            resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
            duration = (container.duration or 0) / av.time_base
            last_percent = -1
            for frame in container.decode(audio=0):
                check_pause(stop)
                frame.pts = None
                for converted in resampler.resample(frame):
                    out.writeframesraw(converted.to_ndarray().tobytes())
                if duration:
                    percent = min(99, int(out.tell() * 100 / (RATE * duration)))
                    if percent != last_percent:
                        report("准备音频", percent)
                        last_percent = percent
            for converted in resampler.resample(None):
                out.writeframesraw(converted.to_ndarray().tobytes())
            if not out.tell():
                raise ValueError("音轨为空。")
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


def chunk_length(samples, final):
    import numpy as np

    if final:
        return len(samples)
    # Place the boundary near a quiet interval in the last five seconds.
    tail = samples[-5 * RATE:].astype(np.float32).reshape(-1, 320)
    power = np.mean(tail * tail, axis=1)
    center = int(np.argmin(np.convolve(power, np.ones(12), "valid"))) + 6
    return len(samples) - len(tail) * 320 + center * 320


def recognize(folder, state, model_name, language, report, stop):
    import numpy as np
    from faster_whisper import WhisperModel

    check_pause(stop)
    report("加载本地模型", None)
    try:
        model = WhisperModel(model_name, device="cpu", compute_type="int8", local_files_only=True)
    except (OSError, ValueError, RuntimeError):
        report("首次下载模型", None)
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
    pos = state.get("cursor", 0)
    cues = state.setdefault("cues", [])
    with wave.open(str(folder / "audio.wav"), "rb") as audio:
        total = audio.getnframes()
        if not (0 <= pos <= total) or audio.getparams()[:3] != (1, 2, RATE):
            raise ValueError("断点音频和进度记录不匹配。")
        audio.setpos(pos)
        while pos < total:
            check_pause(stop)
            report("本机转写", 100 * pos / total)
            raw = np.frombuffer(audio.readframes(CHUNK_SECONDS * RATE), dtype="<i2")
            if not len(raw):
                raise ValueError("断点音频意外中断。")
            length = chunk_length(raw, pos + len(raw) >= total)
            segments, info = model.transcribe(
                raw[:length].astype(np.float32) / 32768,
                language=language or state.get("detected_language"), vad_filter=True,
                vad_parameters={"threshold": 0.30, "min_silence_duration_ms": 650},
                beam_size=1 if model_name == "base" else 5, word_timestamps=True,
            )
            chunk = []
            word_times = {}
            for seg in segments:
                check_pause(stop)
                start = round((pos / RATE + seg.start) * 1000)
                end = round(min((pos + length) / RATE, pos / RATE + seg.end) * 1000)
                if seg.text.strip() and end > start:
                    if state.get("request", {}).get("speakers") and seg.words:
                        words = []
                        for word in seg.words:
                            a = max(start, round((pos / RATE + word.start) * 1000))
                            b = min(end, round((pos / RATE + word.end) * 1000))
                            if b > a:
                                words.append([a, b, word.word])
                            elif words:
                                words[-1][2] += word.word
                        word_times[str(len(cues) + len(chunk))] = words
                    chunk.append((start, end, seg.text.strip()))
            if chunk and not state.get("detected_language"):
                state["detected_language"] = info.language
            pos += length
            audio.setpos(pos)
            cues.extend(chunk)
            if word_times:
                state.setdefault("words", {}).update(word_times)
            state["cursor"] = pos
            save_json(folder / "state.json", state)
            report("本机转写", 100 * pos / total)
    if not cues:
        raise ValueError("没有识别到可转写的人声。")
    return cues, language or state.get("detected_language"), f"本地语音识别（{Path(model_name).name}）"
