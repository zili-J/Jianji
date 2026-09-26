"""深色主题的测试。

两条底线：
1. **导出长图永远浅色**——用户明确要求深色主题「只改界面」。所以 `export_palette()`
   在两套主题下必须一模一样，而且必须等于浅色那套值。
2. **换主题不能把状态弄丢**——正文、光标、滚动位置、快捷键绑定都要活下来。
   主题是「重灌颜色常量 + 重建整个界面」，最容易在这里踩坑。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import storage  # noqa: E402
import main  # noqa: E402
from main import (  # noqa: E402
    DEFAULT_THEME,
    THEME_KEYS,
    THEME_KEYS_SET,
    THEME_LABELS,
    THEMES,
    _DARK_COLORS,
    apply_theme,
    export_palette,
)

import tkinter as tk  # noqa: E402

try:
    _probe = tk.Tk()
    _probe.destroy()
    HAS_DISPLAY = True
except tk.TclError:  # pragma: no cover
    HAS_DISPLAY = False


# ---------- 颜色工具 ----------


def _channel(value: int) -> float:
    """sRGB 单通道 → 线性光（WCAG 2.x 的定义）。"""
    c = value / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(hex_color: str) -> float:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _contrast(front: str, back: str) -> float:
    """两色的对比度，1.0（一样）～21.0（黑白）。"""
    a, b = _luminance(front), _luminance(back)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


def _channel_gap(a: str, b: str) -> int:
    """两色最大的单通道差（0～255）。

    「选中态和底色能不能分开」不能用对比度判：对比度只看**明度**，而
    `#DCE6FC`（淡蓝）和 `#F1F0ED`（暖灰）明度几乎一样、对比度只有 1.10，
    肉眼却一眼能看出是两块不同的颜色。这种「同明度不同色相」的区分
    只有逐通道比才量得出来。
    """
    a, b = a.lstrip("#"), b.lstrip("#")
    return max(abs(int(a[i:i + 2], 16) - int(b[i:i + 2], 16)) for i in (0, 2, 4))


#: 正文会被放在这些底色上，逐个都要看得清。
BODY_BACKDROPS = (
    "CARD_BG", "CARD_AREA_BG", "EDITOR_BG", "NAV_BG", "NAV_HOVER",
    "SURFACE", "SURFACE_ALT", "SURFACE_HOVER",
)


class PaletteShapeTests(unittest.TestCase):
    """两套调色板的「形状」必须完全一致，否则换主题会漏掉某个控件。"""

    def tearDown(self) -> None:
        apply_theme("light")

    def test_default_theme_is_light(self) -> None:
        self.assertEqual(DEFAULT_THEME, "light")

    def test_every_theme_defines_every_key(self) -> None:
        for name, palette in THEMES.items():
            missing = [key for key in THEME_KEYS if key not in palette]
            self.assertEqual(missing, [], f"主题 {name} 缺少这些键：{missing}")

    def test_dark_palette_has_no_unknown_keys(self) -> None:
        """`_DARK_COLORS` 里写错名字的话，深色下会悄悄退回浅色值。"""
        unknown = sorted(set(_DARK_COLORS) - set(THEME_KEYS))
        self.assertEqual(unknown, [], f"这些键不在 THEME_KEYS 里：{unknown}")

    def test_value_types_match_between_themes(self) -> None:
        """颜色是 str、标题是 dict、项目符号是 tuple——类型不能串。"""
        light, dark = THEMES["light"], THEMES["dark"]
        for key in THEME_KEYS:
            with self.subTest(key=key):
                self.assertIsInstance(dark[key], type(light[key]),
                                      f"{key} 在两套主题里的类型不一致")
        self.assertEqual(sorted(dark["HEADING_COLORS"]), list(range(1, 7)))

    def test_light_snapshot_is_frozen_at_import(self) -> None:
        """`THEMES["light"]` 是快照，换主题不能动到它——导出图就靠这一点。"""
        before = dict(THEMES["light"])
        apply_theme("dark")
        self.assertEqual(THEMES["light"], before)

    def test_theme_labels_and_storage_keys_agree(self) -> None:
        """界面上的主题按钮和 storage 认识的键必须一一对应。"""
        self.assertEqual(THEME_KEYS_SET, tuple(storage.THEME_KEYS))
        self.assertEqual({key for key, _label in THEME_LABELS}, set(storage.THEME_KEYS))


class ApplyThemeTests(unittest.TestCase):
    """`apply_theme` 换的是模块全局——所有绘制代码都直接引用这些名字。"""

    def tearDown(self) -> None:
        apply_theme("light")

    def test_applying_dark_swaps_the_module_colours(self) -> None:
        light_text, light_card = main.TEXT, main.CARD_BG
        self.assertEqual(apply_theme("dark"), "dark")
        self.assertEqual(main.TEXT, _DARK_COLORS["TEXT"])
        self.assertEqual(main.CARD_BG, _DARK_COLORS["CARD_BG"])
        self.assertNotEqual(main.TEXT, light_text)
        self.assertNotEqual(main.CARD_BG, light_card)

    def test_applying_light_restores_the_originals(self) -> None:
        originals = {key: main.__dict__[key] for key in THEME_KEYS}
        apply_theme("dark")
        apply_theme("light")
        for key in THEME_KEYS:
            self.assertEqual(main.__dict__[key], originals[key], key)

    def test_unknown_theme_falls_back_to_light(self) -> None:
        """state.json 是用户手边可能被改坏的文件，不能因为一个坏值就崩。"""
        self.assertEqual(apply_theme("紫色"), "light")
        self.assertEqual(main.TEXT, THEMES["light"]["TEXT"])

    def test_dark_theme_actually_darkens_the_surfaces(self) -> None:
        apply_theme("dark")
        for key in ("CARD_BG", "CARD_AREA_BG", "EDITOR_BG", "NAV_BG", "SURFACE"):
            with self.subTest(key=key):
                self.assertLess(_luminance(main.__dict__[key]), 0.15,
                                f"深色主题里 {key} 不够暗")

    def test_body_text_is_readable_on_every_backdrop(self) -> None:
        """两套主题下，正文压在每种底色上都要达到 4.5:1（WCAG AA）。"""
        for name in ("light", "dark"):
            palette = THEMES[name]
            for key in BODY_BACKDROPS:
                with self.subTest(theme=name, backdrop=key):
                    ratio = _contrast(palette["TEXT"], palette[key])
                    self.assertGreaterEqual(
                        round(ratio, 2), 4.5,
                        f"{name} 主题：正文压 {key} 上只有 {ratio:.2f}:1")

    def test_secondary_text_is_readable_on_cards(self) -> None:
        """灰字是次要信息，放宽到 3:1，但也不能糊掉。"""
        for name in ("light", "dark"):
            with self.subTest(theme=name):
                ratio = _contrast(THEMES[name]["MUTED"], THEMES[name]["CARD_BG"])
                self.assertGreaterEqual(round(ratio, 2), 3.0,
                                        f"{name} 主题：灰字只有 {ratio:.2f}:1")

    def test_accent_button_labels_stay_readable(self) -> None:
        """深色下强调色是亮蓝，白字会糊——所以要跟着换 `ON_ACCENT`。"""
        for name in ("light", "dark"):
            palette = THEMES[name]
            with self.subTest(theme=name):
                ratio = _contrast(palette["ON_ACCENT"], palette["ACCENT"])
                self.assertGreaterEqual(round(ratio, 2), 4.5,
                                        f"{name} 主题：按钮文字只有 {ratio:.2f}:1")

    def test_selection_stays_visible_in_both_themes(self) -> None:
        """选中态和它所在的底色要有区分，否则「选中了哪一行」看不出来。

        这里必须逐通道比而不是比对比度：浅色的选中底 `#DCE6FC` 与 `NAV_BG`
        明度几乎相同，对比度只有 1.10，但肉眼一眼能看出区别。
        """
        pairs = (
            ("light", "SELECTION_BG", "NAV_BG"),
            ("dark", "SELECTION_BG", "NAV_BG"),
            ("light", "EDITOR_SELECTION_BG", "EDITOR_BG"),
            ("dark", "EDITOR_SELECTION_BG", "EDITOR_BG"),
        )
        for name, front, back in pairs:
            with self.subTest(theme=name, key=front):
                palette = THEMES[name]
                gap = _channel_gap(palette[front], palette[back])
                self.assertGreaterEqual(
                    gap, 12,
                    f"{name} 主题：{front} 和 {back} 只差 {gap}/255，几乎同色")


class ExportStaysLightTests(unittest.TestCase):
    """用户要求：深色主题只改界面，导出长图保持浅色。"""

    def tearDown(self) -> None:
        apply_theme("light")

    def test_export_palette_is_identical_in_both_themes(self) -> None:
        apply_theme("light")
        light_export = export_palette()
        apply_theme("dark")
        dark_export = export_palette()
        self.assertEqual(light_export, dark_export)

    def test_export_palette_matches_the_light_palette(self) -> None:
        light = THEMES["light"]
        palette = export_palette()
        self.assertEqual(palette.background, light["EDITOR_BG"])
        self.assertEqual(palette.text, light["TEXT"])
        self.assertEqual(palette.bold, light["BOLD_COLOR"])
        self.assertEqual(palette.italic, light["ITALIC_COLOR"])
        self.assertEqual(palette.bullet, light["BULLET_COLORS"])
        self.assertEqual(palette.heading,
                         tuple(light["HEADING_COLORS"][n] for n in range(1, 7)))

    def test_export_palette_stays_light_even_in_dark_mode(self) -> None:
        apply_theme("dark")
        palette = export_palette()
        self.assertEqual(palette.background, THEMES["light"]["EDITOR_BG"])
        self.assertGreater(_luminance(palette.background), 0.5,
                           "导出图的底色应当是浅色")


class ThemeStorageTests(unittest.TestCase):
    """主题选择要能存进 state.json 并读回来。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

    def tearDown(self) -> None:
        storage.default_state_path = self._real

    def test_theme_round_trips_through_settings(self) -> None:
        storage.set_settings({"theme": "dark"})
        self.assertEqual(storage.get_settings()["theme"], "dark")

    def test_unknown_stored_theme_is_ignored(self) -> None:
        storage.set_settings({"theme": "dark"})
        storage.save_state({"settings": {"theme": "彩虹"}})
        self.assertEqual(storage.get_settings()["theme"], "light")

    def test_theme_does_not_disturb_other_settings(self) -> None:
        storage.set_settings({"line_width": 62, "font_size": 17})
        storage.set_settings({"theme": "dark"})
        settings = storage.get_settings()
        self.assertEqual(settings["theme"], "dark")
        self.assertEqual(settings["line_width"], 62)
        self.assertEqual(settings["font_size"], 17)


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class ThemeUiTests(unittest.TestCase):
    """真的把界面切一遍，看状态有没有活下来。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        self.doc = folder / "2026-09-25.md"
        self.doc.write_text(
            "# 深色主题\n\n"
            "正文第一行。\n"
            "正文第二行。\n",
            encoding="utf-8",
        )
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
        apply_theme("light")

    def _bound_handlers(self, sequence: str, needle: str) -> int:
        """数某个快捷键上真正挂了几层处理函数。

        不能去数 Tk 注册过的命令（`info commands`）——`unbind` 只清掉绑定脚本，
        **不会**删除那个 Tcl 命令，所以数命令只会看到一堆已经失效的残留，
        越换主题数字越大，却说明不了有没有真的叠绑。要读 `bind` 返回的脚本本身。
        """
        return (self.root.bind(sequence) or "").count(needle)

    def test_app_starts_in_the_saved_theme(self) -> None:
        self.assertEqual(self.app.theme, "light")
        self.assertEqual(self.root.cget("bg"), THEMES["light"]["CARD_AREA_BG"])

    def test_app_honours_a_saved_dark_theme(self) -> None:
        storage.set_settings({"theme": "dark"})
        from main import JianJiApp

        root = tk.Tk()
        try:
            app = JianJiApp(root)
            self.assertEqual(app.theme, "dark")
            self.assertEqual(root.cget("bg"), THEMES["dark"]["CARD_AREA_BG"])
        finally:
            root.destroy()
            apply_theme("light")

    def test_switching_theme_keeps_the_document(self) -> None:
        text = self.app.editor.get("1.0", "end-1c")
        # 用第 3 行（有内容）而不是空行：Tk 会把 mark 收敛到行尾，
        # 拿「设进去的列号」当期望值就会因为空行被夹到 0 而假红。
        self.app.editor.mark_set("insert", "3.2")
        before = self.app.editor.index("insert")

        self.app.set_theme("dark")

        self.assertEqual(self.app.theme, "dark")
        self.assertEqual(self.app.editor.get("1.0", "end-1c"), text)
        self.assertEqual(self.app.editor.index("insert"), before)
        self.assertIsNotNone(self.app.current_path)

    def test_switching_theme_repaints_the_window(self) -> None:
        self.app.set_theme("dark")
        self.assertEqual(self.root.cget("bg"), THEMES["dark"]["CARD_AREA_BG"])
        self.assertEqual(self.app.editor.cget("bg"), THEMES["dark"]["EDITOR_BG"])
        self.assertEqual(self.app.editor.cget("fg"), THEMES["dark"]["TEXT"])

    def test_switching_back_restores_the_light_colours(self) -> None:
        self.app.set_theme("dark")
        self.app.set_theme("light")
        self.assertEqual(self.app.theme, "light")
        self.assertEqual(self.app.editor.cget("bg"), THEMES["light"]["EDITOR_BG"])

    def test_switching_theme_persists_the_choice(self) -> None:
        self.app.set_theme("dark")
        self.assertEqual(storage.get_settings()["theme"], "dark")

    def test_switching_theme_does_not_stack_root_bindings(self) -> None:
        """`_build_window` 用的是 `add="+"`，重建前不 unbind 就会一层层叠上去。"""
        self.assertEqual(self._bound_handlers("<Destroy>", "_on_root_destroy"), 1)
        self.assertEqual(self._bound_handlers("<Control-s>", "<lambda>"), 1)

        for _ in range(3):
            self.app.set_theme("dark" if self.app.theme == "light" else "light")

        self.assertEqual(self._bound_handlers("<Destroy>", "_on_root_destroy"), 1,
                         "换了几次主题就多绑了几层 <Destroy>")
        self.assertEqual(self._bound_handlers("<Control-s>", "<lambda>"), 1,
                         "换了几次主题，Ctrl+S 就会存几次")

    def test_switching_theme_keeps_the_export_light(self) -> None:
        before = export_palette()
        self.app.set_theme("dark")
        self.assertEqual(export_palette(), before)

    def test_theme_switch_is_idempotent(self) -> None:
        """再点一次当前主题不该白重建一遍界面（会把光标和滚动位置重置）。"""
        self.app.set_theme("dark")
        editor = self.app.editor
        self.app.editor.mark_set("insert", "2.1")
        self.app.set_theme("dark")
        self.assertIs(self.app.editor, editor, "重复点同一个主题不该重建界面")
        self.assertEqual(self.app.editor.index("insert"), "2.1")

    def test_settings_window_offers_both_themes(self) -> None:
        self.app.open_settings()
        window = self.app.settings_window
        self.assertIsNotNone(window)

        labels: list[str] = []

        def walk(widget) -> None:
            for child in widget.winfo_children():
                if isinstance(child, tk.Button):
                    labels.append(child.cget("text"))
                walk(child)

        walk(window)
        self.assertIn("浅色", labels)
        self.assertIn("深色", labels)

    def test_switching_theme_reopens_the_settings_window(self) -> None:
        """主题开关就在设置窗口里，切完把它关掉用户会以为点错了。"""
        self.app.open_settings()
        self.app.set_theme("dark")
        self.assertIsNotNone(self.app.settings_window)
        self.assertTrue(self.app.settings_window.winfo_exists())
        self.assertEqual(self.app.settings_window.cget("bg"),
                         THEMES["dark"]["CARD_BG"])


if __name__ == "__main__":
    unittest.main()
