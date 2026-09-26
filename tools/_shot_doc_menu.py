"""截文稿右键菜单的图（主菜单 + 「移动到」子菜单），用于人工核验。

菜单里现在有「打开 / 在资源管理器中显示 / 导出长图 / 移动到 / 删除文档」。

Windows 上 Tk 的菜单是**原生菜单**：`menu.post()` 走的是 `TrackPopupMenu`，会一直
卡到菜单被点掉才返回（`tk_popup` 同理）。所以「先 post 再截图」的顺序根本走不通——
主线程一进 post 就再也回不来（`tools/_probe_menu_post.py` 里验证过，最后一个标记停在
`before post`）。

办法是把它倒过来：先起一个后台线程，睡一小会儿等菜单画出来，再直接抓屏幕写 PNG，
最后 `os._exit(0)` 收摊。抓屏走的是纯 ctypes 的 GDI（`capture_screen_rect`，不碰 Tk），
所以子线程里调用是安全的。

菜单是原生窗口，没有可抓的客户区（`winfo_id()` 得到的是 1×1），只能连背后的窗口
一起从屏幕上截。

用法（两种菜单各跑一次，每次都会以 os._exit 结束）：

    python tools/_shot_doc_menu.py        # 主菜单 → outputs/文稿右键菜单.png
    python tools/_shot_doc_menu.py sub    # 子菜单 → outputs/文稿右键菜单-移动到.png

**必须在当前（可见）桌面上跑**：菜单只能从屏幕 DC 抓，而隐藏桌面上屏幕 DC 是全黑的
（`tools/quiet_desktop.py` 那个隐藏桌面只能跑测试，不能跑这个）。抓到纯色会打一行醒目警告。

一次性脚本，同类见 _shot_editor_ratio.py。
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))
sys.path.insert(0, str(PROJECT / "tools"))

import storage  # noqa: E402
import tkinter as tk  # noqa: E402
from capture_window import capture_screen_rect_checked  # noqa: E402

# 截一块够大的区域：150% 缩放下主菜单约 300×220 设备像素，子菜单更宽一些。
BOX_WIDTH = 560
BOX_HEIGHT = 460


def _capture_later(delay: float, out: Path, box: tuple[int, int, int, int],
                   done: threading.Event) -> None:
    """后台线程：等菜单画出来 → 抓屏写 PNG → 直接结束进程。

    不碰任何 Tk 对象，所以主线程正卡在 post() 里也不影响。
    """
    time.sleep(delay)
    width, height, blank = capture_screen_rect_checked(out, box)
    if blank:
        # 菜单没有可抓的客户区，只能从屏幕 DC 抓；而隐藏桌面上屏幕 DC 是全黑的。
        # 不报出来的话会静默产出一张纯色图，看着像「核验通过」。
        print(f"**{out.name} 是一片纯色**——八成是在隐藏桌面上跑的。"
              f"截原生菜单必须在当前桌面跑，别套 tools/quiet_desktop.py。",
              flush=True)
    else:
        print(f"已截图 {out.name} {width}x{height}", flush=True)
    done.set()
    sys.stdout.flush()
    os._exit(0)


def _shoot(menu: tk.Menu, out: Path, box: tuple[int, int, int, int]) -> None:
    """摆出菜单并让后台线程把它拍下来。"""
    done = threading.Event()
    threading.Thread(target=_capture_later, args=(1.2, out, box, done),
                     daemon=True).start()
    menu.post(box[0] + 24, box[1] + 24)
    # 正常情况下 post 会一直阻塞（原生菜单的模态循环），线程抓完自己 os._exit。
    # 但窗口还没真正映射出来时 post 可能立刻返回——那就等线程抓完再退出，
    # 否则进程先结束，daemon 线程会被直接掐掉，图就没了（踩过一次）。
    done.wait(15)


def main() -> int:
    want_sub = len(sys.argv) > 1 and sys.argv[1] == "sub"

    tmp = Path(tempfile.mkdtemp())
    real = storage.default_state_path
    storage.default_state_path = lambda: tmp / "state.json"

    folder = tmp / "简记日记"
    folder.mkdir()
    for name in ("读书", "工作", "生活"):
        (folder / name).mkdir()
    (folder / "读书" / "书摘").mkdir()
    (folder / "2026-09-22.md").write_text(
        "# 今天\n\n下午把书架整理了一遍，翻出两本没读完的书。\n", encoding="utf-8")
    (folder / "读书" / "随笔.md").write_text("# 随笔\n\n随便写写。\n", encoding="utf-8")
    storage.set_default_folder(folder)

    import main as app_main

    root = tk.Tk()
    root.geometry("1180x760")
    app = app_main.JianJiApp(root)
    root.update()

    out_dir = PROJECT / "outputs"
    doc = folder / "2026-09-22.md"
    menu = app._doc_menu(doc)

    # 菜单末尾还有第二个级联项（「排序：按名称」），所以要按标签找「移动到」，
    # 不能只取「第一个 cascade」——排序那项是后来加的，顺序变了就会截错菜单。
    index = next(i for i in range(menu.index("end") + 1)
                 if menu.type(i) == "cascade"
                 and menu.entrycget(i, "label") == "移动到")
    sub = menu.nametowidget(menu.entrycget(index, "menu"))

    print("菜单项：", [menu.entrycget(i, "label")
                   for i in range(menu.index("end") + 1)
                   if menu.type(i) != "separator"], flush=True)
    print("子菜单：", [sub.entrycget(i, "label")
                   for i in range(sub.index("end") + 1)
                   if sub.type(i) != "separator"], flush=True)

    # 先把窗口摆到前台并确认它真的显示出来了：窗口没映射时 post 会立刻返回，
    # 菜单一闪即逝，截出来就是一片桌面。
    root.deiconify()
    root.lift()
    root.attributes("-topmost", True)
    for _ in range(50):
        root.update()
        if root.winfo_viewable() and root.winfo_width() > 100:
            break
        time.sleep(0.02)
    root.update()

    left = root.winfo_rootx() + 380
    top = root.winfo_rooty() + 220
    box = (left, top, BOX_WIDTH, BOX_HEIGHT)

    if want_sub:
        print(f"摆出「移动到」子菜单 @ {left},{top}", flush=True)
        _shoot(sub, out_dir / "文稿右键菜单-移动到.png", box)
    else:
        print(f"摆出文稿右键菜单 @ {left},{top}", flush=True)
        _shoot(menu, out_dir / "文稿右键菜单.png", box)

    return 0  # 走不到这里：_shoot 里的线程会 os._exit


if __name__ == "__main__":
    raise SystemExit(main())
