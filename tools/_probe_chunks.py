"""核验长图分张：打印每一张的「累加高度」与「真实高度」差在哪。

`_split_chunks` 用 `sum(line.height)` 判上限，但行与行之间还有
`spacing`（标题上下留白、段间距），`line.height` 不含它——所以累加值
会系统性偏小，真实高度可能超出 `max_height`。这个探针把这个差额量出来。

跑法（必须隐藏桌面，且要用系统 Python 全路径）：

    python tools/quiet_desktop.py <python 全路径> tools/_probe_chunks.py

只读用户 `state.json` 的 `settings`，写文件只用临时 state.json。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import image_export as ie  # noqa: E402
import main as app_main  # noqa: E402
import storage  # noqa: E402
import tkinter as tk  # noqa: E402

REAL_STATE = Path(os.environ.get("LOCALAPPDATA", "")) / "JianJi" / "state.json"


def build_app() -> tuple[tk.Tk, object, Path]:
    tmp = Path(tempfile.mkdtemp())
    saved = storage.default_state_path
    storage.default_state_path = lambda: tmp / "state.json"
    settings = {}
    if REAL_STATE.exists():
        settings = json.loads(REAL_STATE.read_text(encoding="utf-8")).get("settings", {})
    folder = tmp / "简记日记"
    folder.mkdir()
    (tmp / "state.json").write_text(json.dumps({
        "folder": str(folder), "default_folder": str(folder),
        "last_file": "", "settings": settings}, ensure_ascii=False), encoding="utf-8")
    root = tk.Tk()
    root.geometry("1180x760")
    app = app_main.JianJiApp(root)
    root.update()
    storage.default_state_path = saved
    return root, app, tmp


def main() -> int:
    targets = sys.argv[1:] or [
        str(PROJECT / "简记使用说明.md"),
        str(PROJECT / ".3things" / "project.md"),
    ]
    root, app, _tmp = build_app()
    options = app._export_options()
    fonts = ie._Fonts()
    try:
        print("max_height=%d  margin=%d  width=%d  line_height=%d"
              % (options.max_height, options.margin, options.width, options.line_height))
        room = max(200, options.max_height - options.margin * 2)
        print("room = max(200, max_height - margin*2) = %d" % room)
        for target in targets:
            path = Path(target)
            if not path.exists():
                print("\n!! 找不到 %s" % path)
                continue
            text = path.read_text(encoding="utf-8-sig")
            rows = app_main.build_export_rows(text, options)
            lines = ie.build_layout(rows, options, app_main.export_palette(), fonts)
            chunks = ie._split_chunks(lines, options)
            print("\n=== %s ===" % path.name)
            print("源行 %d → 逻辑块 %d → 显示行 %d → %d 张"
                  % (len(app_main.split_document_lines(text)), len(rows),
                     len(lines), len(chunks)))
            for index, chunk in enumerate(chunks):
                used = sum(line.height for line in chunk)
                placed = ie._shift_chunk(chunk, options)
                height = ie._chunk_height(placed, options)
                flag = "  ← 超出上限" if height > options.max_height else ""
                print("  第 %d 张: 累加=%6d  真实=%6d  差=%5d  上限=%d%s"
                      % (index + 1, used, height, height - used,
                         options.max_height, flag))
    finally:
        fonts.close()
        root.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
