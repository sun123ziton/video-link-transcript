# 视频链接转逐字稿

把一条视频分享链接发给 Codex，就能拿到原语言逐字稿（TXT）和带时间轴的字幕（SRT）。优先读取平台字幕，其次尝试平台自动字幕。字幕不可用时，下载音轨，在本机用 faster-whisper 转写，无需 API Key。

## 安装到 Codex

将下面这句话发给 Codex：

> 请从 https://github.com/sun123ziton/video-link-transcript 安装 video-link-transcript Skill 到我的个人技能目录，并按 README 安装 Python 依赖。

也可以手动安装。需要 Git 和 Python 3.11–3.13。

**Windows PowerShell**

```powershell
git clone https://github.com/sun123ziton/video-link-transcript.git "$HOME/.codex/skills/video-link-transcript"
Set-Location "$HOME/.codex/skills/video-link-transcript"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**macOS / Linux**

```sh
git clone https://github.com/sun123ziton/video-link-transcript.git ~/.codex/skills/video-link-transcript
cd ~/.codex/skills/video-link-transcript
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## 使用

安装后打开新的 Codex 对话，粘贴：

> 用 video-link-transcript 把这个视频转成逐字稿，给我 TXT 和 SRT：https://iandevlin.github.io/mdn/video-player-with-captions/

也可以用虚拟环境的 Python 直接运行 `scripts/transcribe.py "视频链接"`。默认存入当前目录的 `transcripts/`，每次单独建文件夹。用 `--output-dir` 可以指定输出位置。

- **只有画面字幕或完全无字幕**：走音频转写。首次使用需要联网下载多语言 `small` 模型，后续复用本机缓存。可用 `--model` 指定模型目录。
- **多种字幕语言**：可用 `--language zh` 指定中文原声。未指定时按站点提供的原声信息选轨，站点未标明时会选可用字幕。若平台只提供译文，可加 `--from-audio` 按原声转写。
- **链接范围**：支持 yt-dlp 能解析并读取媒体或字幕的单视频页面及媒体直链。登录限制、地区限制、DRM 或站点反爬可能导致失败。部分站点或流媒体格式需要额外安装 FFmpeg 或 yt-dlp 对应的站点依赖。纯音乐、无声画面无法生成口播稿。

视频解析使用 [yt-dlp](https://github.com/yt-dlp/yt-dlp)，本地语音识别使用 [faster-whisper](https://github.com/SYSTRAN/faster-whisper)。
