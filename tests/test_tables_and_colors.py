"""表格语法与语法配色的测试。

表格是**跨行**语法，一行看不出来（要下一行是分隔行才算），所以这里既测
拆分与对齐这类纯函数，也测编辑器里真正套上去的标签与画出来的边框。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import storage  # noqa: E402
from main import (  # noqa: E402
    BOLD_COLOR,
    BULLET_COLORS,
    HEADING_COLORS,
    ITALIC_COLOR,
    TABLE_BORDER,
    TABLE_HEAD_BG,
    TABLE_HEAD_TEXT,
    TABLE_PIPE_COLOR,
    TEXT,
    collect_tables,
    build_table_rows,
    parse_block,
    px,
    _looks_like_table_row,
    _split_row,
    _table_separator,
)

import tkinter as tk  # noqa: E402

try:
    _probe = tk.Tk()
    _probe.destroy()
    HAS_DISPLAY = True
except tk.TclError:  # pragma: no cover
    HAS_DISPLAY = False


def _rgb(color: str) -> tuple[int, int, int]:
    """`#RRGGBB` → 三个 0-255 的分量。"""
    return tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))


def _hue_gap(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """两支颜色的色相角差，取 0-180 度。

    只比 RGB 差值的做法会把「同样是深色」当成「差不多」——比如 H3 那支蓝和
    砖红，RGB 分量差得不多，色相上却隔着大半个色环。粗体/斜体要跟标题分开，
    判的是**观感上是不是一类颜色**，所以按色相判。
    """
    def hue(rgb: tuple[int, int, int]) -> float:
        red, green, blue = (value / 255 for value in rgb)
        high, low = max(red, green, blue), min(red, green, blue)
        span = high - low
        if span == 0:
            return 0.0
        if high == red:
            raw = ((green - blue) / span) % 6
        elif high == green:
            raw = (blue - red) / span + 2
        else:
            raw = (red - green) / span + 4
        return raw * 60

    gap = abs(hue(a) - hue(b)) % 360
    return min(gap, 360 - gap)


class TableSplitTests(unittest.TestCase):
    """单元格拆分：竖线、转义竖线、缺省外框竖线。"""

    def test_outer_pipes_are_optional(self) -> None:
        self.assertEqual(_split_row("| a | b |"), ["a", "b"])
        self.assertEqual(_split_row("a | b"), ["a", "b"])

    def test_cells_are_trimmed(self) -> None:
        self.assertEqual(_split_row("|  a   |   b  |"), ["a", "b"])

    def test_empty_cells_are_kept(self) -> None:
        self.assertEqual(_split_row("| a |  | c |"), ["a", "", "c"])

    def test_escaped_pipe_stays_inside_a_cell(self) -> None:
        self.assertEqual(_split_row(r"| a | x \| y |"), ["a", "x | y"])

    def test_plain_line_is_not_a_row(self) -> None:
        self.assertIsNone(_split_row("就是一句普通的话。"))
        self.assertIsNone(_split_row(""))

    def test_looks_like_table_row_needs_a_pipe(self) -> None:
        self.assertTrue(_looks_like_table_row("| a | b |"))
        self.assertTrue(_looks_like_table_row("a | b"))
        self.assertFalse(_looks_like_table_row("no pipes here"))


class TableSeparatorTests(unittest.TestCase):
    """分隔行 → 每列的对齐方式。"""

    def test_default_alignment_is_none(self) -> None:
        self.assertEqual(_table_separator("| --- | --- |"), ["none", "none"])

    def test_colon_positions_decide_alignment(self) -> None:
        self.assertEqual(_table_separator("| :--- | :---: | ---: |"),
                         ["left", "center", "right"])

    def test_single_dash_is_enough(self) -> None:
        self.assertEqual(_table_separator("| - | - |"), ["none", "none"])

    def test_non_separator_lines_are_rejected(self) -> None:
        self.assertIsNone(_table_separator("| abc | --- |"))
        self.assertIsNone(_table_separator("| --- | x |"))
        self.assertIsNone(_table_separator("普通文字"))


class TableCollectTests(unittest.TestCase):
    """整篇扫描：表格的边界、表头、数据行与行号。"""

    SIMPLE = [
        "| 项目 | 数量 |",
        "| --- | ---: |",
        "| 苹果 | 3 |",
        "| 香蕉 | 5 |",
        "",
        "段落。",
    ]

    def test_simple_table(self) -> None:
        tables = collect_tables(self.SIMPLE)
        self.assertEqual(len(tables), 1)
        table = tables[0]
        self.assertEqual(table.rows, [["项目", "数量"], ["苹果", "3"], ["香蕉", "5"]])
        self.assertEqual(table.alignments, ["none", "right"])
        self.assertEqual(table.line_numbers, [1, 3, 4], "行号要与文档对得上")
        self.assertEqual(table.start, 1)
        self.assertEqual(table.end, 4, "end 是表格之后的一行")

    def test_table_stops_at_a_blank_line(self) -> None:
        tables = collect_tables(self.SIMPLE)
        self.assertEqual(tables[0].end, 4, "空行结束表格，不能把后面的段落吞进来")

    def test_pipe_in_prose_is_not_a_table(self) -> None:
        """正文里偶尔出现竖线，只要下一行不是分隔行就不算表格。"""
        lines = ["这里有个 | 竖线", "但下一行是普通文字"]
        self.assertEqual(collect_tables(lines), [])

    def test_header_and_separator_are_required(self) -> None:
        self.assertEqual(collect_tables(["| a | b |"]), [], "只有表头不算表格")
        self.assertEqual(collect_tables(["| a | b |", "文字"]), [])

    def test_short_rows_are_padded(self) -> None:
        lines = ["| a | b | c |", "| --- | --- | --- |", "| 1 |"]
        table = collect_tables(lines)[0]
        self.assertEqual(table.rows[1], ["1", "", ""], "缺的格子补空，表格不能被撑歪")

    def test_extra_cells_are_dropped(self) -> None:
        """数据行比表头多出的格子要丢掉，不然表格会被多出来的一列撑歪。"""
        lines = ["| a | b |", "| --- | --- |", "| 1 | 2 | 3 |"]
        table = collect_tables(lines)[0]
        self.assertEqual(table.columns, 2)
        self.assertEqual(table.cell(1, 0), "1")

    def test_single_column_table(self) -> None:
        """单列表格也要能用：`| 1 |` 只有一个格子，但两侧有框。"""
        lines = ["| 单独 |", "| --- |", "| 一 |", "| 二 |"]
        tables = collect_tables(lines)
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0].rows, [["单独"], ["一"], ["二"]])

    def test_code_block_pipes_are_ignored(self) -> None:
        """代码块里的竖线不能当成表格。"""
        lines = ["```", "| a | b |", "| --- | --- |", "```"]
        self.assertEqual(collect_tables(lines, [True, True, True, True]), [],
                         "标记了代码块就不要把里面的竖线当表格")

    def test_two_tables_are_separate(self) -> None:
        lines = [
            "| a | b |", "| --- | --- |", "| 1 | 2 |",
            "",
            "| c | d |", "| --- | --- |", "| 3 | 4 |",
        ]
        tables = collect_tables(lines)
        self.assertEqual(len(tables), 2)
        self.assertEqual(tables[0].start, 1)
        self.assertEqual(tables[1].start, 5)

    def test_build_table_rows_maps_separator(self) -> None:
        mapping = build_table_rows(self.SIMPLE)
        self.assertIn(1, mapping)
        self.assertIn(2, mapping, "分隔行也要在映射里，好让渲染时整行藏掉")
        self.assertEqual(mapping[2], (mapping[1][0], -1, 1))
        self.assertEqual(mapping[1][1], 0, "第一行是表头")
        self.assertEqual(mapping[3][1], 1, "第三行是第一个数据行")


class TableParseTests(unittest.TestCase):
    """单行解析要把表格行标成 table，而不是普通正文。"""

    def test_table_row_kind(self) -> None:
        self.assertEqual(parse_block("| a | b |").kind, "table")
        self.assertEqual(parse_block("a | b").kind, "table")

    def test_table_row_has_no_marker(self) -> None:
        """表格行没有整体隐藏的记号，竖线是逐格处理的。"""
        self.assertIsNone(parse_block("| a | b |").marker)

    def test_prose_is_still_text(self) -> None:
        self.assertEqual(parse_block("普通文字").kind, "text")

    def test_list_still_wins_over_table(self) -> None:
        """`- | a |` 仍是列表项，列表判定排在表格前面。"""
        self.assertEqual(parse_block("- | a | b |").kind, "bullet")


class PaletteTests(unittest.TestCase):
    """配色常量本身的自检：各级标题、各层项目符号都要能区分开。"""

    def test_heading_colours_are_distinct(self) -> None:
        colors = [HEADING_COLORS[level] for level in range(1, 7)]
        self.assertEqual(len(set(colors)), 6, "六级标题应各有颜色")

    def test_heading_colours_are_dark_enough(self) -> None:
        """标题底色是白的，颜色太浅会看不清。"""
        for level, color in HEADING_COLORS.items():
            red, green, blue = (int(color[index:index + 2], 16) for index in (1, 3, 5))
            self.assertLess(max(red, green, blue), 200, f"H{level} 太浅：{color}")

    def test_bullet_colours_are_distinct(self) -> None:
        self.assertEqual(len(set(BULLET_COLORS)), len(BULLET_COLORS))

    def test_bold_and_italic_do_not_look_like_headings(self) -> None:
        """粗体/斜体的颜色不能和任何一级标题撞色。

        原来粗体直接用了 `HEADING_COLORS[3]`（就是 H3 那支蓝），正文里一段加粗
        看上去就是个小标题——用户报的就是这个。所以这条按「色相」判：粗体和斜体
        必须落在标题那一路冷色之外，不能只是「不完全相等」。
        """
        heading = set(HEADING_COLORS.values())
        for name, color in (("粗体", BOLD_COLOR), ("斜体", ITALIC_COLOR)):
            self.assertNotIn(color, heading, f"{name} {color} 和某级标题同色")
            red, green, blue = _rgb(color)
            for level, level_color in HEADING_COLORS.items():
                lr, lg, lb = _rgb(level_color)
                self.assertGreater(_hue_gap((red, green, blue), (lr, lg, lb)), 60,
                                   f"{name} {color} 与 H{level} {level_color} 色相太近")

    def test_bold_and_italic_are_clearly_distinguishable(self) -> None:
        """粗体和斜体要一眼能分清。

        原来两者是砖红 #A2452E 与赭褐 #8C6239：色相只差 20°、明度也接近，
        同一段文字里几乎分不出哪个是粗、哪个是斜（用户报的）。现在深红更沉、
        琥珀金更亮，**色相和明度一起拉开**——只靠色相不够，因为两个颜色都留在
        暖色段里（标题、链接、序号、图片说明已经把蓝/紫占满了）。
        """
        self.assertNotEqual(BOLD_COLOR, ITALIC_COLOR)
        self.assertGreater(_hue_gap(_rgb(BOLD_COLOR), _rgb(ITALIC_COLOR)), 30)

        # 明度用相对亮度（人眼对绿最敏感）算，别用三通道之和——那会把
        # 「黄绿偏亮」这件事算轻了。
        def luminance(color: str) -> float:
            red, green, blue = _rgb(color)
            return 0.2126 * red + 0.7152 * green + 0.0722 * blue

        gap = abs(luminance(ITALIC_COLOR) - luminance(BOLD_COLOR))
        self.assertGreater(gap, 40, "粗体和斜体的明度差太小，光靠色相分不开")

    def test_emphasis_stands_out_from_body_text(self) -> None:
        """粗体/斜体要和正文黑明显不同。

        **不能用色相比**：正文 `#23211E` 是接近中性的深灰，它的色相是噪声
        （三通道只差 5，算出来 36° 纯属巧合），拿它去比色相毫无意义。
        真正决定「看得出区别」的是明度，所以这里比相对亮度。
        """
        def luminance(color: str) -> float:
            red, green, blue = _rgb(color)
            return 0.2126 * red + 0.7152 * green + 0.0722 * blue

        body = luminance(TEXT)
        for name, color in (("粗体", BOLD_COLOR), ("斜体", ITALIC_COLOR)):
            self.assertGreater(luminance(color) - body, 20,
                               f"{name} {color} 和正文 {TEXT} 的明度差太小")

    def test_emphasis_colours_are_readable_on_white(self) -> None:
        """正文底色是白的，颜色太浅会看不清（和标题那条一样的要求）。"""
        for name, color in (("粗体", BOLD_COLOR), ("斜体", ITALIC_COLOR)):
            self.assertLess(max(_rgb(color)), 200, f"{name} 太浅：{color}")

    def test_table_colours_differ(self) -> None:
        self.assertNotEqual(TABLE_HEAD_BG, TABLE_BORDER)
        self.assertNotEqual(TABLE_HEAD_TEXT, TABLE_BORDER)


@unittest.skipUnless(HAS_DISPLAY, "没有图形环境")
class TableRenderTests(unittest.TestCase):
    """编辑器里的表格渲染：标签、记号隐藏、边框绘制。"""

    DOC = (
        "# 表格演示\n\n"
        "| 项目 | 数量 | 备注 |\n"
        "| --- | ---: | :---: |\n"
        "| 苹果 | 3 | 很甜 |\n"
        "| 香蕉 | 5 | 很软 |\n"
        "\n"
        "表格之后的段落。\n"
    )

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        self.doc = folder / "表格.md"
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

    def _in_tag(self, tag: str, index: str) -> bool:
        raw = self.app.editor.tag_ranges(tag)
        for i in range(0, len(raw), 2):
            if (self.app.editor.compare(index, ">=", raw[i])
                    and self.app.editor.compare(index, "<", raw[i + 1])):
                return True
        return False

    def _hidden(self, index: str) -> bool:
        return self._in_tag("syntax", index) and not self._in_tag("syntax_current", index)

    def test_header_row_is_tagged(self) -> None:
        self.assertTrue(self._in_tag("table_head", "3.2"))
        self.assertFalse(self._in_tag("table_head", "5.2"), "数据行不该用表头样式")

    def test_data_rows_use_cell_style(self) -> None:
        self.assertTrue(self._in_tag("table_cell", "5.2"))
        self.assertTrue(self._in_tag("table_cell", "6.2"))

    def test_separator_line_is_fully_hidden(self) -> None:
        self.assertTrue(self._hidden("4.0"), "分隔行整行都是语法，平时要藏掉")

    def test_pipes_are_recoloured_not_removed(self) -> None:
        """竖线不能 elide：它是唯一撑开列宽的东西，藏了各格文字就挤成一团。

        改成染成边框色当列分隔线，文字位置一点不动。
        """
        self.assertTrue(self._in_tag("table_pipe", "3.0"), "行首竖线要染成边框色")
        self.assertFalse(self._hidden("3.0"), "竖线不能被藏掉")
        self.assertFalse(self._in_tag("table_pipe", "3.2"), "格子里的字不算竖线")

    def test_cell_text_is_untouched(self) -> None:
        """藏/染竖线都不能碰格子里的字。"""
        line = self.app.editor.get("3.0", "3.end").rstrip("\r\n")
        for cell in ("项目", "数量", "备注"):
            offset = line.index(cell)
            self.assertFalse(self._hidden(f"3.{offset}"), f"{cell} 的首字被误藏了")
            self.assertFalse(self._hidden(f"3.{offset + 1}"), f"{cell} 的第二个字被误藏了")

    def test_every_pipe_is_painted_as_a_border(self) -> None:
        line = self.app.editor.get("3.0", "3.end").rstrip("\r\n")
        for index, char in enumerate(line):
            if char == "|":
                self.assertTrue(self._in_tag("table_pipe", f"3.{index}"),
                                f"第 {index} 个字符是竖线，应染成边框色")

    def test_pipe_colour_matches_the_border(self) -> None:
        color = self.app.editor.tag_cget("table_pipe", "foreground")
        self.assertEqual(color.lower(), TABLE_PIPE_COLOR.lower())

    def test_header_row_has_a_tinted_background(self) -> None:
        background = self.app.editor.tag_cget("table_head", "background")
        self.assertEqual(background.lower(), TABLE_HEAD_BG.lower(),
                         "表头铺浅蓝底，一眼能分出表头和数据行")

    def test_data_rows_have_no_header_tint(self) -> None:
        """数据行的底色只能来自斑马纹，不能是表头那支蓝。"""
        stripe = self.app.editor.tag_cget("table_stripe", "background")
        self.assertNotEqual(stripe.lower(), TABLE_HEAD_BG.lower())

    def test_header_row_is_bold(self) -> None:
        font = self.app.editor.tag_cget("table_head", "font")
        self.assertIn("bold", font.lower())

    def test_stripe_alternates(self) -> None:
        self.assertFalse(self._in_tag("table_stripe", "3.2"), "表头不铺斑马纹")
        self.assertTrue(self._in_tag("table_stripe", "6.2"), "第二行数据铺浅底")
        self.assertFalse(self._in_tag("table_stripe", "5.2"), "第一行数据不铺")

    def test_cursor_line_shows_source(self) -> None:
        self.app.editor.mark_set("insert", "3.4")
        self.app._apply_styles_without_dirty_flag()
        self.root.update()
        self.assertTrue(self._in_tag("syntax_current", "3.0"),
                        "光标在表格行时该行显示源码，方便改")

    def test_frame_is_drawn(self) -> None:
        canvases = [c for c in self.app._table_pool if c.winfo_manager()]
        self.assertGreaterEqual(len(canvases), 4, "外框至少四条线")

    def test_frame_is_made_of_thin_lines_not_a_slab(self) -> None:
        """边框必须由细线组成：Tk 画布不透明，一整块盖上去就把格子内容遮没了。"""
        canvases = [c for c in self.app._table_pool if c.winfo_manager()]
        for canvas in canvases:
            # 每条线要么很矮（横线），要么很窄（竖线），不能同时占据一大片
            thin = canvas.winfo_height() <= px(4) or canvas.winfo_width() <= px(4)
            self.assertTrue(thin, f"这块画布太大了，会挡住文字："
                                  f"{canvas.winfo_width()}x{canvas.winfo_height()}")

    def test_frame_has_horizontal_and_vertical_lines(self) -> None:
        canvases = [c for c in self.app._table_pool if c.winfo_manager()]
        horizontal = [c for c in canvases if c.winfo_height() <= px(4)]
        vertical = [c for c in canvases if c.winfo_width() <= px(4)]
        self.assertGreaterEqual(len(horizontal), 3, "上下框线 + 表头下沿 + 行分隔线")
        self.assertEqual(len(vertical), 2, "只画左右两条外框竖线（列分隔线由竖线字符充当）")

    def test_vertical_lines_reach_both_ends(self) -> None:
        """竖线要覆盖整张表格，不能只画到一半。"""
        canvases = [c for c in self.app._table_pool if c.winfo_manager()]
        heights = {c.winfo_height() for c in canvases if c.winfo_width() <= px(4)}
        self.assertEqual(len(heights), 1, f"几条竖线高度应当一致：{heights}")

    def test_frame_spans_the_text_column(self) -> None:
        """横线要横跨整列正文，从正文左边缘开始。"""
        expected_width = self.app.editor.winfo_width() - self.app._current_pad * 2
        expected_x = self.app.editor.winfo_x() + self.app._current_pad
        canvases = [c for c in self.app._table_pool if c.winfo_manager()]
        horizontal = [c for c in canvases if c.winfo_height() <= px(4)
                      and c.winfo_width() > px(4)]
        self.assertTrue(horizontal, "应当有横线")
        for canvas in horizontal:
            self.assertEqual(canvas.winfo_width(), expected_width)
            self.assertEqual(canvas.winfo_x(), expected_x)

    def test_frame_is_not_drawn_on_the_cursor_line(self) -> None:
        self.app.editor.mark_set("insert", "3.4")
        self.app._apply_styles_without_dirty_flag()
        self.root.update()
        canvases = [c for c in self.app._table_pool if c.winfo_manager()]
        self.assertEqual(canvases, [], "光标在表头行时显示源码，不画框免得压住")

    def test_frames_are_reused_not_leaked(self) -> None:
        before = len(self.app._table_pool)
        for _ in range(5):
            self.app._redraw_table_frames()
            self.root.update()
        self.assertEqual(len(self.app._table_pool), before)

    def test_frames_are_hidden_when_no_document(self) -> None:
        self.app.editor.configure(state="normal")
        self.app.editor.delete("1.0", "end")
        self.app.editor.configure(state="disabled")
        self.app.current_path = None
        self.app.preview_mode = False
        self.app._redraw_table_frames()
        self.root.update()
        self.assertEqual([c for c in self.app._table_pool if c.winfo_manager()], [])

    def test_frame_does_not_cover_any_cell_text(self) -> None:
        """框线不能压到任何一格文字上——这是「表格能看」的底线。

        用字面的 bbox 比要留一点余量：bbox 带上了字体的上下留白，
        紧贴着它的边缘其实并没有碰到笔画。真正不能忍的是线**穿过**字。
        """
        canvases = [c for c in self.app._table_pool if c.winfo_manager()]
        regions = [(c.winfo_rootx(), c.winfo_rooty(),
                    c.winfo_rootx() + c.winfo_width(),
                    c.winfo_rooty() + c.winfo_height()) for c in canvases]
        line = self.app.editor.get("3.0", "3.end").rstrip("\r\n")
        slack = px(2)
        for cell in ("项目", "数量", "备注"):
            column = line.index(cell)
            for row in (3, 5, 6):
                box = self.app.editor.bbox(f"{row}.{column}")
                if box is None or box[2] == 0:
                    continue
                left = self.app.editor.winfo_rootx() + box[0] + slack
                top = self.app.editor.winfo_rooty() + box[1] + slack
                right = self.app.editor.winfo_rootx() + box[0] + box[2] - slack
                bottom = self.app.editor.winfo_rooty() + box[1] + box[3] - slack
                for region in regions:
                    overlaps = (left < region[2] and right > region[0]
                                and top < region[3] and bottom > region[1])
                    self.assertFalse(overlaps,
                                     f"第 {row} 行「{cell}」被表格线穿过了")

    def test_inline_marks_work_inside_cells(self) -> None:
        """单元格里的粗体/行内代码照常生效，单元格不会把行内语法挡住。"""
        self.app.editor.configure(state="normal")
        self.app.editor.insert("5.8", "**甜**")
        self.app.editor.configure(state="disabled")
        self.app._apply_styles_without_dirty_flag()
        self.root.update()
        self.assertTrue(self._in_tag("bold", "5.10"), "格子里的粗体要认出来")

    def test_tables_do_not_get_list_decorations(self) -> None:
        specs = [c.decor_spec for c in self.app._decor_pool if c.decor_spec is not None]
        self.assertNotIn("table", [s[0] for s in specs])


if __name__ == "__main__":
    unittest.main()
