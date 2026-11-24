"""Local index of finished exports, shared by the desktop and CLI."""

import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import NamedTuple


DATABASE = Path.home() / ".video-link-transcript" / "history.sqlite3"


class Completed(NamedTuple):
    folder: Path
    title: str
    saved: float


def connect():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATABASE, timeout=2)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS exports (folder TEXT PRIMARY KEY, title TEXT, saved REAL)")
    except sqlite3.Error:
        db.close()
        raise
    return db


def remember_export(timed, title):
    with closing(connect()) as db, db:
        db.execute("INSERT OR REPLACE INTO exports VALUES (?, ?, ?)",
                   (str(timed.resolve().parent), title, timed.stat().st_mtime))


def completed_exports(outputs):
    found = {}
    for root in {Path(output).expanduser() for output in outputs if output}:
        # Earlier versions wrote these pairs one directory below the output root.
        for timed in root.glob("*/transcript.srt"):
            try:
                folder = timed.resolve().parent
                if timed.is_file() and (folder / "transcript.txt").is_file():
                    title = re.sub(r"-[a-z0-9_]{8}$", "", folder.name)
                    found[folder] = Completed(folder, title, timed.stat().st_mtime)
            except OSError:
                continue
    warning = ""
    try:
        with closing(connect()) as db, db:
            db.executemany("INSERT OR IGNORE INTO exports VALUES (?, ?, ?)",
                           ((str(row.folder), row.title, row.saved) for row in found.values()))
            for folder, title, saved in db.execute("SELECT folder, title, saved FROM exports"):
                path = Path(folder)
                found[path] = Completed(path, title, saved)
    except (OSError, sqlite3.Error):
        warning = "本机历史索引暂时不可用，当前列表来自保存目录。"
    return sorted(found.values(), key=lambda row: (row.saved, str(row.folder)), reverse=True), warning
