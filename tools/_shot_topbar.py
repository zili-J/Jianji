"""截顶栏那一行，人工核验「字数 / 字符数 / 创建 / 修改」显示得对不对。

这一行的显示规则是「平时完全不占位置，指针经过才出来」，所以光看代码不放心——
要真的看到三张图：**没悬停**、**悬停**、**还没落盘的文稿**。

截的是**窗口 DC**（`grab_hwnd` + 裁控件区域），所以在隐藏桌面上跑也没问题，
不必占用当前桌面。这一点和截原生菜单的脚本不同（那个只能走屏幕 DC）。

    python tools/quiet_desktop.py <python> tools/_shot_topbar.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))
sys.path.insert(0, str(PROJECT / "tools"))

import storage  # noqa: E402
import tkinter as tk  # noqa: E402
from capture_window import capture_widget_region  # noqa: E402

#: 把创建时间挪到 30 天前，这样「创建」和「修改」在图上明显不同——
#: 不然两串时间一模一样，截出来根本看不出有没有真的读两个字段。
CREATED_DAYS_AGO = 30

BODY = (
    "# 我的日记\n"
    "\n"
    "今天把留白那件事收尾了。一开始以为是行距的问题，后来才发现是标签的范围。\n"
    "改完之后，滚到底也舒服了。\n"
)

OUT = PROJECT / "outputs"


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    real = storage.default_state_path
    storage.default_state_path = lambda: tmp / "state.json"

    folder = tmp / "简记日记"
    folder.mkdir()
    doc = folder / "我的日记.md"
    doc.write_text(BODY, encoding="utf-8")
    storage.set_creation_time(doc, time.time() - CREATED_DAYS_AGO * 86400)
    storage.set_default_folder(folder)

    import main as app_main  # noqa: E402

    root = tk.Tk()
    root.geometry("1180x760")
    app = app_main.JianJiApp(root)
    root.update()
    app.open_file(doc)
    root.update()

    info = doc.stat()
    print(f"文稿：{doc.name}")
    print(f"  创建 {app_main.format_moment(info.st_ctime)}"
          f"   修改 {app_main.format_moment(info.st_mtime)}")

    # ① 没悬停：这一行应当**只有**「专注模式」，一个字都不多
    app._hide_counts_if_outside()
    root.update()
    w, h = capture_widget_region(app.topbar, OUT / "_topbar-idle.png", factor=2)
    print(f"① 没悬停   {w}x{h}  → outputs/_topbar-idle.png")

    # ② 悬停：四个字段一起出来
    app._on_topbar_enter()
    root.update()
    print("   顶栏文案：", app.count_label.cget("text"))
    w, h = capture_widget_region(app.topbar, OUT / "_topbar-hover.png", factor=2)
    print(f"② 悬停     {w}x{h}  → outputs/_topbar-hover.png")

    # ③ 还没落盘的文稿：只能显示字数，不能编造时间
    app.current_path = None
    app._show_counts()
    root.update()
    print("   未落盘文案：", app.count_label.cget("text"))
    w, h = capture_widget_region(app.topbar, OUT / "_topbar-unsaved.png", factor=2)
    print(f"③ 未落盘   {w}x{h}  → outputs/_topbar-unsaved.png")

    storage.default_state_path = real
    root.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
