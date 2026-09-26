"""新增 Markdown 语法的测试：解析、即时渲染（输入即预览）与左侧装饰。

覆盖：H1–H6 标题、分隔线、围栏代码块、引用、无序/有序/任务列表、
粗体（含 __ 变体）、斜体（含 _ 变体）、行内代码、删除线、高亮、链接与图片。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))     # 让 `import _pinning` 找得到

import storage  # noqa: E402
from _pinning import pin_screen_share  # noqa: E402
from main import (  # noqa: E402
    HEADING_LABELS,
    LINK_RE,
    LIST_INDENT,
    LIST_MARKER_GAP,
    fence_step,
    is_real_display_box,
    list_indent,
    parse_block,
    px,
)

import tkinter as tk  # noqa: E402

try:
    _probe = tk.Tk()
    _probe.destroy()
    HAS_DISPLAY = True
except tk.TclError:  # pragma: no cover
    HAS_DISPLAY = False


class BlockParserTests(unittest.TestCase):
    """块级语法解析（不需要图形界面）。"""

    def test_heading_levels_one_to_six(self) -> None:
        for level in range(1, 7):
            info = parse_block("#" * level + " 标题")
            self.assertEqual(info.kind, "heading")
            self.assertEqual(info.level, level)
            self.assertEqual(info.marker, (0, level + 1))

    def test_seven_hashes_is_not_a_heading(self) -> None:
        self.assertEqual(parse_block("####### 七级").kind, "text")

    def test_heading_without_space_is_not_a_heading(self) -> None:
        self.assertEqual(parse_block("#没有空格").kind, "text")

    def test_horizontal_rules(self) -> None:
        for source in ("---", "***", "___", "- - -", "  ---  "):
            self.assertEqual(parse_block(source).kind, "hr", source)

    def test_dashes_inside_a_paragraph_are_not_a_rule(self) -> None:
        self.assertEqual(parse_block("正文 --- 还有字").kind, "text")
        self.assertEqual(parse_block("--").kind, "text")

    def test_quote(self) -> None:
        info = parse_block("> 引用")
        self.assertEqual(info.kind, "quote")
        self.assertEqual(info.marker, (0, 2))
        self.assertEqual(parse_block(">> 嵌套引用").kind, "quote")

    def test_bullet_markers(self) -> None:
        for marker in ("-", "*", "+"):
            info = parse_block(f"{marker} 项目")
            self.assertEqual(info.kind, "bullet", marker)
            self.assertEqual(info.level, 0)

    def test_bullet_nesting_levels(self) -> None:
        self.assertEqual(parse_block("  - 二层").level, 1)
        self.assertEqual(parse_block("    - 三层").level, 2)
        self.assertEqual(parse_block("\t- 制表符也算一层").level, 2)

    def test_bullet_level_is_capped(self) -> None:
        self.assertEqual(parse_block(" " * 40 + "- 很深").level, 4)

    def test_dash_without_space_is_not_a_bullet(self) -> None:
        self.assertEqual(parse_block("-不是列表").kind, "text")

    def test_ordered_list(self) -> None:
        info = parse_block("3. 第三项")
        self.assertEqual(info.kind, "ordered")
        self.assertEqual(info.number, "3.")
        self.assertEqual(parse_block("12) 第十二项").number, "12)")

    def test_task_list(self) -> None:
        todo = parse_block("- [ ] 待办")
        self.assertEqual(todo.kind, "task")
        self.assertFalse(todo.checked)
        done = parse_block("- [x] 完成")
        self.assertEqual(done.kind, "task")
        self.assertTrue(done.checked)
        self.assertTrue(parse_block("- [X] 大写也算").checked)

    def test_task_beats_bullet(self) -> None:
        """`- [ ]` 也符合无序列表的写法，必须先判任务列表。"""
        self.assertEqual(parse_block("- [ ] 待办").kind, "task")

    def test_rule_beats_bullet(self) -> None:
        self.assertEqual(parse_block("***").kind, "hr")

    def test_plain_text(self) -> None:
        info = parse_block("就是一句普通的话。")
        self.assertEqual(info.kind, "text")
        self.assertIsNone(info.marker)

    def test_trailing_carriage_return_is_tolerated(self) -> None:
        """Tk 的 Text 用 \\r 作行分隔符，get() 取回整行会带结尾 \\r。"""
        self.assertEqual(parse_block("---\r").kind, "hr")
        self.assertEqual(parse_block("1. 项\r").kind, "ordered")
        self.assertEqual(parse_block("# 标题\r").level, 1)

    def test_list_indent_grows_with_level(self) -> None:
        self.assertLess(list_indent(0), list_indent(1))
        self.assertLess(list_indent(1), list_indent(2))

    def test_link_pattern_keeps_label_and_url(self) -> None:
        match = LINK_RE.search("看 [说明](https://example.com/a) 吧")
        self.assertEqual(match.group(1), "")
        self.assertEqual(match.group(2), "说明")
        self.assertEqual(match.group(3), "https://example.com/a")

    def test_image_pattern_is_marked(self) -> None:
        match = LINK_RE.search("![图](a.png)")
        self.assertEqual(match.group(1), "!")
        self.assertEqual(match.group(2), "图")


class FenceStateTests(unittest.TestCase):
    """围栏代码块的状态推进。

    回归：只按「遇到围栏就取反」处理的话，想在一份文档里示范一段围栏代码，
    外层的四个反引号会被内层的三个反引号提前闭合。
    """

    def test_simple_fence_opens_and_closes(self) -> None:
        state, opening = fence_step("```python", None)
        self.assertTrue(opening)
        self.assertEqual(state, ("`", 3))
        state, closing = fence_step("```", state)
        self.assertTrue(closing)
        self.assertIsNone(state)

    def test_longer_fence_is_not_closed_by_a_shorter_one(self) -> None:
        state, _ = fence_step("````markdown", None)
        state, inner = fence_step("```python", state)
        self.assertFalse(inner, "更短的围栏应视为代码内容")
        self.assertEqual(state, ("`", 4), "外层围栏状态不能被内层改掉")
        state, closing = fence_step("````", state)
        self.assertTrue(closing)
        self.assertIsNone(state)

    def test_same_length_fence_closes(self) -> None:
        state, _ = fence_step("````", None)
        state, closing = fence_step("````", state)
        self.assertTrue(closing)
        self.assertIsNone(state)

    def test_tilde_fence_is_a_different_character(self) -> None:
        state, _ = fence_step("~~~", None)
        _state, closing = fence_step("```", state)
        self.assertFalse(closing, "反引号不能闭合波浪线围栏")

    def test_plain_line_inside_a_fence_is_content(self) -> None:
        state, _ = fence_step("```", None)
        new_state, delimiter = fence_step("print('hi')", state)
        self.assertFalse(delimiter)
        self.assertEqual(new_state, state)

    def test_fence_outside_a_block_opens_it(self) -> None:
        state, opening = fence_step("~~~text", None)
        self.assertTrue(opening)
        self.assertEqual(state, ("~", 3))

    def test_line_with_trailing_spaces_still_opens(self) -> None:
        state, opening = fence_step("```   ", None)
        self.assertTrue(opening)
        self.assertEqual(state, ("`", 3))


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class MarkdownRenderTests(unittest.TestCase):
    """编辑器里的即时渲染：标签套用、记号隐藏、装饰绘制。"""

    DOC = (
        "# 一级\n\n## 二级\n\n### 三级\n\n#### 四级\n\n##### 五级\n\n###### 六级\n\n"
        "**粗体** *斜体* __另一种粗体__ _另一种斜体_ ~~删掉~~ ==高亮== `代码`\n\n"
        "- 项目一\n- 项目二\n  - 二层\n    - 三层\n\n"
        "1. 有序一\n2. 有序二\n\n"
        "- [ ] 未完成\n- [x] 已完成\n\n"
        "> 引用第一行\n> 引用第二行\n\n"
        "---\n\n"
        "```python\nprint('hi')\n```\n\n"
        "链接 [说明](https://example.com/a) 与图片 ![示意图](a.png)。\n"
    )

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        self.doc = folder / "语法.md"
        self.doc.write_text(self.DOC, encoding="utf-8")
        storage.set_default_folder(folder)

        from main import JianJiApp

        self.root = tk.Tk()
        self.root.geometry("1180x760")
        self.app = JianJiApp(self.root)
        self.root.update()
        self.app.open_file(self.doc)
        self.root.update()
        # 光标停在文末，让所有语法记号都被隐藏
        self.app.editor.mark_set("insert", "end-1c")
        self.app._apply_styles_without_dirty_flag()
        self.root.update()

    def tearDown(self) -> None:
        storage.default_state_path = self._real_state
        self.root.destroy()

    # ---- 辅助 ----

    def _line_of(self, text: str) -> int:
        """找到包含指定文字的记录行号。"""
        for number, line in enumerate(self.app.editor.get("1.0", "end-1c").split("\r"), start=1):
            if text in line:
                return number
        raise AssertionError(f"文档里找不到 {text!r}")

    def _ranges(self, tag: str) -> list[tuple[str, str]]:
        raw = self.app.editor.tag_ranges(tag)
        return [(str(raw[i]), str(raw[i + 1])) for i in range(0, len(raw), 2)]

    def _in_tag(self, tag: str, index: str) -> bool:
        """index 是否落在该标签覆盖的区间里。

        比 bbox 可靠：文档长了以后，视口外的行 bbox 会返回空，会被误判成「被隐藏」。
        """
        raw = self.app.editor.tag_ranges(tag)
        for i in range(0, len(raw), 2):
            if (self.app.editor.compare(index, ">=", raw[i])
                    and self.app.editor.compare(index, "<", raw[i + 1])):
                return True
        return False

    def _hidden(self, index: str) -> bool:
        """该位置是否被当作语法记号藏起来（syntax 标签会 elide）。"""
        return self._in_tag("syntax", index) and not self._in_tag("syntax_current", index)

    def _span_text(self, start: str, end: str) -> str:
        text = self.app.editor.get("1.0", "end-1c")
        offset = self.app.editor.count("1.0", start)[0]
        return text[offset:offset + self.app.editor.count(start, end)[0]]

    def _decorations(self) -> list[tuple]:
        return [c.decor_spec for c in self.app._decor_pool if c.decor_spec is not None]

    # ---- 隐藏语法记号这件事本身 ----

    def test_syntax_tag_really_elides(self) -> None:
        self.assertEqual(int(self.app.editor.tag_cget("syntax", "elide")), 1,
                         "syntax 标签必须 elide，否则源码记号会一直显示")
        self.assertEqual(int(self.app.editor.tag_cget("syntax_current", "elide")), 0,
                         "光标所在行的记号要显示出来")

    def test_elision_is_visible_on_screen(self) -> None:
        """不只是标签对：第一行确实渲染成隐藏状态。"""
        self.assertTrue(self.app.editor.bbox("1.0")[2] == 0 or self.app.editor.bbox("1.0") is None)
        self.assertGreater(self.app.editor.bbox("1.2")[2], 0)

    # ---- 标题 ----

    def test_all_heading_levels_are_tagged(self) -> None:
        for level in range(1, 7):
            self.assertTrue(self._ranges(f"h{level}"), f"h{level} 应有内容")

    def test_heading_markers_are_hidden(self) -> None:
        line = self._line_of("一级")
        self.assertTrue(self._hidden(f"{line}.0"), "非光标行上的 # 应被隐藏")
        self.assertFalse(self._hidden(f"{line}.2"), "标题正文应可见")

    def test_heading_marker_shows_on_cursor_line(self) -> None:
        line = self._line_of("一级")
        self.app.editor.mark_set("insert", f"{line}.1")
        self.app._apply_styles_without_dirty_flag()
        self.root.update()
        # 标题的 `# ` 现在和列表记号走同一条路：露源码 + 补偿宽度，露出来时挂的是
        # `syntax_marker`（不是 `syntax_current`）。判据是「露出来了」，不绑标签名。
        self.assertTrue(
            self._in_tag("syntax_marker", f"{line}.0")
            or self._in_tag("syntax_current", f"{line}.0"),
            "光标所在行要显示 # 方便修改")
        self.assertFalse(self._hidden(f"{line}.0"))

    # ---- 行内样式 ----

    def test_inline_tags_are_applied(self) -> None:
        for tag in ("bold", "italic", "strike", "highlight", "code", "link", "image"):
            self.assertTrue(self._ranges(tag), f"{tag} 应有内容")

    def test_underscore_variants_are_supported(self) -> None:
        bold_spans = [self._span_text(a, b) for a, b in self._ranges("bold")]
        self.assertIn("另一种粗体", bold_spans)
        italic_spans = [self._span_text(a, b) for a, b in self._ranges("italic")]
        self.assertIn("另一种斜体", italic_spans)

    def test_snake_case_is_not_italicised(self) -> None:
        self.app.editor.insert("end-1c", "\n变量 some_long_name 不该变斜体。\n")
        self.app._apply_styles_without_dirty_flag()
        self.root.update()
        for start, end in self._ranges("italic"):
            self.assertNotIn("long", self._span_text(start, end), "snake_case 被误判成斜体")

    def test_link_url_is_hidden_but_label_is_not(self) -> None:
        line = self._line_of("说明")
        self.assertTrue(self._ranges("link"))
        text = self.app.editor.get(f"{line}.0", f"{line}.end")
        label_at = text.index("说明")
        self.assertFalse(self._hidden(f"{line}.{label_at}"), "链接文字要显示")
        self.assertTrue(self._hidden(f"{line}.{label_at - 1}"), "左方括号应隐藏")
        self.assertTrue(self._hidden(f"{line}.{text.index('https')}"), "URL 应隐藏")

    def test_marks_inside_inline_code_are_not_styled(self) -> None:
        self.app.editor.insert("end-1c", "\n这段 `**不是粗体**` 是代码。\n")
        self.app._apply_styles_without_dirty_flag()
        self.root.update()
        for start, end in self._ranges("bold"):
            self.assertNotIn("不是粗体", self._span_text(start, end),
                             "行内代码里的星号不应触发粗体")

    # ---- 块级样式 ----

    def test_bullet_lines_get_hanging_indent_tags(self) -> None:
        self.assertTrue(self._ranges("li0"))
        self.assertTrue(self._ranges("li1"))
        self.assertTrue(self._ranges("li2"))

    def test_list_markers_are_hidden(self) -> None:
        line = self._line_of("项目一")
        self.assertTrue(self._hidden(f"{line}.0"), "非光标行上的 - 应被隐藏")
        self.assertTrue(self._hidden(f"{line}.1"), "标记后的空格也应隐藏")

    def test_quote_is_tagged_and_marker_hidden(self) -> None:
        self.assertTrue(self._ranges("quote"))
        line = self._line_of("引用第一行")
        self.assertTrue(self._hidden(f"{line}.0"))

    def test_fenced_code_block_is_tagged(self) -> None:
        self.assertTrue(self._ranges("codeblock"))
        fence_line = self._line_of("```python")
        code_line = self._line_of("print")
        self.assertTrue(self._hidden(f"{fence_line}.0"), "围栏本身应隐藏")
        self.assertFalse(self._hidden(f"{code_line}.0"), "代码内容要显示")

    def test_code_block_uses_monospace(self) -> None:
        from main import MONO_FAMILY

        self.assertIn(MONO_FAMILY, str(self.app.editor.tag_cget("codeblock", "font")))

    def test_rule_line_is_fully_hidden(self) -> None:
        line = self._line_of("---")
        self.assertTrue(self._hidden(f"{line}.0"), "分隔线的 --- 应隐藏")
        self.assertTrue(self._hidden(f"{line}.2"))

    def test_heading_labels_cover_all_six_levels(self) -> None:
        labels = [self.app.heading_labels.itemcget(item, "text")
                  for item in self.app.heading_labels.find_all()]
        self.assertEqual(sorted(labels), sorted(HEADING_LABELS.values()))

    # ---- 配色 ----

    def test_each_heading_level_has_its_own_colour(self) -> None:
        """六级标题各有颜色，层级不靠加粗单靠颜色也能分辨。"""
        from main import HEADING_COLORS

        colors = [self.app.editor.tag_cget(f"h{level}", "foreground").lower()
                  for level in range(1, 7)]
        self.assertEqual(colors, [HEADING_COLORS[level].lower() for level in range(1, 7)])
        self.assertEqual(len(set(colors)), 6)

    def test_syntax_marks_are_a_lighter_grey_than_body(self) -> None:
        """光标行露出的记号要比正文淡，免得抢正文的注意力。"""
        from main import TEXT

        mark = self.app.editor.tag_cget("syntax_current", "foreground")
        self.assertNotEqual(mark.lower(), TEXT.lower())
        mark_value = sum(int(mark[index:index + 2], 16) for index in (1, 3, 5))
        text_value = sum(int(TEXT[index:index + 2], 16) for index in (1, 3, 5))
        self.assertGreater(mark_value, text_value, "记号应当比正文浅")

    def test_inline_marks_have_distinct_colours(self) -> None:
        """粗体、斜体、行内代码、链接、图片各用各的颜色，不能全是一个色。"""
        colors = {}
        for tag in ("bold", "italic", "code", "link", "image", "strike", "highlight"):
            colors[tag] = self.app.editor.tag_cget(tag, "foreground").lower()
        self.assertEqual(len(set(colors.values())), len(colors),
                         f"这些颜色有重复：{colors}")

    def test_body_text_stays_plain(self) -> None:
        """普通正文不该被染色，否则整篇都是花的。"""
        from main import TEXT

        for tag in ("h3", "h1"):
            self.assertNotEqual(
                self.app.editor.tag_cget(tag, "foreground").lower(), TEXT.lower())


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class DecorationTests(unittest.TestCase):
    """左侧装饰：项目符号、复选框、序号、引用竖条、分隔线。

    用一份短文档，保证所有装饰都在同一屏内——装饰只画在可见的显示行上。
    """

    DOC = (
        "- 项目一\n"
        "- 项目二\n"
        "  - 二层\n"
        "    - 三层\n"
        "1. 有序一\n"
        "2. 有序二\n"
        "- [ ] 未完成\n"
        "- [x] 已完成\n"
        "> 引用第一行\n"
        "> 引用第二行\n"
        "---\n"
    )

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        self.doc = folder / "装饰.md"
        self.doc.write_text(self.DOC, encoding="utf-8")
        storage.set_default_folder(folder)

        from main import JianJiApp

        self.root = tk.Tk()
        self.root.geometry("1180x760")
        self.app = JianJiApp(self.root)
        self.root.update()
        self.app.open_file(self.doc)
        self.root.update()
        self.app.editor.mark_set("insert", "end-1c")
        self.app._apply_styles_without_dirty_flag()
        self.root.update()

    def tearDown(self) -> None:
        storage.default_state_path = self._real_state
        self.root.destroy()

    def _decorations(self) -> list[tuple]:
        return [c.decor_spec for c in self.app._decor_pool if c.decor_spec is not None]

    def _canvas_for(self, spec) -> tk.Canvas:
        return next(c for c in self.app._decor_pool if c.decor_spec == spec)

    def test_every_marker_kind_is_drawn(self) -> None:
        kinds = [s[0] for s in self._decorations()]
        self.assertEqual(kinds.count("bullet"), 4, kinds)
        self.assertEqual(kinds.count("ordered"), 2, kinds)
        self.assertEqual(kinds.count("task"), 2, kinds)
        self.assertEqual(kinds.count("quote"), 2, "引用每一行都要有竖条")
        self.assertEqual(kinds.count("hr"), 1, kinds)

    def test_bullet_levels_use_different_glyphs(self) -> None:
        from main import BULLET_COLORS, EDITOR_BG

        specs = [s for s in self._decorations() if s[0] == "bullet"]
        self.assertEqual([s[1] for s in specs], [0, 0, 1, 2])
        filled = self._canvas_for(("bullet", 0))
        hollow = self._canvas_for(("bullet", 1))
        square = self._canvas_for(("bullet", 2))
        self.assertEqual(filled.itemcget(filled.find_all()[0], "fill"), BULLET_COLORS[0])
        self.assertEqual(filled.itemcget(filled.find_all()[0], "outline"), "")
        self.assertEqual(hollow.itemcget(hollow.find_all()[0], "fill"), EDITOR_BG)
        self.assertEqual(hollow.itemcget(hollow.find_all()[0], "outline"), BULLET_COLORS[1],
                         "第二层应是空心圆")
        self.assertEqual(square.type(square.find_all()[0]), "rectangle",
                         "第三层应换成方块")

    def test_bullet_levels_are_colour_coded(self) -> None:
        """每层项目符号换一种颜色，层级不靠字形单靠颜色也能分辨。"""
        from main import BULLET_COLORS

        colors = []
        for level in (0, 1, 2):
            canvas = self._canvas_for(("bullet", level))
            # 0 层实心圆看 fill，1 层空心圆看 outline，2 层实心方块看 fill
            attribute = "outline" if level == 1 else "fill"
            colors.append(canvas.itemcget(canvas.find_all()[0], attribute))
        self.assertEqual(colors, list(BULLET_COLORS[:3]))
        self.assertEqual(len(set(colors)), 3, "三层颜色应各不相同")

    def test_task_boxes_track_checked_state(self) -> None:
        from main import TASK_BOX_COLOR, TASK_CHECK_COLOR

        specs = [s for s in self._decorations() if s[0] == "task"]
        self.assertEqual([s[2] for s in specs], [False, True])
        unchecked = self._canvas_for(("task", 0, False))
        checked = self._canvas_for(("task", 0, True))
        self.assertEqual(len(unchecked.find_all()), 1, "未勾选只画方框")
        self.assertEqual(len(checked.find_all()), 2, "已勾选还要画对勾")
        box = unchecked.find_all()[0]
        self.assertEqual(unchecked.itemcget(box, "outline"), TASK_BOX_COLOR)
        done_box = checked.find_all()[0]
        self.assertEqual(checked.itemcget(done_box, "fill"), TASK_CHECK_COLOR,
                         "勾选后整格填成绿色，扫一眼就能看出哪些已完成")

    def test_ordered_numbers_are_right_aligned_to_the_gap(self) -> None:
        canvas = self._canvas_for(("ordered", 0, "1."))
        self.assertEqual(canvas.itemcget(canvas.find_all()[0], "text"), "1.")
        self.assertEqual(canvas.itemcget(canvas.find_all()[0], "anchor"), "e")
        right = self.app.editor.winfo_x() + self.app._current_pad + px(list_indent(0)) \
            - px(LIST_MARKER_GAP)
        self.assertEqual(canvas.winfo_x() + canvas.winfo_width(), right)

    def test_ordered_number_matches_the_body_font_size(self) -> None:
        """序号字号要跟正文一致（原来是写死的 11px 小字，比正文小一圈）。"""
        from main import ORDERED_NUMBER_COLOR

        canvas = self._canvas_for(("ordered", 0, "1."))
        item = canvas.find_all()[0]
        size = px(self.app.settings["font_size"])
        self.assertIn(f"-{size}", canvas.itemcget(item, "font"))
        self.assertEqual(canvas.itemcget(item, "fill"), ORDERED_NUMBER_COLOR)

    def test_ordered_number_survives_the_cursor_entering_the_line(self) -> None:
        """光标移到有序列表行上，序号照旧是画布画的蓝字，不该消失。"""
        self.app.editor.mark_set("insert", "5.0")       # 第 5 行是「1. 有序一」
        self.app._apply_styles_without_dirty_flag()
        self.root.update()
        drawn = [c.decor_line for c in self.app._decor_pool if c.decor_spec is not None]
        self.assertIn(5, drawn, "序号不该因为光标停在这一行就变成灰色源码")

    def test_ordered_marker_stays_hidden_on_the_cursor_line(self) -> None:
        """序号由画布画，所以记号**永远** elide——否则源码会和画出来的序号叠在一起。"""
        self.app.editor.mark_set("insert", "5.0")
        self.app._apply_styles_without_dirty_flag()
        self.root.update()
        self.assertIn("5.0", {str(v) for v in self.app.editor.tag_ranges("syntax")})
        self.assertNotIn("5.0",
                         {str(v) for v in self.app.editor.tag_ranges("syntax_current")})

    def test_bullet_still_shows_its_source_on_the_cursor_line(self) -> None:
        """项目符号维持原样（只有有序列表是例外），别把这个例外扩大化。

        露源码用的标签是 `syntax_marker` 而不是 `syntax_current`：无序/任务/引用的
        记号露出来时必须**补偿自己占的宽度、并把这一显示行改成按字符折行**，否则
        正文会被顶右，还会因为 `word` 折行被整个挪到下一显示行（见
        `CaretLineMarkerWrapTests`）。判据是「露出来了」，不绑死具体哪个标签。
        """
        self.app.editor.mark_set("insert", "1.0")       # 第 1 行是「- 项目一」
        self.app._apply_styles_without_dirty_flag()
        self.root.update()
        drawn = [c.decor_line for c in self.app._decor_pool if c.decor_spec is not None]
        self.assertNotIn(1, drawn, "光标所在行显示源码，不该再画符号")
        revealed = {str(v) for v in self.app.editor.tag_ranges("syntax_current")} \
            | {str(v) for v in self.app.editor.tag_ranges("syntax_marker")}
        self.assertIn("1.0", revealed, "光标所在行要显示记号源码")
        self.assertNotIn("1.0", {str(v) for v in self.app.editor.tag_ranges("syntax")},
                         "露出来了就不该还挂在 elide 的 syntax 上")

    def test_bullet_right_edge_sits_at_the_gap(self) -> None:
        canvas = self._canvas_for(("bullet", 0))
        x1, _y1, x2, _y2 = canvas.coords(canvas.find_all()[0])
        right = self.app.editor.winfo_x() + self.app._current_pad + px(list_indent(0)) \
            - px(LIST_MARKER_GAP)
        self.assertEqual(canvas.winfo_x() + x2, right)
        self.assertLess(canvas.winfo_x() + x1, right, "符号本身要有宽度")

    def test_nested_markers_are_indented_further_right(self) -> None:
        first = self._canvas_for(("bullet", 0)).winfo_x()
        second = self._canvas_for(("bullet", 1)).winfo_x()
        third = self._canvas_for(("bullet", 2)).winfo_x()
        self.assertLess(first, second)
        self.assertLess(second, third)

    def test_rule_spans_the_text_column(self) -> None:
        canvas = self._canvas_for(("hr",))
        expected = self.app.editor.winfo_width() - self.app._current_pad * 2
        self.assertEqual(canvas.winfo_width(), expected)
        self.assertEqual(canvas.winfo_x(), self.app.editor.winfo_x() + self.app._current_pad)

    def test_no_decoration_on_the_cursor_line(self) -> None:
        self.app.editor.mark_set("insert", "1.2")
        self.app._apply_styles_without_dirty_flag()
        self.root.update()
        lines = [c.decor_line for c in self.app._decor_pool if c.decor_spec is not None]
        self.assertNotIn(1, lines, "光标所在行显示源码，不该再画装饰")

    def test_decorations_are_reused_not_leaked(self) -> None:
        before = len(self.app._decor_pool)
        for _ in range(5):
            self.app._redraw_decorations()
            self.root.update()
        self.assertEqual(len(self.app._decor_pool), before)

    def test_decorations_are_hidden_when_no_document(self) -> None:
        self.app.editor.configure(state="normal")
        self.app.editor.delete("1.0", "end")
        self.app.editor.configure(state="disabled")
        self.app.current_path = None
        self.app.preview_mode = False
        self.app._redraw_decorations()
        self.root.update()
        self.assertEqual(self._decorations(), [])

    def test_decorations_follow_content_width(self) -> None:
        # 默认窗口下编辑区通常只占屏幕 40% 上下，落在「5% 地板」那一档——那一档里
        # 正文块宽 = 编辑区宽 − 10%·屏幕，**和 line_width 无关**，这条就测不出来。
        # 钉住屏幕宽把用例挪到「正文块宽 = 行宽 × 屏幕宽」那一档（见 _pinning）。
        pin_screen_share(self.app, 0.9)
        self.app._on_setting_changed("line_width", "30")
        self.root.update()
        self.app._apply_editor_geometry()
        self.root.update()
        before = self._canvas_for(("bullet", 0)).winfo_x()
        self.app._on_setting_changed("line_width", "60")
        self.root.update()
        self.app._apply_editor_geometry()
        self.root.update()
        after = self._canvas_for(("bullet", 0)).winfo_x()
        self.assertLess(after, before, "正文变宽、留白变小，装饰应整体左移")


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class RealDisplayBoxTests(unittest.TestCase):
    """`is_real_display_box` 的判据本身。

    这些数字都是实测值：默认设置（行高 22 → 33 像素）和用户那套（行高 34 →
    51 像素）各一组，两组的差别正好是那个坑所在。
    """

    def test_a_box_with_width_is_real(self) -> None:
        self.assertTrue(is_real_display_box((237, 783, 454, 33, 25), 33))

    def test_an_empty_line_box_is_real(self) -> None:
        """空行：宽 0，但高度就是整条显示行的高度、y 也接着上一行——必须认下来。

        认不下来的话，每一行空行都要多问 Tk 一次，而这是滚动时每帧都走的热路径。
        """
        self.assertTrue(is_real_display_box((189, 93, 0, 51, 36), 51))
        self.assertTrue(is_real_display_box((189, 510, 0, 51, 36), 51))

    def test_the_elided_chunk_stub_is_not_real(self) -> None:
        """折行行首那个 elided chunk 的盒子：宽 0、高只有行距那一截。

        左边是默认设置下的实测值（高 2），右边是行高 34 时的实测值（高 16）——
        只按绝对高度设阈值，右边这种就会漏掉。
        """
        for stub, line_height in (((237, 576, 0, 2, 1), 33),
                                  ((189, 875, 0, 16, 8), 51),
                                  ((189, 109, 0, 16, 8), 51)):
            self.assertFalse(is_real_display_box(stub, line_height),
                             f"{stub} 被当成真的显示行盒子了")

    def test_a_heading_box_taller_than_the_body_line_is_real(self) -> None:
        """标题行有自己的上下留白，比正文行还高——不能因此被当成空盒子。"""
        self.assertTrue(is_real_display_box((189, 297, 0, 60, 45), 51))

    def test_none_is_not_real(self) -> None:
        self.assertFalse(is_real_display_box(None, 33))


class WrappedLineGeometryTests(unittest.TestCase):
    """折行的显示行也要量得出几何——序号和引用竖条都靠它定位。

    Tk 对**自动换行的显示行**是按该行第一个 chunk 算几何的。行首那段一旦被
    elide（`1. `、`> `、`# ` 平时都藏着），返回的就是那个宽度为 0 的 elided
    chunk 的盒子：宽 0、高 = 行距、基线 = 行距的一半。以前直接拿它给装饰定位，
    序号画布高度算成 `max(px(16), 行距)`、文字基线算成行距的一半，整个序号被
    顶到画布外面裁掉大半（见 outputs/修复前-序号折行被裁.png）；折行引用的
    第一段竖条也缩成一小截。

    **高度不能当判据**（空盒子的高度随行高变），所以断言一律看宽度，
    并且由子类换一套行高再跑一遍。

    补记：控件后来改成了 `wrap="char"`（治「空格后面的整串中文被挪到下一显示行」，
    见 `SpaceDelimitedWrapTests`），**空盒子从此不再出现**——Tk 对 elide 首块的
    显示行也直接给整条显示行的盒子。`is_real_display_box` 因此退化成一道保险，
    下面的断言仍然有效（只是不再需要它去滤东西）。回退哨兵见
    `TallLineHeightWrappedLineGeometryTests`。
    """

    # 子类把它填成另一套设置再跑一遍同样的断言。默认设置下行距只有 2 像素，
    # 而把行高调到 34 就有 16 像素——「按高度判空盒子」的写法只在默认设置下
    # 成立，用户把行高调大之后就漏了（见 TallLineHeightWrappedLineGeometryTests）。
    SETTINGS: dict = {}

    DOC = (
        "1. 短的一条\n"
        "2. 这一条故意写得很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长，"
        "长到必须折行才行，折行之后序号还能不能完整显示就是要看的东西\n"
        "3. 第三条\n"
        "> 引用的第一行也故意写得很长很长很长很长很长很长很长很长很长很长很长很长很长，"
        "长到必须折行\n"
        "> 引用第二行\n"
        # 缩进最深的有序项：行首记号有 11 列（八个空格 + `1. `）。第一版修复是
        # 「往后逐列找第一个没被 elide 的字」，上限 8 列——这一行就会超上限、
        # 又退回空盒子，序号照旧被裁。所以专门留一行钉住它。
        "        1. 缩进最深的有序项也写得很长很长很长很长很长很长很长很长很长很长很长，"
        "长到必须折行\n"
    )

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        self.doc = folder / "折行.md"
        self.doc.write_text(self.DOC, encoding="utf-8")
        storage.set_default_folder(folder)
        if self.SETTINGS:
            storage.set_settings(dict(self.SETTINGS))

        from main import JianJiApp

        self.root = tk.Tk()
        self.root.geometry("1180x760")
        self.app = JianJiApp(self.root)
        self.root.update()
        self.app.open_file(self.doc)
        self.root.update()
        self.app.editor.mark_set("insert", "end-1c")
        self.app._apply_styles_without_dirty_flag()
        self.root.update()

    def tearDown(self) -> None:
        storage.default_state_path = self._real_state
        self.root.destroy()

    def _box_for_line(self, line_number: int) -> tuple:
        for number, offset, info in self.app._visible_lines():
            if number == line_number and offset == 0:
                return info
        self.fail(f"第 {line_number} 行不在可见范围里")

    def _canvas_for(self, spec) -> tk.Canvas:
        return next(c for c in self.app._decor_pool if c.decor_spec == spec)

    def _is_real(self, info) -> bool:
        """按当前设置判这个盒子是不是真的量到了显示行。"""
        return is_real_display_box(info, px(self.app.settings["line_height"]))

    def test_the_fixture_lines_really_wrap(self) -> None:
        """前提校验：第 2、4、6 行必须真的折了行，否则下面几项测了个寂寞。"""
        for number in (2, 4, 6):
            self.assertTrue(self.app._continuation_lines(number),
                            f"第 {number} 行没有折行，这份样例要再写长一点")

    def test_deeply_indented_ordered_item_is_recognised(self) -> None:
        """前提校验：第 6 行得真的被当成有序项，不然钉不住最深缩进那个坑。"""
        specs = [c.decor_spec for c in self.app._decor_pool if c.decor_spec]
        deep = [s for s in specs if s[0] == "ordered" and s[1] >= 3]
        self.assertTrue(deep, f"没画出深层缩进的序号：{specs}")

    def test_display_line_box_is_never_the_degenerate_one(self) -> None:
        """空盒子（行首 elided chunk 的盒子）不能当成显示行的几何。"""
        for number in (1, 2, 3, 4, 5, 6):
            info = self._box_for_line(number)
            self.assertTrue(self._is_real(info),
                            f"第 {number} 行量到了 Tk 的空盒子：{info}")

    def test_wrapped_line_keeps_the_full_height_and_baseline(self) -> None:
        """折行只影响折到哪儿，不该让显示行的高度和基线缩水。"""
        plain = self._box_for_line(1)           # 没折行的有序项
        for number in (2, 6):                   # 折了行的有序项：普通记号 / 最深缩进
            wrapped = self._box_for_line(number)
            self.assertGreaterEqual(wrapped[3], plain[3] - 1,
                                    f"第 {number} 行折行后高度从 {plain[3]} 掉到了 {wrapped[3]}")
            self.assertGreaterEqual(wrapped[4], plain[4] - 1,
                                    f"第 {number} 行折行后基线从 {plain[4]} 掉到了 {wrapped[4]}")

    def test_wrapped_ordered_number_can_be_drawn_whole(self) -> None:
        """序号画布得装得下整个字。

        高度被 `px(16)` 兜住就说明量到的还是那个空盒子——正常显示行有一个字高，
        比 `px(16)` 高。
        """
        canvas = self._canvas_for(("ordered", 0, "2."))
        self.assertGreater(canvas.winfo_height(), px(16),
                           "画布高度被下限兜住了，序号会被裁")

    def test_every_ordered_number_is_drawn_whole(self) -> None:
        """每个序号都得整块落在自己的画布里——把所有形态一起罩住。

        `bbox` 给的是文字的真实范围，不随画布大小变化；只要它越出画布矩形，
        屏幕上看到的就是被切掉的样子。
        """
        checked = 0
        for canvas in self.app._decor_pool:
            spec = getattr(canvas, "decor_spec", None)
            if not spec or spec[0] != "ordered":
                continue
            checked += 1
            box = canvas.bbox(canvas.find_all()[0])
            self.assertIsNotNone(box, f"{spec} 的序号没画出来")
            width, height = canvas.winfo_width(), canvas.winfo_height()
            where = f"{spec} 的序号 {box} 越出了画布 {width}×{height}"
            self.assertGreaterEqual(box[0], -1, f"左边被裁：{where}")
            self.assertGreaterEqual(box[1], -1, f"上边被裁：{where}")
            self.assertLessEqual(box[2], width + 1, f"右边被裁：{where}")
            self.assertLessEqual(box[3], height + 1, f"下边被裁：{where}")
        self.assertGreaterEqual(checked, 4, f"样例里的有序项太少了：{checked}")

    def test_wrapped_quote_bar_segments_are_about_the_same_height(self) -> None:
        """竖条要连成一条：折行的引用，第一段的竖条不能比后面几段矮一截。"""
        segments = [c for c in self.app._decor_pool if c.decor_spec == ("quote",)]
        heights = [c.winfo_height() for c in segments]
        self.assertGreaterEqual(len(heights), 3, f"样例里引用有三段：{heights}")
        self.assertLessEqual(max(heights) - min(heights), px(4),
                             f"竖条各段高度该基本一致，实际 {heights}")


class TallLineHeightWrappedLineGeometryTests(WrappedLineGeometryTests):
    """同一份样例、同一批断言，换成**用户那套设置**（行高 34 / 字号 18 / 行宽 60）再跑。

    这组才是真正守住那个 bug 的：空盒子的高度是「行高 − 字体行距」，默认设置
    （行高 22 → 33 像素行距、字号 15 → 23 像素字）下只剩 2 像素，靠一个高度阈值
    就能兜住；用户把行高调到 34（51 像素行距）之后它变成 16 像素，高度阈值整个
    漏掉，序号照旧被裁——用户报的「序号折行之后还是显示不全」就是这个。

    换句话说：**这组测试失败 = 判据又回到看高度了。**
    """

    SETTINGS = {"line_width": 60, "font_size": 18, "line_height": 34}

    def test_the_elided_first_chunk_no_longer_yields_a_degenerate_box(self) -> None:
        """前提校验：按字符折行后，行首 elide 的显示行不再量成「空盒子」。

        以前控件是 `wrap="word"`，`dlineinfo("2.0")` 给的是宽 0、高 = 行距
        （这套设置下 16 像素）的空盒子，`is_real_display_box` 就是为滤掉它而写的；
        这条用例当年断言「空盒子确实比旧阈值 6 像素高」，好让这组测试真的重现漏网
        的场景。

        控件改成 `wrap="char"` 之后**空盒子整个消失了**——Tk 直接给整条显示行的盒子
        （实测宽 570、高 51，和 `px(行高)` 相等），`is_real_display_box` 于是退化成
        一道保险。所以这里反过来钉住新事实，**同时当回退哨兵**：谁把控件改回 `word`，
        这条立刻红，报错信息直接指向 `wrap`。
        """
        raw = self.app.editor.dlineinfo("2.0")
        self.assertIsNotNone(raw, "第 2 行不在视口里，量不到")
        self.assertTrue(
            self._is_real(raw),
            f"第 2 行又量到空盒子了（{raw}）——控件是不是被改回 `wrap=\"word\"` 了？")
        self.assertGreaterEqual(raw[2], 100,
                                f"盒子宽只有 {raw[2]}，不像整条显示行")


class CaretGeometryTestCase(unittest.TestCase):
    """「光标在别处 ↔ 光标在本行」两态几何对照的公共脚手架。

    子类给 `DOC`（一行一个用例）、`WITNESS`（见证行的行号）、可选的 `SETTINGS`，
    再用行号常量指明要看哪几行。

    **量几何一律走 `dlineinfo`，不看 `bbox`。** `bbox` 会给 elide 的字符返回「它自己
    那个块」的盒子——那块没有字、高只剩行距那一截（用户设置下 16 像素），于是凭空
    多出一个「假显示行」：断点会算成 2、显示行数多算一个。`无序+粗体` 就是被这么
    误判成「光标选中即折行」的（其实两态折行完全一致）。`dlineinfo` 给的是**显示行**
    盒子，再过一遍生产代码用的 `is_real_display_box` 把假盒子滤掉，判据才和屏幕一致。

    **量之前必须显式跑一次 `_refresh_cursor_line()`**：`mark_set` 只动 Tk 的插入点，
    应用侧的标签互换排在 `after_idle` 里，不跑就量到旧状态、得出「根本没这个问题」
    的假结论（这一条踩过）。
    """

    # 子类换一套设置再跑同一批断言：行高不同，elide 空盒子那截从 2 像素变成 16 像素。
    SETTINGS: dict = {}
    DOC: str = ""
    WITNESS: int = 0

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        self.doc = folder / "记号.md"
        self.doc.write_text(self.DOC, encoding="utf-8")
        storage.set_default_folder(folder)
        if self.SETTINGS:
            storage.set_settings(dict(self.SETTINGS))

        from main import JianJiApp

        self.root = tk.Tk()
        self.root.geometry("1180x760")
        self.app = JianJiApp(self.root)
        self.root.update()
        self.app.open_file(self.doc)
        self.root.update()
        # 先整篇重解析一遍并把光标放到末尾，让 `_cursor_line` 有个确定的起点，
        # 之后每次移动才会真的走 `_swap_cursor_marks`。
        self.app.editor.mark_set("insert", "end-1c")
        self.app._apply_styles_without_dirty_flag()
        self.root.update()

    def tearDown(self) -> None:
        storage.default_state_path = self._real_state
        self.root.destroy()

    # ---------- 工具 ----------

    def _move(self, line: int) -> None:
        """把光标放到第 line 行行尾，并走一遍应用自己的「光标换行」刷新。"""
        self.app.editor.mark_set("insert", f"{line}.end")
        self.app.editor.see("insert")
        self.app._refresh_cursor_line()
        self.root.update()

    def _elided_columns(self, line: int) -> list[tuple[int, int]]:
        """这一行被 elide 的字符区间（`syntax` 标签：行首记号 + 行内记号）。

        `syntax_marker` / `syntax_current` 是「露源码」那一态，**不 elide**，别算进来。
        """
        editor = self.app.editor
        spans = []
        ranges = editor.tag_ranges("syntax")
        for start, end in zip(ranges[0::2], ranges[1::2]):
            head = editor.index(start).split(".")
            if int(head[0]) != line:
                continue
            spans.append((int(head[1]), int(editor.index(end).split(".")[1])))
        return spans

    def _display_lines(self, line: int) -> list[list] | None:
        """这一行的**真实**显示行，每项 `[(y, 高), 首字符下标, 末字符下标]`。"""
        editor = self.app.editor
        editor.tk.call(editor, "count", "-update", "1.0", "end")
        source = editor.get(f"{line}.0", f"{line}.end")
        height = px(self.app.settings["line_height"])
        groups: list[list] = []
        for index in range(len(source) + 1):
            info = editor.dlineinfo(f"{line}.{index}")
            if not is_real_display_box(info, height):
                continue                      # elide 首块的假盒子，不是显示行
            key = (info[1], info[3])
            if groups and groups[-1][0] == key:
                groups[-1][2] = index
            else:
                groups.append([key, index, index])
        return groups or None

    def _body_column(self, line: int, body: int) -> int | None:
        """从 body 往后找第一个**没被 elide** 的字符：正文真正开始的地方。"""
        editor = self.app.editor
        source = editor.get(f"{line}.0", f"{line}.end")
        spans = self._elided_columns(line)
        for index in range(body, len(source) + 1):
            if not any(a <= index < b for a, b in spans):
                return index
        return None

    def _geometry(self, line: int) -> tuple | None:
        """(断点, 显示行数, 正文首字符 x)，量不到返回 None。

        断点 = 第一个真实显示行承载到的字符下标 + 1，也就是**第二个显示行的第一个
        字符**——它才是「折在哪里」这件事的真身。
        """
        editor = self.app.editor
        groups = self._display_lines(line)
        if groups is None:
            return None
        source = editor.get(f"{line}.0", f"{line}.end")
        marker = parse_block(source).marker
        column = self._body_column(line, marker[1] if marker else 0)
        body_box = editor.bbox(f"{line}.{column}") if column is not None else None
        if body_box is None:
            return None
        return groups[0][2] + 1, len(groups), body_box[0]

    def _shift_below(self, line: int) -> int | None:
        """记号从隐藏变露出时，第 line 行**下方**整体上移了多少像素。

        用末尾那行普通正文当见证：它自己没有记号、几何恒定，位置只受上方各行高度的
        影响。**不要拿 `bbox(f"{line + 1}.0")` 去减**——行首被 elide 的行
        （引用、列表）那个下标量不到盒子，会得到 `None`。
        """
        editor = self.app.editor
        editor.tk.call(editor, "count", "-update", "1.0", "end")
        self._move(self.WITNESS)
        away = editor.bbox(f"{self.WITNESS}.0")
        self._move(line)
        here = editor.bbox(f"{self.WITNESS}.0")
        if away is None or here is None:
            return None
        return away[1] - here[1]

    def _option(self, tag: str, name: str) -> str:
        return self.app.editor.tag_configure(tag)[name][4]

    def _assert_wrap_is_stable(self, lines, witness: int | None = None) -> None:
        """光标进出前后，断点 / 显示行数 / 正文 x 三项必须完全一致。

        `lines` 是目标行号；`witness` 是「光标在别处」时停留的普通正文行，默认
        `self.WITNESS`。要逐行配不同见证行时，直接把 `lines` 写成
        `(目标行, 见证行)` 的列表，`witness` 留空。
        """
        if lines and isinstance(lines[0], tuple):
            pairs = list(lines)
        else:
            spot = self.WITNESS if witness is None else witness
            pairs = [(line, spot) for line in lines]
        for line, spot in pairs:
            self._move(spot)
            away = self._geometry(line)
            self._move(line)
            here = self._geometry(line)
            self.assertIsNotNone(away, f"第 {line} 行在「光标在别处」时量不到")
            self.assertEqual(away, here,
                             f"第 {line} 行：光标进出改变了折行（{away} -> {here}）")


class CaretLineMarkerWrapTests(CaretGeometryTestCase):
    """光标进到列表行，**折行位置不能变**。

    用户报的：「修复选中无序列表所在行也会自动换行的问题」。根因有两层，缺一层
    都修不掉：

    1. 行首记号平时 `elide`（宽度 0），光标行要露源码 → 正文被顶右「记号宽」那么多，
       可用宽度也少那么多；
    2. **更要命的是控件级折行方式是 `word`**——它的规则是「一个词在当前显示行的
       **剩余**宽度里放不下，就整个挪到下一显示行」。正文平时就是这一显示行的第一个
       词，放不下只能在原地按字符断开；**记号一露出来，这一显示行就有内容了，正文
       那个词于是被整个挪到下一显示行**，显示行凭空多一行。

    所以判据是「光标在别处」与「光标在本行」两态下逐项相等：
    第一个显示行断在第几个字符、这一行占几个显示行、正文首字符的 x。
    """

    DOC = (
        "# 记号核验\n"
        "\n"
        "- 无序项的正文要够长才会折行，这里故意写得长一些，长到必须折成两个显示行才行。\n"
        "- [ ] 任务项的正文要够长才会折行，这里故意写得长一些，长到必须折成两个显示行。\n"
        "> 引用的正文要够长才会折行，这里故意写得长一些，长到必须折成两个显示行才行。\n"
        "1. 有序项的正文要够长才会折行，这里故意写得长一些，长到必须折成两个显示行。\n"
        "见证行。\n"
    )
    BULLET, TASK, QUOTE, ORDERED, WITNESS = 3, 4, 5, 6, 7
    # 光标行会把记号露出来的三种（有序项的序号永远由画布画，不在此列）
    REVEALED = (BULLET, TASK, QUOTE)
    # 折行位置不该因为光标进出而改变的全部行（含有序项这个对照组）
    WRAP_TARGETS = (BULLET, TASK, QUOTE, ORDERED)

    # ---------- 前提校验 ----------

    def test_the_fixture_lines_really_wrap(self) -> None:
        """样例里每个列表项都得真的折行，否则下面几条测了个寂寞。"""
        for line in self.WRAP_TARGETS:
            geometry = self._geometry(line)
            self.assertIsNotNone(geometry, f"第 {line} 行不在视口里，量不到")
            self.assertGreaterEqual(geometry[1], 2,
                                    f"第 {line} 行只占 {geometry[1]} 个显示行，样例要再写长一点")

    def test_the_marker_really_is_revealed_on_the_caret_line(self) -> None:
        """前提校验：光标进到这些行时记号确实露出来了，测的才是「露出来」这件事。"""
        for line in self.REVEALED:
            self._move(line)
            self.assertIn("syntax_marker", self.app.editor.tag_names(f"{line}.0"),
                          f"第 {line} 行的记号没露出来")

    # ---------- 真正要守住的东西 ----------

    def test_the_wrap_does_not_change_when_the_caret_enters(self) -> None:
        """光标进出前后，断点 / 显示行数 / 正文 x 三项必须完全一致。"""
        self._assert_wrap_is_stable(self.WRAP_TARGETS)

    def test_the_revealed_marker_compensates_its_own_width(self) -> None:
        """露出来的记号要把自己的宽度从 `lmargin1` 里减掉，并改成按字符折行。"""
        self._move(self.BULLET)
        self.assertEqual(self._option("syntax_marker", "wrap"), "char",
                         "不按字符折行，正文会被整个挪到下一显示行、凭空多一行")
        lmargin1 = int(self._option("syntax_marker", "lmargin1"))
        lmargin2 = int(self._option("syntax_marker", "lmargin2"))
        self.assertEqual(lmargin2, px(list_indent(0)), "续行仍要从缩进处开始")
        self.assertLess(lmargin1, lmargin2, "没有减掉记号宽度，正文会被顶右")

    def test_the_compensation_matches_the_marker_width(self) -> None:
        """补偿量 = 记号宽度，且正好把正文放回隐藏时的位置。"""
        for line in (self.BULLET, self.TASK):
            self._move(self.WITNESS)
            hidden = self._geometry(line)[2]
            self._move(line)
            self.assertEqual(self._geometry(line)[2], hidden,
                             f"第 {line} 行：记号露出来后正文位置变了")
            source = self.app.editor.get(f"{line}.0", f"{line}.end")
            marker = parse_block(source).marker
            width = self.app._body_text_width(source[:marker[1]])
            self.assertEqual(int(self._option("syntax_marker", "lmargin1")),
                             px(list_indent(0)) - width,
                             f"第 {line} 行：补偿量不等于 px(缩进) − 记号宽度")

    def test_the_quote_marker_is_compensated_too(self) -> None:
        """引用有同一个毛病（`> ` 一露出来正文那个词也被挪走），一并修掉。"""
        self._move(self.QUOTE)
        self.assertIn("syntax_marker", self.app.editor.tag_names(f"{self.QUOTE}.0"))
        self.assertEqual(self._option("syntax_marker", "wrap"), "char")
        self.assertEqual(int(self._option("syntax_marker", "lmargin2")), px(LIST_INDENT))

    def test_ordered_and_plain_lines_are_not_touched(self) -> None:
        """有序项的序号永远由画布画、记号永远 elide，别把例外扩大到它身上。"""
        for line in (self.ORDERED, self.WITNESS):
            self._move(line)
            self.assertNotIn("syntax_marker", self.app.editor.tag_names(f"{line}.0"),
                             f"第 {line} 行不该套补偿标签")

    def test_the_known_height_residual_stays_within_the_elide_padding(self) -> None:
        """已知残留：记号露出来后这一行会矮「elide 首块那截额外行距」。

        Tk 把 elide 的首块当成一个「零高的显示行」、还照算一份 `spacing2`，所以
        记号**隐藏**时这一行会多出 `px(行高) − 字体行距`（默认设置 2 像素、行高 34
        时 16 像素）。`spacing1` / `spacing2` 这些**标签**选项实测补不回来
        （见 `_marker_options` 的注释），要根治得改行距模型，改动面比这个 bug 大。

        这里只钉「别比这个上界更糟」：残留一旦变大就说明又动了行距，
        得回头核对上面那几条折行断言。
        """
        padding = max(0, px(self.app.settings["line_height"])
                      - int(self.app._body_font_object().metrics("linespace")))
        for line in self.REVEALED:
            shift = self._shift_below(line)
            self.assertIsNotNone(shift, f"第 {line} 行量不到见证行的位置")
            self.assertLessEqual(shift, padding + 1,
                                 f"第 {line} 行：下方整体上移了 {shift} 像素，"
                                 f"超过了 elide 空盒子的 {padding}")


class TallSettingsCaretLineMarkerWrapTests(CaretLineMarkerWrapTests):
    """同一份样例、同一批断言，换成**用户那套设置**（行宽 60 / 字号 18 / 行高 34）。

    这套设置下行高 51 像素、字体行距 35，elide 空盒子那截是 16 像素（默认设置下
    只有 2）——「点击列表行下方文字会跳一下」在用户那儿才明显。**这组失败 = 判据
    又回到默认设置下才能成立了。**
    """

    SETTINGS = {"line_width": 60, "font_size": 18, "line_height": 34}


class SpaceDelimitedWrapTests(CaretGeometryTestCase):
    """中文里出现空格时，第一显示行也必须填满。

    用户报的第 11 轮两条：「引用有中英混合时会出现换行」「文字间插入空格会出现换行」。

    根因在控件级的 `wrap`。Tk 的 `word` 折行把「以空格分隔的一串字符」当成一个
    **不可拆的词**：中文整行没有空格时，那个「词」就是整行，放不下只好按字符断，
    反而填得满（实测 99.3%）；可只要行里出现**一个空格**——自己敲的，或者中英混排
    带出来的——空格后面那一长串中文就成了一个词，放不下就**整串挪到下一显示行**，
    本行右边空掉一大块。实测最坏情况第一显示行只填 **4.7%**（整行就剩个「短 」），
    引用/列表项那种也才 **41.9%**，而且整段多折一行。

    这同时解释了用户为什么觉得「编辑区的显示比例不对」：行只填到 4.7% 或 41.9%，
    看起来就像正文块比设置的比例窄得多。控件改成 `wrap="char"` 后，同样几句分别
    填到 96.0% / 100.0%，显示行还少一行。

    阈值取 90%：断在空格处时行尾那个空格是正常消耗掉的，扣掉它行末天然会空出
    「一个空格」的宽度（94.6%~95.6%），跟「整串被挪走」不是一个量级。
    """

    DOC = (
        "# 空格折行\n"
        "\n"
        "短 后面这一整串中文没有任何空格所以会被当成一个词整串挪到下一行去。\n"
        "\n"
        "> 引用开头 English 后面这一整串中文没有任何空格会被整串挪到下一行去。\n"
        "\n"
        "- 列表开头 English 后面这一整串中文没有任何空格会被整串挪到下一行去。\n"
        "\n"
        "这一段中文里夹杂 English words 和 more words，用来观察中英边界处的断行。\n"
        "\n"
        "留一行普通正文当见证。\n"
    )
    WITNESS = 11
    TARGETS = (3, 5, 7, 9)

    def _first_line_fill(self, line: int) -> float:
        """第一显示行的填充率 = 墨迹宽 ÷ 可用宽（缩进行按自己的缩进算可用宽）。"""
        editor = self.app.editor
        groups = self._display_lines(line)
        if not groups:
            return 0.0
        _key, first, last = groups[0]
        source = editor.get(f"{line}.0", f"{line}.end")
        # 行尾空格是「断在空格处」正常消耗掉的，量墨迹要跳过它
        end = last
        while end >= first and not source[end:end + 1].strip():
            end -= 1
        column = self._body_column(line, first)     # 跳过被 elide 的行首记号
        if column is None or column > end:
            return 0.0
        left_box = editor.bbox(f"{line}.{column}")
        right_box = editor.bbox(f"{line}.{end}")
        if left_box is None or right_box is None:
            return 0.0
        left = left_box[0]
        used = right_box[0] + right_box[2] - left
        available = editor.winfo_width() - self.app._current_pad - left
        return used / available if available > 0 else 0.0

    # ---------- 真正要守住的东西 ----------

    def test_the_editor_wraps_by_character(self) -> None:
        """控件级必须按字符折行——回到 `word` 就会把整串中文挪到下一显示行。"""
        self.assertEqual(
            self.app.editor.cget("wrap"), "char",
            "`word` 会把空格后面的整串中文当成一个词，整串挪到下一显示行")

    def test_a_space_does_not_push_a_whole_run_to_the_next_line(self) -> None:
        """有空格的行，第一显示行也要填满。"""
        for line in self.TARGETS:
            self._move(self.WITNESS)
            fill = self._first_line_fill(line)
            self.assertGreaterEqual(
                fill, 0.90,
                f"第 {line} 行第一显示行只填了 {fill * 100:.1f}%："
                f"空格后面那串中文被整串挪到下一行了")

    def test_the_fill_holds_with_the_caret_on_the_line_too(self) -> None:
        """光标停在这一行上时同样要填满（露出记号不能把折行带歪）。"""
        for line in self.TARGETS:
            self._move(line)
            fill = self._first_line_fill(line)
            self.assertGreaterEqual(fill, 0.90,
                                    f"第 {line} 行（光标在本行）只填了 {fill * 100:.1f}%")


class TallSettingsSpaceDelimitedWrapTests(SpaceDelimitedWrapTests):
    """同一份样例、同一批断言，换成**用户那套设置**（行宽 60 / 字号 18 / 行高 34）。"""

    SETTINGS = {"line_width": 60, "font_size": 18, "line_height": 34}


class InlineMarkRevealTests(CaretGeometryTestCase):
    """光标进到**带行内记号**的那一行，折行位置也不能变。

    用户报的：「分有序列表也存在光标选中该行即换行的问题」。

    有序列表自己的序号是**清白**的：它永远由左侧画布绘制、`1. ` 永远 elide、
    不记进 `_line_marks`，实测光标进出两态几何逐项相等。真凶是**行内记号**：
    `**` / `` ` `` 平时 elide、宽度为 0，光标进到这一行就露出来——等于往这一行里
    凭空塞进几个字符。控件折行是 `word`，卡在折行边界上的行于是多折一行、整行往下挪。

    修法见 `_inline_reveal_fits`：只有「连记号一起量、还塞得进**一个**显示行」的行
    才露。单行的行露出来一定还是单行，所以这条判据是精确的（不是近似）；
    本来就是折行的行一律不露——整行往下挪比看不到 `**` 更烦。

    样例故意覆盖四种「记号 + 行内记号」的组合，以及一个短行（短行必须照旧露源码，
    别为了修这个 bug 把「光标行显示源码」这个功能一起关掉）。
    """

    DOC = (
        "# 行内记号核验\n"
        "\n"
        "- **无序项的正文要够长才会折行，这里故意写得长一些，长到必须折成两个显示行才行**：说明。\n"
        "- [ ] **任务项的正文要够长才会折行，这里故意写得长一些，长到必须折成两个显示行**：说明。\n"
        "> **引用的正文要够长才会折行，这里故意写得长一些，长到必须折成两个显示行才行**：说明。\n"
        "1. **有序项的正文要够长才会折行，这里故意写得长一些，长到必须折成两个显示行**：说明。\n"
        "2. `有序项的行内代码要够长才会折行，这里故意写得长一些，长到必须折成两个显示行`：说明。\n"
        "**正文里的粗体要够长才会折行，这里故意写得长一些，长到必须折成两个显示行才行**：说明。\n"
        "**短行**：说明。\n"
        "见证行。\n"
    )
    BULLET, TASK, QUOTE, ORDERED, ORDERED_CODE, PLAIN_BOLD, SHORT, WITNESS = (
        3, 4, 5, 6, 7, 8, 9, 10)
    WRAP_TARGETS = (BULLET, TASK, QUOTE, ORDERED, ORDERED_CODE, PLAIN_BOLD)

    # ---------- 前提校验 ----------

    def test_the_fixture_lines_really_wrap(self) -> None:
        """每个目标行都得真的折行，否则「多折一行」这件事无从谈起。"""
        for line in self.WRAP_TARGETS:
            geometry = self._geometry(line)
            self.assertIsNotNone(geometry, f"第 {line} 行不在视口里，量不到")
            self.assertGreaterEqual(geometry[1], 2,
                                    f"第 {line} 行只占 {geometry[1]} 个显示行，样例要再写长一点")

    def test_the_fixture_lines_really_carry_inline_marks(self) -> None:
        """前提校验：这些行真的带行内记号，否则测的是个空壳。"""
        for line in self.WRAP_TARGETS:
            self._move(self.WITNESS)
            marks = [entry for entry in self.app._line_marks.get(line, ())
                     if entry[0] == "syntax" and entry[4] is None]
            self.assertTrue(marks, f"第 {line} 行没有行内记号")

    # ---------- 真正要守住的东西 ----------

    def test_the_wrap_does_not_change_when_the_caret_enters(self) -> None:
        """光标进出前后，断点 / 显示行数 / 正文 x 三项必须完全一致。

        这正是用户报的那一条：有序项 + 粗体 / 行内代码，光标点进去不该折行。
        """
        self._assert_wrap_is_stable(self.WRAP_TARGETS)

    def _inline_mark_tags(self, line: int) -> list[set[str]]:
        """这一行每个**行内**记号（`indent is None`）当前挂着的标签集合。

        不看固定列号：行首记号的宽度随形状变（`- ` 两列、`- [ ] ` 六列），
        写死 `.{line}.4` 有时落在正文上、有时落在第二个 `**` 上。
        """
        editor = self.app.editor
        return [set(editor.tag_names(f"{line}.{start}"))
                for off_tag, _on_tag, start, _end, indent
                in self.app._line_marks.get(line, ())
                if off_tag == "syntax" and indent is None]

    def test_the_gate_agrees_with_the_measured_geometry(self) -> None:
        """判据本身要和屏幕对得上：说「塞不进一个显示行」的行，必须真的折了行。"""
        for line in self.WRAP_TARGETS:
            source = self.app.editor.get(f"{line}.0", f"{line}.end")
            self.assertFalse(self.app._inline_reveal_fits(line, source),
                             f"第 {line} 行被判成「塞得进一个显示行」，但它其实是折行的")
            self.assertGreaterEqual(self._geometry(line)[1], 2)

    def test_the_inline_marks_stay_hidden_on_a_wrapping_caret_line(self) -> None:
        """折行的光标行上，行内记号不许露出来（露了就会多折一行）。"""
        for line in self.WRAP_TARGETS:
            self._move(line)
            tags = self._inline_mark_tags(line)
            self.assertTrue(tags, f"第 {line} 行没有行内记号可查")
            for names in tags:
                self.assertNotIn("syntax_current", names,
                                 f"第 {line} 行折行了，行内记号却露出来了")

    def test_a_short_line_still_reveals_its_marks(self) -> None:
        """反面：塞得进一个显示行的短行，光标进去**照旧**露源码。

        这条守住「光标行显示源码」这个功能本身——别为了修折行把它一起关掉。
        """
        self._move(self.SHORT)
        source = self.app.editor.get(f"{self.SHORT}.0", f"{self.SHORT}.end")
        self.assertTrue(self.app._inline_reveal_fits(self.SHORT, source))
        tags = self._inline_mark_tags(self.SHORT)
        self.assertTrue(tags, "短行没有行内记号可查")
        self.assertTrue(all("syntax_current" in names for names in tags),
                        f"短行的行内记号该露出来，实际 {tags}")

    def test_the_gate_never_hides_the_line_start_marker(self) -> None:
        """闸门只管**行内**记号，行首记号（`- ` / `> `）该露还得露。

        行首记号有自己的宽度补偿（`syntax_marker`），露出来不改变折行；
        把它一起闸掉会让光标行看不到 `- `，白丢一个功能。
        """
        for line in (self.BULLET, self.TASK, self.QUOTE):
            self._move(line)
            self.assertIn("syntax_marker", self.app.editor.tag_names(f"{line}.0"),
                          f"第 {line} 行的行首记号被行内闸门误伤了")

    def test_plain_bold_text_has_no_line_start_marker(self) -> None:
        """正文里的粗体只该被行内闸门管，不该套行首补偿标签。"""
        self._move(self.PLAIN_BOLD)
        self.assertNotIn("syntax_marker", self.app.editor.tag_names(f"{self.PLAIN_BOLD}.0"))


class TallSettingsInlineMarkRevealTests(InlineMarkRevealTests):
    """同一份样例、同一批断言，换成**用户那套设置**（行宽 60 / 字号 18 / 行高 34）。

    这套设置下正文列宽只有 571 像素，比默认设置窄得多，折行边界的位置完全不同——
    「有序项 + 粗体光标点进去就折行」在用户那儿才复现。**这组失败 = 修复只在默认
    设置下成立。**
    """

    SETTINGS = {"line_width": 60, "font_size": 18, "line_height": 34}


class HeadingMarkerWrapTests(CaretGeometryTestCase):
    """光标进到**会折行的长标题**，折行位置也不能变。

    标题的 `# ` 和列表记号是同一类问题：平时 elide（宽度 0），光标行一露出来就把正文
    顶右、可用宽度变小，卡在折行边界上的标题会凭空多折一行。

    标题没有缩进（`h{n}` 只配了字体、没配 `lmargin`），所以补偿方式不一样：把 `# `
    **往左挂到留白里去**（`lmargin1` 取负、`lmargin2` 取 0），正文位置与可用宽度都
    回到隐藏时的值。

    **样例必须扫一串长度**（`LENGTHS`），不能只挑一两条：露出来的 `# ` 只占 30~40 像素，
    只有「最后一个词正好落在边界前后那几十像素里」的标题才会重折。

    真正**能抓到回退**的是 `test_the_revealed_heading_marker_hangs_into_the_padding`：
    把标题记号退回 `_tag_syntax` 之后，它的 `_line_marks` 记成 `indent is None`，会被
    行内记号那道闸门顺手一起闸掉（标题于是既不露 `# `、也不重折）——折行断言因此
    测不出差别，记号断言才测得出。折行那条仍然留着：以后谁把记号露出来又忘了补偿，
    它就会红。
    """

    FILLER = "标题的正文要够长才会折行，这里故意写得长一些，用来观察折行位置。"
    LENGTHS = range(34, 47)
    MARKERS = ("## ", "#### ")

    def _build(self) -> tuple[str, list[tuple[int, int]]]:
        """一行标题 + 一行见证，返回 (文稿, [(目标行, 见证行)])。"""
        lines = ["# 标题记号核验", ""]
        pairs = []
        for size in self.LENGTHS:
            for marker in self.MARKERS:
                lines.append(marker + (self.FILLER * 4)[:size])
                lines.append("见证行。")
                pairs.append((len(lines) - 1, len(lines)))
        return "\n".join(lines) + "\n", pairs

    def setUp(self) -> None:
        self.DOC, self.TARGETS = self._build()
        super().setUp()

    def test_the_fixture_headings_really_wrap(self) -> None:
        """样例里的标题得真的折行，否则下面两条测了个寂寞。"""
        wrapped = 0
        for line, witness in self.TARGETS:
            self._move(witness)          # 先滚到这一对附近，视口外的行量不到
            geometry = self._geometry(line)
            self.assertIsNotNone(geometry, f"第 {line} 行不在视口里，量不到")
            if geometry[1] >= 2:
                wrapped += 1
        self.assertGreater(wrapped, len(self.TARGETS) // 2,
                           f"只有 {wrapped}/{len(self.TARGETS)} 条标题折了行，样例要再写长一点")

    def test_the_wrap_does_not_change_when_the_caret_enters(self) -> None:
        self._assert_wrap_is_stable(self.TARGETS)

    def test_the_revealed_heading_marker_hangs_into_the_padding(self) -> None:
        """补偿是把 `# ` 挂到左侧留白里：`lmargin1` 为负、`lmargin2` 为 0。"""
        for line, _witness in self.TARGETS[:2]:
            self._move(line)
            self.assertIn("syntax_marker", self.app.editor.tag_names(f"{line}.0"),
                          f"第 {line} 行的 `# ` 没露出来")
            self.assertEqual(self._option("syntax_marker", "wrap"), "char")
            self.assertLess(int(self._option("syntax_marker", "lmargin1")), 0,
                            "标题记号没有挂到留白里，正文会被顶右")
            self.assertEqual(int(self._option("syntax_marker", "lmargin2")), 0,
                             "标题没有缩进，续行也不该缩")


class TallSettingsHeadingMarkerWrapTests(HeadingMarkerWrapTests):
    """同一批断言换成**用户那套设置**（行宽 60 / 字号 18 / 行高 34）。"""

    SETTINGS = {"line_width": 60, "font_size": 18, "line_height": 34}


class OrderedNumberAtViewportEdgeTests(unittest.TestCase):
    """滚到任意位置，序号都不该被自己的画布切掉——**包括正好卡在视口下沿那一行**。

    这一组补的是 `WrappedLineGeometryTests` 盖不到的地方。Tk 在视口下沿会把那一
    显示行的盒子按可见部分裁一刀：`box[3]` 从 51 掉到 42（实测），而 `box[4]`
    （基线偏移）**不裁**。装饰画布要是照着裁过的 `box[3]` 定高，序号的下沿就比
    画布低两个像素，被切掉一条——而这一行本身就在视口边上，用户一滚就看见。
    修法是拿「一个健康显示行的高度」当下限（`_display_line_height`）。

    单独起一组是因为**必须真的滚**：不滚到那个位置，`dlineinfo` 根本不裁。
    """

    COUNT = 30

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        self.doc = folder / "长清单.md"
        # 每一条都长到折行，行数足够把视口撑满，才滚得动
        lines = [
            f"{index}. 第 {index} 条故意写得很长很长很长很长很长很长很长很长很长，"
            f"长到这一条自己就得占掉两个显示行，滚到边上时正好卡在下沿"
            for index in range(1, self.COUNT + 1)
        ]
        self.doc.write_text("\n".join(lines) + "\n", encoding="utf-8")
        storage.set_default_folder(folder)

        from main import JianJiApp

        self.root = tk.Tk()
        self.root.geometry("1180x760")
        self.app = JianJiApp(self.root)
        self.root.update()
        self.app.open_file(self.doc)
        self.root.update()
        # 正文块宽取决于屏幕宽（见 main.EDITOR_SIDE_RATIO），钉住它这组才可复现：
        # 不钉的话「屏幕越宽 → 正文块越宽 → 折行越少 → 文稿越矮」，宽屏机器上
        # 文稿就撑不满视口了（实测 yview()[0] 掉到 0.43，前提校验直接红）。
        pin_screen_share(self.app, 1.5)
        # 光标放开头：光标所在行不画序号，放结尾会少一个观测点
        self.app.editor.mark_set("insert", "1.0")
        self.app._apply_styles_without_dirty_flag()
        self.root.update()

    def tearDown(self) -> None:
        storage.default_state_path = self._real_state
        self.root.destroy()

    def _scroll_and_collect(self) -> list[str]:
        """逐个滚动位置量一遍，返回「被裁」的描述（空列表 = 全好）。"""
        problems = []
        for step in range(21):
            self.app.editor.yview_moveto(step / 20)
            self.root.update()
            self.app._redraw_margins()
            self.root.update()
            for canvas in self.app._decor_pool:
                spec = getattr(canvas, "decor_spec", None)
                if not spec or spec[0] != "ordered" or not canvas.winfo_manager():
                    continue
                box = canvas.bbox(canvas.find_all()[0])
                if box is None:
                    problems.append(f"{spec} 没画出来")
                    continue
                width, height = canvas.winfo_width(), canvas.winfo_height()
                if box[3] > height + 1:
                    problems.append(f"滚动位置 {step}/20：{spec} 的序号 {box} "
                                    f"下沿越出画布高 {height}")
                elif box[1] < -1:
                    problems.append(f"滚动位置 {step}/20：{spec} 的序号 {box} "
                                    f"上沿越出画布")
        return problems

    def test_the_document_really_scrolls(self) -> None:
        """前提校验：文稿得比视口长，否则下面那条测的是「没滚动」。"""
        self.app.editor.yview_moveto(1.0)
        self.root.update()
        self.assertGreater(self.app.editor.yview()[0], 0.5,
                           "文稿没撑满视口，滚不动，这组测试白跑")

    def test_no_ordered_number_is_clipped_at_any_scroll_position(self) -> None:
        problems = self._scroll_and_collect()
        self.assertEqual(problems, [], "序号在滚动时被画布裁掉了：\n" + "\n".join(problems[:10]))

    def test_the_canvas_keeps_a_whole_line_height(self) -> None:
        """画布高度不能被 Tk 在视口下沿裁过的那一刀带着走。"""
        self.app.editor.yview_moveto(1.0)
        self.root.update()
        self.app._redraw_margins()
        self.root.update()
        expected = self.app._display_line_height()
        self.assertEqual(expected, px(self.app.settings["line_height"]))
        canvases = [c for c in self.app._decor_pool
                    if getattr(c, "decor_spec", None)
                    and c.decor_spec[0] == "ordered" and c.winfo_manager()]
        self.assertTrue(canvases, "底部应当还剩着几个序号")
        for canvas in canvases:
            self.assertGreaterEqual(canvas.winfo_height(), expected,
                                    f"{canvas.decor_spec} 的画布矮于一个显示行")


if __name__ == "__main__":
    unittest.main()
