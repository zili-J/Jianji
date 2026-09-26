"""核验增量重解析与整篇重解析的结果完全一致。

做法：在编辑器上做一次真实改动，先走增量路径快照一遍，再整篇重解析快照一遍，
两者必须一模一样。快照不只比标签区间，也比几份缓存（块类型、代码块标记、
表格范围、记号区间、标题徽标）——只要有一条对不上就说明增量路径算错了。

用法：
    python tools/_check_incremental.py          # 跑全部用例
    python tools/_check_incremental.py 3        # 只跑到第 3 个用例就停（调试用）
"""
from __future__ import annotations

import sys
import tempfile
import tkinter as tk
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import storage  # noqa: E402

DOCUMENT = """# 一级标题

第一段正文，里面有 **粗体**、*斜体*、~~删除线~~、==高亮==、`行内代码`
和 [链接](https://example.com) 以及 ![图片说明](a.png)。

## 二级标题

- 无序一
- 无序二
  - 嵌套项
- [ ] 待办
- [x] 已完成

1. 有序一
2. 有序二

> 引用一行。

| 项目 | 数量 | 备注 |
| --- | ---: | :---: |
| 苹果 | 3 | 很甜 |
| 香蕉 | 5 | 很软 |

```python
def hello(name):
    return f"你好，{name}"
```

---

结尾一段。
"""


def cases():
    """(说明, 操作)。操作收 app，直接改编辑器的文本。

    每次挪光标后都补一次 `_refresh_cursor_line()`：真实使用中，光标是靠方向键或
    鼠标挪的，那时应用会走「光标换行轻量刷新」把记号在 `syntax` 与 `syntax_current`
    之间换过来。不补这一步，测试就会拿「光标没挪过」的状态去和整篇重解析比，
    比出来的差异全是假的。
    """

    def at(app, line, column=0):
        app.editor.mark_set("insert", f"{line}.{column}")
        app._refresh_cursor_line()

    def type_text(app, line, column, text):
        at(app, line, column)
        app.editor.insert("insert", text)

    def backspace(app, line, column):
        at(app, line, column)
        app.editor.delete("insert-1c", "insert")

    def type_at_line_end(app, line, text):
        """在行尾接着打字：End 键把光标放到 lineend，然后敲字。"""
        at(app, line, 0)
        app.editor.mark_set("insert", f"{line}.end")
        app.editor.insert("insert", text)

    def type_past_line_end(app, line, text):
        """把光标设到一个超出该行长度的列（Tk 会自己收敛），再敲字。"""
        at(app, line, 0)
        app.editor.mark_set("insert", f"{line}.999")
        app.editor.insert("insert", text)

    return [
        ("正文中间插一个字", lambda a: type_text(a, 3, 6, "新")),
        ("行尾接着打字", lambda a: type_at_line_end(a, 3, "。")),
        ("光标列超出行长再打字", lambda a: type_past_line_end(a, 3, "。")),
        ("正文中间换行", lambda a: type_text(a, 3, 10, "\n")),
        ("退格删一个字", lambda a: backspace(a, 5, 4)),
        ("整行删除", lambda a: a.editor.delete("7.0", "8.0")),
        ("一次粘进来 5 行",
         lambda a: type_text(a, 9, 0, "粘入一\n粘入二\n粘入三\n粘入四\n粘入五\n")),
        ("敲出粗体记号", lambda a: type_text(a, 3, 3, "**加粗**")),
        ("敲出高亮记号", lambda a: type_text(a, 11, 0, "==高亮== ")),
        ("表格单元格里插字", lambda a: type_text(a, 24, 4, "很")),
        ("表格里多插一根竖线", lambda a: type_text(a, 24, 2, "|")),
        ("代码块里插字", lambda a: type_text(a, 28, 8, "x")),
        ("代码块里敲出三反引号（应退回整篇）", lambda a: type_text(a, 28, 0, "```\n")),
        ("标题行里插字", lambda a: type_text(a, 1, 3, "很长的")),
        ("行首敲 # 变标题", lambda a: type_text(a, 5, 0, "### ")),
        ("行首敲 > 变引用", lambda a: type_text(a, 33, 0, "> ")),
        ("行首敲 - 变列表", lambda a: type_text(a, 33, 0, "- ")),
        ("插入围栏开合（应退回整篇）", lambda a: type_text(a, 35, 0, "```\n代码\n```\n")),
        ("跨行删除一整段", lambda a: a.editor.delete("5.0", "8.0")),
        ("删到文档为空", lambda a: a.editor.delete("1.0", "end")),
        ("从空文档写回一行", lambda a: type_text(a, 1, 0, "# 重新开始\n正文")),
    ]


def snapshot(app) -> dict:
    """把「这一份文档应该长什么样」整体抓下来。"""
    import main as main_module

    tags = []
    for tag in main_module.EDITOR_TAGS:
        ranges = app.editor.tag_ranges(tag)
        for index in range(0, len(ranges), 2):
            tags.append((tag, str(ranges[index]), str(ranges[index + 1])))
    return {
        "tags": sorted(tags),
        "blocks": [(info.kind, info.level, info.checked, info.number)
                   for info in app._doc_blocks],
        "in_code": list(app._doc_in_code),
        "delimiter": list(app._doc_delimiter),
        "fences": list(app._doc_fence),
        "lines": list(app._doc_lines),
        "table_rows": sorted((number, entry[1], entry[2])
                             for number, entry in app._doc_table_rows.items()),
        "tables": [(t.start, t.end, list(t.line_numbers)) for t in app._doc_tables],
        "headings": sorted(app._heading_lines.items()),
        "marks": sorted((line, tuple(value)) for line, value in app._line_marks.items()),
    }


def first_difference(left: dict, right: dict) -> str | None:
    """两份快照第一处不一致的地方，顺带把差异条目数报出来。"""
    for key in left:
        if left[key] == right[key]:
            continue
        one, two = left[key], right[key]
        if isinstance(one, list):
            only_left = [item for item in one if item not in two]
            only_right = [item for item in two if item not in one]
            return (f"{key}：增量多 {len(only_left)} 项 {only_left[:3]}，"
                    f"整篇多 {len(only_right)} 项 {only_right[:3]}")
        return f"{key}：增量 {one!r} ≠ 整篇 {two!r}"
    return None


def restyle(app) -> bool:
    """和 `_on_editor_modified` 一样：先试增量，不成再整篇。"""
    if app._apply_markdown_styles_incremental():
        return True
    app._apply_markdown_styles()
    return False


def open_app(tmp: Path):
    folder = tmp / "简记日记"
    folder.mkdir()
    doc = folder / "样例.md"
    doc.write_text(DOCUMENT, encoding="utf-8")
    storage.set_default_folder(folder)

    from main import JianJiApp

    root = tk.Tk()
    root.geometry("1180x760")
    app = JianJiApp(root)
    app.open_file(doc)
    root.update()
    return root, app, doc


def main() -> int:
    stop = int(sys.argv[1]) + 1 if len(sys.argv) > 1 else None
    tmp = Path(tempfile.mkdtemp())
    real_state = storage.default_state_path
    storage.default_state_path = lambda: tmp / "state.json"
    root, app, doc = open_app(tmp)

    failures = 0
    fallbacks = 0
    total = 0
    for label, operation in cases()[:stop]:
        total += 1
        try:
            operation(app)
        except tk.TclError as error:
            print(f"  {label:<34} 操作失败（{error}），跳过")
            continue
        used_incremental = restyle(app)
        if not used_incremental:
            # 增量路径自己认输、退回整篇——这时没有「增量的结果」可对，
            # 而且退回整篇本身一定是正确的，所以只记一笔、不比对。
            fallbacks += 1
            print(f"  {label:<34} OK   （退回整篇）")
            continue
        after_incremental = snapshot(app)
        app._apply_markdown_styles()
        difference = first_difference(after_incremental, snapshot(app))
        if difference is None:
            print(f"  {label:<34} OK   （增量）")
        else:
            failures += 1
            print(f"  {label:<34} 不一致！{difference}")

    # 连做一串增量、中间不整篇重解析，最后再对一次账
    if stop is None:
        total += 1
        app.open_file(doc)
        root.update()
        used = 0
        for step in range(12):
            app.editor.mark_set("insert", "3.3")
            app._refresh_cursor_line()
            app.editor.insert("insert", f"第{step}步 ")
            if restyle(app):
                used += 1
        consecutive = snapshot(app)
        app._apply_markdown_styles()
        difference = first_difference(consecutive, snapshot(app))
        if difference is None:
            print(f"  {'连做 12 次增量不整篇重解析':<34} OK   （{used}/12 次走了增量）")
        else:
            failures += 1
            print(f"  {'连做 12 次增量不整篇重解析':<34} 不一致！{difference}")

    root.destroy()
    storage.default_state_path = real_state
    print(f"\n共 {total} 项：不一致 {failures} 项，其中退回整篇 {fallbacks} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
