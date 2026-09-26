"""量「窗口能缩到多小」以及是谁挡住的。

用户第 12 轮要求「软件最小宽度 = 屏幕的 40%」。`root.minsize()` 只是下限的
**声明**，Tk 还会被内容撑住：任何控件的**请求宽度**都会抬高真实下限。
这个探针把请求宽度一个个试出来，看真实能缩到多小、是谁挡的。

    python tools/quiet_desktop.py <python 全路径> tools/_probe_min_width.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import storage  # noqa: E402
import tkinter as tk  # noqa: E402

REAL_STATE = Path(os.environ.get("LOCALAPPDATA", "")) / "JianJi" / "state.json"


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    saved = storage.default_state_path
    storage.default_state_path = lambda: tmp / "state.json"
    settings = {}
    if REAL_STATE.is_file():
        settings = json.loads(REAL_STATE.read_text(encoding="utf-8")).get("settings", {})
    folder = tmp / "简记日记"
    folder.mkdir()
    doc = folder / "最小宽度核验.md"
    doc.write_text("# 最小宽度核验\n\n正文一行。\n", encoding="utf-8")
    (tmp / "state.json").write_text(json.dumps({
        "folder": str(folder), "default_folder": str(folder),
        "last_file": str(doc), "settings": settings,
    }, ensure_ascii=False), encoding="utf-8")

    import main as app_main  # noqa: E402

    root = tk.Tk()
    root.geometry("1180x760")
    app = app_main.JianJiApp(root)
    root.update()
    app.open_file(doc)
    root.update()

    screen_w = root.winfo_screenwidth()
    print(f"虚拟桌面宽 {screen_w}   SCALE={app_main.SCALE}")
    print(f"声明下限 minsize = {root.minsize()}")
    print(f"新规则下限应为 导航+列表+40%·屏幕 = "
          f"{app_main.NAV_W + app_main.CARDS_W + int(screen_w * 0.4)}")
    print(f"导航 {app_main.NAV_W} + 卡片 {app_main.CARDS_W} = "
          f"{app_main.NAV_W + app_main.CARDS_W}\n")

    print(f"{'请求宽':>7} {'实际宽':>7} {'导航':>6} {'卡片':>6} {'编辑列':>7} {'控件宽':>7}")
    for want in (400, 500, 600, 700, 800, 900, 1000, 1100, 1200, 1300):
        root.geometry(f"{want}x760")
        root.update()
        root.update_idletasks()
        print(f"{want:>7} {root.winfo_width():>7} {app.nav.winfo_width():>6} "
              f"{app.cards_panel.winfo_width():>6} "
              f"{app.editor.master.winfo_width():>7} {app.editor.winfo_width():>7}")

    print("\n--- 各控件的请求宽度（谁在抬高下限）---")
    seen = []

    def walk(widget, depth=0):
        try:
            req = widget.winfo_reqwidth()
        except tk.TclError:
            return
        seen.append((req, depth, str(widget)))
        for child in widget.winfo_children():
            walk(child, depth + 1)

    walk(root)
    for req, depth, name in sorted(seen, reverse=True)[:12]:
        print(f"  请求宽 {req:>5}  {'  ' * depth}{name}")

    root.destroy()
    storage.default_state_path = saved
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
