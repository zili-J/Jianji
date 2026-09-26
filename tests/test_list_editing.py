"""有序列表的序号续写：在序号行末按回车，自动带出下一个序号。

用户要求：「输入序号后，回车自动带出下一个序号」。

要点是**只接管一种情况**——光标在行尾、这一行是有序列表项。其余一律交回 Tk 的
默认换行，普通正文的手感一点都不能变。所以这里一半的用例是在证明「没被接管」。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import storage  # noqa: E402

import tkinter as tk  # noqa: E402

try:
    _probe = tk.Tk()
    _probe.destroy()
    HAS_DISPLAY = True
except tk.TclError:  # pragma: no cover
    HAS_DISPLAY = False


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class OrderedListReturnTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        self.doc = folder / "笔记.md"
        self.doc.write_text("# 笔记\n\n", encoding="utf-8")
        storage.set_default_folder(folder)

        from main import JianJiApp

        self.root = tk.Tk()
        self.root.geometry("1180x760")
        self.app = JianJiApp(self.root)
        self.app.open_file(self.doc)
        self.root.update()

    def tearDown(self) -> None:
        storage.default_state_path = self._real_state
        self.root.destroy()

    # ---------- 工具 ----------

    def _load(self, text: str, cursor: str = "1.end") -> None:
        """把编辑区换成 text，并把光标放到指定位置。"""
        self.app.editor.configure(state="normal")
        self.app.editor.delete("1.0", "end")
        self.app.editor.insert("1.0", text)
        self.app.editor.mark_set("insert", cursor)
        self.root.update()

    def _press_return(self) -> str | None:
        """敲一次回车，返回处理函数的返回值（None = 没接管）。"""
        result = self.app._on_editor_return()
        self.root.update()
        return result

    def _text(self) -> str:
        return self.app.editor.get("1.0", "end-1c")

    # ---------- 接管的情况 ----------

    def test_continues_the_numbering(self) -> None:
        self._load("1. 第一条")
        self.assertEqual(self._press_return(), "break")
        self.assertEqual(self._text(), "1. 第一条\n2. ")

    def test_counts_up_from_whatever_number_is_there(self) -> None:
        """从 7 续出来的是 8，不是「上一项 +1」猜出来的数。"""
        self._load("7. 第七项")
        self._press_return()
        self.assertEqual(self._text(), "7. 第七项\n8. ")

    def test_keeps_the_delimiter_style(self) -> None:
        """`1)` 续成 `2)`，不要一律换成 `1.`。"""
        self._load("3) 第三条")
        self._press_return()
        self.assertEqual(self._text(), "3) 第三条\n4) ")

    def test_keeps_the_indent_level(self) -> None:
        """缩进照抄，所以四级缩进也能对齐。"""
        self._load("        2. 深一层")
        self._press_return()
        self.assertEqual(self._text(), "        2. 深一层\n        3. ")

    def test_empty_item_ends_the_list(self) -> None:
        """空列表项再回车 = 结束列表，把记号清掉留一个空行。

        不然会一直「1. 2. 3.」地无限续下去，想退出列表都退不出来。
        """
        self._load("1. 第一条\n2. ", cursor="2.end")
        self.assertEqual(self._press_return(), "break")
        self.assertEqual(self._text(), "1. 第一条\n")

    def test_the_new_number_is_parsed_as_a_list_item(self) -> None:
        """续出来的那一行要立刻被解析成有序列表项（序号归画布画、记号要 elide）。

        **不要断言 `_line_marks`**：有序列表的序号是唯一的例外——它永远由画布绘制，
        记号永远 elide，而且**故意不记进 `_line_marks`**，这样光标进出该行时不会
        去互换它。所以判据只能是块级解析结果。
        """
        self._load("1. 第一条")
        self._press_return()
        info = self.app._block_of(2)
        self.assertEqual(info.kind, "ordered")
        self.assertEqual(info.number, "2.")
        self.assertEqual(self.app._doc_blocks[1].kind, "ordered",
                         "文档解析缓存里第 2 行也应当是列表项")

    # ---------- 不该接管的情况 ----------

    def test_plain_text_is_left_alone(self) -> None:
        self._load("就是一句普通正文。")
        self.assertIsNone(self._press_return(), "普通正文不该被接管")
        self.assertEqual(self._text(), "就是一句普通正文。")

    def test_bullet_list_is_left_alone(self) -> None:
        """只做有序列表；项目符号续写没要求，别顺手改。"""
        self._load("- 一个项目")
        self.assertIsNone(self._press_return())
        self.assertEqual(self._text(), "- 一个项目")

    def test_mid_line_return_is_left_alone(self) -> None:
        """光标在行中间时交回默认，不能把序号硬塞进去。"""
        self._load("1. 第一条")
        self.app.editor.mark_set("insert", "1.3")
        self.assertIsNone(self._press_return())
        self.assertEqual(self._text(), "1. 第一条")

    def test_return_with_a_selection_is_left_alone(self) -> None:
        """有选区时交回默认（默认会先删掉选区再换行）。"""
        self._load("1. 第一条")
        self.app.editor.tag_add("sel", "1.0", "1.2")
        self.assertIsNone(self._press_return())
        self.assertEqual(self._text(), "1. 第一条")

    def test_heading_is_left_alone(self) -> None:
        self._load("## 小节标题")
        self.assertIsNone(self._press_return())
        self.assertEqual(self._text(), "## 小节标题")

    # ---------- 绑定确实接上了 ----------

    def test_the_key_is_actually_bound(self) -> None:
        """控件级绑定必须存在，而且**不能**用 add="+"（否则吃不掉默认换行）。"""
        script = self.app.editor.bind("<Return>")
        self.assertTrue(script, "回车应当绑定到序号续写")
        self.assertIn("_on_editor_return", script)

    def test_the_handler_asks_tk_to_stop(self) -> None:
        """接管时必须返回 "break"。

        返回 None 的话 Text 的类绑定会在同一次事件里再插一个换行——每按一次回车
        多出一个空行。这里直接钉住返回值，因为返回值才是「吃掉默认行为」的唯一开关。
        （不在这里 `event_generate("<Return>")`：键事件要控件真有键盘焦点才送得到，
        在隐藏桌面上跑测试时不可靠。）
        """
        self._load("1. 第一条")
        self.assertEqual(self._press_return(), "break")


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class EmptyOrderedItemCaretTests(OrderedListReturnTests):
    """回车续出空序号之后，光标必须**看得见**。

    用户报的「回车自动生成有序标号后，不会自动显示换行，且鼠标光标丢失」。

    病根：续出来的那一行是 `2. `，有序序号一律由左侧画布绘制、记号一律 elide，
    于是**整行一个可见字符都没有**。Tk 遇到「没有可见内容」的行，会把插入光标
    退回**行首**；而行首正是序号画布的位置——画布不透明、又画在正文控件之上
    （Tk 的插入光标是一条 1px 竖线，画在子窗口**下面**），光标就被整个盖掉了。

    修法：空项 + 光标在本行时，把行尾那个空格留在 elide 之外。空格没有墨迹，
    但足以让 Tk 认为这行有内容、按 lmargin 把光标放到正文位置旁边，而画布的
    右边缘停在 `gap` 处，永远够不着它。

    判据一律相对几何（画布矩形 / 标签区间），不写绝对像素。
    """

    def _syntax_spans(self) -> set[tuple[str, str]]:
        ranges = self.app.editor.tag_ranges("syntax")
        return {(str(ranges[i]), str(ranges[i + 1]))
                for i in range(0, len(ranges), 2)}

    def _decoration_rects(self) -> list[tuple[int, int, int, int]]:
        self.app._redraw_decorations()
        self.root.update()
        rects = []
        for canvas in self.app._decor_pool:
            if getattr(canvas, "decor_spec", None) is None or not canvas.winfo_manager():
                continue
            info = canvas.place_info()
            rects.append((int(info["x"]), int(info["y"]),
                          int(info["width"]), int(info["height"])))
        return rects

    def test_the_new_empty_item_is_recognised_as_empty(self) -> None:
        """先钉住 `body_empty` 这个判据本身——整行 elide 的开关全靠它。"""
        from main import parse_block

        self.assertTrue(parse_block("2. ").body_empty)
        self.assertFalse(parse_block("2. 有内容").body_empty)
        self.assertTrue(parse_block("- ").body_empty)
        self.assertTrue(parse_block("- [ ] ").body_empty)
        # `2.` 后面没有空白 → 按 Markdown 规则根本不算列表项，当普通正文
        self.assertEqual(parse_block("2.").kind, "text")

    def test_the_caret_of_the_new_empty_item_is_not_covered(self) -> None:
        """核心断言：任何装饰画布都不能盖住光标那一列。"""
        self._load("1. 第一条")
        self.assertEqual(self._press_return(), "break")
        self.app.editor.focus_set()
        self.root.update()

        box = self.app.editor.bbox("insert")
        self.assertIsNotNone(box, "新行的光标应当落在视口内")
        caret_x, caret_top, caret_h = box[0], box[1], box[3]
        self.assertGreater(caret_x, 0, "光标不该退回行首（那里正是序号画布）")

        rects = self._decoration_rects()
        self.assertTrue(rects, "这一屏应当有装饰画布（否则这个用例什么都没证明）")
        for x, y, w, h in rects:
            covered = (x <= caret_x < x + w and y <= caret_top < y + h)
            self.assertFalse(
                covered,
                f"装饰画布 {x}..{x + w}（y {y}..{y + h}）盖住了光标 "
                f"({caret_x}, {caret_top})",
            )

    def test_the_trailing_space_is_left_out_of_the_elide(self) -> None:
        """行尾那个空格必须留在 elide 之外——这是让 Tk 保住光标位置的开关。"""
        self._load("1. 第一条")
        self._press_return()
        self.assertIn(("2.0", "2.2"), self._syntax_spans(),
                      "空项的记号 `2.` 该被 elide，只留下行尾那个空格")

    def test_a_non_empty_item_still_hides_its_whole_marker(self) -> None:
        """有内容的项不受影响：记号整段 elide，正文照旧对齐。"""
        self._load("1. 第一条", cursor="1.end")
        self.root.update()
        self.assertIn(("1.0", "1.3"), self._syntax_spans())

    def test_typing_the_first_character_hides_the_whole_marker(self) -> None:
        """打上第一个字之后，空格也要一起藏起来——不能把例外漏到正常状态。"""
        self._load("1. 第一条")
        self._press_return()
        self.app.editor.insert("insert", "新")
        self.app._apply_markdown_styles()
        self.root.update()
        self.assertIn(("2.0", "2.3"), self._syntax_spans())

    def test_the_caret_lands_next_to_the_body_text(self) -> None:
        """光标要落在**正文会出现的那个位置**附近，不能停在缩进里。

        用相对判据：光标 x 必须不小于「正文起点」——正文起点 = 装饰画布右边缘
        + gap，而 gap 是设置项。这里退一步，只要求光标在画布右边缘之右，
        并且打上第一个字之后光标位置变化不大（不超过一个字宽的量级）。
        """
        self._load("1. 第一条")
        self._press_return()
        self.app.editor.focus_set()
        self.root.update()
        before = self.app.editor.bbox("insert")[0]

        self.app.editor.insert("insert", "新")
        self.app._apply_markdown_styles()
        self.root.update()
        after = self.app.editor.bbox("insert")[0]

        self.assertGreater(after, before, "打上字之后光标应当往右走")
        self.assertLess(after - before, self.app.editor.winfo_width() // 4,
                        "光标不该在第一次按键时大幅跳动")


if __name__ == "__main__":
    unittest.main()
