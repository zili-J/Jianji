"""测量「简记」在长文档下的关键路径耗时，用来判断卡顿出在哪里。

关注两条热路径：
1. 光标移动 / 打字 → `_apply_styles_without_dirty_flag()`（当前是整篇重解析）
2. 滚动 → `_on_editor_scrolled()` → `_redraw_margins()`（行号 + 徽标 + 装饰 + 表格框）
3. 专注模式切换 → `toggle_focus()`
"""
from __future__ import annotations

import statistics
import sys
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import storage  # noqa: E402
import tkinter as tk  # noqa: E402


def build_document(repeats: int) -> str:
    """造一份有代表性的长文档：标题、列表、引用、表格、代码块都有。"""
    # 注意：里面有代码示例的花括号，所以用替换而不是 str.format
    block = """## 第 §N§ 节

这是一段普通的正文，长度适中，用来撑出真实的排版量。这里再多写一些字，
让自动换行也参与进来，因为换行会直接影响显示行的数量。

- 列表项一
- 列表项二
  - 嵌套项
    - 更深一层
- [ ] 待办事项
- [x] 已完成事项

1. 有序一
2. 有序二

> 一段引用，看看竖条画得对不对。

| 项目 | 数量 | 单价 | 备注 |
| --- | ---: | ---: | :---: |
| 苹果 | 3 | 5.50 | 很甜 |
| 香蕉 | 5 | 3.20 | 很软 |

```python
def hello(name):
    return f"你好，{name}"
```

行内也有 **粗体**、*斜体*、~~删除线~~、==高亮==、`代码` 和 [链接](https://example.com)。

---

"""
    return "".join(block.replace("§N§", str(index)) for index in range(repeats))


def measure(label: str, function, times: int = 5) -> float:
    """跑 times 次取中位数，避免单次抖动影响判断。"""
    samples = []
    for _ in range(times):
        start = time.perf_counter()
        function()
        samples.append((time.perf_counter() - start) * 1000)
    median = statistics.median(samples)
    print(f"  {label:<46} {median:8.1f} ms   (最慢 {max(samples):.1f} ms)")
    return median


def main() -> None:
    repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 40

    tmp = Path(tempfile.mkdtemp())
    folder = tmp / "简记日记"
    folder.mkdir()
    text = build_document(repeats)
    doc = folder / "长文档.md"
    doc.write_text(text, encoding="utf-8")
    real_state = storage.default_state_path
    storage.default_state_path = lambda: tmp / "state.json"
    storage.set_default_folder(folder)

    from main import JianJiApp

    root = tk.Tk()
    root.geometry("1180x760")
    app = JianJiApp(root)
    app.open_file(doc)
    root.update()

    lines = text.count("\n") + 1
    print(f"文档规模：{lines} 行 / {len(text)} 字符\n")

    app.editor.mark_set("insert", "1.0")
    app._apply_styles_without_dirty_flag()
    root.update()

    print("【输入/光标移动】")
    measure("整篇重解析（打字走这条）", lambda: app._apply_markdown_styles())

    def typing(restyle) -> float:
        """在文档中段敲一个字再擦掉，量「敲一下」的真实耗时。

        复位（删掉刚敲的字 + 整篇重解析）放在计时之外，否则量到的是复位那一下。
        """
        samples = []
        for _ in range(8):
            app.editor.mark_set("insert", "10.5")
            start = time.perf_counter()
            app.editor.insert("insert", "字")
            restyle()
            samples.append((time.perf_counter() - start) * 1000)
            app.editor.delete("10.5", "10.6")
            app._apply_markdown_styles()
        return statistics.median(samples)

    full_typing = typing(app._apply_markdown_styles)
    incremental_typing = typing(app._apply_markdown_styles_incremental)
    print(f"  {'敲一个字（整篇重解析）':<46} {full_typing:8.1f} ms")
    print(f"  {'敲一个字（增量重解析）':<46} {incremental_typing:8.1f} ms"
          f"   快 {full_typing / incremental_typing:.1f} 倍")

    def move_cursor() -> None:
        """模拟上下方向键：光标换行，文本没变。"""
        app.editor.mark_set("insert", "end-1c")
        app._refresh_cursor_line()
        app.editor.mark_set("insert", "1.0")
        app._refresh_cursor_line()

    measure("光标换行轻量刷新（只换两行标签）", move_cursor)

    print("\n【滚动】")
    app.editor.yview_moveto(0.5)
    root.update()
    measure("_redraw_margins()（原地重画，几何未变）", app._redraw_margins)

    def scroll_step() -> None:
        """真实滚动：视口挪一点，然后重画——这时几何真的变了。"""
        app.editor.yview_scroll(1, "units")
        app._redraw_margins()

    measure("滚动一格 + 重画（几何每帧都变）", scroll_step)

    measure("_visible_lines()（只走一遍可见行）", app._visible_lines)
    measure("_redraw_table_frames()", app._redraw_table_frames)
    measure("_redraw_decorations()", app._redraw_decorations)
    measure("_redraw_line_numbers()", app._redraw_line_numbers)
    measure("_redraw_heading_labels()", app._redraw_heading_labels)

    print("\n【专注模式】")
    measure("toggle_focus()", app.toggle_focus, times=4)

    print("\n【表格帧明细】")
    import main as main_module
    original = main_module.JianJiApp._table_line
    calls = {"n": 0, "lift": 0}

    def counted(self, used, key, x, y, width, height, color=main_module.TABLE_BORDER):
        calls["n"] += 1
        return original(self, used, key, x, y, width, height, color)

    main_module.JianJiApp._table_line = counted
    app._redraw_table_frames()
    print(f"  一次重画表格用了 {calls['n']} 条线（每条一次 place + lift）")
    main_module.JianJiApp._table_line = original

    root.destroy()
    storage.default_state_path = real_state


if __name__ == "__main__":
    main()
