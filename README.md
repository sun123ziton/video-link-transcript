# 视频转逐字稿

把视频链接粘贴进窗口，或把电脑里的视频、录音拖进去，完成后直接打开 TXT 逐字稿和 SRT 时间轴。工具也可以作为 Codex Skill 使用。独立窗口在本机运行，无需 Codex 或转写 API Key。

## 安装

需要 Python 3.11–3.13、Tk 和 Git。已经安装旧版的用户，在原安装目录执行 `git pull --ff-only`，再用该目录的虚拟环境重新安装 `requirements.txt`。

**Windows PowerShell**

```powershell
git clone https://github.com/sun123ziton/video-link-transcript.git "$HOME/.codex/skills/video-link-transcript"
Set-Location "$HOME/.codex/skills/video-link-transcript"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

双击安装目录里的 **打开逐字稿工具.cmd**。也可以请 Codex 从本仓库安装 Skill、配置依赖并创建桌面快捷方式。

**macOS / Linux**

```sh
git clone https://github.com/sun123ziton/video-link-transcript.git ~/.codex/skills/video-link-transcript
cd ~/.codex/skills/video-link-transcript
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/window.py
```

如果系统 Python 缺少 Tk，请先安装与该 Python 版本对应的 Tk 支持。命令行转写可以在无图形界面的机器上运行。

## 使用窗口

1. 粘贴单个视频链接，或拖入一个本地音视频文件。也可以点「选择文件」。
2. 选保存位置，点「开始 / 继续」。窗口分别显示下载、音频准备和语音识别进度。时长未知时显示活动进度条。
3. 完成后窗口提示音响起，点「打开逐字稿 TXT」或「打开时间轴 SRT」。每次导出使用单独目录。

重新打开工具后，点「已完成记录」，可以按保存时间找到逐字稿，双击标题打开 TXT，也可打开 SRT 或所在文件夹。新完成的窗口和命令行任务都会记入本机历史，切换保存位置后仍可查看。旧版结果也能找回：把「保存位置」选为当时的输出目录，再点「已完成记录」，工具会识别其中已有的 TXT / SRT 文件对。

「区分发言人」默认开启。TXT 和 SRT 都会显示“发言人1：…”、“发言人2：…”；编号仅在这一段视频内有效。字幕已经逐条标注发言人时会直接复用。普通字幕需要同时取得音轨，程序在本机分析声音，再按字幕时间对齐。原声转写可以依据词的时间拆分一句中的换人。单条字幕覆盖多个人且无法分词定位时会列出多个编号；没有对应人声时会标“发言人待确认”。

发言人模型首次联网下载，之后从本机缓存加载。长录音每五分钟保存一次发言人分析进度。抢话、很短的插话、背景音乐和相似音色可能造成误分。只想快速提取普通字幕时可取消勾选，命令行使用 `--no-speakers`。

有平台字幕时优先提取。对本地文件，先找同名的 UTF-8 SRT/VTT 或语言后缀字幕（例如 `课程.zh.srt`），再查视频里的文字字幕轨。缺少可用字幕时按原声转写。指定原声语言可减少自动识别语言的开销；要跳过字幕，可勾选「忽略字幕，按原声转写」。

窗口默认使用**精细 small**。赶时间可选 **快速 base**；术语多、同音词多时可试 **高精度 medium**，本机 CPU 耗时更长。首次使用相应模型需要联网下载。知道原声语言时，可在「原声」选中文或英语。部分流媒体需要系统 FFmpeg 或 yt-dlp 对应的站点依赖。

「中文字形」默认简体中文，也可选繁體中文或保留原文。这个选项同时作用于中文 TXT 和 SRT，保留原来的时间轴。命令行使用 `--chinese-script simplified|traditional|original`。

遇到登录提示、401/403 或无法识别的分享页，工具会打开原网站。在弹出的浏览器里按网站提示登录，播放你要转写的那一段，再点页面上的「转写当前播放」。同一播放器页面有多段内容时，只播放目标视频。如果默认解析报错或选错内容，勾选「直接用浏览器打开」再开始，命令行可加 `--browser`。

通用浏览器模式支持常见的网页视频、音频和能明确对应到播放器的流媒体地址。同站文件可在当前登录会话中读取，登录状态按分享站点保存在本机。部分跨站鉴权、DRM、实时直播或专用播放器仍有限制，可用平台提供下载的音视频文件。弹窗需要图形桌面。

风变熊猫课堂的 `fclive.pandacollege.cn/st-live/playback` 回放可以直接粘贴原分享链接。点「开始 / 继续」后会弹出课程专用浏览器，首次按平台提示扫码登录；如果播放器没有自动载入，点一次「播放」。工具读到回放媒体后会关闭课程窗口，继续下载和转写。登录状态存在本机独立的浏览器资料目录，平台要求重新登录时会再次提示。Windows 优先使用 Edge，其他系统可用 Chrome；也可在虚拟环境执行 `python -m playwright install chromium`。

## 暂停和继续

点「暂停」或关闭正在运行的窗口，会保留已写入的断点；首次下载模型、解析网页等步骤可能需要等当前请求返回。在同一保存位置点「未完成任务」选中记录，再点「开始 / 继续」。选中记录只回填设置。

转写按约一分钟的音频分段，尽量在安静处切分；下次从最后一个已完成片段继续。准备音频时暂停会重做解码；下载中断后能否续传取决于媒体服务器。更改模型、语言、字幕选项、保存位置或替换本地源文件会创建另一项任务。

输出目录下的 `.video-link-transcript` 保存未完成任务的音轨、来源和进度，其中可能包含临时播放链接。需要暂停的任务应保留这个目录。正常完成后会清理该任务的音轨和断点。若要分享给别人，发最终的 TXT、SRT 即可。

## CLI 和 Skill

保留原来的命令行调用方式，也支持本地文件。命令行默认 `small`，可传 `--model base`；Ctrl+C 中断后，重复相同命令即可续转。

```sh
python scripts/transcribe.py "https://iandevlin.github.io/mdn/video-player-with-captions/"
python scripts/transcribe.py "课程录音.m4a" --model base --language zh --output-dir transcripts
```

使用上述命令时请调用安装了依赖的虚拟环境 Python。在 Codex 中可说「用 video-link-transcript 把这个链接或文件转成逐字稿」。独立使用窗口或 CLI 时，下载、识别、进度显示和导出都由本地程序执行。

[视频解析使用 yt-dlp](https://github.com/yt-dlp/yt-dlp) · [语音识别使用 faster-whisper](https://github.com/SYSTRAN/faster-whisper)

[发言人分析模型及说明（sherpa-onnx）](https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/index.html)
