"""内容搜索：Ctrl+F，两种范围（当前文稿 / 全部文稿）。

用户要求：「在软件中加入内容搜索功能，ctrl+F为快捷键。1.可全局搜索所有文档；
2.可搜索当前文档内容。」

这一组守四件事：

① **搜索规则是字面量、不区分大小写**——用户敲 `(` 就是在找左括号，不是正则写错了；
② **行号 1 起、列号 0 起**，拼起来能直接当 Tk 的 `行.列` 索引用；
③ **两条路径各管各的**：本文范围把光标带到命中处，全局范围先选结果、回车才打开
   （否则按一次方向键就把正在看的那篇文档换掉）；
④ **搜索条是浮出来的，不进 grid**——开一次搜索不能让正文重新排版。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import search  # noqa: E402
import storage  # noqa: E402

import tkinter as tk  # noqa: E402

try:
    _probe = tk.Tk()
    _probe.destroy()
    HAS_DISPLAY = True
except tk.TclError:  # pragma: no cover
    HAS_DISPLAY = False

SAMPLE = "# 我的日记\n\n今天下雨了。\n雨停之后有彩虹。\n"


class MatchingTests(unittest.TestCase):
    """匹配规则本身（纯函数，没有图形环境也能跑）。"""

    def test_finds_a_literal_substring(self) -> None:
        hits = search.find_in_text("今天下雨了", "下雨")
        self.assertEqual(len(hits), 1)
        self.assertEqual((hits[0].line, hits[0].column, hits[0].length), (1, 2, 2))

    def test_matching_is_case_insensitive(self) -> None:
        hits = search.find_in_text("Hello World hello", "hello")
        self.assertEqual([hit.column for hit in hits], [0, 12])

    def test_line_numbers_start_at_one(self) -> None:
        hits = search.find_in_text(SAMPLE, "彩虹")
        self.assertEqual(hits[0].line, 4)

    def test_columns_are_character_offsets_within_the_line(self) -> None:
        hits = search.find_in_text("abc目标", "目标")
        self.assertEqual(hits[0].column, 3)

    def test_every_occurrence_on_one_line_is_reported(self) -> None:
        hits = search.find_in_text("雨雨雨", "雨")
        self.assertEqual([hit.column for hit in hits], [0, 1, 2])

    def test_regex_metacharacters_are_literal(self) -> None:
        """用户敲的是「左括号」，不是「一个分组」。"""
        hits = search.find_in_text("a(b) c", "(b)")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].column, 1)
        self.assertEqual(search.find_in_text("abc", "a.c"), [])

    def test_an_empty_query_finds_nothing(self) -> None:
        self.assertEqual(search.find_in_text(SAMPLE, "   "), [])
        self.assertEqual(search.find_in_text(SAMPLE, ""), [])

    def test_crlf_documents_report_the_right_lines(self) -> None:
        hits = search.find_in_text("第一行\r\n第二行\r\n", "第二行")
        self.assertEqual((hits[0].line, hits[0].column), (2, 0))

    def test_the_limit_is_respected(self) -> None:
        hits = search.find_in_text("雨" * 30, "雨", limit=5)
        self.assertEqual(len(hits), 5)

    def test_the_index_property_is_a_tk_index(self) -> None:
        hit = search.find_in_text("abc目标", "目标")[0]
        self.assertEqual(hit.index, "1.3")


class SnippetTests(unittest.TestCase):
    def test_short_lines_are_shown_whole(self) -> None:
        self.assertEqual(search.snippet_for("今天下雨了。", 2, 2), "今天下雨了。")

    def test_leading_whitespace_is_trimmed(self) -> None:
        self.assertEqual(search.snippet_for("    关键词在这里", 4, 3), "关键词在这里")

    def test_long_lines_are_windowed_around_the_match(self) -> None:
        line = "前" * 80 + "目标" + "后" * 120
        snippet = search.snippet_for(line, 80, 2)
        self.assertIn("目标", snippet)
        self.assertTrue(snippet.startswith("…"))
        self.assertTrue(snippet.endswith("…"))
        self.assertLessEqual(len(snippet), search.SNIPPET_MAX + 2)

    def test_a_match_at_the_start_has_no_leading_ellipsis(self) -> None:
        snippet = search.snippet_for("目标" + "后" * 200, 0, 2)
        self.assertFalse(snippet.startswith("…"))
        self.assertTrue(snippet.endswith("…"))

    def test_tabs_become_spaces(self) -> None:
        self.assertNotIn("\t", search.snippet_for("\t关键词", 1, 3))


class TitleTests(unittest.TestCase):
    def test_the_first_h1_names_the_document(self) -> None:
        self.assertEqual(search.title_of("# 我的日记\n正文", Path("x.md")), "我的日记")

    def test_a_leading_blank_line_does_not_cost_the_title(self) -> None:
        """开头先敲一个回车很常见，不能因此让结果列表全变成文件名。"""
        self.assertEqual(search.title_of("\n# 我的日记\n", Path("x.md")), "我的日记")

    def test_without_an_h1_the_file_stem_is_used(self) -> None:
        self.assertEqual(search.title_of("只是一段正文", Path("2026-10-19.md")),
                         "2026-10-19")

    def test_a_non_heading_first_line_is_not_a_title(self) -> None:
        self.assertEqual(search.title_of("正文\n# 后面才有的标题", Path("x.md")), "x")


class FileSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name: str, text: str) -> Path:
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_hits_carry_the_file_path(self) -> None:
        path = self._write("a.md", "有雨\n")
        hits, truncated = search.search_files([path], "雨")
        self.assertFalse(truncated)
        self.assertEqual(hits[0].path, path)
        self.assertEqual(hits[0].title, "a")

    def test_the_total_limit_truncates_and_says_so(self) -> None:
        paths = [self._write(f"{index}.md", "雨\n" * 5) for index in range(3)]
        hits, truncated = search.search_files(paths, "雨", limit=7)
        self.assertEqual(len(hits), 7)
        self.assertTrue(truncated)

    def test_a_single_file_cannot_flood_the_list(self) -> None:
        path = self._write("a.md", "雨\n" * 40)
        hits, _truncated = search.search_files([path], "雨", per_file=5)
        self.assertEqual(len(hits), 5)

    def test_results_follow_the_given_order(self) -> None:
        first = self._write("a.md", "雨\n")
        second = self._write("b.md", "雨\n")
        hits, _truncated = search.search_files([second, first], "雨")
        self.assertEqual([hit.path for hit in hits], [second, first])

    def test_an_unreadable_file_is_skipped_not_fatal(self) -> None:
        bad = self.tmp / "bad.md"
        bad.write_bytes(b"\xff\xfe\x00bad")
        good = self._write("good.md", "雨\n")
        hits, _truncated = search.search_files([bad, good], "雨")
        self.assertEqual([hit.path for hit in hits], [good])

    def test_a_custom_reader_is_used(self) -> None:
        """界面层用它挂缓存、以及让「还没存盘的当前文稿」走编辑区里的正文。"""
        path = self._write("a.md", "磁盘上的旧内容\n")
        hits, _truncated = search.search_files(
            [path], "编辑区", read=lambda _path: "# 新\n编辑区里的新内容\n")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].line, 2)

    def test_a_reader_returning_none_is_skipped(self) -> None:
        path = self._write("a.md", "雨\n")
        hits, _truncated = search.search_files([path], "雨", read=lambda _path: None)
        self.assertEqual(hits, [])


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class SearchUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        self.folder = folder
        self.first = folder / "第一篇.md"
        self.first.write_text(
            "# 第一篇\n\n今天下雨了。\n雨停之后有彩虹。\n", encoding="utf-8")
        self.second = folder / "第二篇.md"
        self.second.write_text("# 第二篇\n\n另一天也有雨。\n", encoding="utf-8")
        (folder / "晴天.md").write_text("# 晴天\n\n今天大太阳。\n", encoding="utf-8")
        sub = folder / "工作"
        sub.mkdir()
        self.nested = sub / "会议.md"
        self.nested.write_text("# 会议\n\n下雨也要开会。\n", encoding="utf-8")
        trash = folder / ".简记回收站"
        trash.mkdir()
        (trash / "已删除.md").write_text("# 已删除\n\n雨天的旧稿。\n", encoding="utf-8")

        storage.set_default_folder(folder)

        from main import JianJiApp

        self.root = tk.Tk()
        self.root.geometry("1180x760")
        self.app = JianJiApp(self.root)
        self.app.open_file(self.first)
        self.root.update()

    def tearDown(self) -> None:
        storage.default_state_path = self._real_state
        self.root.destroy()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- 辅助 --

    def _open(self) -> None:
        self.app.open_search()
        self.root.update()

    def _type(self, text: str) -> None:
        self.app.search_var.set(text)
        self.app._run_search()
        self.root.update()

    def _hits(self) -> list:
        return self.app.search_hits

    def _paths(self) -> set:
        return {hit.path for hit in self.app.search_hits}

    # -- 快捷键与打开 --

    def test_the_bar_is_hidden_until_asked(self) -> None:
        self.assertFalse(self.app.search_open)

    def test_ctrl_f_is_bound_to_the_editor_and_the_window(self) -> None:
        """两处都要绑：焦点在正文里时走控件级，焦点在别处时走窗口级。

        **不在这里 `event_generate("<Control-f>")`**：键事件要控件真有键盘焦点才
        送得到，隐藏桌面上跑测试时不可靠（`test_list_editing` 也踩过这一条）。
        所以这里钉「绑定指向谁」，行为另外直接调处理函数验。
        """
        self.assertIn("_on_find_shortcut", self.app.editor.bind("<Control-f>"))
        self.assertIn("_on_find_shortcut", self.root.bind("<Control-f>"))

    def test_ctrl_f_opens_the_bar_and_focuses_the_entry(self) -> None:
        self.app._on_find_shortcut()
        self.root.update()
        self.assertTrue(self.app.search_open)
        self.assertTrue(self.app.search_bar.winfo_ismapped())
        # 焦点在隐藏桌面上读不准（`focus_get()` 可能是 None），所以只钉「条子开着
        # 且输入框已经建好」；真正把焦点交出去的那句在 `open_search` 里。
        self.assertIsNotNone(self.app.search_entry)

    def test_ctrl_f_again_closes_it(self) -> None:
        self._open()
        self.app._on_find_shortcut()
        self.root.update()
        self.assertFalse(self.app.search_open)

    def test_the_default_scope_is_the_current_document(self) -> None:
        self._open()
        self.assertEqual(self.app.search_scope, "doc")

    def test_the_bar_does_not_change_the_editor_width(self) -> None:
        """搜索条是 place 出来的：开一次搜索不能让正文重新排版。"""
        before = self.app.editor.winfo_width()
        self._open()
        self._type("雨")
        self.root.update()
        self.assertEqual(self.app.editor.winfo_width(), before)

    # -- 本文范围 --

    def test_typing_counts_the_matches_in_the_current_document(self) -> None:
        self._open()
        self._type("雨")
        self.assertEqual(len(self._hits()), 2)
        self.assertEqual(self.app.search_status.cget("text"), "1/2")

    def test_matches_are_highlighted(self) -> None:
        self._open()
        self._type("雨")
        ranges = self.app.editor.tag_ranges("search_hit")
        self.assertEqual(len(ranges), 4)          # 两处，各一对起止

    def test_the_first_match_is_selected_as_you_type(self) -> None:
        self._open()
        self._type("彩虹")
        self.assertEqual(self.app.search_index, 0)
        self.assertEqual(self.app.editor.index("insert"), "4.5")
        self.assertTrue(self.app.editor.tag_ranges("search_current"))

    def test_enter_walks_through_the_matches_and_wraps(self) -> None:
        self._open()
        self._type("雨")
        self.app._on_search_enter()
        self.assertEqual(self.app.search_index, 1)
        self.assertEqual(self.app.editor.index("insert"), "4.0")
        self.app._on_search_enter()
        self.assertEqual(self.app.search_index, 0, "最后一条之后要绕回第一条")
        self.assertEqual(self.app.editor.index("insert"), "3.3")

    def test_up_goes_backwards(self) -> None:
        self._open()
        self._type("雨")
        self.app._search_step(-1)
        self.assertEqual(self.app.search_index, 1)

    def test_a_query_without_matches_says_so(self) -> None:
        self._open()
        self._type("根本没有这个词")
        self.assertEqual(self.app.search_status.cget("text"), "无结果")
        self.assertEqual(self.app.editor.tag_ranges("search_hit"), ())

    def test_escape_closes_the_bar_and_drops_the_highlights(self) -> None:
        self._open()
        self._type("雨")
        self.assertTrue(self.app.editor.tag_ranges("search_hit"))
        self.assertTrue(self.app.search_entry.bind("<Escape>"),
                        "输入框里要绑 Esc，否则按了没反应")
        self.app._search_escape()
        self.root.update()
        self.assertFalse(self.app.search_open)
        self.assertEqual(self.app.editor.tag_ranges("search_hit"), ())

    def test_escape_while_reading_the_document_still_closes_the_bar(self) -> None:
        """点开一条结果之后焦点在正文里，Esc 也要能关掉搜索。"""
        self._open()
        self._type("雨")
        self.assertIn("_on_escape", self.root.bind("<Escape>"))
        self.app._on_escape()
        self.root.update()
        self.assertFalse(self.app.search_open)

    def test_escape_falls_back_to_leaving_focus_mode(self) -> None:
        """搜索没开的时候，Esc 还是原来的「退出专注模式」。"""
        self.app.toggle_focus()
        self.root.update()
        self.assertTrue(self.app.focus_mode)
        self.app._on_escape()
        self.root.update()
        self.assertFalse(self.app.focus_mode)

    def test_a_selection_becomes_the_query(self) -> None:
        """和浏览器一样：选中一段再按 Ctrl+F，输入框里就是那几个字。"""
        self.app.editor.tag_add("sel", "4.0", "4.2")
        self._open()
        self.assertEqual(self.app.search_var.get(), "雨停")

    def test_editing_the_document_moves_the_highlights(self) -> None:
        """标签按 行.列 贴的，前面插字之后必须重贴，否则高亮会歪。"""
        self._open()
        self._type("彩虹")
        self.app.editor.mark_set("insert", "1.0")
        self.app.editor.insert("1.0", "前言\n")
        self.root.update()
        ranges = self.app.editor.tag_ranges("search_hit")
        self.assertEqual(len(ranges), 2)
        self.assertEqual(self.app.editor.index(ranges[0]), "5.5")

    # -- 全局范围 --

    def test_switching_to_all_searches_every_document(self) -> None:
        self._open()
        self._type("雨")
        self.app.set_search_scope("all")
        self.root.update()
        self.assertEqual(self.app.search_scope, "all")
        self.assertEqual(self._paths(), {self.first, self.second, self.nested})

    def test_the_trash_is_not_searched(self) -> None:
        self._open()
        self._type("旧稿")
        self.app.set_search_scope("all")
        self.root.update()
        self.assertEqual(self._hits(), [], "回收站里的稿子不该出现在搜索结果里")

    def test_switching_scope_keeps_the_query(self) -> None:
        self._open()
        self._type("雨")
        self.app.set_search_scope("all")
        self.root.update()
        self.assertEqual(self.app.search_var.get(), "雨")

    def test_global_results_show_a_snippet_and_a_title(self) -> None:
        self._open()
        self._type("开会")
        self.app.set_search_scope("all")
        self.root.update()
        self.assertEqual(len(self._hits()), 1)
        self.assertEqual(self._hits()[0].title, "会议")
        self.assertIn("开会", self._hits()[0].snippet)
        self.assertTrue(self.app.search_results.winfo_ismapped())

    def test_opening_a_result_switches_document_and_jumps_to_the_line(self) -> None:
        self._open()
        self._type("开会")
        self.app.set_search_scope("all")
        self.root.update()
        self.app._open_search_hit(0)
        self.root.update()
        self.assertEqual(self.app.current_path, self.nested)
        self.assertEqual(self.app.editor.index("insert"), "3.4")

    def test_arrow_keys_in_global_scope_do_not_switch_documents(self) -> None:
        """全局范围下方向键只挪选中项，换文档得按回车——不然一路翻过去全打开了。"""
        self._open()
        self._type("雨")
        self.app.set_search_scope("all")
        self.root.update()
        opened = self.app.current_path
        self.app._search_step(1)
        self.root.update()
        self.assertEqual(self.app.current_path, opened)

    def test_global_navigation_does_not_paint_the_open_document(self) -> None:
        """选中项在别的文稿里时，「行.列」是那篇的坐标，不能照贴到这一篇上。"""
        self._open()
        self._type("雨")
        self.app.set_search_scope("all")
        self.root.update()
        for _ in range(len(self._hits())):
            self.app._search_step(1)
            self.root.update()
            hit = self._hits()[self.app.search_index]
            if hit.path != self.app.current_path:
                self.assertEqual(
                    self.app.editor.tag_ranges("search_current"), (),
                    "选中的结果在别的文稿里，当前正文不该有「当前命中」高亮")

    def test_enter_opens_the_selected_result(self) -> None:
        self._open()
        self._type("开会")
        self.app.set_search_scope("all")
        self.root.update()
        self.app._on_search_enter()
        self.root.update()
        self.assertEqual(self.app.current_path, self.nested)

    def test_without_a_document_the_scope_is_all(self) -> None:
        self.app._clear_editor()
        self.app.search_query = ""
        self.app.close_search()
        self.app.open_search()
        self.root.update()
        self.assertEqual(self.app.search_scope, "all")

    def test_the_document_button_is_disabled_without_a_document(self) -> None:
        self.app._clear_editor()
        self.app.close_search()
        self.app.open_search()
        self.root.update()
        self.assertEqual(str(self.app.search_scope_buttons["doc"].cget("state")),
                         "disabled")

    # -- 换主题 --

    def test_the_search_survives_a_theme_rebuild(self) -> None:
        self._open()
        self._type("雨")
        self.app.set_theme("dark")
        self.root.update()
        self.assertTrue(self.app.search_open, "换主题后搜索条应该还在")
        self.assertEqual(self.app.search_var.get(), "雨")
        self.assertEqual(len(self._hits()), 2)
        self.assertTrue(self.app.editor.tag_ranges("search_hit"))


if __name__ == "__main__":
    unittest.main()
