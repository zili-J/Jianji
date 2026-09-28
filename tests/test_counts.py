"""顶栏的字数 / 字符数：计数规则，以及「只在鼠标经过时显示」。

用户要求：「增加字数显示。显示在专注模式标识所在行，且只在鼠标经过时显示，
显示两个内容，字数和字符数。」

这一组守三件事：

① **两个数各算各的**——字数回答「我写了多少东西」（标点、Markdown 记号不计），
   字符数回答「这篇正文有多长」（除换行外全算）；
② **平时不占位置**，指针经过顶栏那一行才出来，离开就收回去；
③ **指针在顶栏内部挪动时不能一闪一闪**——Tk 在父控件与子控件之间也会发
   `<Leave>`，所以判据是按指针位置算的，不是收到 `<Leave>` 就藏。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import storage  # noqa: E402
from main import document_counts  # noqa: E402

import tkinter as tk  # noqa: E402

try:
    _probe = tk.Tk()
    _probe.destroy()
    HAS_DISPLAY = True
except tk.TclError:  # pragma: no cover
    HAS_DISPLAY = False

DOC = "# 我的日记\n\n今天把留白那件事收尾了。\n"


class DocumentCountTests(unittest.TestCase):
    """计数规则本身（纯函数，没有图形环境也能跑）。"""

    def test_chinese_counts_one_per_character(self) -> None:
        self.assertEqual(document_counts("你好世界"), (4, 4))

    def test_latin_counts_one_per_word(self) -> None:
        self.assertEqual(document_counts("hello world"), (2, 11))

    def test_mixed_chinese_and_latin(self) -> None:
        self.assertEqual(document_counts("hello 你好"), (3, 8))

    def test_punctuation_counts_as_characters_but_not_as_words(self) -> None:
        self.assertEqual(document_counts("你好，世界！"), (4, 6))

    def test_markdown_marks_are_not_words(self) -> None:
        self.assertEqual(document_counts("# 我的日记"), (4, 6))
        self.assertEqual(document_counts("**粗体**"), (2, 6))
        self.assertEqual(document_counts("- 第一项"), (3, 5))

    def test_newlines_are_not_characters(self) -> None:
        """换行是排版结构，不是内容——不然一百行的文稿会平白多出一百个。"""
        self.assertEqual(document_counts("第一行\n第二行"), (6, 6))

    def test_carriage_returns_are_not_characters_either(self) -> None:
        self.assertEqual(document_counts("a\r\nb"), (2, 2))

    def test_digits_count_as_one_word_each(self) -> None:
        self.assertEqual(document_counts("2026 09 28"), (3, 10))

    def test_hyphenated_and_apostrophised_latin_is_one_word(self) -> None:
        self.assertEqual(document_counts("state-of-the-art")[0], 1)
        self.assertEqual(document_counts("don't")[0], 1)

    def test_empty_text_is_zero(self) -> None:
        self.assertEqual(document_counts(""), (0, 0))

    def test_whitespace_counts_as_characters_but_not_as_words(self) -> None:
        # 三个空格 + 一个空格 = 4 个字符；中间那两个换行不算
        self.assertEqual(document_counts("   \n\n "), (0, 4))

    def test_the_two_numbers_differ_on_a_real_document(self) -> None:
        """真要是一样，这两个数就没必要并排显示了。"""
        words, characters = document_counts(DOC)
        self.assertEqual(words, 15)          # 我的日记 4 + 正文 11
        self.assertEqual(characters, 18)     # 21 个字符减掉 3 个换行
        self.assertGreater(characters, words)

    def test_it_never_returns_a_negative_character_count(self) -> None:
        for text in ("", "\n", "\r", "\r\n", "\n\n\n"):
            words, characters = document_counts(text)
            self.assertGreaterEqual(words, 0)
            self.assertGreaterEqual(characters, 0)


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class TopBarCountsTests(unittest.TestCase):
    """顶栏那一行：默认不显示，指针经过才出来。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        self.doc = folder / "我的日记.md"
        self.doc.write_text(DOC, encoding="utf-8")
        storage.set_default_folder(folder)

        from main import JianJiApp

        self.root = tk.Tk()
        self.root.geometry("1180x760")
        self.app = JianJiApp(self.root)
        self.root.update()

    def tearDown(self) -> None:
        storage.default_state_path = self._real_state
        self.root.destroy()

    def _open_doc(self) -> None:
        self.app.open_file(self.doc)
        self.root.update()

    def _hover(self) -> None:
        self.app._on_topbar_enter()
        self.root.update()

    # ---- 默认与位置 ----

    def test_it_is_hidden_until_the_pointer_comes_by(self) -> None:
        self._open_doc()
        self.assertFalse(self.app.count_label.winfo_ismapped(),
                         "平时不该占顶栏的位置")

    def test_it_sits_on_the_same_row_as_the_focus_button(self) -> None:
        """用户要求：显示在「专注模式」标识所在的那一行。"""
        self._open_doc()
        self._hover()
        self.assertEqual(self.app.count_label.grid_info()["row"],
                         self.app.focus_button.grid_info()["row"])
        self.assertEqual(self.app.count_label.master, self.app.focus_button.master)

    # ---- 显示内容 ----

    def test_it_shows_both_numbers(self) -> None:
        self._open_doc()
        self._hover()
        self.assertTrue(self.app.count_label.winfo_ismapped())
        text = self.app.count_label.cget("text")
        self.assertIn("字数", text)
        self.assertIn("字符数", text)

    def test_the_numbers_match_the_document(self) -> None:
        self._open_doc()
        self._hover()
        expected_words, expected_characters = document_counts(
            self.app.editor.get("1.0", "end-1c"))
        text = self.app.count_label.cget("text")
        self.assertIn(f"字数 {expected_words:,}", text)
        self.assertIn(f"字符数 {expected_characters:,}", text)

    def test_big_numbers_get_thousand_separators(self) -> None:
        self._open_doc()
        self.app.editor.insert("1.0", "字" * 1200)
        self.root.update()
        if not self.app.dirty:
            self.app._on_editor_modified()
        self._hover()
        self.assertIn("1,2", self.app.count_label.cget("text"))

    def test_an_empty_document_shows_nothing(self) -> None:
        self._hover()                       # 没有打开任何一篇
        self.assertFalse(self.app.count_label.winfo_ismapped())

    def test_it_follows_the_typing_while_it_is_visible(self) -> None:
        self._open_doc()
        self._hover()
        before = self.app.count_label.cget("text")
        self.app.editor.insert("end-1c", "又写了几个字。")
        self.root.update()
        if not self.app.dirty:
            self.app._on_editor_modified()
        self.root.update()
        self.assertNotEqual(self.app.count_label.cget("text"), before)

    # ---- 什么时候收回去 ----

    def test_it_hides_again_when_the_pointer_leaves_the_row(self) -> None:
        self._open_doc()
        self._hover()
        self.app._pointer_is_on_topbar = lambda: False
        self.app._hide_counts_if_outside()
        self.root.update()
        self.assertFalse(self.app.count_label.winfo_ismapped())

    def test_moving_inside_the_row_keeps_it_visible(self) -> None:
        """指针从 topbar 挪到它自己的子控件上时 Tk 也会发 `<Leave>`——不能闪。"""
        self._open_doc()
        self._hover()
        self.app._pointer_is_on_topbar = lambda: True
        self.app._hide_counts_if_outside()
        self.root.update()
        self.assertTrue(self.app.count_label.winfo_ismapped())

    def test_leaving_cancels_a_pending_hide(self) -> None:
        """先出后进（离开的定时器还没到又回来了）不能把它藏掉。"""
        self._open_doc()
        self._hover()
        self.app._pointer_is_on_topbar = lambda: True
        self.app._on_topbar_leave()          # 排一次「隐藏」
        self.app._on_topbar_enter()          # 立刻又回来
        self.root.update()
        self.assertTrue(self.app.count_label.winfo_ismapped())

    def test_the_pointer_check_walks_up_the_widget_tree(self) -> None:
        """判据是「指针压在 topbar 或者它的子控件上」，所以要往上找父控件。"""
        widget = self.app.count_label
        while widget is not None:
            if widget is self.app.topbar:
                break
            widget = getattr(widget, "master", None)
        self.assertIsNotNone(widget, "count_label 应当是 topbar 的后代")

    # ---- 绑定 ----

    def test_every_widget_on_the_row_is_bound(self) -> None:
        """`<Enter>`/`<Leave>` 在父控件与子控件之间挪动时都会发，
        所以这一行上的每个控件都得绑上，否则横向移动就会闪。"""
        for name, widget in (("topbar", self.app.topbar),
                             ("status_label", self.app.status_label),
                             ("count_label", self.app.count_label),
                             ("focus_button", self.app.focus_button)):
            self.assertIn("_on_topbar_enter", widget.bind("<Enter>"), name)
            self.assertIn("_on_topbar_leave", widget.bind("<Leave>"), name)

    def test_the_hide_timer_is_cancelled_on_destroy(self) -> None:
        """窗口销毁时还排着期的话，回调会在解释器销毁后触发。"""
        self._open_doc()
        self.app._on_topbar_leave()
        self.assertIsNotNone(self.app._counts_job)
        self.app._cancel_after_jobs()
        self.assertIsNone(self.app._counts_job)


if __name__ == "__main__":
    unittest.main()
