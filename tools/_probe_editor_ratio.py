"""量「内容行宽」新规则（用户第 12 轮）有没有按规格落地。

规则：设屏幕宽 S、**编辑区**宽 C、内容行宽比例 r，

    正文块宽 T = min(r·S,  C − 2×5%·S)

- C ≥ (r + 10%)·S → T = r·S，两侧平分剩下的空白；
- 否则 → T = C − 10%·S，两侧各留 5% 等比例缩小。
窗口下限 = 导航栏 + 文稿列表 + 40%·S（让编辑区至少占屏幕 40%）。

屏幕宽用 `app._screen_width_override` 钉住，跑在哪台机器上结果都一样。

    python tools/quiet_desktop.py <python 全路径> tools/_probe_editor_ratio.py

只读用户 `state.json` 的 `settings`；文档写在临时目录里。
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
SCREEN = int(os.environ.get("JIANJI_SCREEN_W", "2560"))
LINE_WIDTHS = [30, 60, 80]


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    saved = storage.default_state_path
    storage.default_state_path = lambda: tmp / "state.json"
    settings = {}
    if REAL_STATE.is_file():
        settings = json.loads(REAL_STATE.read_text(encoding="utf-8")).get("settings", {})
    folder = tmp / "简记日记"
    folder.mkdir()
    doc = folder / "比例核验.md"
    doc.write_text("# 比例核验\n\n正文一行。\n", encoding="utf-8")
    (tmp / "state.json").write_text(json.dumps({
        "folder": str(folder), "default_folder": str(folder),
        "last_file": str(doc), "settings": settings,
    }, ensure_ascii=False), encoding="utf-8")

    import main as app_main  # noqa: E402

    root = tk.Tk()
    app = app_main.JianJiApp(root)
    root.update()
    app.open_file(doc)
    root.update()

    app._screen_width_override = SCREEN          # 钉住屏幕宽，结果与机器无关
    app._current_pad = -1
    app._apply_editor_geometry()
    root.update()

    editor = app.editor
    column_widget = editor.master
    min_w = root.minsize()[0]
    print(f"屏幕宽（钉住）S = {SCREEN}   SCALE = {app_main.SCALE}")
    print(f"导航 {app_main.NAV_W} + 列表 {app_main.CARDS_W} = "
          f"{app_main.NAV_W + app_main.CARDS_W}")
    print(f"窗口下限 = {min_w}  （= 导航+列表+40%·S = "
          f"{app_main.NAV_W + app_main.CARDS_W + int(SCREEN * 0.4)}）")
    print(f"用户设置 {app.settings}\n")

    widths = [min_w, min_w + 200, 2100, 2400, SCREEN]
    bad = 0
    for line_width in LINE_WIDTHS:
        app.settings["line_width"] = line_width
        print(f"—— 内容行宽 {line_width}% ——")
        print(f"{'窗口宽':>7} {'编辑区':>7} {'占屏':>7} {'控件宽':>7} {'留白':>6} "
              f"{'正文宽':>7} {'占屏':>7} {'应得':>7}  档位")
        for want in widths:
            root.geometry(f"{want}x{app.root.winfo_height()}")
            root.update()
            app._current_pad = -1
            app._apply_editor_geometry()
            root.update()
            win = root.winfo_width()
            column = column_widget.winfo_width()
            widget = editor.winfo_width()
            pad = app._current_pad
            content = widget - 2 * pad
            ratio = line_width / 100.0
            target = max(0, min(int(ratio * SCREEN), column - int(2 * 0.05 * SCREEN)))
            # 正文块在控件里居中，所以实际比目标多出的那点 = 控件宽 − 编辑区宽
            expected_content = target
            ok = abs(content - expected_content) <= 2
            case = "按设定比例" if ratio * SCREEN <= column - 2 * 0.05 * SCREEN else "5% 地板"
            if not ok:
                bad += 1
            print(f"{win:>7} {column:>7} {column / SCREEN * 100:>6.1f}% {widget:>7} "
                  f"{pad:>6} {content:>7} {content / SCREEN * 100:>6.1f}% "
                  f"{expected_content:>7}  {case}{'' if ok else '  ← 不对'}")
        print()

    print(f"对不上的档位：{bad} 个")
    root.destroy()
    storage.default_state_path = saved
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
