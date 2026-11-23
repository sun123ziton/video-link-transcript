---
name: video-link-transcript
description: 从单个网络视频分享链接生成原语言逐字稿和带时间轴的 SRT。有可用字幕时优先提取，缺少字幕时在本机识别音频。适用于粘贴视频链接求逐字稿、提取视频字幕、转写无字幕视频。
---

从分享文案中提取用户指定视频的完整 HTTP(S) 链接，保留视频标识及分 P 等查询参数。

运行本 Skill 的 `scripts/transcribe.py`。首次使用，在 Skill 目录内用 Python 3.11–3.13 建立 `.venv`，用该环境的 Python 执行 `-m pip install -r requirements.txt`。之后直接复用这个解释器。将工作目录下的 `transcripts` 或用户指定的目录作为输出目录。

```text
<venv-python> <skill-dir>/scripts/transcribe.py "<视频链接>" --output-dir "<输出目录>"
```

用户指定视频原声语言时传 `--language zh` 或相应语言代码。用户要求按实际口播核对、字幕为译文或和声音不一致时，传 `--from-audio`。烧录在画面中的字幕通常没有独立字幕轨，默认流程会转写音轨。工具处理单条已发布视频。

成功后把 TXT 和 SRT 的实际文件链接交给用户，并说明脚本报告的来源与语言。保留逐字稿中的原话与重复表达。识别结果中的人名、术语和数字可能需要对照原声核实。失败时根据命令错误说明具体原因，平台拒绝访问时可请用户提供能直接打开的媒体链接。
