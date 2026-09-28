"""编辑区外壳的行为：顶部栏常显、左上角编辑状态、输入法组字字体。

三件都是「平时不该碍事」的东西，所以守的也是这类不变量：

1. **顶部栏**：常显。左侧编辑状态、右侧「专注模式」，不再跟着鼠标进出收放——
   收起来的时候左上角那个「…」也跟着看不见了，而那正是打字时最想看到的东西。
2. **编辑状态**：编辑中是「…」，存完清空——安静，不跟正文抢注意力。
3. **输入法组字**（拼音字母预览）：字号跟着正文字号走，不再用输入法自己那一套。
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

from main import EDITOR_BOTTOM_PAD_RATIO, px  # noqa: E402

try:
    _probe = tk.Tk()
    _probe.destroy()
    HAS_DISPLAY = True
except tk.TclError:  # pragma: no cover
    HAS_DISPLAY = False


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class TopBarAlwaysVisibleTests(unittest.TestCase):
    """顶部栏常显：不再自动收起，专注模式一直待在右上角。

    自动收起试过一版（鼠标扫到编辑区顶部那条窄带才滑出来）。问题在于：顶部栏
    收着的时候，左上角的编辑状态也一起没了——而「正在编辑」恰恰是打字时最需要
    看到的反馈。所以整套自动收放的机制都撤掉，退回常显。
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        self.doc = folder / "外壳.md"
        self.doc.write_text("# 标题\n\n正文一段。\n\n- 项目\n", encoding="utf-8")
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

    def test_topbar_is_visible_right_after_startup(self) -> None:
        self.assertNotEqual(self.app.topbar.grid_info(), {})
        self.assertTrue(self.app.topbar.winfo_ismapped())

    def test_no_auto_hide_machinery_remains(self) -> None:
        """自动收起那套东西不该留下任何残骸——留着就会被误当成还有这个功能。"""
        for name in ("topbar_visible", "topbar_hide_job", "_panel_rect",
                     "_set_topbar_visible", "_hide_topbar", "_schedule_topbar_hide",
                     "_cancel_topbar_hide", "_on_pointer_motion", "_topbar_zone",
                     "_panel_geometry", "_on_window_geometry_changed"):
            self.assertFalse(hasattr(self.app, name), f"{name} 是自动收起的残留")

    def test_toplevel_has_no_motion_binding(self) -> None:
        """鼠标移动不再牵动任何界面逻辑。"""
        self.assertEqual(self.app.root.bind("<Motion>"), "")

    def test_topbar_survives_toggling_focus_mode(self) -> None:
        """专注模式进出，顶部栏都得在——不然那个按钮自己就没法再点了。"""
        self.app.toggle_focus()
        self.root.update()
        self.assertNotEqual(self.app.topbar.grid_info(), {})
        self.app.toggle_focus()
        self.root.update()
        self.assertNotEqual(self.app.topbar.grid_info(), {})

    def test_focus_mode_button_lives_in_the_topbar(self) -> None:
        self.assertIs(self.app.focus_button.master, self.app.topbar)
        self.assertEqual(self.app.focus_button.cget("text"), "专注模式")

    def test_editor_sits_below_the_topbar(self) -> None:
        """顶部栏占掉自己那一行，正文从它下面开始，不会压在它身上。"""
        self.assertGreaterEqual(
            self.app.editor.winfo_rooty(),
            self.app.topbar.winfo_rooty() + self.app.topbar.winfo_height(),
        )

    def test_topbar_keeps_its_height_when_the_status_text_changes(self) -> None:
        """高度写死：状态文字长短、有无都不该让整条顶栏跳动。"""
        height = self.app.topbar.winfo_height()
        self.app.status_label.configure(text="正在生成长图…")
        self.root.update()
        self.assertEqual(self.app.topbar.winfo_height(), height)


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class EditStatusTests(unittest.TestCase):
    """左上角的编辑状态：编辑中「…」，存完清空。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        self.doc = folder / "状态.md"
        self.doc.write_text("正文一段。\n", encoding="utf-8")
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

    def test_status_is_empty_right_after_opening(self) -> None:
        self.assertEqual(self.app.status_label.cget("text"), "")

    def test_status_label_is_actually_on_screen(self) -> None:
        """状态得有地方显示——顶部栏一旦被收起，这里就白设了。"""
        self.assertIs(self.app.status_label.master, self.app.topbar)
        self.assertTrue(self.app.status_label.winfo_ismapped())

    def test_status_is_an_ellipsis_while_editing(self) -> None:
        self.app.editor.insert("end", "新写的一句")
        self.root.update()
        self.assertEqual(self.app.status_label.cget("text"), "…")

    def test_status_is_empty_again_after_saving(self) -> None:
        self.app.editor.insert("end", "新写的一句")
        self.root.update()
        self.assertTrue(self.app.save_now())
        self.root.update()
        self.assertEqual(self.app.status_label.cget("text"), "",
                         "存完就不该再显示任何字")
        self.assertFalse(self.app.dirty)


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class ImeFontTests(unittest.TestCase):
    """输入法的组字（拼音字母预览）要跟正文同字号。

    真正把字体送进输入法那一步依赖 Windows 的输入法上下文，测试里造不出来；
    但「该用哪个字体」是纯计算，这一层必须测——不然改错了字号也没人发现。
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        self.doc = folder / "输入.md"
        self.doc.write_text("正文一段。\n", encoding="utf-8")
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

    def test_ime_font_matches_the_body_font(self) -> None:
        logfont = self.app._ime_logfont()
        size = px(self.app.settings["font_size"])
        self.assertEqual(logfont.lfHeight, -size,
                         "lfHeight 取负 = 字符高度，且必须等于正文字号")
        self.assertEqual(logfont.lfFaceName, self.app._writing_font())

    def test_ime_font_follows_a_font_size_change(self) -> None:
        before = self.app._ime_logfont().lfHeight
        self.app._on_setting_changed("font_size", str(self.app.settings["font_size"] + 4))
        self.root.update()
        after = self.app._ime_logfont().lfHeight
        self.assertNotEqual(after, before, "改了字号，组字字体也得跟着变")
        self.assertEqual(after, -px(self.app.settings["font_size"]))

    def test_typography_change_reapplies_the_ime_font(self) -> None:
        calls = {"n": 0}
        original = self.app._apply_ime_font

        def counted():
            calls["n"] += 1
            return original()

        self.app._apply_ime_font = counted
        try:
            self.app._apply_typography()
        finally:
            self.app._apply_ime_font = original
        self.assertGreater(calls["n"], 0, "排版变了就该把组字字体重新设一遍")

    def test_applying_the_ime_font_never_raises(self) -> None:
        """拿不到输入法上下文也得安静地过去——绝不能因为这事打断打字。"""
        self.app._apply_ime_font()


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class EditorBottomPadTests(unittest.TestCase):
    """编辑区底部的呼吸空间：滚到底时最后一行下面留出约 25% 的视口高度。

    用户要求「长文滚动到底部时，编辑区仍能显示 25% 区域的空白」——写长文时
    最后一行紧贴窗口下沿，光标停在末尾时连「下一行」都看不见。

    实现是挂在**最后一行**上的 `spacing3` 标签。所以这里既要守「滚到底真的
    空出那一截」，也要守「它只是显示层的空白，一个字都不许写进正文」，还要守
    「打字时不要每敲一下都重挂标签」（重挂会让 Tk 重排一次最后一行）。

    判据一律相对 `EDITOR_BOTTOM_PAD_RATIO` 与视口高度，不写绝对像素。
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        self.doc = folder / "长文.md"
        self.doc.write_text(
            "# 长文\n\n"
            + "\n".join(f"第 {i} 行正文，够长了好让它折行。" for i in range(1, 120))
            + "\n最后一句正文。\n",
            encoding="utf-8",
        )
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

    # ---------- 工具 ----------

    def _blank_below_last_line(self) -> tuple[int, int]:
        editor = self.app.editor
        box = editor.bbox("end-1c")
        self.assertIsNotNone(box, "滚到底时最后一行应当可见")
        height = editor.winfo_height()
        return height - (box[1] + box[3]), height

    def _assert_about_a_quarter(self, blank: int, height: int, what: str) -> None:
        wanted = height * EDITOR_BOTTOM_PAD_RATIO
        self.assertGreaterEqual(blank, wanted * 0.8, f"{what}：空白太少（{blank}）")
        self.assertLessEqual(blank, wanted * 1.2, f"{what}：空白太多（{blank}）")

    # ---------- 核心行为 ----------

    def test_scrolling_to_the_bottom_leaves_a_quarter_blank(self) -> None:
        self.app.editor.yview_moveto(1.0)
        self.root.update()
        blank, height = self._blank_below_last_line()
        self._assert_about_a_quarter(blank, height, "yview_moveto(1.0)")

    def test_the_wheel_also_stops_with_room_to_spare(self) -> None:
        """用户实际是用滚轮滚到底的——那条路径也要留出空白。"""
        event = type("E", (), {"delta": -120})()
        previous = None
        for _ in range(4000):
            self.app._on_editor_wheel(event)
            current = self.app.editor.yview()
            if previous is not None and current == previous:
                break
            previous = current
        self.root.update()
        blank, height = self._blank_below_last_line()
        self._assert_about_a_quarter(blank, height, "滚轮滚到底")

    # ---------- 只是显示层的空白 ----------

    def test_the_pad_never_touches_the_document_text(self) -> None:
        """留白绝不能写进正文——存盘、导出读的都是正文。"""
        editor = self.app.editor
        before = editor.get("1.0", "end-1c")
        self.app._apply_bottom_pad(force=True)
        self.root.update()
        self.assertEqual(editor.get("1.0", "end-1c"), before)

    # ---------- 末尾那个空行（留白挂得住的唯一位置） ----------

    def test_a_document_without_a_trailing_newline_gets_one(self) -> None:
        """末尾没有换行的文稿，进编辑器时补一个——留白只有挂在空行上才躲得开高亮。

        用户报的：「选中最后一行的文字后，阴影会覆盖下面留白」。根因是留白挂在
        **正文行**上时，Tk 把整个显示行盒子（含 `spacing3`）一起涂成选中色。
        实测（用户设置，视口 1071px、留白 267px）：末尾无换行 → 阴影高 310px；
        末尾有换行 → 51px。**两者相差 ≈ 留白值**（310 − 51 = 259 ≈ 267）——
        留白有多大，阴影就多出多大；绝对值会随文稿和设置浮动，别当常量。
        用户文稿里 21 份有 5 份末尾没有换行。
        """
        plain = self.tmp / "简记日记" / "没有末尾换行.md"
        plain.write_bytes("# 标题\n\n正文最后一句。".encode("utf-8"))
        self.app.open_file(plain)
        self.root.update()

        editor = self.app.editor
        self.assertTrue(editor.get("1.0", "end-1c").endswith("\n"),
                        "末尾应当补上一个换行")
        self.assertFalse(self.app.dirty, "补这个换行不算用户改动，不该让文稿变脏")

        ranges = editor.tag_ranges("bottom_pad")
        self.assertEqual(len(ranges), 2, "留白标签应当只有一段")
        self.assertEqual(editor.index(ranges[0]), editor.index("end-1c linestart"),
                         "留白标签没有挂在末尾那个空行上")

        # 最直接的判据：正文末行的显示行盒子**不能**含留白——含了就会被涂成选中色
        editor.yview_moveto(1.0)
        self.root.update()
        last_content = int(editor.index("end-1c").split(".")[0])
        while last_content > 1 and not editor.get(f"{last_content}.0",
                                                  f"{last_content}.end"):
            last_content -= 1
        box = editor.dlineinfo(f"{last_content}.0")
        self.assertIsNotNone(box, "滚到底时正文末行应当可见")
        self.assertLess(box[3], self.app._bottom_pad,
                        f"正文末行的行盒 {box[3]}px 把留白 {self.app._bottom_pad}px 也算进去了")

    def test_a_document_that_already_ends_with_a_newline_is_untouched(self) -> None:
        """已经有末尾换行的文稿不许被补成两个空行。

        这里一律用 `write_bytes` 写文稿：Windows 上 `write_text` 会把 `\\n` 翻成
        `\\r\\n`，而 Tk 的 `Text` 只拿 `\\n` 分行、行尾那个 `\\r` 算**行内容**，
        断言正文时会莫名其妙地不相等。
        """
        doc = self.tmp / "简记日记" / "有末尾换行.md"
        doc.write_bytes("# 标题\n\n正文最后一句。\n".encode("utf-8"))
        self.app.open_file(doc)
        self.root.update()
        self.assertEqual(self.app.editor.get("1.0", "end-1c"),
                         "# 标题\n\n正文最后一句。\n")

    def test_an_empty_document_is_left_empty(self) -> None:
        """空文稿不补——它没有正文行，留白本来就挂在空行上。"""
        blank = self.tmp / "简记日记" / "空文稿.md"
        blank.write_bytes(b"")
        self.app.open_file(blank)
        self.root.update()
        self.assertEqual(self.app.editor.get("1.0", "end-1c"), "")

    def test_the_saved_file_keeps_a_trailing_newline(self) -> None:
        """补进来的换行要跟着存盘——「文稿末尾自动补空行」落到文件上才算数。"""
        plain = self.tmp / "简记日记" / "存盘看末尾.md"
        plain.write_bytes("正文最后一句。".encode("utf-8"))
        self.app.open_file(plain)
        self.root.update()
        self.app.dirty = True
        self.assertTrue(self.app.save_now())
        self.assertTrue(plain.read_bytes().endswith(b"\n"),
                        "存盘后文件末尾应当有换行符")

    def test_the_pad_tag_stays_on_one_line(self) -> None:
        """留白标签必须只落在**一行**上。

        曾经写成 `end-1c linestart` → `end`，那是**两**行，`spacing3` 被算两遍，
        Tk 认的总高度被撑虚——可见留白仍是 267px 看不出异常，但滚到真正的底时
        `yview()[1]` 只有 0.96，编辑区滚动条的滑块到不了底。

        **这里只断言「标签不跨行」这个确定的机制**，不断言 `yview()[1] == 1.0`：
        那个值取决于 Tk 有没有把整篇版面算完，**同一份代码换个上下文就读出不同的数**
        （同一个探针里用例顺序不同，量到过 0.73 / 0.53 / 0.96 / 1.00），
        拿它当判据既会报假红也会报假绿。真实的留白量的是「末行下方的空白」，
        由 `tools/_probe_pad_tag_range.py` 量（两种文稿结尾都是 24.9%）。
        """
        editor = self.app.editor
        ranges = editor.tag_ranges("bottom_pad")
        self.assertEqual(len(ranges), 2, "留白标签应当只有一段")
        self.assertLess(str(ranges[0]), str(ranges[1]), "标签不能是空区间")
        self.assertEqual(editor.index(f"{ranges[0]} linestart"),
                         editor.index(f"{ranges[1]} -1c linestart"),
                         "标签不该跨到第二行上")
        # `spacing3` 也得真的挂在标签上，否则「不跨行」只是空话
        self.assertEqual(int(editor.tag_cget("bottom_pad", "spacing3")),
                         int(editor.winfo_height() * EDITOR_BOTTOM_PAD_RATIO))

    def test_the_pad_works_for_both_document_endings(self) -> None:
        """结尾有换行、没换行**两种**文稿都要有留白。

        Tk 的 Text 自己带一个「隐形换行」，所以 `end-1c` 落在哪一行取决于文稿是不是
        以换行结尾。固定用某一种范围，另一种就会静默失效（只剩 8px，肉眼几乎看不出）。
        """
        cases = {
            "无换行结尾": "# 无换行\n\n"
                          + "\n".join(f"第 {i} 行正文。" for i in range(1, 120)),
            "有换行结尾": "# 有换行\n\n"
                          + "\n".join(f"第 {i} 行正文。" for i in range(1, 120)) + "\n",
        }
        for name, text in cases.items():
            with self.subTest(结尾=name):
                doc = self.doc.parent / f"{name}.md"
                doc.write_text(text, encoding="utf-8")
                self.app.open_file(doc)
                self.root.update_idletasks()
                self.root.update()
                editor = self.app.editor
                ranges = editor.tag_ranges("bottom_pad")
                self.assertEqual(len(ranges), 2, f"{name}：留白标签必须非空")
                self.assertLess(str(ranges[0]), str(ranges[1]),
                                f"{name}：标签不能是空区间")
                editor.yview_moveto(1.0)
                self.root.update_idletasks()
                self.root.update()
                blank, height = self._blank_below_last_line()
                self._assert_about_a_quarter(blank, height, name)

    def test_opening_a_document_reapplies_the_pad(self) -> None:
        """换文稿时正文被整段删掉，标签也跟着没了——必须重新挂上。"""
        other = self.doc.parent / "另一篇.md"
        other.write_text("# 另一篇\n\n" + "正文。\n" * 200, encoding="utf-8")
        self.app.open_file(other)
        self.root.update()
        self.assertEqual(len(self.app.editor.tag_ranges("bottom_pad")), 2)

    # ---------- 跟着视口高度走 ----------

    def test_the_pad_follows_the_viewport_height(self) -> None:
        self.root.geometry("1180x520")
        self.root.update()
        self.app._apply_bottom_pad()
        pad = int(self.app.editor.tag_cget("bottom_pad", "spacing3"))
        self.assertEqual(pad, int(self.app.editor.winfo_height() * EDITOR_BOTTOM_PAD_RATIO))

    # ---------- 打字时不能每敲一下就重挂 ----------

    def _count_tag_removes(self):
        """把 `tag_remove` 包一层，记录被清过哪些标签。返回 (记录, 还原函数)。"""
        editor = self.app.editor
        seen: list[str] = []
        original = editor.tag_remove

        def counted(tag, *args):
            seen.append(str(tag))
            return original(tag, *args)

        editor.tag_remove = counted

        def restore():
            del editor.tag_remove          # 删掉实例属性，退回类方法

        return seen, restore

    def test_typing_a_character_does_not_reapply_the_pad(self) -> None:
        """标签已经盖住最后一行时，再打字不该重挂——重挂会让 Tk 重排最后一行。

        这条是性能契约：`tag_remove` + `tag_add` 会让 Tk 认为最后一行高度变了，
        每敲一个字都来一次，既费时间又会把滚动位置顶歪。

        唯一的例外是「末行本来是空行」那一次，见
        `test_typing_a_run_of_characters_reapplies_the_pad_only_once`。
        """
        editor = self.app.editor
        editor.mark_set("insert", "end-1c")
        editor.insert("insert", "起")           # 先把末行写成「有内容」再量
        self.app._apply_bottom_pad(force=True)
        self.root.update()
        line_before = self.app._bottom_pad_line
        pad_before = self.app._bottom_pad

        seen, restore = self._count_tag_removes()
        try:
            self.app.editor.insert("insert", "字")
            self.app._on_editor_modified()
        finally:
            restore()

        self.assertNotIn("bottom_pad", seen, "打字时不该重挂底部留白标签")
        self.assertEqual(self.app._bottom_pad_line, line_before)
        self.assertEqual(self.app._bottom_pad, pad_before)

    def test_typing_a_run_of_characters_reapplies_the_pad_only_once(self) -> None:
        """在末尾**空行**上连着敲，只在第一个字上重挂一次。

        第一个字必须重挂：末行是空行时标签只有一个换行符，插入点正好是标签起点，
        新字继承的是**前一个字符**的标签（没打上），标签起点被推到新字后面——
        见 `test_typing_on_an_empty_last_line_keeps_the_pad`。

        之后插入点落在标签**末尾**，新字会继承标签，就不该再重挂了。
        """
        editor = self.app.editor
        editor.mark_set("insert", "end-1c")
        self.app._apply_bottom_pad(force=True)
        self.root.update()

        seen, restore = self._count_tag_removes()
        try:
            for _ in range(5):
                editor.insert("insert", "字")
                self.app._on_editor_modified()
        finally:
            restore()
        self.assertEqual(seen.count("bottom_pad"), 1,
                         f"五个字里重挂了 {seen.count('bottom_pad')} 次，应当只有一次")

    def test_typing_on_an_empty_last_line_keeps_the_pad(self) -> None:
        """在末尾那个**空行**上敲字，25% 留白不能没。

        用户报的：「编辑区的空白，有一定概率在点击回车后闪回」。

        末行是空行时留白标签是 `end-1c` → `end`（**只有一个换行符**）。插入点正好是
        标签的起点，新字按 Tk 的规矩继承**前一个字符**的标签（没打上），标签起点于是
        被推到新字后面，整段只剩那个换行符——`spacing3` 只对「标签盖住的最后那个显示
        行」生效，留白当场消失（实测只剩 8px），而**行号一点没变**，原来那道
        「最后一行行号变了才重挂」的判据永远修不回来。
        """
        editor = self.app.editor
        editor.mark_set("insert", "end-1c")
        self.app._apply_bottom_pad(force=True)
        self.root.update()

        editor.insert("insert", "字")
        self.app._on_editor_modified()
        self.root.update()

        editor.yview_moveto(1.0)
        self.root.update()
        blank, height = self._blank_below_last_line()
        self._assert_about_a_quarter(blank, height, "在末尾空行上敲了一个字")

        ranges = editor.tag_ranges("bottom_pad")
        self.assertEqual(len(ranges), 2, "留白标签缩成了空区间")
        self.assertLess(str(ranges[0]), str(ranges[1]), "标签不能是空区间")
        self.assertEqual(editor.index(ranges[0]), editor.index("end-1c linestart"),
                         "标签没有盖住刚敲进去的那个字")

    def test_deleting_the_last_character_keeps_the_pad(self) -> None:
        """把末行最后一个字删掉，留白也得在（同一条路的反方向）。

        这条是**配套不变量**，抓不住旧判据：删掉末行唯一那个字之后，Tk 会把标签的
        起点和终点一起挪到 `N.0`，区间正好变成「空行 + 换行」这个合法形状，
        旧判据（只看行号）和新判据都判定为「盖住了」。留着它是防止以后有人为了修
        别的 bug 把这段区间算歪——那时它就会红。
        """
        editor = self.app.editor
        editor.mark_set("insert", "end-1c")
        editor.insert("insert", "字")
        self.app._on_editor_modified()
        self.app._apply_bottom_pad(force=True)    # 让标签正好盖住这个字
        self.root.update()
        self.assertEqual(len(editor.tag_ranges("bottom_pad")), 2)

        editor.delete("insert-1c", "insert")
        self.app._on_editor_modified()
        self.root.update()

        editor.yview_moveto(1.0)
        self.root.update()
        blank, height = self._blank_below_last_line()
        self._assert_about_a_quarter(blank, height, "把末尾那个字删掉")
        self.assertTrue(self.app._bottom_pad_covers_last_line(),
                        "标签缩成空区间之后没能自己修回来")

    def test_the_guard_notices_a_tag_that_slipped_off_the_last_line(self) -> None:
        """判据量的是标签的**真实区间**，不是「最后一行行号变没变」。

        「行号没变、标签却已经滑走」是真实存在的情形（见上面那条），判据必须能发现，
        而且要能在**不加 force** 的情况下自己修回来。
        """
        editor = self.app.editor
        self.app._apply_bottom_pad(force=True)
        self.root.update()
        self.assertTrue(self.app._bottom_pad_covers_last_line(), "刚挂好就该判定为盖住")

        editor.tag_remove("bottom_pad", "1.0", "end")
        editor.tag_add("bottom_pad", "end-2l linestart", "end-2l lineend")
        self.assertFalse(self.app._bottom_pad_covers_last_line(),
                         "标签滑到倒数第二行上了，判据没发现")

        self.app._apply_bottom_pad()             # 刻意不加 force
        self.root.update()
        self.assertTrue(self.app._bottom_pad_covers_last_line(), "没能自己修回来")

    def test_pressing_return_at_the_end_reapplies_the_pad(self) -> None:
        """末尾回车多出一行，标签得跟过去，否则留白会挂在倒数第二行上。

        这里断言的是「盖住的是不是新的最后一行」，**不是「一定重挂了一次」**：
        Tk 的标签区间自己会跟着插入点滑（起点右引力），所以有没有重挂是实现细节，
        用户在意的只有「留白还在不在」。
        """
        editor = self.app.editor
        self.app._apply_bottom_pad(force=True)
        self.root.update()
        line_before = int(editor.index("end-1c").split(".")[0])

        editor.mark_set("insert", "end-1c")
        editor.insert("insert", "\n")
        self.app._on_editor_modified()
        self.root.update()

        self.assertGreater(int(editor.index("end-1c").split(".")[0]), line_before,
                           "回车之后末行号应当变大")
        self.assertTrue(self.app._bottom_pad_covers_last_line(),
                        "留白标签没跟到新的最后一行上")
        ranges = editor.tag_ranges("bottom_pad")
        self.assertEqual(len(ranges), 2, "留白标签得跟到新的最后一行上")
        self.assertLess(str(ranges[0]), str(ranges[1]), "标签不能是空区间")
        self.assertEqual(editor.index(f"{ranges[0]} linestart"),
                         editor.index(f"{ranges[1]} -1c linestart"),
                         "标签不该跨到第二行上")

        editor.yview_moveto(1.0)
        self.root.update()
        blank, height = self._blank_below_last_line()
        self._assert_about_a_quarter(blank, height, "末尾回车之后")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
