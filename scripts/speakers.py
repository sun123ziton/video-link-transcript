"""Local speaker diarization and timestamp alignment."""

import hashlib
import json
import shutil
import tarfile
import wave
from bisect import bisect_left
from pathlib import Path
from urllib.request import urlopen

import numpy as np
from filelock import FileLock

from audio_job import RATE, check_pause, save_json

BLOCK_SECONDS = 300
RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
SEGMENT = "sherpa-onnx-pyannote-segmentation-3-0"
EMBED = "nemo_en_titanet_small.onnx"


def model_file(name, url, digest, report, stop, member=None):
    root = Path.home() / ".video-link-transcript" / "models"
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    with FileLock(str(path) + ".lock"):
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
            return path
        report("首次下载发言人模型", None)
        download = root / (name + ".download")
        unpacked = root / (name + ".part")
        try:
            with urlopen(RELEASE + url, timeout=45) as response, download.open("wb") as out:
                while block := response.read(256 * 1024):
                    check_pause(stop)
                    out.write(block)
            if member:
                with tarfile.open(download, "r:bz2") as archive, unpacked.open("wb") as out:
                    with archive.extractfile(member) as stream:
                        shutil.copyfileobj(stream, out)
            else:
                download.replace(unpacked)
            if hashlib.sha256(unpacked.read_bytes()).hexdigest() != digest:
                raise ValueError("发言人模型下载校验失败，请重试。")
            unpacked.replace(path)
        finally:
            download.unlink(missing_ok=True)
            unpacked.unlink(missing_ok=True)
    return path


def load_models(report, stop):
    import sherpa_onnx as s

    seg = model_file("segmentation-3.onnx", f"speaker-segmentation-models/{SEGMENT}.tar.bz2",
                     "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079",
                     report, stop, f"{SEGMENT}/model.onnx")
    emb = model_file(EMBED, f"speaker-recongition-models/{EMBED}",
                     "ad4a1802485d8b34c722d2a9d04249662f2ece5d28a7a039063ca22f515a789e", report, stop)
    embedding = s.SpeakerEmbeddingExtractorConfig(model=str(emb), num_threads=2)
    config = s.OfflineSpeakerDiarizationConfig(
        segmentation=s.OfflineSpeakerSegmentationModelConfig(
            pyannote=s.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(seg)), num_threads=2),
        embedding=embedding, clustering=s.FastClusteringConfig(num_clusters=-1, threshold=0.75),
        min_duration_on=0.30, min_duration_off=0.50,
    )
    return s.OfflineSpeakerDiarization(config), s.SpeakerEmbeddingExtractor(embedding)


def unit(vector):
    return vector / max(float(np.linalg.norm(vector)), 1e-12)


def voice_vectors(samples, turns, extractor, stop):
    # Use single-speaker portions for matching local clusters across blocks.
    clips = {}
    for i, (start, end, speaker) in enumerate(turns):
        left = max(start, turns[i - 1][1]) if i else start
        right = min(end, turns[i + 1][0]) if i + 1 < len(turns) else end
        if right - left >= 0.5:
            clips.setdefault(speaker, []).append((right - left, left, right))
    vectors = {}
    for speaker, candidates in clips.items():
        features = []
        for _, start, end in sorted(candidates, reverse=True)[:3]:
            check_pause(stop)
            stream = extractor.create_stream()
            stream.accept_waveform(RATE, samples[round(start * RATE):round(min(end, start + 8) * RATE)])
            stream.input_finished()
            if extractor.is_ready(stream):
                features.append(unit(np.asarray(extractor.compute(stream))))
        if features:
            vectors[speaker] = unit(np.mean(features, axis=0))
    return vectors


def global_ids(local_ids, vectors, centers):
    # One local cluster can match at most one existing speaker and vice versa.
    pairs = sorted(((float(np.dot(vec, unit(np.asarray(c)))), local, i)
                    for local, vec in vectors.items() for i, c in enumerate(centers) if c),
                   reverse=True)
    assigned, used = {}, set()
    for score, local, i in pairs:
        if score < 0.50:
            break
        if local not in assigned and i not in used:
            assigned[local] = i
            used.add(i)
    for local in local_ids:
        if local not in assigned:
            assigned[local] = len(centers)
            centers.append([])
        i = assigned[local]
        if local in vectors:
            old = np.asarray(centers[i]) if centers[i] else 0
            centers[i] = (old + vectors[local]).tolist()
    return assigned


def diarize(folder, report, stop):
    cache = folder / "speakers.json"
    state = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else dict(cursor=0, turns=[], centers=[])
    with wave.open(str(folder / "audio.wav"), "rb") as wav:
        total = wav.getnframes()
        if state["cursor"] == total:
            return state["turns"]
        detector, extractor = load_models(report, stop)
        wav.setpos(state["cursor"])
        while state["cursor"] < total:
            check_pause(stop)
            start = state["cursor"]
            samples = np.frombuffer(wav.readframes(BLOCK_SECONDS * RATE), dtype="<i2").astype(np.float32) / 32768
            if not samples.size:
                raise ValueError("说话人分析所需音频不完整。")
            def progress(done, count):
                report("区分发言人", 100 * (start + samples.size * done / max(count, 1)) / total)
                return int(stop.is_set())
            # Pad very short tails for the segmentation model, clipping output below.
            signal = np.pad(samples, (0, max(0, 10 * RATE - samples.size)))
            segments = detector.process(signal, callback=progress).sort_by_start_time()
            check_pause(stop)
            turns = [(max(0, r.start), min(r.end, samples.size / RATE), r.speaker)
                     for r in segments if min(r.end, samples.size / RATE) > max(0, r.start)]
            vectors = voice_vectors(samples, turns, extractor, stop) if total > BLOCK_SECONDS * RATE else {}
            ids = global_ids(dict.fromkeys(s for _, _, s in turns), vectors, state["centers"])
            for a, b, local in turns:
                state["turns"].append([round((start / RATE + a) * 1000),
                                       round((start / RATE + b) * 1000), ids[local] + 1])
            state["cursor"] = start + samples.size
            save_json(cache, state)
    return state["turns"]


def label_cues(cues, words_by_cue, turns):
    # Prefix maxima let word/segment lookups skip earlier turns, including overlaps.
    ordered = sorted(turns)
    ends = np.maximum.accumulate([b for _, b, _ in ordered]).tolist() if ordered else []

    def speaker(a, b, word=False):
        scores = {}
        i = bisect_left(ends, a + 1)
        while i < len(ordered) and ordered[i][0] < b:
            start, end, who = ordered[i]
            overlap = max(0, min(end, b) - max(start, a))
            if overlap:
                scores[who] = scores.get(who, 0) + overlap
            i += 1
        if not scores:
            if word and b - a <= 500:
                # VAD and word timestamps can disagree by a few frames at the
                # start of speech. Bridge only short gaps next to a single voice.
                left = bisect_left(ends, a - 150)
                nearby = set()
                while left < len(ordered) and ordered[left][0] <= b + 150:
                    if ordered[left][1] >= a - 150:
                        nearby.add(ordered[left][2])
                    left += 1
                if len(nearby) == 1:
                    return f"发言人{nearby.pop()}"
            return "发言人待确认"
        best = max(scores.values())
        candidates = sorted(who for who, score in scores.items() if score >= best * 0.65)
        return " / ".join(f"发言人{who}" for who in candidates)

    result = []
    for i, (start, end, text) in enumerate(cues):
        words = words_by_cue.get(str(i), [])
        if not words or "".join(w[2] for w in words).strip() != text.strip():
            result.append((start, end, f"{speaker(start, end)}：{text}"))
            continue
        groups = []
        for a, b, word in words:
            who = speaker(a, b, word=True)
            if groups and groups[-1][2] == who:
                groups[-1][1] = b
                groups[-1][3] += word
            else:
                groups.append([a, b, who, word])
        for a, b, who, words in groups:
            result.append((a, b, f"{who}：{words.strip()}"))
    return result
