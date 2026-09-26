"""量三处滚轮一格到底走多远：编辑区（像素）、文稿卡片（单位）、文件夹树（行）。

为什么不能只读常量：编辑区走的是「Tk 公式 × 倍数」，卡片和树走的是「单位」
（一单位 = 视口的十分之一 / 一行），三种量的单位都不一样，只能各量各的。

两个量不到数的坑（都踩过）：
  · 文档短到一屏放得下时，滚动是空操作，量出来 0 —— 要准备一篇够长的文档。
  · 文件夹树节点太少同理，要建够多子文件夹。

用法：

    python tools/_check_wheel_speed.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import storage  # noqa: E402
import tkinter as tk  # noqa: E402

# 上一版的编辑区倍数：本次改动的目标就是「上一版的 3 倍」，用来对账。
PREVIOUS_EDITOR_FACTOR = 2


class Wheel:
    """够用的滚轮事件替身：处理函数只看 delta。"""

    def __init__(self, delta: int) -> None:
        self.delta = delta


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    real = storage.default_state_path
    storage.default_state_path = lambda: tmp / "state.json"

    folder = tmp / "日记"
    folder.mkdir()
    for i in range(60):
        (folder / f"第{i:02d}篇.md").write_text(
            f"# 标题{i}\n\n第{i}篇的正文，写长一点好把列表撑起来。\n第二行。\n第三行。\n",
            encoding="utf-8")
    long_doc = folder / "长文.md"
    long_doc.write_text(
        "# 长文\n\n" + "".join(f"第 {i} 行：随便写点正文，够长就行。\n" for i in range(300)),
        encoding="utf-8")
    storage.set_default_folder(folder)

    import main as app_main

    root = tk.Tk()
    root.geometry("1180x760")
    app = app_main.JianJiApp(root)
    app.open_file(long_doc)
    root.update()
    print(f"EDITOR_WHEEL_FACTOR={app_main.EDITOR_WHEEL_FACTOR}  "
          f"LIST_WHEEL_UNITS={app_main.LIST_WHEEL_UNITS}\n")

    # ---- 编辑区：量像素位移，和 Tk 默认比 ----
    def wheel_pixels(delta: int, bound: bool) -> int:
        """滚一格后视口实际移动了多少像素。

        向下滚（delta<0）先把视口顶到最上面、向上滚顶到最下面：
        已经到头时滚动是空操作，量出来是 0，看不出差别。
        """
        if bound:
            app.editor.bind("<MouseWheel>", app._on_editor_wheel)
        else:
            app.editor.unbind("<MouseWheel>")
        root.update()
        app.editor.yview_moveto(0.0 if delta < 0 else 1.0)
        root.update()
        before = app.editor.yview()[0]
        app.editor.event_generate("<MouseWheel>", delta=delta)
        root.update()
        raw = app.editor.count("1.0", "end-1c", "ypixels")
        if isinstance(raw, (tuple, list)):     # 有的 Tk 版本回一个列表
            raw = raw[0]
        return round(abs(app.editor.yview()[0] - before) * int(raw))

    print(f"编辑区（视口高 {app.editor.winfo_height()} px）：")
    for delta in (-120, 120, -240, 240):
        native = wheel_pixels(delta, bound=False)
        ours = wheel_pixels(delta, bound=True)
        ratio = ours / native if native else float("inf")
        print(f"  delta={delta:>5}: Tk 默认 {native:>4} px → 简记 {ours:>4} px"
              f"  = 默认的 {ratio:.2f}×，上一版（{PREVIOUS_EDITOR_FACTOR}×）的 "
              f"{ratio / PREVIOUS_EDITOR_FACTOR:.2f}×")
    app.editor.bind("<MouseWheel>", app._on_editor_wheel)

    # ---- 文稿卡片：一格走几个单位 ----
    canvas = app.cards_canvas
    unit = canvas.winfo_height() / 10
    canvas.yview_moveto(0.0)
    root.update()
    before = canvas.canvasy(0)
    app._on_cards_wheel(Wheel(-120))
    root.update()
    moved = canvas.canvasy(0) - before
    print(f"\n文稿卡片：一格 {moved:.0f} px（视口 {canvas.winfo_height()} ÷ 10 = {unit:.0f}，"
          f"应为 {app_main.LIST_WHEEL_UNITS} 单位 = {unit * app_main.LIST_WHEEL_UNITS:.0f}）")

    # ---- 文件夹树：一格走几行 ----
    for i in range(40):
        sub = folder / f"子文件夹{i:02d}"
        sub.mkdir(exist_ok=True)
        (sub / f"里{i:02d}.md").write_text("# 里\n\n正文。\n", encoding="utf-8")
    app._refresh_tree_and_cards()
    root.update()
    tree = app.folder_tree
    total = len(app._tree_nodes)
    tree.yview_moveto(0.0)
    root.update()
    before = tree.yview()[0]
    app._on_tree_wheel(Wheel(-120))
    root.update()
    rows = (tree.yview()[0] - before) * total
    print(f"文件夹树：一格约 {rows:.1f} 行（共 {total} 个节点，"
          f"应为 {app_main.LIST_WHEEL_UNITS} 行）")

    # ---- 顺手回归：内容矮时往上滚，卡片列表仍不许被推下去 ----
    short = tmp / "短"
    short.mkdir()
    (short / "短篇.md").write_text("# 短\n\n很短。\n", encoding="utf-8")
    app.scope_folder = short
    app.refresh_files()
    root.update()
    for _ in range(3):
        app._on_cards_wheel(Wheel(120))
    root.update()
    print(f"\n一屏放得下时连滚 3 格往上：canvasy(0)={canvas.canvasy(0):.1f}（应为 0.0）")

    root.destroy()
    storage.default_state_path = real
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
