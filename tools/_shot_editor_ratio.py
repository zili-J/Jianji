"""截「内容行宽」新规则（用户第 12 轮）的图：同一篇正文在三个窗口宽度下的留白。

规则：正文块宽 = min(行宽 × 屏幕宽,  编辑区宽 − 10% × 屏幕宽)。

- 窗口够宽（编辑区 ≥ 行宽+10% 的屏幕）→ 正文块 = 行宽 × 屏幕宽，两侧平分剩下的空白；
- 窗口不够宽 → 两侧各留 5% × 屏幕宽，正文块跟着编辑区缩。

产出放 outputs/。同类见 _shot_doc_menu.py。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))
sys.path.insert(0, str(PROJECT / "tools"))

import storage  # noqa: E402
import tkinter as tk  # noqa: E402
from capture_window import capture_hwnd  # noqa: E402

REAL_STATE = Path(os.environ.get("LOCALAPPDATA", "")) / "JianJi" / "state.json"

DOC = (
    "# 内容行宽核验\n"
    "\n"
    "设屏幕宽 S、编辑区宽 C、内容行宽 r，正文块宽 = min(r×S, C − 10%×S)。\n"
    "\n"
    "窗口够宽时正文块就是「屏幕的 r」，两侧平分剩下的空白；窗口不够宽时两侧各留\n"
    "屏幕的 5%，正文块跟着编辑区一起缩。左边这条竖线是引用的装饰，它的位置跟着\n"
    "留白走，所以从它的位置也能看出留白变没变。\n"
    "\n"
    "> 引用一段：引用竖条贴在正文左边缘上，留白一变它就跟着挪。\n"
    "\n"
    "- 列表项：项目符号也一样贴着正文左边缘。\n"
)


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    real = storage.default_state_path
    storage.default_state_path = lambda: tmp / "state.json"

    folder = tmp / "简记日记"
    folder.mkdir()
    doc = folder / "行宽核验.md"
    doc.write_text(DOC, encoding="utf-8")
    storage.set_default_folder(folder)
    # 核验在**用户真实设置**下做（行宽、字号、行高都影响折行）
    settings = {}
    if REAL_STATE.is_file():
        settings = json.loads(REAL_STATE.read_text(encoding="utf-8")).get("settings", {})
    if settings:
        storage.set_settings(dict(settings))
        print(f"用户设置：{settings}")

    import main as app_main

    root = tk.Tk()
    root.geometry("1180x760")
    app = app_main.JianJiApp(root)
    root.update()
    app.open_file(doc)
    root.update()

    out = PROJECT / "outputs"
    screen = app._screen_width()
    minimum = root.minsize()[0]
    print(f"屏幕宽 S = {screen}   窗口下限 = {minimum}（编辑区正好占 40%·S）")
    print(f"{'窗口宽':>7} {'编辑区':>7} {'占屏':>7} {'留白':>6} {'正文块':>7} "
          f"{'占屏':>7} {'档位':>10}")

    plan = [(minimum, "最小窗口"), (int(minimum * 1.25), "中等窗口"), (screen, "最大化")]
    for width, label in plan:
        root.geometry(f"{width}x{root.winfo_height()}")
        root.update()
        app._current_pad = -1
        app._apply_editor_geometry()
        root.update()
        column = app.editor.master.winfo_width()
        pad = app._current_pad
        block = app.editor.winfo_width() - 2 * pad
        ratio = app.settings["line_width"] / 100.0
        case = "按设定比例" if ratio * screen <= column - int(0.1 * screen) else "5% 地板"
        print(f"{root.winfo_width():>7} {column:>7} {column / screen * 100:>6.1f}% "
              f"{pad:>6} {block:>7} {block / screen * 100:>6.1f}% {case:>10}")
        name = f"核验-行宽-{label}.png"
        size = capture_hwnd(int(root.winfo_id()), out / name)
        print(f"    → {name} {size[0]}×{size[1]}")

    root.destroy()
    storage.default_state_path = real
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
