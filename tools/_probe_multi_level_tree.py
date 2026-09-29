"""多层文件夹树：打印结构 + 截图，人工核验。

「多层」这种东西光看代码不放心——要真的看到**缩进层级**、**收起状态**，
以及「点进去只看它自己那一层」。所以这个探针做三件事：

1. 打一棵三层嵌套的树，把 parent/child 关系直接印出来；
2. 截左栏那张图（窗口 DC，所以隐藏桌面上跑也没问题）；
3. 点几个文件夹，核对中间文稿列表里出现的是不是「只有它自己的」。

    python tools/quiet_desktop.py <python> tools/_probe_multi_level_tree.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))
sys.path.insert(0, str(PROJECT / "tools"))

import storage  # noqa: E402
import tkinter as tk  # noqa: E402
from capture_window import capture_widget  # noqa: E402

OUT = PROJECT / "outputs"

#: 三层嵌套 + 每层都放一篇文稿，用来验证「只看自己那一层」
LAYOUT = {
    "": ["根目录的日记.md"],
    "工作": ["周会.md"],
    "工作/项目A": ["需求.md"],
    "工作/项目A/2026": ["计划.md"],
    "读书": [],
    "读书/随笔": ["随手记.md"],
}


def build(folder: Path) -> None:
    for relative, names in LAYOUT.items():
        directory = folder / relative if relative else folder
        directory.mkdir(parents=True, exist_ok=True)
        for name in names:
            (directory / name).write_text(f"# {Path(name).stem}\n\n正文。\n",
                                          encoding="utf-8")


def dump(app) -> None:
    tree = app.folder_tree

    def visit(parent: str, depth: int) -> None:
        for iid in tree.get_children(parent):
            opened = "展开" if tree.item(iid, "open") else "收起"
            text = tree.item(iid, "text")
            print(f"    {'    ' * depth}{text}   [{opened}]")
            visit(iid, depth + 1)

    visit("", 0)


def scope_of(app, folder: Path) -> list[str]:
    app._apply_tree_selection(str(folder))
    app.refresh_files()
    app.root.update()
    return sorted(path.name for path in app.file_paths)


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    real = storage.default_state_path
    storage.default_state_path = lambda: tmp / "state.json"

    folder = tmp / "简记日记"
    folder.mkdir()
    build(folder)
    storage.set_default_folder(folder)

    import main as app_main  # noqa: E402

    root = tk.Tk()
    root.geometry("1180x760")
    app = app_main.JianJiApp(root)
    root.update()

    print("① 树的形状（缩进 = 层级）")
    dump(app)

    print()
    print("② 每层都看得到，且只显示自己那一层")
    checks = [
        (folder, "全部文稿（递归）", None),
        (folder / "工作", "工作", ["周会.md"]),
        (folder / "工作" / "项目A", "工作/项目A", ["需求.md"]),
        (folder / "工作" / "项目A" / "2026", "工作/项目A/2026", ["计划.md"]),
        (folder / "读书", "读书", []),
    ]
    ok = True
    for path, label, expected in checks:
        if expected is None:
            app.show_all()
            app.root.update()
            got = sorted(p.name for p in app.file_paths)
            want = sorted(n for names in LAYOUT.values() for n in names)
            note = "（递归，全都在）"
        else:
            got = scope_of(app, path)
            want = sorted(expected)
            note = ""
        mark = "✓" if got == want else "✗"
        ok = ok and got == want
        print(f"    {mark} 点「{label}」→ {got} {note}")
        if got != want:
            print(f"       期望 {want}")

    print()
    print("③ 收起状态在重建之后仍然保留")
    work = str(folder / "工作")
    app.folder_tree.item(work, open=False)
    app._rebuild_tree()
    app.show_all()
    app.root.update()
    collapsed = not app.folder_tree.item(work, "open")
    ok = ok and collapsed
    print(f"    {'✓' if collapsed else '✗'} 「工作」收起后重建，仍然是收起的")
    print("    重建后的形状：")
    dump(app)

    # 截图前把树整个展开，图上一眼能看到三层
    app.folder_tree.item(work, open=True)
    for iid in app.folder_tree.get_children(""):
        app.folder_tree.item(iid, open=True)
    app.show_all()
    root.update()
    w, h = capture_widget(app.folder_tree, OUT / "_tree-multi-level.png")
    print()
    print(f"④ 截图 {w}x{h} → outputs/_tree-multi-level.png")

    storage.default_state_path = real
    root.destroy()
    print()
    print("PROBE", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
