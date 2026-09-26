"""截「排序」右键菜单的图，用于人工核验。

排序入口现在在**文稿列表的右键菜单**里（原来挂在标题栏，被用户要求挪走）：
文稿菜单末尾多一项「排序：按名称」，展开就是三个单选。

Windows 上 Tk 的菜单是**原生菜单**：`menu.post()` 走 `TrackPopupMenu`，会一直卡到
菜单被点掉才返回，所以「先 post 再截图」走不通（详见 _shot_doc_menu.py 的说明）。
办法是反过来：先起后台线程睡一小会儿再抓屏写 PNG，最后 `os._exit(0)`。
抓屏是纯 ctypes 的 GDI，不碰 Tk，子线程里调用安全。

用法（两种菜单各跑一次，每次都会以 os._exit 结束）：

    python tools/_shot_sort_menu.py        # 文稿右键菜单 → outputs/文稿右键菜单.png
    python tools/_shot_sort_menu.py sub    # 排序子菜单   → outputs/排序菜单.png

**必须在当前（可见）桌面上跑**：菜单只能从屏幕 DC 抓，而隐藏桌面上屏幕 DC 是全黑的。
抓到纯色会打一行醒目的警告。

一次性核验脚本，同类见 _shot_doc_menu.py。
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

BOX_WIDTH = 640
BOX_HEIGHT = 500


def _capture_later(delay: float, out: Path, box: tuple[int, int, int, int],
                   done: threading.Event) -> None:
    """后台线程：等菜单画出来 → 抓屏写 PNG → 直接结束进程。

    不碰任何 Tk 对象，所以主线程正卡在 post() 里也不影响。
    """
    time.sleep(delay)
    width, height, blank = capture_screen_rect_checked(out, box)
    if blank:
        # 原生菜单没有可抓的客户区，只能从屏幕 DC 抓；而隐藏桌面上屏幕 DC 是全黑的。
        # 不报出来的话，这里会静默产出一张纯色图，看着像「核验通过」。
        print(f"**{out.name} 是一片纯色**——八成是在隐藏桌面上跑的。"
              f"截原生菜单必须在当前桌面跑，别套 tools/quiet_desktop.py。",
              flush=True)
    else:
        print(f"已截图 {out.name} {width}x{height}", flush=True)
    done.set()
    sys.stdout.flush()
    os._exit(0)


def _shoot(menu: tk.Menu, out: Path, box: tuple[int, int, int, int]) -> None:
    done = threading.Event()
    threading.Thread(target=_capture_later, args=(1.2, out, box, done),
                     daemon=True).start()
    menu.post(box[0] + 24, box[1] + 24)
    # 正常情况下 post 会一直阻塞（原生菜单的模态循环），线程抓完自己 os._exit。
    # 但窗口还没真正映射出来时 post 可能立刻返回——那就等线程抓完再退出，
    # 否则进程先结束、daemon 线程被掐掉，图会静默丢失（踩过一次）。
    done.wait(15)


def main() -> int:
    want_sub = len(sys.argv) > 1 and sys.argv[1] == "sub"

    tmp = Path(tempfile.mkdtemp())
    real = storage.default_state_path
    storage.default_state_path = lambda: tmp / "state.json"

    folder = tmp / "简记日记"
    folder.mkdir()
    for name in ("2026-09-24.md", "2026-09-20.md", "随笔.md"):
        (folder / name).write_text(f"# {name}\n\n随手记一点。\n", encoding="utf-8")
    storage.set_default_folder(folder)

    import main as app_main  # noqa: E402

    root = tk.Tk()
    root.geometry("1180x760")
    app = app_main.JianJiApp(root)
    root.update()

    out_dir = PROJECT / "outputs"
    menu = app._doc_menu(app.file_paths[0])
    index = next(i for i in range(menu.index("end") + 1)
                 if menu.type(i) == "cascade"
                 and menu.entrycget(i, "label").startswith("排序："))
    sub = menu.nametowidget(menu.entrycget(index, "menu"))

    print("文稿菜单：", [menu.entrycget(i, "label")
                     for i in range(menu.index("end") + 1)
                     if menu.type(i) != "separator"], flush=True)
    print("排序子菜单：", [sub.entrycget(i, "label")
                      for i in range(sub.index("end") + 1)], flush=True)

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

    # 菜单摆在预览栏（第 2 栏）上，能同时看到卡片列表的新宽度
    left = root.winfo_rootx() + 280
    top = root.winfo_rooty() + 120
    box = (left, top, BOX_WIDTH, BOX_HEIGHT)

    if want_sub:
        print(f"摆出排序子菜单 @ {left},{top}", flush=True)
        _shoot(sub, out_dir / "排序菜单.png", box)
    else:
        print(f"摆出文稿右键菜单 @ {left},{top}", flush=True)
        _shoot(menu, out_dir / "文稿右键菜单.png", box)

    return 0  # 走不到这里：_shoot 里的线程会 os._exit


if __name__ == "__main__":
    raise SystemExit(main())
