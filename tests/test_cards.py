"""卡片列（第二列）的测试。

预览栏显示尽量多的正文：第一行是 H1 也照收，正文的回车换行接成一个空格；
字体固定为 16 号微软雅黑，不跟随编辑区的字体设置。
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
    CARD_EXCERPT_LINES,
    CARD_EXCERPT_ROWS,
    CARD_FONT_FAMILY,
    CARD_FONT_SIZE,
    _card_excerpt,
    px,
)

import tkinter as tk  # noqa: E402
from tkinter import font as tkfont  # noqa: E402

try:
    _probe = tk.Tk()
    _probe.destroy()
    HAS_DISPLAY = True
except tk.TclError:  # pragma: no cover
    HAS_DISPLAY = False


class CardExcerptTests(unittest.TestCase):
    """摘录逻辑：标题也收、行间接成空格、去掉 Markdown 标记。"""

    def _write(self, folder: Path, name: str, text: str) -> Path:
        path = folder / name
        path.write_text(text, encoding="utf-8")
        return path

    def _excerpt(self, text: str, name: str = "2026-09-13.md") -> str:
        with tempfile.TemporaryDirectory() as temp:
            return _card_excerpt(self._write(Path(temp), name, text))

    def _pieces(self, text: str, name: str = "2026-09-13.md") -> list[str]:
        """摘录被接成一整段了，按空格拆回逐条来断言，读起来还是逐条对照。"""
        return self._excerpt(text, name).split(" ")

    def test_keeps_leading_h1_title(self) -> None:
        """第一行就是 H1 时，标题也要出现在预览里（用户要求）。

        以前把它当「文档标题」跳过了，可很多文稿的 H1 本身就是正文的第一句，
        跳过之后卡片直接从第二段开始，看着像丢了内容。
        """
        out = self._excerpt("# 今天的日记\n\n今天阳光很好。\n")
        self.assertIn("今天的日记", out)
        self.assertEqual(out, "今天的日记 今天阳光很好。")

    def test_lines_are_joined_with_spaces(self) -> None:
        """正文里的回车换行在预览里变成一个空格（用户要求）。

        一篇日记常常一行一句，按行断的话卡片右半边全是空白、4 行就写满了；
        接成一段之后文字自己折行铺满，同样高度能多显示一倍多的内容。
        """
        out = self._excerpt("# 标题\n\n第一行。\n第二行。\n第三行。\n")
        self.assertNotIn("\n", out, "预览里不应保留换行")
        self.assertEqual(out, "标题 第一行。 第二行。 第三行。")

    def test_excerpt_budget_is_generous_enough_to_fill_a_card(self) -> None:
        """读入行数要够多：行接成空格之后，几行源文本往往只折成一两个显示行。"""
        self.assertGreaterEqual(CARD_EXCERPT_LINES, 20)

    def test_respects_explicit_max_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self._write(Path(temp), "笔记.md", "一。\n二。\n三。\n四。\n五。\n")
            self.assertEqual(len(_card_excerpt(path, max_lines=2).split(" ")), 2)

    def test_without_heading_keeps_first_line(self) -> None:
        self.assertEqual(self._pieces("随手写的一句话。\n还有第二句。\n", name="随笔.md"),
                         ["随手写的一句话。", "还有第二句。"])

    def test_blank_lines_are_skipped(self) -> None:
        self.assertEqual(self._pieces("# 标题\n\n\n第一段。\n\n\n第二段。\n"),
                         ["标题", "第一段。", "第二段。"])

    def test_empty_file_falls_back_to_filename(self) -> None:
        self.assertEqual(self._excerpt("", name="空白.md"), "空白")

    def test_title_only_file_shows_the_title(self) -> None:
        """只有标题、没有正文时显示标题本身——它现在也算正文。

        文件名故意取成别的，好证明显示的是标题而不是退回的文件名。
        """
        self.assertEqual(self._excerpt("# 只有标题\n", name="别的名字.md"), "只有标题")

    def test_missing_file_is_safe(self) -> None:
        path = Path(tempfile.gettempdir()) / "简记不存在-abcdef.md"
        self.assertEqual(_card_excerpt(path), path.stem)

    def test_strips_markdown_marks(self) -> None:
        out = self._excerpt("# 标题\n\n> **重点**：`代码` 与 *斜体*\n")
        self.assertNotIn("**", out)
        self.assertNotIn("`", out)
        self.assertNotIn("*", out)
        self.assertIn("重点", out)

    def test_strips_list_and_heading_marks(self) -> None:
        pieces = self._pieces("# 标题\n\n## 小节\n\n- 第一条\n- 第二条\n1. 有序项\n")
        self.assertIn("小节", pieces)
        self.assertIn("第一条", pieces)
        self.assertIn("有序项", pieces)
        for piece in pieces:
            self.assertFalse(piece.startswith("#"), piece)
            self.assertFalse(piece.startswith("-"), piece)

    def test_keeps_link_text_drops_url(self) -> None:
        out = self._excerpt("# 标题\n\n参考 [说明文档](https://example.com/a) 即可。\n")
        self.assertIn("说明文档", out)
        self.assertNotIn("example.com", out)

    def test_skips_code_fences(self) -> None:
        out = self._excerpt("# 标题\n\n```python\nprint('x')\n```\n\n真正的正文。\n")
        self.assertNotIn("```", out)
        self.assertIn("真正的正文。", out)

    def test_all_heading_levels_are_content(self) -> None:
        """H1 和 H2 都算正文——H1 不再被当成「文档标题」跳过。"""
        out = self._excerpt("# 我的标题\n\n## 读到的句子\n\n正文。\n")
        self.assertIn("读到的句子", out)
        self.assertIn("我的标题", out)
        self.assertNotIn("#", out)

    # ---- 新增语法也要在摘录里清干净 ----

    def test_strips_task_list_markers(self) -> None:
        pieces = self._pieces("# 标题\n\n- [ ] 还没做\n- [x] 已经做完\n")
        self.assertEqual(pieces, ["标题", "还没做", "已经做完"])
        for piece in pieces:
            self.assertNotIn("[", piece)

    def test_skips_horizontal_rules(self) -> None:
        pieces = self._pieces("# 标题\n\n第一段。\n\n---\n\n第二段。\n")
        self.assertEqual(pieces, ["标题", "第一段。", "第二段。"])
        self.assertNotIn("-", "".join(pieces))

    def test_skips_code_block_content(self) -> None:
        out = self._excerpt("# 标题\n\n```python\nprint('x')\n```\n\n正文。\n")
        self.assertNotIn("print", out)
        self.assertEqual(out, "标题 正文。")

    def test_strips_strike_and_highlight_marks(self) -> None:
        out = self._excerpt("# 标题\n\n~~删掉~~ 与 ==高亮== 都要干净。\n")
        self.assertNotIn("~", out)
        self.assertNotIn("=", out)
        self.assertIn("删掉", out)
        self.assertIn("高亮", out)

    def test_keeps_image_alt_text(self) -> None:
        out = self._excerpt("# 标题\n\n![示意图](https://example.com/a.png)\n")
        self.assertIn("示意图", out)
        self.assertNotIn("example.com", out)


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class CardListUiTests(unittest.TestCase):
    """第二列的实际渲染：标题也显示、无硬换行、固定字体、间隔减半。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        self.doc = folder / "2026-09-13.md"
        self.doc.write_text(
            "# 今天的天气\n\n"
            "今天下午下了一场雨，我坐在窗边写日记。\n"
            "雨停之后天边有一道很淡的彩虹。\n"
            "很安静的一个傍晚。\n"
            "明天想早点起来。\n",
            encoding="utf-8",
        )
        (folder / "第二篇.md").write_text("# 第二篇\n\n另一篇的正文。\n", encoding="utf-8")
        storage.set_default_folder(folder)
        self.folder = folder

        from main import JianJiApp

        self.root = tk.Tk()
        self.root.geometry("1180x760")
        self.app = JianJiApp(self.root)
        self.app.open_file(self.doc)
        self.root.update()

    def tearDown(self) -> None:
        storage.default_state_path = self._real_state
        self.root.destroy()

    def _card_text_items(self) -> list[str]:
        canvas = self.app.cards_canvas
        return [
            canvas.itemcget(item, "text")
            for item in canvas.find_all()
            if canvas.type(item) == "text"
        ]

    def _font_signature(self, spec) -> tuple[str, int]:
        """字体签名：族名 + 行高。

        不用 actual("size")——Tk 会把像素字号换算回磅再取整，反而不可比；
        linespace 是直接的像素高度，随字号单调变化。
        """
        probe = tkfont.Font(root=self.root, font=spec)
        return probe.actual("family"), probe.metrics("linespace")

    def _card_signatures(self) -> set[tuple[str, int]]:
        canvas = self.app.cards_canvas
        return {
            self._font_signature(canvas.itemcget(item, "font"))
            for item in canvas.find_all()
            if canvas.type(item) == "text"
        }

    def test_card_shows_document_title(self) -> None:
        """第一行是 H1 时，标题也要出现在预览里（用户要求）。"""
        joined = " ".join(self._card_text_items())
        self.assertIn("今天的天气", joined)
        self.assertIn("第二篇", joined)

    def test_card_excerpt_has_no_hard_line_breaks(self) -> None:
        """预览里不该有硬换行：正文的回车换行要变成一个空格（用户要求）。"""
        texts = self._card_text_items()
        self.assertTrue(texts, "应当绘制了卡片文字")
        for text in texts:
            self.assertNotIn("\n", text, f"卡片文字里不该有换行：{text!r}")

    def test_card_font_is_fixed_and_ignores_editor_font(self) -> None:
        """预览栏字体固定为 16 号微软雅黑，不跟编辑区走（用户要求）。

        编辑区调字号是为了自己写着舒服，预览栏要的是一眼扫过去能看清哪篇是哪篇，
        两者诉求不同——所以**故意断言它不等于编辑区字体**。
        """
        card_sigs = self._card_signatures()
        self.assertTrue(card_sigs)
        expected = self._font_signature((CARD_FONT_FAMILY, -px(CARD_FONT_SIZE)))
        self.assertEqual(card_sigs, {expected}, "预览栏应用固定的 16 号微软雅黑")
        self.assertNotEqual(card_sigs,
                            {self._font_signature(self.app.editor.cget("font"))},
                            "预览栏字体不该跟编辑区一致")

    def test_card_font_does_not_follow_font_size_setting(self) -> None:
        """把编辑区字号调大，预览栏必须纹丝不动。"""
        before = self._card_signatures()
        self.app._on_setting_changed("font_size", "20")
        self.root.update()
        self.app._rebuild_cards()
        self.root.update()
        self.assertEqual(self._card_signatures(), before,
                         "编辑区字号变了，预览栏不该跟着变")

    def test_card_gap_is_half_of_the_old_value(self) -> None:
        """卡片之间的间隔是原来（px(12)）的一半（用户要求）。"""
        self.assertEqual(self.app._card_gap(), px(6))
        self.assertEqual(self.app._card_gap() * 2, px(12))

    def test_cards_do_not_overlap_after_gap_change(self) -> None:
        """间隔减半之后卡片也不能叠在一起——命中范围是按间隙撑开的。"""
        bounds = self.app._card_bounds
        self.assertGreaterEqual(len(bounds), 2)
        for earlier, later in zip(bounds, bounds[1:]):
            self.assertLessEqual(earlier[1], later[0] + 1,
                                 "相邻卡片的可点范围不该交叉")

    def test_cards_still_hit_testable(self) -> None:
        canvas = self.app.cards_canvas
        width = canvas.winfo_width()
        hit = self.app._card_at(width - px(25), 40)
        self.assertIsNotNone(hit, "改版后卡片仍应可点击")
        self.assertIn(hit, self.app.file_paths)

    # ---------- 按显示行裁剪 ----------

    def test_trim_keeps_short_text_intact(self) -> None:
        canvas = self.app.cards_canvas
        text = "短短两行。\n第二行。"
        out = self.app._trim_card_text(canvas, text, ("Microsoft YaHei UI", -px(15)),
                                       px(200), px(200))
        self.assertEqual(out, text)

    def test_trim_cuts_long_text_to_allowed_height(self) -> None:
        canvas = self.app.cards_canvas
        long_text = "这是一段很长很长的正文，" * 20
        font_spec = ("Microsoft YaHei UI", -px(15))
        allowed = self.app._font_linespace(15) * 3
        out = self.app._trim_card_text(canvas, long_text, font_spec, px(200), allowed)
        self.assertTrue(out.endswith("…"), out)
        self.assertLess(len(out), len(long_text))
        self.assertLessEqual(
            self.app._card_text_height(canvas, out, font_spec, px(200)), allowed,
        )

    def test_trim_avoids_punctuation_before_ellipsis(self) -> None:
        canvas = self.app.cards_canvas
        font_spec = ("Microsoft YaHei UI", -px(15))
        allowed = self.app._font_linespace(15)
        out = self.app._trim_card_text(canvas, "第一句。第二句。第三句。", font_spec,
                                       px(60), allowed)
        self.assertTrue(out.endswith("…"), out)
        self.assertFalse(out.endswith("。…"), out)

    def test_trim_empty_text_is_safe(self) -> None:
        canvas = self.app.cards_canvas
        self.assertEqual(
            self.app._trim_card_text(canvas, "", ("Microsoft YaHei UI", -px(15)),
                                     px(200), px(20)),
            "",
        )

    def test_every_card_fits_row_budget(self) -> None:
        """实际渲染出来的每张卡片都不应超过显示行预算。

        预算要按**预览栏自己的字体**算——预览栏字号是固定的，跟编辑区设置无关。
        """
        canvas = self.app.cards_canvas
        allowed = (self.app._font_linespace(CARD_FONT_SIZE, CARD_FONT_FAMILY)
                   * CARD_EXCERPT_ROWS)
        checked = 0
        for item in canvas.find_all():
            if canvas.type(item) != "text":
                continue
            box = canvas.bbox(item)
            if not box:
                continue
            checked += 1
            self.assertLessEqual(
                box[3] - box[1], allowed + 1,
                f"卡片超出 {CARD_EXCERPT_ROWS} 行预算：{canvas.itemcget(item, 'text')!r}",
            )
        self.assertGreater(checked, 0)


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class CardScrollBoundsTests(unittest.TestCase):
    """内容比视口矮时，往上滚不能把整列推到下方（顶部留白）。

    Tk 的 Canvas 只在「滚动区比视口高」时才夹住原点。文档少的时候滚动区更矮，
    这时 yview_scroll(-1) 会把原点推成负数：内容整体下移、顶上空出一条，
    而 yview() 仍报 (0.0, 1.0)，滚动条上看不出任何异常。
    """

    class _Wheel:
        def __init__(self, delta: int) -> None:
            self.delta = delta

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "只有一篇"
        folder.mkdir()
        self.doc = folder / "2026-09-13.md"
        self.doc.write_text("# 标题\n\n很短的一篇。\n", encoding="utf-8")
        storage.set_default_folder(folder)

        from main import JianJiApp

        self.root = tk.Tk()
        self.root.geometry("1180x760")
        self.app = JianJiApp(self.root)
        self.root.update()

    def tearDown(self) -> None:
        storage.default_state_path = self._real_state
        self.root.destroy()

    def _region_height(self) -> int:
        return int(str(self.app.cards_canvas.cget("scrollregion")).split()[3])

    def _content_fits(self) -> bool:
        return self._region_height() <= self.app.cards_canvas.winfo_height()

    def _top_card_screen_y(self) -> float:
        """最靠上的卡片文字顶边在**视口**里的位置（0 = 视口最上沿）。

        bbox 给的是画布坐标，不随滚动变化，所以要减掉原点才是肉眼看到的位置。
        """
        canvas = self.app.cards_canvas
        boxes = [canvas.bbox(item) for item in canvas.find_all()
                 if canvas.type(item) == "text"]
        return min(box[1] for box in boxes if box) - canvas.canvasy(0)

    def test_wheel_up_when_content_fits_keeps_the_list_at_the_top(self) -> None:
        canvas = self.app.cards_canvas
        self.assertTrue(self._content_fits(), "前置条件：内容应比视口矮")
        before = self._top_card_screen_y()
        self.assertEqual(canvas.canvasy(0), 0.0)

        self.app._on_cards_wheel(self._Wheel(120))  # 往上滚
        self.root.update()

        self.assertEqual(canvas.canvasy(0), 0.0, "往上滚不该把原点推成负数")
        self.assertEqual(self._top_card_screen_y(), before, "卡片不该被推下去")

    def test_repeated_wheel_up_never_accumulates_blank(self) -> None:
        canvas = self.app.cards_canvas
        self.assertTrue(self._content_fits(), "前置条件：内容应比视口矮")
        before = self._top_card_screen_y()
        for _ in range(5):
            self.app._on_cards_wheel(self._Wheel(120))
            self.root.update()
            self.assertEqual(canvas.canvasy(0), 0.0)
            self.assertEqual(self._top_card_screen_y(), before, "留白不该累积")

    def test_scrollbar_command_cannot_push_the_list_down(self) -> None:
        self.app._on_cards_scrollbar("scroll", "-1", "units")
        self.root.update()
        self.assertEqual(self.app.cards_canvas.canvasy(0), 0.0)

    def test_rebuild_clears_a_negative_origin(self) -> None:
        """即使原点已经越界，重建一次也应回到顶部。"""
        canvas = self.app.cards_canvas
        self.assertTrue(self._content_fits(), "前置条件：内容应比视口矮")
        canvas.yview_scroll(-1, "units")  # 内容矮时，这一下就把原点推成负数
        self.assertLess(canvas.canvasy(0), 0)
        self.app._rebuild_cards()
        self.root.update()
        self.assertEqual(canvas.canvasy(0), 0.0)

    def test_wheel_still_scrolls_when_the_list_is_long(self) -> None:
        """夹原点不能把正常滚动一起夹掉。"""
        folder = self.tmp / "很多篇"
        folder.mkdir()
        for i in range(30):
            (folder / f"第{i:02d}篇.md").write_text(
                f"# 标题{i}\n\n第{i}篇的正文，写长一点好把列表撑起来。\n", encoding="utf-8",
            )
        self.app.scope_folder = folder
        self.app.refresh_files()
        self.root.update()

        canvas = self.app.cards_canvas
        self.assertFalse(self._content_fits(), "前置条件：内容应比视口高")
        self.app._on_cards_wheel(self._Wheel(-120))  # 往下滚
        self.root.update()
        self.assertGreater(canvas.canvasy(0), 0, "长列表仍应能往下滚")

        for _ in range(50):
            self.app._on_cards_wheel(self._Wheel(120))  # 一路滚回顶部
        self.root.update()
        self.assertEqual(canvas.canvasy(0), 0.0, "回到顶部应停在原点 0")


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class CardClickAndScrollTests(unittest.TestCase):
    """点击卡片要选得中、点完之后列表不该乱跳。

    这两件事都属于「点一下卡片」这一个动作，所以放一起守：

    1. **选得中**：命中判定过去把 `event.x/event.y`（控件坐标）直接喂给
       `canvas.find_overlapping`（要画布坐标）。列表没滚动时两者一样，一滚动就
       差一个原点——点靠上的卡片什么都撞不到，点下面的卡片会选中别的
       （见 outputs/复现-点击预览栏选不中.png）。
    2. **不乱跳**：滚到当前卡片过去用 `yview_moveto(index / total - 0.15)`，
       把「第几张 ÷ 共几张」当成滚动比例了。可 `yview_moveto` 要的是**内容高度**
       的比例，而卡片高度按摘录行数在 1～4 行之间变，两者根本对不上。
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        self.folder = self.tmp / "简记日记"
        self.folder.mkdir()
        # 24 篇，卡片高度故意有高有低，把「按张数算比例」的错法逼出来
        for day in range(1, 25):
            body = ["# 第 %d 篇" % day, ""]
            if day % 2:
                body.append("只有一行正文。")
            else:
                body.extend(f"正文第 {i} 行，写长一点好让它折行、把卡片撑高。" for i in range(1, 5))
            (self.folder / f"2026-09-{day:02d}.md").write_text(
                "\n".join(body) + "\n", encoding="utf-8")
        storage.set_default_folder(self.folder)

        from main import JianJiApp

        self.root = tk.Tk()
        self.root.geometry("1180x760")
        self.app = JianJiApp(self.root)
        self.root.update()
        self.app.open_file(self.folder / "2026-09-24.md")
        self.root.update()

    def tearDown(self) -> None:
        storage.default_state_path = self._real_state
        self.root.destroy()

    # ---- 辅助 ----

    def _click_card_centre(self, order: int) -> int | None:
        """按卡片中心点击（换算成控件坐标，走真实回调用的那条路径）。"""
        canvas = self.app.cards_canvas
        box = canvas.bbox(f"cardbg{order}")
        self.assertIsNotNone(box, f"第 {order} 张卡片没画出来")
        widget_y = int((box[1] + box[3]) / 2 - canvas.canvasy(0))
        widget_x = int((box[0] + box[2]) / 2 - canvas.canvasx(0))
        return self.app._card_index_at(widget_x, widget_y)

    def _scroll_to(self, fraction: float) -> None:
        self.app.cards_canvas.yview_moveto(fraction)
        self.root.update()

    def _assert_every_card_hits_itself(self, where: str) -> None:
        missed = []
        for order in range(len(self.app.file_paths)):
            if self._click_card_centre(order) != order:
                missed.append(order)
        self.assertEqual(missed, [], f"{where}：这些卡片点不中自己 {missed}")

    # ---- 选得中 ----

    def test_clicking_a_card_at_the_top_hits_it(self) -> None:
        self._assert_every_card_hits_itself("列表在顶部")

    def test_clicking_a_card_still_hits_it_after_scrolling(self) -> None:
        """滚动之后坐标才对得上——这一条就是过去漏掉的。"""
        for fraction in (0.25, 0.5, 0.75, 1.0):
            self._scroll_to(fraction)
            self._assert_every_card_hits_itself(f"滚到 {fraction}")

    def test_clicking_the_gap_between_cards_picks_the_nearer_one(self) -> None:
        """卡片之间那条空隙、以及左右留白，都该归最近的卡片。

        用户看着明明点在卡片上，就不该没反应。
        """
        canvas = self.app.cards_canvas
        for order in (0, 1, 2):
            box = canvas.bbox(f"cardbg{order}")
            centre_x = (box[0] + box[2]) / 2
            just_below = int(box[3] + 2 - canvas.canvasy(0))
            self.assertEqual(
                self.app._card_index_at(int(centre_x), just_below), order,
                f"第 {order} 张卡片正下方 2px 处该选中它自己")
            # 卡片左侧留白（卡片从 px(14) 才开始）也算它
            self.assertEqual(
                self.app._card_index_at(px(4), int((box[1] + box[3]) / 2
                                                   - canvas.canvasy(0))), order)

    def test_clicking_far_below_the_last_card_hits_nothing(self) -> None:
        canvas = self.app.cards_canvas
        last = len(self.app.file_paths) - 1
        below = int(canvas.bbox(f"cardbg{last}")[3] + px(60) - canvas.canvasy(0))
        self.assertIsNone(self.app._card_index_at(px(60), below))

    def test_left_click_opens_the_card_under_the_pointer(self) -> None:
        """走一遍真实回调，确认选中的是点的那一篇。"""
        self._scroll_to(0.5)
        canvas = self.app.cards_canvas
        order = len(self.app.file_paths) - 3
        box = canvas.bbox(f"cardbg{order}")
        event = type("Event", (), {
            "x": int((box[0] + box[2]) / 2 - canvas.canvasx(0)),
            "y": int((box[1] + box[3]) / 2 - canvas.canvasy(0)),
        })()
        self.app._on_cards_left_click(event)
        self.root.update()
        self.assertEqual(self.app.current_path, self.app.file_paths[order])

    # ---- 不乱跳 ----

    def test_opening_a_visible_card_does_not_move_the_list(self) -> None:
        """已经看得见的卡片，点它的时候列表一点都不该动。"""
        self._scroll_to(0.5)
        canvas = self.app.cards_canvas
        origin = canvas.canvasy(0)
        for order in range(len(self.app.file_paths)):
            box = canvas.bbox(f"cardbg{order}")
            if box[1] >= origin and box[3] <= origin + canvas.winfo_height():
                self.app.open_file(self.app.file_paths[order])
                self.root.update()
                self.assertEqual(canvas.canvasy(0), origin,
                                 f"第 {order} 张本来整张可见，列表不该动")
                return
        self.fail("这一屏里没有整张可见的卡片，样例要调一下")

    def test_opening_an_offscreen_card_brings_it_fully_into_view(self) -> None:
        canvas = self.app.cards_canvas
        for order in (6, 12, 18, 23):
            self._scroll_to(0.0)
            self.app.open_file(self.app.file_paths[order])
            self.root.update()
            box = canvas.bbox(f"cardbg{order}")
            top = canvas.canvasy(0)
            self.assertGreaterEqual(box[1], top - 1,
                                    f"第 {order} 张的上沿被滚出了视口顶")
            self.assertLessEqual(box[3], top + canvas.winfo_height() + 1,
                                 f"第 {order} 张的下沿没进来")


if __name__ == "__main__":
    unittest.main()
