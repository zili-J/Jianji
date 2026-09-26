"""「内容行宽」的新规则（用户第 12 轮）本身。

用户原话：

> 1. 无论是否是在专注模式，当整个编辑区占屏幕比例超过内容行宽比例+10% 时，
>    文本区按设定的最高比例显示，两侧平均分配空白。
> 2. 当编辑区小于设定的内容行宽比例+10% 时，编辑区两侧始终各留 5%，进行等比例缩小。
>    软件最小宽度为屏幕比例的 40%。

设屏幕宽 S（**显示器**宽，用户明确选的）、编辑区宽 C、内容行宽比例 r，两条合成
一个式子：

    正文块宽 = min(r·S,  C − 2×5%·S)

- C ≥ (r + 10%)·S → 正文块 = r·S，两侧平分剩下的空白；
- 否则 → 正文块 = C − 10%·S，两侧各留 5%·S。

两边在 C = (r + 10%)·S 处正好接上，没有跳变。窗口下限 = 导航栏 + 文稿列表 + 40%·S。

**注意第二档里 `line_width` 是不起作用的**（正文块宽只跟编辑区宽和屏幕宽有关）。
三栏布局下编辑区通常只占屏幕 40% 上下，默认窗口就落在这一档——这是规则本身的结果，
不是 bug，所以专门有一条测试把它钉住，免得以后被当成故障「修掉」。
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
    CARDS_W,
    EDITOR_MIN_SHARE,
    EDITOR_SIDE_RATIO,
    NAV_W,
    px,
)

import tkinter as tk  # noqa: E402

try:
    _probe = tk.Tk()
    _probe.destroy()
    HAS_DISPLAY = True
except tk.TclError:  # pragma: no cover
    HAS_DISPLAY = False


def target_width(app, column: int, ratio: float) -> int:
    """按规格独立算一遍正文块应有的宽度（不用被测代码里的函数）。"""
    screen = app._screen_width()
    return max(0, min(int(ratio * screen),
                      column - int(2 * EDITOR_SIDE_RATIO * screen)))

@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class EditorRatioRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        self.doc = folder / "比例.md"
        self.doc.write_text("# 比例\n\n正文一段。\n", encoding="utf-8")
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

    # ---- 工具 ----

    @property
    def column(self) -> int:
        return self.app.editor.master.winfo_width()

    def text_block(self) -> int:
        return self.app.editor.winfo_width() - self.app._current_pad * 2

    def set_ratio(self, percent: int) -> None:
        self.app._on_setting_changed("line_width", str(percent))
        self.root.update()
        self.app._current_pad = -1
        self.app._apply_editor_geometry()
        self.root.update()

    def assert_close(self, actual: int, expected: int, msg: str = "") -> None:
        """正文块宽和规格最多差 1 像素。

        `padx` 是**对称**的，两侧留白合计必须是偶数；规格算出来的目标宽和控件宽
        奇偶性不一致时，只能差这 1 像素——不是实现没对上，是这条路本来就走不出奇数。
        """
        self.assertLessEqual(abs(actual - expected), 1,
                             f"{msg}（实际 {actual}，规格 {expected}）")

    # ---- 第一档：编辑区够宽 ----

    def test_the_text_block_is_the_setting_share_of_the_screen(self) -> None:
        """编辑区够宽时，正文块宽 = 行宽 × **屏幕**宽（不是控件宽）。"""
        pin_screen_share(self.app, 0.9)          # 编辑区占屏幕 90%，必然够宽
        screen = self.app._screen_width()
        for percent in (30, 40, 60):
            self.set_ratio(percent)
            self.assert_close(self.text_block(), int(percent / 100 * screen),
                              f"{percent}% 时正文块宽应正好是屏幕的 {percent}%")

    def test_the_two_sides_share_the_rest_evenly(self) -> None:
        """两侧留白要平分——正文块在控件里居中，padx 是对称的。"""
        pin_screen_share(self.app, 0.9)
        self.set_ratio(40)
        width = self.app.editor.winfo_width()
        pad = self.app._current_pad
        self.assertEqual(width - 2 * pad, self.text_block())
        self.assertEqual(pad, (width - self.text_block()) // 2)
        self.assertGreater(pad, int(EDITOR_SIDE_RATIO * self.app._screen_width()),
                           "够宽的时候留白应当大于 5% 那个地板")

    # ---- 第二档：编辑区不够宽 ----

    def test_the_five_percent_floor_applies_when_the_editor_is_narrow(self) -> None:
        """编辑区不够宽时，两侧各留 5%·屏幕——正文块 = 编辑区宽 − 10%·屏幕。"""
        screen = self.app._screen_width()
        minimum = self.root.minsize()[0]
        self.root.geometry(f"{minimum}x{self.root.winfo_height()}")
        self.root.update()
        self.app._current_pad = -1
        self.app._apply_editor_geometry()
        self.root.update()
        for percent in (50, 80):
            self.set_ratio(percent)
            used = self.column - self.text_block()
            self.assert_close(used, int(2 * EDITOR_SIDE_RATIO * screen),
                              f"{percent}% 时两侧留白合计应为屏幕的 10%")

    def test_the_setting_is_inert_when_the_editor_is_too_narrow(self) -> None:
        """第二档里 `line_width` 不起作用——这是规则本身，不是 bug。

        三栏布局下编辑区通常只占屏幕 40% 上下，默认窗口就落在这一档。
        谁要是「顺手」把它改成跟着设置走，这条会红。
        """
        screen = self.app._screen_width()
        minimum = self.root.minsize()[0]
        self.root.geometry(f"{minimum}x{self.root.winfo_height()}")
        self.root.update()
        self.set_ratio(50)
        narrow = self.text_block()
        self.set_ratio(80)
        wide = self.text_block()
        self.assertEqual(narrow, wide, "第二档里改行宽不该动正文块")
        self.assert_close(narrow, self.column - int(2 * EDITOR_SIDE_RATIO * screen),
                          "第二档的正文块宽应为「编辑区宽 − 10%·屏幕」")

    # ---- 两档的接缝 ----

    def test_the_two_cases_meet_without_a_jump(self) -> None:
        """沿行宽从 30% 扫到 80%：正文块单调不减，且和规格逐档吻合。"""
        pin_screen_share(self.app, 0.75)     # 编辑区占 75% → 换档点在 r = 65%
        screen = self.app._screen_width()
        column = self.column
        previous = -1
        for percent in range(30, 81):
            self.set_ratio(percent)
            block = self.text_block()
            self.assert_close(block, target_width(self.app, column, percent / 100),
                              f"{percent}% 的正文块宽和规格对不上")
            self.assertGreaterEqual(block, previous,
                                    f"{percent}% 比上一档还窄了")
            previous = block
        # 换档点 C = (r + 10%)·S：两档在这里给出同一个宽度，所以不会跳
        boundary = column / screen - 0.10
        self.assertGreater(boundary, 0.30, "换档点落在扫描范围外，用例白跑")
        self.assertLess(boundary, 0.80, "换档点落在扫描范围外，用例白跑")
        at_boundary = target_width(self.app, column, boundary)
        self.assert_close(at_boundary, int(boundary * screen),
                          "换档点处第一档的宽度")
        self.assert_close(at_boundary,
                          column - int(2 * EDITOR_SIDE_RATIO * screen),
                          "换档点处第二档的宽度")

    # ---- 最小窗口宽度 ----

    def test_the_minimum_window_keeps_the_editor_at_forty_percent(self) -> None:
        screen = self.app._screen_width()
        minimum = self.root.minsize()[0]
        self.assertEqual(minimum, NAV_W + CARDS_W + int(screen * EDITOR_MIN_SHARE))
        self.root.geometry(f"{minimum}x{self.root.winfo_height()}")
        self.root.update()
        self.assertEqual(self.column, int(screen * EDITOR_MIN_SHARE),
                         "缩到下限时编辑区应当正好占屏幕 40%")

    def test_the_initial_window_is_never_below_the_minimum(self) -> None:
        """启动时窗口就不该小于下限，否则会被 minsize 顶一下、看着像跳了一下。"""
        self.assertGreaterEqual(self.root.winfo_width(), self.root.minsize()[0])

    # ---- 专注模式 ----

    def test_focus_mode_does_not_change_the_text_block_when_wide_enough(self) -> None:
        """用户要求「无论是否在专注模式」正文块一样宽（第一档里）。"""
        pin_screen_share(self.app, 0.9)
        self.set_ratio(40)
        normal = self.text_block()
        self.app.toggle_focus()
        self.root.update()
        self.app._current_pad = -1
        self.app._apply_editor_geometry()
        self.root.update()
        self.assertEqual(self.text_block(), normal,
                         "专注模式下编辑区变宽，正文块不该跟着变宽")
        self.app.toggle_focus()
        self.root.update()
