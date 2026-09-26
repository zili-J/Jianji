"""字体选择测试：设置持久化、缺字体回退、字体应用到编辑器/行号。

注意预览栏**不在**这个名单里——它的字体固定为 16 号微软雅黑，不跟写作区联动。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import storage  # noqa: E402
from storage import (  # noqa: E402
    DEFAULT_FONT_FAMILY,
    FONT_FAMILY_MAX_LEN,
    get_settings,
    load_state,
    save_state,
    set_settings,
)

import tkinter as tk  # noqa: E402
from tkinter import font as tkfont  # noqa: E402

from main import CARD_FONT_FAMILY, FONT_CANDIDATES, MONO_FAMILY  # noqa: E402

try:
    _probe = tk.Tk()
    _probe.destroy()
    HAS_DISPLAY = True
except tk.TclError:  # pragma: no cover
    HAS_DISPLAY = False


class FontSettingStorageTests(unittest.TestCase):
    """字体设置写进 state.json 的读写规则。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

    def tearDown(self) -> None:
        storage.default_state_path = self._real_state

    def test_default_font_family(self) -> None:
        self.assertEqual(get_settings()["font_family"], DEFAULT_FONT_FAMILY)

    def test_font_family_round_trip(self) -> None:
        values = get_settings()
        values["font_family"] = "KaiTi"
        set_settings(values)
        self.assertEqual(get_settings()["font_family"], "KaiTi")

    def test_blank_font_family_falls_back_to_default(self) -> None:
        state = load_state()
        state["settings"] = {"font_family": "   "}
        save_state(state)
        self.assertEqual(get_settings()["font_family"], DEFAULT_FONT_FAMILY)

    def test_non_string_font_family_is_ignored(self) -> None:
        state = load_state()
        state["settings"] = {"font_family": 123}
        save_state(state)
        self.assertEqual(get_settings()["font_family"], DEFAULT_FONT_FAMILY)

    def test_font_family_is_trimmed_and_truncated(self) -> None:
        values = get_settings()
        values["font_family"] = "  " + "X" * 100 + "  "
        set_settings(values)
        stored = get_settings()["font_family"]
        self.assertEqual(stored, "X" * FONT_FAMILY_MAX_LEN)

    def test_font_family_does_not_break_numeric_clamping(self) -> None:
        values = get_settings()
        values.update({
            "font_size": 99, "line_height": 99, "line_width": 1, "font_family": "SimSun",
        })
        set_settings(values)
        out = get_settings()
        self.assertEqual(out["font_size"], 24)
        self.assertEqual(out["line_height"], 40)
        self.assertEqual(out["line_width"], 30)
        self.assertEqual(out["font_family"], "SimSun")

    def test_missing_font_key_keeps_default(self) -> None:
        state = load_state()
        state["settings"] = {"font_size": 18}
        save_state(state)
        out = get_settings()
        self.assertEqual(out["font_family"], DEFAULT_FONT_FAMILY)
        self.assertEqual(out["font_size"], 18)


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class FontUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        (folder / "第一篇.md").write_text(
            "# 第一篇\n\n今天的天气很好，我写下这段话。\n", encoding="utf-8",
        )
        storage.set_default_folder(folder)
        self.folder = folder

        from main import JianJiApp

        self.root = tk.Tk()
        self.root.geometry("1180x760")
        self.app = JianJiApp(self.root)
        self.app.open_file(folder / "第一篇.md")
        self.root.update()

    def tearDown(self) -> None:
        try:
            self.app._close_settings()
        except Exception:
            pass
        storage.default_state_path = self._real_state
        self.root.destroy()

    # ---- 辅助 ----

    def _family_of(self, spec) -> str:
        """widget.cget('font') 这类字体描述 → Tk 实际使用的族名。"""
        return tkfont.Font(root=self.root, font=spec).actual("family")

    def _resolved(self, family: str) -> str:
        """族名 → Tk 实际解析出的族名（中文系统上 KaiTi 会变成 楷体）。"""
        return tkfont.Font(root=self.root, family=family, size=-15).actual("family")

    def _real_alternative(self) -> str | None:
        """找一个本机真实安装、且不是默认字体的候选字体（返回系统实际名字）。"""
        for _label, aliases in FONT_CANDIDATES:
            actual = self.app._resolve_installed(aliases)
            if actual and actual.lower() != DEFAULT_FONT_FAMILY.lower():
                return actual
        return None

    # ---- 纯逻辑：字体解析与回退 ----

    def test_writing_font_uses_installed_choice(self) -> None:
        self.app._font_families_cache = {"kaiti": "KaiTi", "microsoft yahei ui": "Microsoft YaHei UI"}
        self.app.settings["font_family"] = "KaiTi"
        self.assertEqual(self.app._writing_font(), "KaiTi")

    def test_writing_font_falls_back_when_font_missing(self) -> None:
        self.app._font_families_cache = {"microsoft yahei ui": "Microsoft YaHei UI"}
        self.app.settings["font_family"] = "A Font That Does Not Exist"
        self.assertEqual(self.app._writing_font(), DEFAULT_FONT_FAMILY)

    def test_writing_font_falls_back_when_unset(self) -> None:
        self.app._font_families_cache = {"kaiti": "KaiTi"}
        self.app.settings["font_family"] = ""
        self.assertEqual(self.app._writing_font(), DEFAULT_FONT_FAMILY)

    def test_writing_font_label_maps_chinese_name(self) -> None:
        self.app._font_families_cache = {"kaiti": "KaiTi", "microsoft yahei ui": "Microsoft YaHei UI"}
        self.app.settings["font_family"] = "KaiTi"
        self.assertEqual(self.app._writing_font_label(), "楷体")

    def test_writing_font_label_maps_localized_name(self) -> None:
        """中文 Windows 上 font.families() 返回「楷体」，也要能映射成显示名。"""
        self.app._font_families_cache = {"楷体": "楷体", "microsoft yahei ui": "Microsoft YaHei UI"}
        self.app.settings["font_family"] = "楷体"
        self.assertEqual(self.app._writing_font(), "楷体")
        self.assertEqual(self.app._writing_font_label(), "楷体")

    def test_writing_font_label_falls_back_to_family_name(self) -> None:
        self.app._font_families_cache = {"my custom face": "My Custom Face"}
        self.app.settings["font_family"] = "My Custom Face"
        self.assertEqual(self.app._writing_font_label(), "My Custom Face")

    def test_font_row_label_uses_chinese_name(self) -> None:
        # 中文系统报 "楷体"，英文系统报 "KaiTi"，两种都应显示为「楷体」
        self.assertEqual(self.app._font_row_label("楷体"), "楷体")
        self.assertEqual(self.app._font_row_label("KaiTi"), "楷体")
        self.assertEqual(self.app._font_row_label("Microsoft YaHei UI"), "微软雅黑")

    def test_font_row_label_falls_back_to_family(self) -> None:
        self.assertEqual(self.app._font_row_label("Some Random Font"), "Some Random Font")

    def test_available_choices_only_include_installed(self) -> None:
        self.app._font_families_cache = {"kaiti": "KaiTi", "simsun": "SimSun"}
        self.assertEqual(
            self.app._available_font_choices(),
            [("宋体", "SimSun"), ("楷体", "KaiTi")],
        )

    def test_available_choices_resolve_localized_aliases(self) -> None:
        """候选里写的是拉丁名，系统报的是中文名，仍应被识别为已安装。"""
        self.app._font_families_cache = {"楷体": "楷体", "等线": "等线"}
        self.assertEqual(
            self.app._available_font_choices(),
            [("等线", "等线"), ("楷体", "楷体")],
        )

    def test_system_font_list_puts_recommended_first(self) -> None:
        self.app._font_families_cache = None
        ordered = self.app._system_font_families()
        self.assertTrue(ordered)
        installed_preferred = [family for _label, family in self.app._available_font_choices()]
        self.assertTrue(installed_preferred, "本机应至少有一个推荐字体可用")
        self.assertEqual(ordered[: len(installed_preferred)], installed_preferred)
        # 不重复
        self.assertEqual(len(ordered), len({name.lower() for name in ordered}))

    # ---- 应用效果 ----

    def test_changing_font_reaches_editor_and_headings(self) -> None:
        family = self._real_alternative()
        if family is None:
            self.skipTest("本机没有可用的备选字体")
        self.app._font_families_cache = None
        self.app._on_font_changed(family)
        self.assertEqual(self.app.settings["font_family"], family)
        self.assertEqual(self._family_of(self.app.editor.cget("font")), family)
        self.assertEqual(self._family_of(self.app.editor.tag_cget("h1", "font")), family)
        self.assertEqual(self._family_of(self.app.editor.tag_cget("bold", "font")), family)

    def test_code_tag_stays_monospace(self) -> None:
        family = self._real_alternative()
        if family is None:
            self.skipTest("本机没有可用的备选字体")
        self.app._font_families_cache = None
        self.app._on_font_changed(family)
        self.assertEqual(self._family_of(self.app.editor.tag_cget("code", "font")), MONO_FAMILY)

    def test_changing_font_reaches_the_gutter(self) -> None:
        """行号跟着写作区字体走（和正文对齐才好看）。"""
        family = self._real_alternative()
        if family is None:
            self.skipTest("本机没有可用的备选字体")
        self.app._font_families_cache = None
        self.app._on_font_changed(family)

        self.app._redraw_line_numbers()
        self.root.update()

        gutter_items = self.app.gutter.find_all()
        self.assertTrue(gutter_items, "行号应当已绘制")
        gutter_fonts = {
            self._family_of(self.app.gutter.itemcget(item, "font"))
            for item in gutter_items
        }
        self.assertIn(family, gutter_fonts)

    def test_changing_font_leaves_the_cards_alone(self) -> None:
        """预览栏字体固定为 16 号微软雅黑，**不跟写作区走**（用户要求）。

        写作区换字体是为了自己写着舒服；预览栏要的是一眼扫过去能看清哪篇是哪篇，
        两者诉求不同——所以这里**故意断言它没跟着变**。
        """
        family = self._real_alternative()
        if family is None:
            self.skipTest("本机没有可用的备选字体")
        expected = self._resolved(CARD_FONT_FAMILY)
        if family == expected:
            self.skipTest("本机的备选字体恰好就是预览栏字体，比不出差别")

        self.app._font_families_cache = None
        self.app._on_font_changed(family)
        self.app._rebuild_cards()
        self.root.update()

        card_fonts = {
            self._family_of(self.app.cards_canvas.itemcget(item, "font"))
            for item in self.app.cards_canvas.find_all()
            if self.app.cards_canvas.type(item) == "text"
        }
        self.assertTrue(card_fonts, "应当绘制了卡片文字")
        self.assertNotIn(family, card_fonts)
        self.assertEqual(card_fonts, {expected})

    def test_font_choice_is_persisted(self) -> None:
        family = self._real_alternative() or "KaiTi"
        self.app._font_families_cache = None
        self.app._on_font_changed(family)
        self.app._persist_settings()
        self.assertEqual(get_settings()["font_family"], family)

    def test_font_choice_survives_restart(self) -> None:
        family = self._real_alternative()
        if family is None:
            self.skipTest("本机没有可用的备选字体")
        self.app._font_families_cache = None
        self.app._on_font_changed(family)
        self.app._persist_settings()

        from main import JianJiApp

        root2 = tk.Tk()
        try:
            app2 = JianJiApp(root2)
            root2.update()
            self.assertEqual(app2.settings["font_family"], family)
            self.assertEqual(app2._writing_font(), family)
        finally:
            root2.destroy()

    # ---- 设置窗口与字体选择窗口 ----

    def test_settings_row_shows_current_font_label(self) -> None:
        family = self._real_alternative()
        if family is None:
            self.skipTest("本机没有可用的备选字体")
        self.app._font_families_cache = None
        self.app.settings["font_family"] = family
        self.app.open_settings()
        self.root.update()
        self.app._sync_font_row()
        label = self.app.font_row_value.cget("text")
        self.assertEqual(label, self.app._writing_font_label())
        self.assertNotEqual(label, "")
        # 这一行本身用所选字体渲染，本身就是预览
        self.assertEqual(
            self._family_of(self.app.font_row_value.cget("font")),
            self._resolved(family),
        )

    def test_font_picker_opens_and_closes(self) -> None:
        self.app.open_font_picker()
        self.root.update()
        self.assertIsNotNone(self.app.font_window)
        self.assertTrue(self.app.font_window.winfo_exists())
        self.app._close_font_picker()
        self.assertIsNone(self.app.font_window)

    def test_font_picker_reuses_existing_window(self) -> None:
        self.app.open_font_picker()
        self.root.update()
        first = self.app.font_window
        self.app.open_font_picker()
        self.assertIs(self.app.font_window, first)
        self.app._close_font_picker()

    def test_closing_settings_closes_font_picker(self) -> None:
        self.app.open_settings()
        self.app.open_font_picker()
        self.root.update()
        self.app._close_settings()
        self.assertIsNone(self.app.font_window)
        self.assertIsNone(self.app.settings_window)


if __name__ == "__main__":
    unittest.main()
