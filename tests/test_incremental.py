"""增量重解析的回归测试。

打字走的是「只重算改动那几行」的增量路径（`_apply_markdown_styles_incremental`），
它是为了治长文档打字卡顿（4341 行时整篇重解析要 68 ms）。增量是有风险的：
行号会因为插入/删除整体位移，围栏和表格是跨行语法，光标行还要在
`elide` 与显示源码之间切换——任何一处算漏，屏幕上的样式就会和整篇重算的结果不一样。

所以这里守三条：

1. **增量结果必须与整篇重解析逐项一致**。做一次真实改动，先走增量抓一份快照，
   再整篇重解析抓一份，标签区间、块类型、围栏状态、表格范围、记号区间、
   标题徽标全都要一模一样。
2. **行模型必须和控件一致**。`_doc_lines` 的长度要等于 `index("end-1c")` 的行号，
   否则行号整体错位（`\\r` 的处理就踩过这个坑）。
3. **增量真的只动了改动区**。数 Tcl 的 `tag` 调用次数，远低于整篇重解析才算数——
   不然哪天它悄悄退化成整篇，性能测试也未必看得出来。
"""
from __future__ import annotations

import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import main as app_main  # noqa: E402
import storage  # noqa: E402
from main import split_document_lines  # noqa: E402

try:
    _probe = tk.Tk()
    _probe.destroy()
    HAS_DISPLAY = True
except tk.TclError:  # pragma: no cover
    HAS_DISPLAY = False


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


def parity_cases():
    """(说明, 操作)。操作收 app，直接改编辑器的文本。

    每次挪光标后都补一次 `_refresh_cursor_line()`：真实使用中光标是靠方向键或
    鼠标挪的，那时应用会走「光标换行轻量刷新」。不补这一步，比出来的差异全是假的。
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
        ("代码块里敲出三反引号", lambda a: type_text(a, 28, 0, "```\n")),
        ("标题行里插字", lambda a: type_text(a, 1, 3, "很长的")),
        ("行首敲 # 变标题", lambda a: type_text(a, 5, 0, "### ")),
        ("行首敲 > 变引用", lambda a: type_text(a, 33, 0, "> ")),
        ("行首敲 - 变列表", lambda a: type_text(a, 33, 0, "- ")),
        ("插入围栏开合", lambda a: type_text(a, 35, 0, "```\n代码\n```\n")),
        ("跨行删除一整段", lambda a: a.editor.delete("5.0", "8.0")),
        ("删到文档为空", lambda a: a.editor.delete("1.0", "end")),
        ("从空文档写回一行", lambda a: type_text(a, 1, 0, "# 重新开始\n正文")),
    ]


def snapshot(app) -> dict:
    """把「这一份文档应该长什么样」整体抓下来。"""
    tags = []
    for tag in app_main.EDITOR_TAGS:
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
    """两份快照第一处不一致的地方（没差异返回 None）。"""
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


class TclCounter:
    """数某个控件发出了多少次 Tcl 调用（按子命令分类）。

    `_tkinter.tkapp.call` 本身只读、打不了补丁，但控件的 `tk` 是普通属性，
    换成转发代理就能数。
    """

    def __init__(self, real) -> None:
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "calls", {})

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_real"), name)

    def call(self, *args):
        real = object.__getattribute__(self, "_real")
        calls = object.__getattribute__(self, "calls")
        key = "?"
        if args:
            first = args[0]
            if isinstance(first, (tuple, list)) and len(first) > 1:
                key = str(first[1])          # 子命令，如 tag / insert / index
            elif isinstance(first, str):
                key = first
        calls[key] = calls.get(key, 0) + 1
        return real.call(*args)


class SplitDocumentLinesTests(unittest.TestCase):
    """切行必须和 Tk 的行模型一致，否则行号整体错位。"""

    def test_line_feed_is_the_only_separator(self) -> None:
        self.assertEqual(split_document_lines("甲\n乙\n"), ["甲", "乙", ""])

    def test_trailing_carriage_return_is_stripped(self) -> None:
        self.assertEqual(split_document_lines("甲\r\n乙\r\n"), ["甲", "乙", ""])

    def test_lone_carriage_return_does_not_start_a_new_line(self) -> None:
        """单独的 `\\r` 在 Tk 里是行内字符，不是换行。

        以前用 `re.split(r"\\r\\n|\\r|\\n")` 会把它当换行，切出来的行数比控件
        实际的行数多，样式就套到错误的行上。
        """
        self.assertEqual(split_document_lines("甲\r乙"), ["甲\r乙"])
        self.assertEqual(len(split_document_lines("甲\r乙\n丙")), 2)

    def test_vertical_tab_does_not_split(self) -> None:
        """`str.splitlines()` 会在 \\x0b、U+2028 处断开，这里不能。"""
        self.assertEqual(split_document_lines("甲\x0b乙"), ["甲\x0b乙"])
        self.assertEqual(split_document_lines("甲\u2028乙"), ["甲\u2028乙"])

    def test_matches_the_widget_line_model(self) -> None:
        """按 `\\n` 切出来的行数就是换行符数加一（空文本也算一行）。"""
        cases = [
            "甲\n乙\n丙\n",
            "甲\n乙\n丙",
            "",
            "\n",
            "甲\r\n乙\r\n丙\r\n",
            "甲\r乙\n丙\r",
        ]
        for text in cases:
            with self.subTest(text=repr(text)):
                self.assertEqual(len(split_document_lines(text)),
                                 text.count("\n") + 1)


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class IncrementalParityTests(unittest.TestCase):
    """做一次真实改动，增量结果必须和整篇重解析逐项一致。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"
        folder = self.tmp / "简记日记"
        folder.mkdir()
        self.doc = folder / "样例.md"
        self.doc.write_text(DOCUMENT, encoding="utf-8")
        storage.set_default_folder(folder)

        self.root = tk.Tk()
        self.root.geometry("1180x760")
        self.app = app_main.JianJiApp(self.root)
        self.app.open_file(self.doc)
        self.root.update()

    def tearDown(self) -> None:
        self.root.destroy()
        storage.default_state_path = self._real_state

    def test_every_edit_matches_a_full_reparse(self) -> None:
        used_incremental = 0
        fell_back = 0
        for label, operation in parity_cases():
            with self.subTest(编辑=label):
                try:
                    operation(self.app)
                except tk.TclError:
                    continue
                if not restyle(self.app):
                    fell_back += 1
                    continue
                used_incremental += 1
                after_incremental = snapshot(self.app)
                self.app._apply_markdown_styles()
                self.assertIsNone(
                    first_difference(after_incremental, snapshot(self.app)),
                    f"「{label}」走增量之后的样式与整篇重解析不一致")
        # 大部分情况都该走增量；全都退回整篇的话，这个测试就白跑了
        self.assertGreater(used_incremental, len(parity_cases()) // 2)
        self.assertLess(fell_back, len(parity_cases()) // 2)

    def test_a_long_run_of_edits_stays_in_sync(self) -> None:
        """连着敲一串、中间不整篇重解析，最后仍然要和整篇结果一致。"""
        for step in range(12):
            self.app.editor.mark_set("insert", "3.3")
            self.app._refresh_cursor_line()
            self.app.editor.insert("insert", f"第{step}步 ")
            self.assertTrue(restyle(self.app), f"第 {step} 步没能走增量")
        consecutive = snapshot(self.app)
        self.app._apply_markdown_styles()
        self.assertIsNone(first_difference(consecutive, snapshot(self.app)))

    def test_line_model_never_drifts_from_the_widget(self) -> None:
        """每一步之后，缓存的行数都必须等于控件的行号。"""
        for label, operation in parity_cases():
            try:
                operation(self.app)
            except tk.TclError:
                continue
            restyle(self.app)
            with self.subTest(编辑=label):
                self.assertEqual(len(self.app._doc_lines),
                                 self.app._editor_line_count())
                self.assertEqual(len(self.app._doc_blocks),
                                 len(self.app._doc_lines))
                self.assertEqual(len(self.app._doc_in_code),
                                 len(self.app._doc_lines))
                self.assertEqual(len(self.app._doc_fence),
                                 len(self.app._doc_lines))
                self.assertEqual(len(self.app._doc_delimiter),
                                 len(self.app._doc_lines))

    def test_opening_a_fence_puts_the_whole_tail_in_code(self) -> None:
        """在开头插一个围栏：它会把后面一大段都变成代码块。

        样例文档自己带一段围栏，所以新开的这个会被那个 ```` ```python ```` 闭合——
        正好用来验证「跨行状态一路传下去」：从新围栏起、一直到原来的闭合围栏
        （含）都在代码块里，再往后就不在了。

        这份文档比比对窗口还短，增量能把整段一起重算，所以走增量是合法的；
        结果仍须与整篇重解析逐项一致。长文档下同样的改动会被 `_region_context`
        的尾状态校验拦下、退回整篇——见
        `IncrementalIsActuallyIncrementalTests.test_an_unclosed_fence_forces_a_full_reparse`。
        """
        self.app.editor.mark_set("insert", "3.0")
        self.app._refresh_cursor_line()
        self.app.editor.insert("insert", "```\n")
        restyle(self.app)

        self.assertEqual(self.app._doc_lines[2], "```")
        close = self.app._doc_lines.index("```python")
        self.assertTrue(all(self.app._doc_in_code[2:close + 1]),
                        f"新围栏没有一路延伸到第 {close + 1} 行的闭合围栏")
        self.assertFalse(self.app._doc_in_code[close + 1],
                         "闭合围栏之后不该还算代码块")

        after = snapshot(self.app)
        self.app._apply_markdown_styles()
        self.assertIsNone(first_difference(after, snapshot(self.app)))


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class IncrementalIsActuallyIncrementalTests(unittest.TestCase):
    """增量路径必须真的只动改动区，不能悄悄退化成整篇。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"
        folder = self.tmp / "简记日记"
        folder.mkdir()
        self.doc = folder / "长文档.md"
        # 够长才有说服力：整篇重解析的 Tcl 调用次数与行数成正比
        self.doc.write_text(DOCUMENT * 12, encoding="utf-8")
        storage.set_default_folder(folder)

        self.root = tk.Tk()
        self.root.geometry("1180x760")
        self.app = app_main.JianJiApp(self.root)
        self.app.open_file(self.doc)
        self.root.update()

    def tearDown(self) -> None:
        self.root.destroy()
        storage.default_state_path = self._real_state

    def _tag_calls(self, action) -> int:
        proxy = TclCounter(self.app.editor.tk)
        self.app.editor.tk = proxy
        try:
            action()
        finally:
            self.app.editor.tk = proxy._real
        return proxy.calls.get("tag", 0)

    def test_typing_touches_far_fewer_lines_than_a_full_reparse(self) -> None:
        # 挑一行**普通正文**。表格行（分隔行、单元格）和围栏行会让增量主动退回
        # 整篇重算——那是它对跨行语法的保守策略，拿那种行来量就把保守当成了没生效。
        # DOCUMENT 第 3 行是第一段正文；表格在 19–22 行、围栏在 24–27 行。
        line = 3
        self.assertNotIn("|", self.app._doc_lines[line - 1])
        self.assertFalse(self.app._doc_in_code[line - 1])

        def type_and_restyle_incrementally() -> None:
            self.app.editor.mark_set("insert", f"{line}.2")
            self.app._refresh_cursor_line()
            self.app.editor.insert("insert", "字")
            self.assertTrue(self.app._apply_markdown_styles_incremental())

        def type_and_restyle_fully() -> None:
            self.app.editor.mark_set("insert", f"{line}.2")
            self.app._refresh_cursor_line()
            self.app.editor.insert("insert", "字")
            self.app._apply_markdown_styles()

        incremental = self._tag_calls(type_and_restyle_incrementally)
        # 把刚敲进去的字删掉、整篇重算一遍，让两次测量的文本完全一样
        self.app.editor.delete(f"{line}.2", f"{line}.3")
        self.app._apply_markdown_styles()
        full = self._tag_calls(type_and_restyle_fully)
        self.assertGreater(full, incremental * 4,
                           f"整篇用了 {full} 次 tag 调用，增量用了 {incremental} 次，"
                           "增量看起来并没有真的变增量")

    def test_an_unclosed_fence_forces_a_full_reparse(self) -> None:
        """在开头开一个**不闭合**的围栏：后面几百行的「是否代码块」全变了。

        比对窗口只看得见光标附近那几十行，看不出「区外的围栏状态也变了」。
        这种时候必须老实退回整篇重算，否则区外的样式会留在原地——
        这正是 `_region_context` 里那道尾状态校验存在的理由。
        """
        self.app.editor.mark_set("insert", "3.0")
        self.app._refresh_cursor_line()
        self.app.editor.insert("insert", "```\n")
        self.assertFalse(self.app._apply_markdown_styles_incremental())

    def test_scrolling_does_not_reparse(self) -> None:
        """滚动只该重画，不该重解析——解析结果随文本一起缓存。"""
        before = snapshot(self.app)
        self.app.editor.yview_moveto(0.5)
        self.root.update()
        self.app._redraw_margins()
        self.assertIsNone(first_difference(before, snapshot(self.app)))


if __name__ == "__main__":
    unittest.main()
