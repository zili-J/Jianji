"""导出长图 + 滚轮速度的回归测试。

守四件事：

1. **记号必须真的消失**。编辑器里靠 `elide` 把 `**`、`` ` ``、`[`、`](url)`
   藏起来；图里没有 elide 这回事，必须把那些字符从文字里剔掉，
   否则导出的图上会明晃晃地写着 Markdown 源码。
2. **块级结构复用编辑器那套解析**：围栏代码块的行、表格的分隔行、
   标题的层级，图里和屏幕上必须一致。
3. **排版不越界**：任何显示行都不能超过正文宽度，纵坐标必须单调递增，
   基线必须落在自己的行框里——这三条任意一条破了，图就会叠字或错位。
4. **滚轮正好是 `EDITOR_WHEEL_FACTOR` 倍**（断言直接取常量，改倍数不用改测试），
   且换行/表格切分不会把一张表劈成两半。
"""
from __future__ import annotations

import re
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import image_export  # noqa: E402
import main as app_main  # noqa: E402
import storage  # noqa: E402
from image_export import (  # noqa: E402
    Options,
    Palette,
    Piece,
    _Fonts,
    build_layout,
    render,
)

import tkinter as tk  # noqa: E402

try:
    _probe = tk.Tk()
    _probe.destroy()
    HAS_DISPLAY = True
except tk.TclError:  # pragma: no cover
    HAS_DISPLAY = False


def png_size(data: bytes) -> tuple[int, int]:
    return struct.unpack(">II", data[16:24])


def png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    """按 PNG 的分块结构切开，顺便校验每块的 CRC。

    这一步不能省：压好的数据少包一层 IDAT，文件头看着仍然像 PNG，
    看图工具也可能宽容地认了，但结构其实是坏的——必须真的走一遍分块。
    """
    assert data.startswith(b"\x89PNG\r\n\x1a\n"), "缺少 PNG 签名"
    chunks: list[tuple[bytes, bytes]] = []
    position = 8
    while position < len(data):
        assert position + 12 <= len(data), \
            f"第 {position} 字节处剩不下一个完整分块（缺 length/tag/CRC？）"
        length = struct.unpack(">I", data[position:position + 4])[0]
        tag = data[position + 4:position + 8]
        assert position + 12 + length <= len(data), \
            f"{tag} 块声明长度 {length}，但文件里装不下"
        body = data[position + 8:position + 8 + length]
        stored = struct.unpack(">I", data[position + 8 + length:position + 12 + length])[0]
        assert stored == zlib.crc32(tag + body) & 0xFFFFFFFF, f"{tag} 的 CRC 不对"
        chunks.append((tag, body))
        position += 12 + length
    assert position == len(data), "分块长度和文件长度对不上"
    return chunks


def small_options(**overrides) -> Options:
    values = dict(width=600, margin=40, body_size=24, line_height=36, mono_size=22,
                  list_indent=32, list_marker_gap=16, marker_radius=5, task_box=18,
                  quote_bar_width=5, quote_bar_gap=22, hr_thickness=2, badge_size=14,
                  badge_gap=12, head_gap=8, table_padding=14)
    values.update(overrides)
    return Options(**values)


# ---------- 行内样式 ----------

class InlineStyleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.options = small_options()

    def pieces(self, line: str, base: dict | None = None):
        return app_main._export_pieces(
            line, base or {"family": "A", "size": 24}, self.options)

    def text_of(self, pieces) -> str:
        return "".join(piece.text for piece in pieces)

    def test_marks_are_removed_from_the_text(self) -> None:
        pieces = self.pieces("有 **粗体** 和 `代码` 和 [链接](http://a.b) 和 ~~删~~。")
        self.assertEqual(self.text_of(pieces), "有 粗体 和 代码 和 链接 和 删。")
        for piece in pieces:
            for mark in ("*", "`", "[", "]", "(http://a.b)"):
                self.assertNotIn(mark, piece.text)

    def test_bold_italic_strike_highlight_get_their_own_style(self) -> None:
        pieces = self.pieces("**粗**、*斜*、~~删~~、==亮==")
        styles = {piece.text: piece for piece in pieces}
        self.assertTrue(styles["粗"].bold)
        self.assertTrue(styles["斜"].italic)
        self.assertTrue(styles["删"].strike)
        self.assertEqual(styles["亮"].background, app_main.HIGHLIGHT_BG)
        self.assertEqual(styles["粗"].color, app_main.BOLD_COLOR)
        self.assertEqual(styles["斜"].color, app_main.ITALIC_COLOR)

    def test_inline_code_switches_to_the_mono_font(self) -> None:
        pieces = self.pieces("前 `x = 1` 后")
        code = next(piece for piece in pieces if piece.text == "x = 1")
        self.assertEqual(code.family, self.options.mono_family)
        self.assertEqual(code.size, self.options.mono_size)
        self.assertEqual(code.background, app_main.CODE_BG)
        self.assertEqual(code.color, app_main.CODE_TEXT)

    def test_link_and_image_are_distinguished(self) -> None:
        pieces = self.pieces("[文字](a) 与 ![图](b.png)")
        link = next(piece for piece in pieces if piece.text == "文字")
        image = next(piece for piece in pieces if piece.text == "图")
        self.assertTrue(link.underline)
        self.assertEqual(link.color, app_main.LINK_COLOR)
        self.assertFalse(link.italic)
        self.assertTrue(image.italic)
        self.assertEqual(image.color, app_main.IMAGE_COLOR)

    def test_code_wins_over_emphasis_inside_it(self) -> None:
        """`` `**x**` `` 里的星号是代码内容，不能当粗体记号。"""
        pieces = self.pieces("`**x**`")
        self.assertEqual(self.text_of(pieces), "**x**")
        self.assertEqual(len(pieces), 1)
        self.assertEqual(pieces[0].family, self.options.mono_family)
        self.assertFalse(pieces[0].bold)

    def test_link_label_can_still_be_bold(self) -> None:
        pieces = self.pieces("[**粗**](a)")
        self.assertEqual(self.text_of(pieces), "粗")
        self.assertTrue(pieces[0].bold)
        self.assertTrue(pieces[0].underline)

    def test_plain_line_is_a_single_piece(self) -> None:
        pieces = self.pieces("一行没有任何记号的正文")
        self.assertEqual(len(pieces), 1)
        self.assertEqual(pieces[0].text, "一行没有任何记号的正文")
        self.assertFalse(pieces[0].bold)

    def test_underscore_in_english_words_is_not_italic(self) -> None:
        pieces = self.pieces("snake_case_name 不该变斜体")
        self.assertEqual(len(pieces), 1)
        self.assertFalse(pieces[0].italic)


# ---------- 块级结构 ----------

class ExportRowsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.options = small_options()

    def rows(self, text: str):
        return app_main.build_export_rows(text, self.options)

    def test_heading_gets_level_and_badge(self) -> None:
        rows = self.rows("## 小标题\n")
        self.assertEqual(rows[0].kind, "heading")
        self.assertEqual(rows[0].level, 2)
        self.assertEqual(rows[0].badge, "H2")
        self.assertEqual(rows[0].pieces[0].text, "小标题")
        self.assertEqual(rows[0].pieces[0].color, app_main.HEADING_COLORS[2])

    def test_fence_lines_are_dropped_and_content_is_code(self) -> None:
        rows = self.rows("```python\nprint(1)\n```\n")
        self.assertEqual([row.kind for row in rows], ["code"])
        self.assertEqual(rows[0].pieces[0].text, "print(1)")
        self.assertEqual(rows[0].pieces[0].color, app_main.CODEBLOCK_TEXT)
        self.assertEqual(rows[0].pieces[0].background, app_main.CODE_BG)

    def test_fence_of_different_length_does_not_close(self) -> None:
        """四个反引号开的块，里面写三个反引号是内容，不是结束。"""
        rows = self.rows("````\n```\n````\n")
        self.assertEqual([row.kind for row in rows], ["code"])
        self.assertEqual(rows[0].pieces[0].text, "```")

    def test_list_kinds_and_numbers(self) -> None:
        rows = self.rows("- 点\n  - 深一层\n1. 有序\n- [x] 完成\n- [ ] 未完成\n")
        self.assertEqual([row.kind for row in rows],
                         ["bullet", "bullet", "ordered", "task", "task"])
        self.assertEqual([row.level for row in rows], [0, 1, 0, 0, 0])
        self.assertEqual(rows[2].number, "1.")
        self.assertTrue(rows[3].checked)
        self.assertFalse(rows[4].checked)
        self.assertEqual(rows[0].pieces[0].text, "点")

    def test_quote_and_hr(self) -> None:
        rows = self.rows("> 引用\n\n---\n")
        self.assertEqual(rows[0].kind, "quote")
        self.assertEqual(rows[0].pieces[0].text, "引用")
        self.assertEqual(rows[0].pieces[0].color, app_main.QUOTE_TEXT)
        self.assertEqual(rows[1].kind, "blank")
        self.assertEqual(rows[2].kind, "hr")

    def test_table_becomes_one_row_with_all_lines(self) -> None:
        rows = self.rows("| 甲 | 乙 |\n| :-- | --: |\n| 1 | 2 |\n| 3 | 4 |\n")
        self.assertEqual(len(rows), 1)
        table = rows[0]
        self.assertEqual(table.kind, "table")
        self.assertEqual(len(table.cells), 3)            # 表头 + 两行数据，分隔行没有
        self.assertEqual(table.alignments, ["left", "right"])
        self.assertEqual(table.cells[0][0][0].text, "甲")
        self.assertTrue(table.cells[0][0][0].bold)
        self.assertEqual(table.cells[0][0][0].color, app_main.TABLE_HEAD_TEXT)
        self.assertFalse(table.cells[1][0][0].bold)

    def test_code_block_content_is_verbatim(self) -> None:
        """代码块里的记号是要照着写的代码内容，不能当语法处理掉。"""
        rows = self.rows("```markdown\n**粗体** 和 ~~删除线~~ 和 # 井号\n```\n")
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0].pieces), 1)
        piece = rows[0].pieces[0]
        self.assertEqual(piece.text, "**粗体** 和 ~~删除线~~ 和 # 井号")
        self.assertFalse(piece.bold)
        self.assertFalse(piece.strike)
        self.assertEqual(piece.family, self.options.mono_family)

    def test_table_in_code_block_is_not_a_table(self) -> None:
        """代码块里的竖线是内容，不能当表格；两行内容各占一行，围栏不画。"""
        rows = self.rows("```\n| a | b |\n| - | - |\n```\n")
        self.assertEqual([row.kind for row in rows], ["code", "code"])
        self.assertEqual(rows[0].pieces[0].text, "| a | b |")
        self.assertEqual(rows[1].pieces[0].text, "| - | - |")

    def test_trailing_blank_lines_are_trimmed(self) -> None:
        rows = self.rows("正文\n\n\n\n")
        self.assertEqual([row.kind for row in rows], ["text"])

    def test_empty_document_produces_no_rows(self) -> None:
        self.assertEqual(self.rows("\n\n  \n"), [])

    def test_blank_line_between_paragraphs_is_kept(self) -> None:
        rows = self.rows("一\n\n二\n")
        self.assertEqual([row.kind for row in rows], ["text", "blank", "text"])


class ContinuationJoinTests(unittest.TestCase):
    """缩进续行必须并进上一段（Markdown 的惰性续行）。

    不并的话，列表项写成长短两行时会被从中间截断——第一行末尾常常正好是个
    逗号，读起来就是「逗号后面的文字换到了新的一行」（用户报的导出异常换行）。
    续行行首那两格是 Markdown 的排版空白，也不该当成正文画出来。
    """

    def setUp(self) -> None:
        self.options = small_options()

    def rows(self, text: str):
        return app_main.build_export_rows(text, self.options)

    @staticmethod
    def joined(row) -> str:
        return "".join(piece.text for piece in row.pieces)

    def test_an_indented_continuation_joins_the_list_item(self) -> None:
        rows = self.rows("- 前半句，\n  后半句。\n")
        self.assertEqual([row.kind for row in rows], ["bullet"])
        self.assertEqual(self.joined(rows[0]), "前半句，后半句。")

    def test_the_continuation_indent_is_not_rendered_as_content(self) -> None:
        rows = self.rows("- 前半句，\n  后半句。\n")
        self.assertNotIn(" ", self.joined(rows[0]))

    def test_a_plain_paragraph_continuation_joins_too(self) -> None:
        rows = self.rows("第一行，\n  第二行。\n")
        self.assertEqual([row.kind for row in rows], ["text"])
        self.assertEqual(self.joined(rows[0]), "第一行，第二行。")

    def test_several_continuations_all_join(self) -> None:
        rows = self.rows("- 一，\n  二，\n  三。\n")
        self.assertEqual([row.kind for row in rows], ["bullet"])
        self.assertEqual(self.joined(rows[0]), "一，二，三。")

    def test_a_hard_break_stops_the_join(self) -> None:
        """行尾两个空格是 Markdown 的硬换行：那两行是特意分开的，不能粘起来。"""
        rows = self.rows("- 第一行  \n  第二行\n")
        self.assertEqual([row.kind for row in rows], ["bullet", "text"])

    def test_a_hard_break_continuation_drops_its_indent(self) -> None:
        """硬换行续行的行首缩进同样是排版空白，不能画成一小块留白。"""
        rows = self.rows("- 第一行  \n  第二行\n")
        self.assertEqual(self.joined(rows[1]), "第二行")

    def test_an_isolated_indented_line_keeps_its_indent(self) -> None:
        """上一行是空行时不是续行，缩进原样留着。"""
        rows = self.rows("段落\n\n  缩进的一行\n")
        self.assertEqual(self.joined(rows[-1]), "  缩进的一行")

    def test_a_blank_line_stops_the_join(self) -> None:
        rows = self.rows("- 第一行\n\n  第二行\n")
        self.assertEqual([row.kind for row in rows], ["bullet", "blank", "text"])

    def test_an_indented_nested_list_item_is_not_absorbed(self) -> None:
        """缩进的「- 」是下一层列表项，不是续行。"""
        rows = self.rows("- 外层\n  - 内层\n")
        self.assertEqual([row.kind for row in rows], ["bullet", "bullet"])
        self.assertEqual(rows[1].level, 1)

    def test_a_heading_is_never_absorbed_into(self) -> None:
        rows = self.rows("## 标题\n  下一行\n")
        self.assertEqual([row.kind for row in rows], ["heading", "text"])

    def test_english_continuation_gets_a_space(self) -> None:
        """英文折行后需要那个空格；中文之间不能插空格。"""
        rows = self.rows("- first part\n  second part\n")
        self.assertEqual(self.joined(rows[0]), "first part second part")


# ---------- 折行 ----------

class WrapTests(unittest.TestCase):
    """折行的宽度账目。

    断在空格后时，断点之后的字必须重新累加一遍宽度。曾经漏了这一步：
    那些字「走过」但没算进新行，于是新行悄悄超宽，画出来就是右边被切掉。
    """

    def setUp(self) -> None:
        self.options = small_options()
        self.fonts = _Fonts()

    def tearDown(self) -> None:
        self.fonts.close()

    def wrap(self, text: str, width: int):
        return image_export._wrap(
            [Piece(text, self.options.family, self.options.body_size)],
            width, self.fonts)

    def width_of(self, pieces) -> int:
        return sum(self.fonts.width(piece.text, piece.font) for piece in pieces)

    def test_space_break_does_not_overflow(self) -> None:
        """带空格的长中文行：断在空格后，续行的宽度账目必须重新算。"""
        text = "输入停顿约 0.7 秒后自动保存。" + "保存采用整体替换的方式，" * 6
        lines = self.wrap(text, 700)
        self.assertGreater(len(lines), 2)
        for line in lines:
            self.assertLessEqual(self.width_of(line), 700,
                                 f"折出来的行超宽了：{''.join(p.text for p in line)!r}")

    def test_english_wraps_at_spaces_and_stays_within_width(self) -> None:
        text = " ".join(["word"] * 60)
        lines = self.wrap(text, 300)
        self.assertGreater(len(lines), 5)
        for line in lines:
            self.assertLessEqual(self.width_of(line), 300)
            self.assertFalse(line[-1].text.endswith(" "), "行尾不该留空格")

    def test_trailing_hard_break_spaces_do_not_make_an_empty_line(self) -> None:
        """Markdown 用行尾两个空格表示换行，不该因此在图里多出一行空白。"""
        lines = self.wrap("一句话。  ", 900)
        self.assertEqual(len(lines), 1)
        self.assertEqual("".join(p.text for p in lines[0]), "一句话。")

    def test_every_visible_character_survives_the_wrap(self) -> None:
        """折行只该拆行、丢掉断点处的那个空格，不该吃字。"""
        text = "中文与 English 混排的一句话，里面还有 0.7 这样的数字。"
        lines = self.wrap(text, 260)
        joined = "".join(p.text for line in lines for p in line)
        strip = lambda value: "".join(value.split())      # noqa: E731
        self.assertEqual(strip(joined), strip(text))

    def test_empty_input_yields_one_empty_line(self) -> None:
        self.assertEqual(self.wrap("", 300), [[]])

    def test_a_single_wide_character_still_gets_a_line(self) -> None:
        lines = self.wrap("宽", 10)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0][0].text, "宽")

    def test_a_line_starting_with_spaces_does_not_make_an_empty_line(self) -> None:
        """行首的空格不能变成一整行空白（用户报的「随机增加换行」）。

        Markdown 用行尾两个空格表示换行，编辑器里那些「续行」就带着两个前导空格。
        折行时如果回头去找空格，找到的正是那两个前导空格，于是甩出一行纯空白——
        图里看就是凭空多了一个空行。
        """
        text = "  " + "语法记号平时自动隐藏，光标移到哪一行那一行的记号就显示出来。" * 3
        lines = self.wrap(text, 700)
        self.assertGreater(len(lines), 1, "这一段本来就该折行")
        for line in lines:
            content = "".join(piece.text for piece in line)
            self.assertTrue(content.strip(), f"折出了一行空白：{content!r}")

    def test_no_wrapped_line_starts_with_a_break_space(self) -> None:
        """断点上的那个空格属于上一行行尾，不该被推到下一行行首。"""
        text = ("输入停顿约 0.7 秒后自动保存。保存采用整体替换的方式，"
                "写入前先落一份临时文件再改名，避免中途断电留下半个文件。")
        lines = self.wrap(text, 500)
        self.assertGreater(len(lines), 1)
        for line in lines[1:]:
            self.assertFalse(line[0].text.startswith(" "),
                             f"续行以空格开头：{''.join(p.text for p in line)!r}")

    def test_chinese_lines_fill_up_before_breaking(self) -> None:
        """中文行要断在**最紧**的位置，不能因为「上一个空格」白扔半行。

        中文本来就能在任意字前断开，回头去找十几字以前的那个空格，会让一行只填到
        一半，下一行还得接着排——肉眼看同样是「多了一个换行」。
        """
        text = "字体保持不变。修复中文 Windows 字体名为本地化导致推荐字体识别失败的问题。"
        width = 700
        lines = self.wrap(text, width)
        self.assertGreater(len(lines), 1, "这一段本来就该折行")
        for line in lines[:-1]:                    # 最后一行允许填不满
            used = self.width_of(line)
            self.assertGreaterEqual(
                used, width * 0.9,
                f"这一行只填了 {used}/{width}：{''.join(p.text for p in line)!r}")


class BreakIndexTests(unittest.TestCase):
    """`_break_index` 的断点规则：只在「空格后」或「宽字符旁」断，且取最近的一个。

    这条规则是「导出长图随机增加换行」的根：退得太远会白扔半行，
    退到行首的空白里会直接甩出一整行空白。
    """

    def test_a_wide_character_breaks_immediately(self) -> None:
        self.assertEqual(image_export._break_index("很长的中文句子", 0, 3), 3)

    def test_an_overflowing_space_breaks_after_itself(self) -> None:
        self.assertEqual(image_export._break_index("abc def", 0, 3), 4)

    def test_a_latin_word_backs_up_to_the_nearest_space(self) -> None:
        self.assertEqual(image_export._break_index("hello world", 0, 9), 6)

    def test_the_nearest_break_wins_over_a_distant_space(self) -> None:
        text = "字体保持不变。修复中文 Windows 字体名为本地化（楷体/KaiTi）"
        index = text.index("i）")                  # 卡在 KaiTi 这个词里面
        self.assertEqual(image_export._break_index(text, 0, index),
                         text.index("/KaiTi"))     # 断在「楷体」后面，而不是退到空格

    def test_leading_spaces_are_not_a_break_point(self) -> None:
        """行首那两个空格不能当断点——照它断会甩出一整行空白。

        形状取自真实文稿：缩进 + 一个不含空格的长串（URL、路径、长单词）。
        """
        text = "  " + "https://example.com/a/very/long/path"
        index = text.index("a/very")
        self.assertEqual(image_export._break_index(text, 0, index), index)

    def test_a_very_long_latin_run_still_breaks_somewhere(self) -> None:
        """整段没有任何可断点（超长单词）时只能按字断，不能死循环或返回 0。"""
        self.assertEqual(image_export._break_index("x" * 40, 0, 20), 20)

    def test_the_break_never_goes_before_the_line_start(self) -> None:
        """行内没有可断点时返回当前位置，绝不越过 start 往回跑。"""
        self.assertEqual(image_export._break_index("abcdefg", 4, 6), 6)


class KinsokuTests(unittest.TestCase):
    """中文禁则：收尾类标点不能落到行首，开启类标点不能落到行尾。

    用户报的「导出长图后还是异常换行，例如在逗号后」就是缺了这条：断点规则只认
    「最近的可断点」，而中文标点**自己就是可断点**（宽字符），于是 `，`、`。`、
    `）」` 这些会被顶到行首去——图里就是标点单独占一行。
    """

    def test_a_closing_punctuation_is_pulled_back_one_character(self) -> None:
        """`。` 放不下时不能自己占一行，要把前一个字一起带下去。"""
        text = "写作区的字体设置变。"
        self.assertEqual(image_export._break_index(text, 0, 9), 8)

    def test_a_whole_punctuation_run_is_pulled_back(self) -> None:
        """连着几个标点（`）」`）要一起带下去，不能只退一格又撞上另一个标点。"""
        text = "一篇文档）」。"
        self.assertEqual(image_export._break_index(text, 0, 6), 3)

    def test_an_opening_punctuation_does_not_end_the_line(self) -> None:
        """行尾禁则：`「` 留在上一行末尾也是坏断行。"""
        text = "这是一句说明「引用」"
        self.assertEqual(image_export._break_index(text, 0, 7), 6)

    def test_a_punctuation_after_a_space_may_start_a_line(self) -> None:
        """前面是空格说明它本来就是个独立记号（`.简记回收站`、`3.12`），断在那儿是对的。"""
        text = "简记文件夹内的 .简记回收站"
        index = text.index(".")
        self.assertEqual(image_export._break_index(text, 0, index), index)

    def test_the_kinsoku_never_eats_the_whole_line(self) -> None:
        """整段都是标点时退不动了，就原样返回（调用方会退化成按字断）。"""
        self.assertEqual(image_export._kinsoku("，，，", 0, 2), 0)

    def test_the_kinsoku_stops_at_the_line_start(self) -> None:
        """`cut` 不能退到 `start` 之前——退过去就把上一行的字吃掉了。"""
        self.assertEqual(image_export._kinsoku("ab，。", 2, 3), 2)


class KinsokuWrapTests(unittest.TestCase):
    """折行整体上守住禁则——**包括标点单独待在一个片段里的情况**。

    标点经常自己成段（`**粗体**。` 的句号、行内代码后面的 `）`）。按片段各折各的话，
    断点正好落在片段开头，`_break_index` 在那个片段里没有任何回退余地，禁则就落空了
    （实测使用说明里 13 处行首标点，修完段内的还剩 5 处，全是这种）。
    """

    def setUp(self) -> None:
        self.options = small_options()
        self.fonts = _Fonts()

    def tearDown(self) -> None:
        self.fonts.close()

    def body(self, text: str) -> Piece:
        return Piece(text, self.options.family, self.options.body_size)

    def test_a_punctuation_in_its_own_piece_is_not_left_at_the_line_start(self) -> None:
        head = self.body("在文稿列表里右键一篇文档 →「移动到」")
        tail = self.body("，选一个文件夹即可。")
        width = sum(self.fonts.char_widths(head.text, head.font)) \
            + self.fonts.width("，", head.font) - 1
        lines = image_export._wrap([head, tail], width, self.fonts)
        self.assertGreater(len(lines), 1, "这一段本来就该折行")
        for line in lines[1:]:
            content = "".join(piece.text for piece in line)
            self.assertNotIn(content[0], image_export._CLOSING,
                             f"续行以标点开头：{content!r}")

    def test_the_line_is_still_filled_when_a_long_token_follows(self) -> None:
        """回扫不许跨片段：长代码标识符前面那个「、」不能当断点。

        跨过去的话这一行会白扔 40% 的宽度（实测 project.md 里从 2 处涨到 37 处），
        看着同样是「凭空多了一个换行」。
        """
        head = self.body("…滚动后仍点得中自己、卡片空隙/左右留白归最近的卡片、点完不乱跳）、")
        code = Piece("tests/test_move_document.py", self.options.mono_family,
                     self.options.mono_size)
        width = sum(self.fonts.char_widths(head.text, head.font)) \
            + self.fonts.width("tests/test_move_do", code.font)
        lines = image_export._wrap([head, code], width, self.fonts)
        self.assertGreater(len(lines), 1, "这一段本来就该折行")
        first = "".join(piece.text for piece in lines[0])
        self.assertTrue(first.startswith(head.text))
        self.assertGreater(len(first), len(head.text),
                           "第一行白扔了宽度：标识符被整个推到了下一行")

    def test_the_pieces_keep_their_own_style_after_wrapping(self) -> None:
        """摊平再拼回来不能把样式弄混——粗体、等宽各归各位。"""
        bold = Piece("加粗的一段中文，", self.options.family, self.options.body_size,
                     bold=True)
        mono = Piece("code_here", self.options.mono_family, self.options.mono_size)
        plain = Piece("后面还有普通正文，足够长到必须折行才行。",
                      self.options.family, self.options.body_size)
        lines = image_export._wrap([bold, mono, plain], 300, self.fonts)
        self.assertGreater(len(lines), 1)
        for line in lines:
            for piece in line:
                if "加粗" in piece.text:
                    self.assertTrue(piece.bold, f"粗体样式丢了：{piece.text!r}")
                if "code_here" in piece.text:
                    self.assertEqual(piece.font[0], self.options.mono_family)
                    self.assertFalse(piece.bold)


class ExportKinsokuLayoutTests(unittest.TestCase):
    """整篇排版层面：折出来的续行不能以收尾类标点开头。"""

    # 重复句式是故意的：每行大约放得下 20 来个字，逗号会频繁正好卡在行尾，
    # 只要禁则没做，续行就会以逗号开头。
    # 每行开头的 `+` 不能省——相邻字面量会被 Python 先隐式拼起来再乘，那样
    # 重复之间会塞进 `\n\n`，每一遍都变成独立一段，反而折不了行。
    DOC = (
        "这是一句够长的中文说明，" * 6 + "\n\n"
        + "- 列表项里的中文也要够长，" * 5 + "\n\n"
        + "> 引用里的中文也一样，" * 5 + "\n\n"
        + "行内代码后面跟标点 `简记使用说明.md`，" * 4 + "\n"
    )

    def test_no_continuation_line_starts_with_a_closing_punctuation(self) -> None:
        options = small_options()
        fonts = _Fonts()
        try:
            rows = app_main.build_export_rows(self.DOC, options)
            lines = build_layout(rows, options, Palette(), fonts)
            groups: list[list] = []
            for line in lines:
                if groups and groups[-1][0].row is line.row:
                    groups[-1].append(line)
                else:
                    groups.append([line])
            wrapped = 0
            for group in groups:
                for number, line in enumerate(group):
                    if not number:
                        continue
                    content = "".join(piece.text for piece in line.pieces)
                    if not content:
                        continue
                    wrapped += 1
                    self.assertNotIn(
                        content[0], image_export._CLOSING,
                        f"第 {number + 1} 显示行以标点开头：{content[:20]!r}")
            self.assertGreater(wrapped, 3, "这份文稿本来就该折出好几行")
        finally:
            fonts.close()


# ---------- 排版 ----------

class LayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.options = small_options()
        self.fonts = _Fonts()

    def tearDown(self) -> None:
        self.fonts.close()

    def layout(self, text: str, options: Options | None = None):
        options = options or self.options
        rows = app_main.build_export_rows(text, options)
        return options, build_layout(rows, options, app_main.export_palette(),
                                     self.fonts)

    def test_no_display_line_exceeds_the_content_width(self) -> None:
        text = ("# 标题\n\n" + "很长的正文。" * 40 + "\n\n"
                "- " + "列表里很长的一段话。" * 20 + "\n\n"
                # 带空格的行 + 行尾两个空格（Markdown 硬换行）：最容易漏算宽度的形状
                "输入停顿约 0.7 秒后自动保存。保存采用整体替换的方式，  \n"
                "a very long english sentence that must wrap at spaces correctly.  \n\n"
                "> " + "引用里也要能正确折行的一段话。" * 12 + "\n\n"
                "| 很宽的列标题 | 另一列 |\n| --- | --- |\n| " + "格子内容。" * 30 + " | x |\n")
        options, lines = self.layout(text)
        right = options.margin + options.content_width
        for line in lines:
            if line.table is not None:
                continue                      # 表格自己按列宽排，另有一条测试
            used = sum(self.fonts.width(piece.text, piece.font)
                       for piece in line.pieces)
            self.assertLessEqual(line.x + used, right + 2,
                                 f"第 {line.top} px 那一行超出正文宽度："
                                 f"{''.join(p.text for p in line.pieces)!r}")

    def test_no_display_line_is_blank_inside_a_paragraph(self) -> None:
        """段落内部不能凭空多出一个空显示行（用户报的「随机增加换行」）。

        空行只该来自文稿里真的空了一行（`blank` 行），折行本身绝不能造出空行。
        """
        text = ("  语法记号平时自动隐藏，光标移到哪一行那一行的记号就显示出来，方便修改。\n"
                "- **输入即预览**：输入 Markdown 语法后立刻呈现排版效果，没有切换。  \n"
                "正文。  \n")
        options, lines = self.layout(text)
        for line in lines:
            if line.row.kind == "blank":
                continue
            content = "".join(piece.text for piece in line.pieces)
            self.assertTrue(content.strip(),
                            f"第 {line.top} px 是一个空显示行（块类型 {line.row.kind}）")

    def test_table_never_exceeds_the_content_width(self) -> None:
        text = ("| " + " | ".join(f"列{index}" for index in range(6)) + " |\n"
                "| " + " | ".join("---" for _ in range(6)) + " |\n"
                "| " + " | ".join("很长很长的单元格内容" for _ in range(6)) + " |\n")
        options, lines = self.layout(text)
        for line in lines:
            if line.table is None:
                continue
            widths = line.table[0]
            self.assertLessEqual(sum(widths), options.content_width + 1)

    def test_tops_increase_and_baselines_stay_inside_their_line(self) -> None:
        text = ("# 标题\n\n正文一段。\n\n- 列表\n- [x] 待办\n\n> 引用\n\n```\ncode\n```\n\n"
                "---\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\n结尾。\n")
        _options, lines = self.layout(text)
        previous = -1
        for line in lines:
            self.assertGreater(line.top, previous)
            previous = line.top
            self.assertGreater(line.baseline, line.top)
            self.assertLess(line.baseline, line.top + line.height + 1)

    def test_first_chunk_keeps_the_top_margin(self) -> None:
        options, lines = self.layout("正文\n")
        self.assertGreaterEqual(lines[0].top, 0)
        images = render(app_main.build_export_rows("正文\n", options), options,
                        app_main.export_palette(), self.fonts)
        width, height = png_size(images[0])
        self.assertEqual(width, options.width)
        self.assertGreaterEqual(height, options.margin * 2)


# ---------- 渲染 ----------

class RenderTests(unittest.TestCase):
    def test_png_header_is_valid_and_width_is_fixed(self) -> None:
        options = small_options()
        images = render(app_main.build_export_rows("# 标题\n\n正文\n", options), options,
                        app_main.export_palette())
        self.assertEqual(len(images), 1)
        self.assertTrue(images[0].startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(png_size(images[0])[0], options.width)

    def test_height_grows_with_content(self) -> None:
        options = small_options()
        palette = app_main.export_palette()
        short = render(app_main.build_export_rows("一行\n", options), options, palette)
        long = render(app_main.build_export_rows("一行\n" * 40, options), options, palette)
        self.assertGreater(png_size(long[0])[1], png_size(short[0])[1])

    def test_tall_document_is_split_into_several_images(self) -> None:
        options = small_options(max_height=500)
        images = render(app_main.build_export_rows("正文一行。\n" * 60, options), options,
                        app_main.export_palette())
        self.assertGreater(len(images), 1)
        for data in images:
            self.assertLessEqual(png_size(data)[1], 500)

    def test_a_table_is_never_split_across_images(self) -> None:
        options = small_options(max_height=460)
        text = "".join(f"| 第 {index} 行 | 内容 |\n" for index in range(12))
        text = "| 甲 | 乙 |\n| --- | --- |\n" + text
        rows = app_main.build_export_rows(text, options)
        fonts = _Fonts()
        try:
            lines = build_layout(rows, options, app_main.export_palette(), fonts)
            chunks = image_export._split_chunks(lines, options)
        finally:
            fonts.close()
        table_chunks = [chunk for chunk in chunks
                        if any(line.row.kind == "table" for line in chunk)]
        self.assertEqual(len(table_chunks), 1, "表格被切到两张图里了")
        self.assertEqual(len(table_chunks[0]), len(lines), "表格的行没全在一张里")

    def test_empty_input_still_produces_an_image(self) -> None:
        options = small_options()
        images = render([], options, app_main.export_palette())
        self.assertEqual(len(images), 1)
        self.assertGreater(png_size(images[0])[1], 0)

    def test_png_chunk_structure_is_valid(self) -> None:
        """压好的数据必须包在 IDAT 里——少包一层，文件头照样像 PNG。"""
        options = small_options()
        data = render(app_main.build_export_rows("# 标题\n\n正文\n", options),
                      options, app_main.export_palette())[0]
        chunks = png_chunks(data)
        self.assertEqual([tag for tag, _body in chunks],
                         [b"IHDR", b"IDAT", b"IEND"])
        header = dict(chunks)[b"IHDR"]
        width, height, depth, color = struct.unpack(">IIBB", header[:10])
        self.assertEqual((width, height, depth, color),
                         (options.width, png_size(data)[1], 8, 6))

        # IDAT 解出来的应该是「每行 1 字节过滤器 + 一整行像素」
        raw = zlib.decompress(dict(chunks)[b"IDAT"])
        self.assertEqual(len(raw), height * (options.width * 4 + 1))
        self.assertEqual({raw[index] for index in
                          range(0, len(raw), options.width * 4 + 1)}, {0})


# ---------- 配色与尺寸 ----------

class PaletteAndOptionsTests(unittest.TestCase):
    def test_palette_matches_the_editor_constants(self) -> None:
        palette = app_main.export_palette()
        self.assertEqual(palette.background, app_main.EDITOR_BG)
        self.assertEqual(palette.text, app_main.TEXT)
        self.assertEqual(palette.heading,
                         tuple(app_main.HEADING_COLORS[level] for level in range(1, 7)))
        self.assertEqual(palette.bullet, app_main.BULLET_COLORS)
        self.assertEqual(palette.code_bg, app_main.CODE_BG)
        self.assertEqual(palette.table_border, app_main.TABLE_BORDER)
        self.assertEqual(palette.hr, app_main.HR_COLOR)

    def test_list_left_follows_the_editor_indent_rule(self) -> None:
        options = small_options()
        for level in range(5):
            self.assertEqual(options.list_left(level),
                             options.margin + options.list_indent * (level + 1))
        # 超过最大层级就封顶，和编辑区 list_indent() 一致
        self.assertEqual(options.list_left(9), options.list_left(4))

    def test_colorref_is_bgr(self) -> None:
        self.assertEqual(image_export.colorref("#FF8000"), 0x000080FF)
        self.assertEqual(image_export.colorref("#000000"), 0)
        self.assertEqual(image_export.colorref("#FFFFFF"), 0x00FFFFFF)


# ---------- 界面（需要图形环境） ----------

@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class ExportUiTests(unittest.TestCase):
    DOC = ("# 导出\n\n正文一段。\n\n- 列表\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n")

    # 量滚轮必须用长文档：文档比视口还短时根本没得滚，量出来永远是 0
    LONG = "".join(f"第 {index} 行正文，用来把文档撑到远超一屏。\n\n" for index in range(1, 121))

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"
        folder = self.tmp / "简记日记"
        folder.mkdir()
        self.doc = folder / "导出.md"
        self.doc.write_text(self.DOC, encoding="utf-8")
        self.long_doc = folder / "长文.md"
        self.long_doc.write_text(self.LONG, encoding="utf-8")
        storage.set_default_folder(folder)

        from main import JianJiApp

        self.root = tk.Tk()
        self.root.geometry("1180x760")
        self.app = JianJiApp(self.root)
        self.root.update()
        self.app.open_file(self.doc)
        self.root.update()

    def tearDown(self) -> None:
        storage.default_state_path = self._real_state
        self.root.destroy()

    def _visible_line_ys(self) -> dict[int, int]:
        """当前每个可见逻辑行的 y（只看第一显示行，续行不算）。

        `dlineinfo` 对视口外的行返回 `None`，所以这份字典天然只含可见行。
        """
        editor = self.app.editor
        last = int(editor.index("end-1c").split(".")[0])
        ys = {}
        for line in range(1, last + 1):
            info = editor.dlineinfo(f"{line}.0")
            if info is not None:
                ys[line] = info[1]
        return ys

    def _wheel_pixels(self, delta: int, bound: bool) -> int:
        """滚一格，返回视口实际移动的像素数。

        向下滚先顶到最上面、向上滚先顶到最下面——已经到头时滚动是空操作。

        **量法：拿一条「滚动前后都可见」的行，直接比它的 `dlineinfo` y。**
        别用 `|Δyview[0]| × count("1.0","end-1c","ypixels")` 反推：
        Tk 只为**视口附近**的行排版，远处的行还挂着「估计高度」，所以
        `count ... ypixels` 会**随着滚动越量越准**（实测 13938 → … → 27627，
        滚完才收敛到真值），分母飘、反推出来的像素也跟着飘——真实 240 px
        会被算成 231 px，倍数从 6.0 掉到 5.775，报一条假红。
        （和底部留白无关：把 `EDITOR_BOTTOM_PAD_RATIO` 设成 0 一样飘。）
        `dlineinfo` 给的是**当前真实几何**，不受这个影响。
        """
        editor = self.app.editor
        if bound:
            editor.bind("<MouseWheel>", self.app._on_editor_wheel)
        else:
            editor.unbind("<MouseWheel>")
        self.root.update()
        editor.yview_moveto(0.0 if delta < 0 else 1.0)
        self.root.update()
        before = self._visible_line_ys()
        editor.event_generate("<MouseWheel>", delta=delta)
        self.root.update()
        after = self._visible_line_ys()
        editor.bind("<MouseWheel>", self.app._on_editor_wheel)

        shared = sorted(set(before) & set(after))
        if not shared:
            return 0
        # 取中位数：最上/最下那一行可能被视口边缘裁到，位移不完整。
        moves = sorted(abs(before[k] - after[k]) for k in shared)
        return moves[len(moves) // 2]

    def test_wheel_scrolls_exactly_the_configured_factor(self) -> None:
        """一格滚轮的位移 = 系统默认 × `EDITOR_WHEEL_FACTOR`。

        倍数直接取常量，别再写死数字——上一版这里写死 2.0，滚轮调到 6 倍时
        这条测试就过期了（当时按用户要求没跑全量，没当场发现）。
        """
        factor = float(app_main.EDITOR_WHEEL_FACTOR)
        self.app.open_file(self.long_doc)
        self.root.update()
        for delta in (-120, 120, -240):
            native = self._wheel_pixels(delta, bound=False)
            ours = self._wheel_pixels(delta, bound=True)
            self.assertGreater(native, 0, f"delta={delta} 时系统默认没滚动，量不出来")
            self.assertAlmostEqual(ours / native, factor, delta=0.08,
                                   msg=f"delta={delta}: {native} → {ours} px"
                                       f"（应为默认的 {factor:g} 倍）")

    def test_wheel_handler_swallows_the_event(self) -> None:
        """不返回 "break" 的话 Text 的类绑定会再滚一遍，变成三倍。"""
        event = type("E", (), {"delta": -120})()
        self.assertEqual(self.app._on_editor_wheel(event), "break")

    def test_wheel_with_zero_delta_does_nothing(self) -> None:
        event = type("E", (), {"delta": 0})()
        self.assertEqual(self.app._on_editor_wheel(event), "break")

    def test_export_options_scale_with_the_editor_font_size(self) -> None:
        options = self.app._export_options()
        self.assertEqual(options.width, app_main.EXPORT_WIDTH)
        self.assertEqual(options.body_size, app_main.EXPORT_BODY_SIZE)
        self.assertEqual(options.family, self.app._writing_font())

        # 编辑器字号翻倍 → 缩放比减半 → 缩进等尺寸减半（正文目标字号不变）
        bigger = dict(self.app.settings, font_size=self.app.settings["font_size"] * 2)
        self.app.settings = bigger
        self.root.update()
        scaled = self.app._export_options()
        self.assertEqual(scaled.body_size, app_main.EXPORT_BODY_SIZE)
        self.assertAlmostEqual(scaled.list_indent, options.list_indent / 2, delta=1)

    def test_export_is_in_the_document_menu_not_the_top_bar(self) -> None:
        """「导出长图」从顶栏挪到了文稿右键菜单里。"""
        self.assertFalse(hasattr(self.app, "export_button"), "顶栏不该再有导出按钮")
        menu = self.app._doc_menu(self.doc)
        labels = [menu.entrycget(i, "label")
                  for i in range(menu.index("end") + 1)
                  if menu.type(i) != "separator"]
        self.assertIn("导出长图", labels)

    def _stub_export(self, target: Path):
        """把保存对话框与「打开文件夹」都换掉，返回记录用的容器。

        `seen` 收 render_document_image 拿到的正文，`opened` 收 reveal_in_explorer
        收到的路径——这两个才是要断言的东西，不用真去写盘。
        """
        seen: list[str] = []
        opened: list[Path] = []
        original_save = app_main.filedialog.asksaveasfilename
        original_render = self.app.render_document_image
        original_reveal = app_main.reveal_in_explorer
        app_main.filedialog.asksaveasfilename = lambda **_kwargs: str(target)
        app_main.reveal_in_explorer = opened.append
        self.app.render_document_image = lambda text: (seen.append(text), [b"\x89PNG\r\n\x1a\n"])[1]

        def restore() -> None:
            app_main.filedialog.asksaveasfilename = original_save
            app_main.reveal_in_explorer = original_reveal
            self.app.render_document_image = original_render

        return seen, opened, restore

    def test_export_menu_exports_that_document_even_if_not_open(self) -> None:
        """右键哪一篇就导出哪一篇：长文.md 没打开，也要导出它的内容。"""
        seen, _opened, restore = self._stub_export(self.tmp / "长文.png")
        try:
            self.app.export_long_image(self.long_doc)
        finally:
            restore()
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0].startswith("第 1 行正文"), seen[0][:20])
        self.assertIn("第 120 行正文", seen[0])

    def test_export_of_the_open_document_uses_the_editor_text(self) -> None:
        """导出正在编辑的那篇时用编辑区的文本——还没自动保存的改动也要算上。"""
        self.app.editor.insert("end", "刚敲下还没保存的一句")
        self.root.update()
        seen, _opened, restore = self._stub_export(self.tmp / "导出.png")
        try:
            self.app.export_long_image(self.doc)
        finally:
            restore()
        self.assertEqual(len(seen), 1)
        self.assertIn("刚敲下还没保存的一句", seen[0])

    def test_export_opens_the_containing_folder(self) -> None:
        """导出完自动打开所在文件夹，并且选中的就是刚写出的那张。"""
        target = self.tmp / "导出的长图.png"
        _seen, opened, restore = self._stub_export(target)
        try:
            self.app.export_long_image()
        finally:
            restore()
        self.assertEqual([Path(p) for p in opened], [target])

    def test_export_writes_a_png_next_to_the_document(self) -> None:
        target = self.tmp / "导出的长图.png"
        original = app_main.filedialog.asksaveasfilename
        original_reveal = app_main.reveal_in_explorer
        app_main.filedialog.asksaveasfilename = lambda **_kwargs: str(target)
        app_main.reveal_in_explorer = lambda _path: None      # 别真弹资源管理器
        try:
            self.app.export_long_image()
        finally:
            app_main.filedialog.asksaveasfilename = original
            app_main.reveal_in_explorer = original_reveal
        self.assertTrue(target.exists())
        data = target.read_bytes()
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(png_size(data)[0], app_main.EXPORT_WIDTH)
        self.assertIn("已导出长图", self.app.status_label.cget("text"))

    def test_export_cancelled_by_the_user_writes_nothing(self) -> None:
        before = self.app.status_label.cget("text")
        opened: list[Path] = []
        original = app_main.filedialog.asksaveasfilename
        original_reveal = app_main.reveal_in_explorer
        app_main.filedialog.asksaveasfilename = lambda **_kwargs: ""
        app_main.reveal_in_explorer = opened.append
        try:
            self.app.export_long_image()
        finally:
            app_main.filedialog.asksaveasfilename = original
            app_main.reveal_in_explorer = original_reveal
        self.assertEqual(self.app.status_label.cget("text"), before)
        self.assertNotIn("已导出", self.app.status_label.cget("text"))
        self.assertEqual(list(self.tmp.glob("*.png")), [])
        self.assertEqual(opened, [], "取消了就不该去打开文件夹")

    def test_export_on_empty_document_says_so(self) -> None:
        self.app.editor.configure(state="normal")
        self.app.editor.delete("1.0", "end")
        self.app.editor.configure(state="disabled")
        self.app.export_long_image()
        self.assertIn("没有内容", self.app.status_label.cget("text"))

    def test_default_export_path_follows_the_document_name(self) -> None:
        self.assertEqual(self.app._export_default_path(), self.doc.with_suffix(".png"))
        # 也可以直接指定是哪一篇（右键菜单就是这么用的）
        self.assertEqual(self.app._export_default_path(self.long_doc),
                         self.long_doc.with_suffix(".png"))

    def test_export_matches_what_the_editor_parses(self) -> None:
        """同一段文字，编辑器认出的块级类型和导出认出的必须一致。"""
        text = self.app.editor.get("1.0", "end-1c")
        options = self.app._export_options()
        rows = app_main.build_export_rows(text, options)
        kinds = [row.kind for row in rows]
        self.assertEqual(kinds, ["heading", "blank", "text", "blank", "bullet",
                                 "blank", "table"])
        self.assertEqual(rows[0].badge, "H1")
        self.assertEqual(len(rows[6].cells), 2)


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class EditorParityTests(unittest.TestCase):
    """拿《简记使用说明》整篇做对账。

    编辑器和导出是**两条独立实现**：前者在 `_apply_markdown_styles` 里走一遍
    围栏、表格、分隔行，后者在 `build_export_rows` 里又走一遍。两边都调
    `parse_block` / `fence_step`，但「哪些行该跳过」「哪些行合成一张表」
    是各写各的——这一篇文档正好覆盖了全部语法，拿它逐行对齐最省事。
    """

    MANUAL = PROJECT / "简记使用说明.md"

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"
        folder = self.tmp / "简记日记"
        folder.mkdir()
        self.doc = folder / "使用说明.md"
        self.doc.write_text(self.MANUAL.read_text(encoding="utf-8"), encoding="utf-8")
        storage.set_default_folder(folder)

        from main import JianJiApp

        self.root = tk.Tk()
        self.root.geometry("1240x820")
        self.app = JianJiApp(self.root)
        self.root.update()
        self.app.open_file(self.doc)
        self.root.update()

    def tearDown(self) -> None:
        storage.default_state_path = self._real_state
        self.root.destroy()

    def test_the_manual_covers_every_block_kind(self) -> None:
        """这一篇是给对账用的，先确认它真的覆盖到了各种块级语法。"""
        text = self.app.editor.get("1.0", "end-1c")
        rows = app_main.build_export_rows(text, self.app._export_options())
        kinds = {row.kind for row in rows}
        for kind in ("heading", "text", "blank", "bullet", "ordered", "task",
                     "quote", "code", "hr", "table"):
            self.assertIn(kind, kinds, f"使用说明里没有 {kind}，对账覆盖不到")

    def test_export_rows_track_the_editor_block_by_block(self) -> None:
        text = self.app.editor.get("1.0", "end-1c")
        # 不裁掉收尾的空行：编辑器的 _doc_in_code / _doc_blocks 就是按这个切分建的，
        # 两边的行数必须一一对应
        lines = re.split(r"\r\n|\r|\n", text)

        # 编辑器那一边的事实
        in_code = self.app._doc_in_code
        blocks = self.app._doc_blocks
        table_starts = {table.start for table in self.app._doc_tables}
        # 表头、分隔行、数据行都算表内行；导出把它们合成一个块
        table_lines: set[int] = set()
        for table in self.app._doc_tables:
            table_lines.add(table.start)
            table_lines.add(table.start + 1)
            table_lines.update(table.line_numbers)
        self.assertEqual(len(in_code), len(lines))
        self.assertEqual(len(blocks), len(lines))

        # 围栏分隔线：编辑器把它们和代码行一起算 in_code，但导出会整行跳过
        delimiters: set[int] = set()
        fence = None
        for number, line in enumerate(lines, start=1):
            fence, is_delimiter = app_main.fence_step(line, fence)
            if is_delimiter:
                delimiters.add(number)

        pairs: list[tuple[str, int]] = []
        for number, line in enumerate(lines, start=1):
            block = blocks[number - 1]
            if in_code[number - 1]:
                if number not in delimiters:
                    pairs.append(("code", number))
            elif block.kind == "table":
                if number in table_lines:
                    if number in table_starts:      # 整张表只在表头那一行出一个块
                        pairs.append(("table", number))
                else:
                    # 「看着像表格行、下一行却不是分隔行」的行编辑器并不当表格，
                    # 只是普通正文
                    pairs.append(("text", number))
            elif block.kind in ("heading", "quote", "bullet", "task", "ordered", "hr"):
                pairs.append((block.kind, number))
            elif not line.strip():
                pairs.append(("blank", number))
            else:
                pairs.append(("text", number))
        # 导出会丢掉收尾的空行（图末尾留一大片空白没意义）
        while pairs and pairs[-1][0] == "blank":
            pairs.pop()
        # 缩进续行会被并进上一段（Markdown 的惰性续行），不单独成块：
        # 列表项写成长短两行、第二行缩进两格时，导出的折行必须连着排，
        # 否则这一项会被从中间截断（第一行末尾常常正好是个逗号）。
        merged: list[tuple[str, int]] = []
        for kind, number in pairs:
            line = lines[number - 1]
            previous = lines[number - 2] if number >= 2 else ""
            if (kind == "text" and line[:1].isspace() and merged
                    and merged[-1][0] in ("text", "bullet", "task", "ordered", "quote")
                    and not app_main._ends_with_hard_break(previous)):
                continue
            merged.append((kind, number))
        pairs = merged
        expected = [kind for kind, _number in pairs]

        rows = app_main.build_export_rows(text, self.app._export_options())
        actual = [row.kind for row in rows]
        self.assertEqual(len(actual), len(expected),
                         f"块数对不上：导出 {len(actual)}，编辑器 {len(expected)}")
        for index, (got, (want, number)) in enumerate(zip(actual, pairs)):
            if got != want:
                self.fail(f"第 {index} 个块：导出 {got}，编辑器 {want}；"
                          f"对应第 {number} 行 {lines[number - 1]!r}")

    def test_exported_text_matches_the_editor_text_with_marks_removed(self) -> None:
        """导出不该吞掉正文，也不该多出正文。"""
        text = self.app.editor.get("1.0", "end-1c")
        rows = app_main.build_export_rows(text, self.app._export_options())

        exported = []
        for row in rows:
            if row.kind == "table":
                exported.extend(piece.text for cells in row.cells
                                for cell in cells for piece in cell)
            else:
                exported.extend(piece.text for piece in row.pieces)
        produced = "".join(exported)

        # 编辑器认得出的「正文」（去掉记号、围栏行、表格分隔行、行尾空白）
        lines = re.split(r"\r\n|\r|\n", text)
        in_code = self.app._doc_in_code
        delimiters: set[int] = set()
        fence = None
        for number, line in enumerate(lines, start=1):
            fence, is_delimiter = app_main.fence_step(line, fence)
            if is_delimiter:
                delimiters.add(number)
        keep = [line for number, line in enumerate(lines, start=1)
                if not (in_code[number - 1] and number in delimiters)]
        source = "".join(keep)

        strip = lambda value: "".join(value.split())   # noqa: E731
        # 导出去掉了所有 Markdown 记号，所以只比对「有没有把正文弄丢」：
        # 正文里每个非空白字符都必须在导出里出现
        source_chars = strip(source)
        produced_chars = strip(produced)
        for char in set(source_chars):
            self.assertLessEqual(produced_chars.count(char), source_chars.count(char) + 1,
                                 f"字符 {char!r} 在导出里凭空变多了")
        self.assertGreater(len(produced_chars), len(source_chars) * 0.55,
                           "导出出来的正文比原文少太多，可能吞了内容")


if __name__ == "__main__":
    unittest.main()
