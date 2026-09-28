"""「先 import main 还是先建 Tk 根」会把整个进程的几何都带歪——同一份代码，只换顺序。

`main` 在**导入时**声明进程 DPI 感知（`enable_dpi_awareness()` → `SetProcessDpiAwareness(2)`），
而 Tk 是在**第一次 `Tk()`** 时按当时的 DPI 定下 `tk scaling` 的。顺序反了，Tk 就按
「未声明感知」的 96 DPI 算缩放，之后声明也没用了。

这一条真咬过人：新加的 `tests/test_audio_export.py` 名字排在最前，模块级探针写在
`import main` 之前，于是**整个测试进程**后面量几何的用例全歪——
`test_heading_labels` 与 `test_markdown_syntax` 红了 3 条，而单独跑那两个模块全绿。
兜底钉在 `tools/run_tests.py` 的 `run_in_process()` 里（发现测试模块之前先 `import main`）。

    python tools/quiet_desktop.py <python> tools/_probe_scale_order.py

要看的就三行：`tk scaling`、`_current_pad`、fill。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
sys.path.insert(0, str(PROJECT / "app"))


def measure(label: str) -> None:
    """量一遍「折行填充率」那个用例关心的几个数。"""
    import main as app_main
    import test_markdown_syntax as syntax

    case = syntax.SpaceDelimitedWrapTests(
        "test_a_space_does_not_push_a_whole_run_to_the_next_line")
    case.setUp()
    try:
        app, root = case.app, case.root
        fills = []
        for line in case.TARGETS:
            case._move(case.WITNESS)
            fills.append(case._first_line_fill(line))
        print(f"[{label}]")
        print(f"    SCALE（main 自己算的） = {app_main.SCALE}")
        print(f"    tk scaling（Tk 自己算的） = {root.tk.call('tk', 'scaling'):.3f}")
        print(f"    编辑区宽 {app.editor.winfo_width()}   留白 _current_pad = {app._current_pad}")
        print(f"    徽标画布 manager={app.heading_labels.winfo_manager()!r}"
              f"  x={app.heading_labels.winfo_x()}")
        print("    各行填充率 " + "  ".join(f"第{n}行={f:.4f}"
                                        for n, f in zip(case.TARGETS, fills)))
    finally:
        case.tearDown()


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "both"
    if mode in ("both", "probe-first"):
        # 模拟「某个测试模块在模块级先建了一个探针根」
        import tkinter as tk

        probe = tk.Tk()
        probe.destroy()
        measure("先建 Tk 根，再 import main（错的顺序）")
    if mode in ("both", "main-first"):
        # 在**子进程**里跑才干净：同一个进程里没法把 DPI 感知退回去
        import subprocess

        if mode == "both":
            print()
            print("重新开一个进程，先 import main 再建根：")
            subprocess.run([sys.executable, __file__, "main-first"], check=False)
            return 0
        measure("先 import main，再建 Tk 根（对的顺序）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
