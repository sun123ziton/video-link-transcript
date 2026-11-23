"""Standalone desktop entry point. Run with the skill's virtual environment."""

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from tkinterdnd2 import DND_FILES, TkinterDnD

from audio_job import Paused, save_json
from transcribe import run_job

CONFIG = Path.home() / ".video-link-transcript" / "window.json"
MODES = {"快速 · base": "base", "精细 · small": "small"}
LANGUAGES = {"自动识别": "", "中文": "zh", "英语": "en"}


def open_file(path):
    if sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


class Window:
    def __init__(self, root):
        self.root = root
        root.title("视频转逐字稿")
        root.geometry("640x365")
        root.minsize(535, 355)
        try:
            prefs = json.loads(CONFIG.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            prefs = {}
        self.source = tk.StringVar(value=prefs.get("source", ""))
        self.output = tk.StringVar(value=prefs.get("output", str(Path.home() / "Documents" / "视频逐字稿")))
        self.mode = tk.StringVar(value=prefs.get("mode", next(iter(MODES))))
        self.language = tk.StringVar(value=prefs.get("language", "自动识别"))
        self.audio_only = tk.BooleanVar(value=prefs.get("audio_only", False))
        self.browser = tk.BooleanVar(value=False)
        self.speakers = tk.BooleanVar(value=prefs.get("speakers", True))
        self.status = tk.StringVar(value="就绪")
        self.stop = threading.Event()
        self.events = queue.Queue()
        self.busy = False
        self.closing = False
        self.result = None
        self.editable = []

        panel = ttk.Frame(root, padding=18)
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(0, weight=1)
        ttk.Label(panel, text="视频链接或本地文件（可拖入）").grid(row=0, column=0, sticky="w")
        self.source_entry = ttk.Entry(panel, textvariable=self.source)
        self.source_entry.grid(row=1, column=0, sticky="ew", pady=(4, 12))
        browse = ttk.Button(panel, text="选择文件…", command=self.choose_source)
        browse.grid(row=1, column=1, padx=(8, 0), pady=(4, 12))
        root.drop_target_register(DND_FILES)
        root.dnd_bind("<<Drop>>", self.drop)
        self.source_entry.drop_target_register(DND_FILES)
        self.source_entry.dnd_bind("<<Drop>>", self.drop)
        ttk.Label(panel, text="保存位置").grid(row=2, column=0, sticky="w")
        dest = ttk.Entry(panel, textvariable=self.output)
        dest.grid(row=3, column=0, sticky="ew", pady=(4, 12))
        dest_button = ttk.Button(panel, text="选择目录…", command=self.choose_output)
        dest_button.grid(row=3, column=1, padx=(8, 0), pady=(4, 12))

        options = ttk.Frame(panel)
        options.grid(row=4, column=0, columnspan=2, sticky="w")
        for label, var, values in (("模型", self.mode, MODES), ("原声", self.language, LANGUAGES)):
            ttk.Label(options, text=label).pack(side="left")
            combo = ttk.Combobox(options, textvariable=var, values=list(values), width=13, state="readonly")
            combo.pack(side="left", padx=(4, 14))
            self.editable.append(combo)
        audio = ttk.Checkbutton(options, text="忽略字幕，按原声转写", variable=self.audio_only)
        audio.pack(side="left")
        speakers = ttk.Checkbutton(panel, text="区分发言人", variable=self.speakers)
        speakers.grid(row=5, column=1, sticky="e")
        self.editable.extend((audio, speakers, self.source_entry, browse, dest, dest_button))

        actions = ttk.Frame(panel)
        actions.grid(row=5, column=0, sticky="w", pady=(15, 5))
        self.start_button = ttk.Button(actions, text="开始 / 继续", command=self.start)
        self.start_button.pack(side="left")
        self.pause_button = ttk.Button(actions, text="暂停", command=self.pause, state="disabled")
        self.pause_button.pack(side="left", padx=8)
        history = ttk.Menubutton(actions, text="未完成任务 ▾")
        self.history_menu = tk.Menu(history, tearoff=False, postcommand=self.refresh_history)
        history.configure(menu=self.history_menu)
        history.pack(side="left")
        browser = ttk.Checkbutton(actions, text="直接用浏览器打开", variable=self.browser)
        browser.pack(side="left", padx=(8, 0))
        self.editable.extend((history, browser))
        self.progress = ttk.Progressbar(panel, maximum=100)
        self.progress.grid(row=6, column=0, columnspan=2, sticky="ew", pady=6)
        ttk.Label(panel, textvariable=self.status, wraplength=510).grid(row=7, column=0, columnspan=2, sticky="w")
        files = ttk.Frame(panel)
        files.grid(row=8, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.file_buttons = []
        for idx, name in enumerate(("打开逐字稿 TXT", "打开时间轴 SRT")):
            button = ttk.Button(files, text=name, state="disabled", command=lambda i=idx: open_file(self.result[i]))
            button.pack(side="left", padx=(0, 8))
            self.file_buttons.append(button)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.timer = root.after(150, self.poll)

    def choose_source(self):
        path = filedialog.askopenfilename(title="选择本地视频或音频")
        if path:
            self.source.set(path)

    def choose_output(self):
        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            self.output.set(path)

    def refresh_history(self):
        menu = self.history_menu
        menu.delete(0, "end")
        def select(req):
            self.source.set(req["source"])
            self.mode.set(next(k for k, v in MODES.items() if v == req["model"]))
            self.language.set(next(k for k, v in LANGUAGES.items() if v == req["language"]))
            self.audio_only.set(req["from_audio"])
            self.speakers.set(req.get("speakers", True))
            self.browser.set(req.get("browser", False))
            self.status.set("已选中任务。点「开始 / 继续」恢复。")
        cache = Path(self.output.get()).expanduser() / ".video-link-transcript"
        for path in sorted(cache.glob("*/state.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                req = record["request"]
                if req["model"] not in MODES.values() or req["language"] not in LANGUAGES.values():
                    continue
                seconds = int(record.get("cursor", 0) / 16000)
                menu.add_command(label=f"{record['title'][:46]}　{seconds // 60}:{seconds % 60:02}",
                                 command=lambda opts=req: select(opts))
            except (OSError, ValueError, KeyError):
                continue
        if menu.index("end") is None:
            menu.add_command(label="此保存位置没有未完成任务", state="disabled")

    def drop(self, event):
        paths = self.root.tk.splitlist(event.data)
        if not self.busy and len(paths) == 1:
            self.source.set(paths[0])
        elif not self.busy:
            self.status.set("请一次拖入一个文件。")
        return "copy"

    def start(self):
        if self.busy:
            return
        source, dest = self.source.get().strip(), self.output.get().strip()
        if not source or not dest:
            self.status.set("请填写视频来源和保存位置。")
            return
        mode, lang, audio = self.mode.get(), self.language.get(), self.audio_only.get()
        speakers = self.speakers.get()
        browser = self.browser.get()
        try:
            CONFIG.parent.mkdir(parents=True, exist_ok=True)
            save_json(CONFIG, dict(source=source, output=dest, mode=mode, language=lang, audio_only=audio, speakers=speakers))
        except OSError as exc:
            self.status.set(f"无法保存窗口设置：{exc}")
            return
        self.busy = True
        self.result = None
        self.stop.clear()
        self.status.set("正在启动…")
        self.progress.configure(value=0)
        for widget in [self.start_button, *self.file_buttons, *self.editable]:
            widget.state(["disabled"])
        self.pause_button.state(["!disabled"])

        def worker():
            def report(stage, percent):
                self.events.put(("progress", (stage, percent)))
            try:
                result = run_job(source, Path(dest).expanduser(), MODES[mode], LANGUAGES[lang],
                                 audio, report, self.stop, speakers=speakers, browser=browser)
                self.events.put(("done", result))
            except Paused:
                self.events.put(("paused", "已暂停。点「开始 / 继续」可从上次保存的位置继续。"))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def pause(self):
        self.stop.set()
        self.pause_button.state(["disabled"])
        self.status.set("正在暂停，保留已完成的片段…")

    def close(self):
        if self.busy:
            self.closing = True
            self.pause()
        else:
            self.root.after_cancel(self.timer)
            self.root.destroy()

    def poll(self):
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind == "progress":
                stage, pct = value
                if not self.stop.is_set():
                    self.status.set(stage if pct is None else f"{stage}　{int(pct)}%")
                    if pct is None and str(self.progress["mode"]) != "indeterminate":
                        self.progress.configure(mode="indeterminate")
                        self.progress.start(40)
                    elif pct is not None:
                        self.progress.stop()
                        self.progress.configure(mode="determinate", value=pct)
                continue
            self.busy = False
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.pause_button.state(["disabled"])
            for widget in [self.start_button, *self.editable]:
                widget.state(["!disabled"])
            if kind == "done":
                self.result = value
                self.progress.configure(value=100)
                self.status.set(f"已完成 · {value[3]} · {value[2]} · 共 {value[4]} 段")
                for button in self.file_buttons:
                    button.state(["!disabled"])
                self.root.bell()
            else:
                self.status.set(value if kind == "paused" else f"生成失败：{value}")
            if self.closing:
                self.root.destroy()
                return
        self.timer = self.root.after(150, self.poll)


if __name__ == "__main__":
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = sys.stderr
    app = TkinterDnD.Tk()
    Window(app)
    app.mainloop()
