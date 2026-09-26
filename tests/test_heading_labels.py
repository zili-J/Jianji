"""标题左侧 H1/H2/H3 徽标的测试。

背景：各级标题字号改成与正文一致后，层级只能靠加粗和留白区分，
所以要在标题行左侧画出 H1/H2/H3 徽标，把层级说明白。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))     # 让 `import _pinning` 找得到

import tkinter as tk  # noqa: E402

import storage  # noqa: E402
from _pinning import pin_screen_share  # noqa: E402
from main import (  # noqa: E402
    HEADING_LABEL_GAP,
    HEADING_LABEL_W,
    px,
)

try:
    _probe = tk.Tk()
    _probe.destroy()
    HAS_DISPLAY = True
except tk.TclError:  # pragma: no cover
    HAS_DISPLAY = False


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class HeadingLabelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        self.doc = folder / "徽标.md"
        self.doc.write_text(
            "# 一级标题\n\n正文一段。\n\n"
            "## 二级标题\n\n正文二段。\n\n"
            "### 三级标题\n\n正文三段。\n",
            encoding="utf-8",
        )
        storage.set_default_folder(folder)
        self.folder = folder

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

    # ---- 辅助 ----

    def _labels(self) -> list[str]:
        """当前画布上所有徽标文字，按纵向位置排序。"""
        app = self.app
        app._apply_markdown_styles()
        self.root.update()
        items = sorted(app.heading_labels.find_all(),
                       key=lambda item: app.heading_labels.coords(item)[1])
        return [app.heading_labels.itemcget(item, "text") for item in items]

    def _label_items(self):
        app = self.app
        app._apply_markdown_styles()
        self.root.update()
        return app.heading_labels.find_all()

    # ---- 徽标内容 ----

    def test_every_heading_gets_a_label(self) -> None:
        self.assertEqual(self._labels(), ["H1", "H2", "H3"])

    def test_label_text_matches_heading_level(self) -> None:
        app = self.app
        self.assertEqual(app._heading_label_at(1), "H1")
        self.assertEqual(app._heading_label_at(5), "H2")
        self.assertEqual(app._heading_label_at(9), "H3")

    def test_body_lines_get_no_label(self) -> None:
        app = self.app
        for line in (2, 3, 4, 6, 7, 8, 10, 11):
            self.assertIsNone(app._heading_label_at(line), f"第 {line} 行不是标题，不该有徽标")

    def test_labels_use_the_same_grey_for_every_level(self) -> None:
        fills = {self.app.heading_labels.itemcget(item, "fill") for item in self._label_items()}
        self.assertEqual(len(fills), 1, f"徽标颜色应统一，实际：{fills}")

    # ---- 跟随文档变化 ----

    def test_label_follows_level_change(self) -> None:
        self.app.editor.delete("1.0", "1.end")
        self.app.editor.insert("1.0", "### 改成三级")
        self.assertEqual(self._labels(), ["H3", "H2", "H3"])

    def test_label_disappears_when_heading_marker_removed(self) -> None:
        self.app.editor.delete("1.0", "1.2")  # 去掉 "# "
        self.assertEqual(self._labels(), ["H2", "H3"])

    def test_adding_a_heading_adds_a_label(self) -> None:
        self.app.editor.insert("end-1c", "\n\n#### 四级标题\n")
        self.assertEqual(self._labels(), ["H1", "H2", "H3", "H4"])
        self.app.editor.insert("end-1c", "\n# 新的一级\n")
        self.assertEqual(self._labels(), ["H1", "H2", "H3", "H4", "H1"])

    def test_seven_hashes_is_not_a_heading(self) -> None:
        self.app.editor.insert("end-1c", "\n\n####### 七级不算标题\n")
        self.assertEqual(self._labels(), ["H1", "H2", "H3"])

    def test_all_six_heading_levels_get_a_label(self) -> None:
        self.app.editor.delete("1.0", "end")
        self.app.editor.insert("1.0", "".join(f"{'#' * n} 第{n}级\n\n" for n in range(1, 7)))
        self.assertEqual(self._labels(), ["H1", "H2", "H3", "H4", "H5", "H6"])

    def test_wrapped_heading_gets_only_one_label(self) -> None:
        long_heading = "# " + "很长的标题内容" * 12
        self.app.editor.delete("1.0", "1.end")
        self.app.editor.insert("1.0", long_heading)
        app = self.app
        app._apply_markdown_styles()
        self.root.update()
        display_lines = int(app.editor.count("1.0", "1.end", "displaylines")[0])
        self.assertGreater(display_lines, 1, "该用例需要标题确实发生换行")
        self.assertEqual(self._labels().count("H1"), 1, "换行后的续行不应重复徽标")

    def test_no_labels_when_no_document_open(self) -> None:
        self.app.editor.configure(state="normal")
        self.app.editor.delete("1.0", "end")
        self.app.editor.configure(state="disabled")
        self.app.current_path = None
        self.app.preview_mode = False
        self.app._redraw_heading_labels()
        self.root.update()
        self.assertEqual(self.app.heading_labels.find_all(), ())

    # ---- 位置：贴在左侧留白里，不压正文 ----

    def test_label_sits_in_left_margin_without_overlapping_text(self) -> None:
        self._labels()
        app = self.app
        canvas = app.heading_labels
        self.assertEqual(canvas.winfo_manager(), "place", "徽标画布应已放置")
        content_left = app.editor.winfo_x() + app._current_pad
        self.assertLessEqual(canvas.winfo_x() + HEADING_LABEL_W, content_left,
                             "徽标右边缘不能越过正文左边缘")
        self.assertEqual(content_left - (canvas.winfo_x() + HEADING_LABEL_W),
                         HEADING_LABEL_GAP, "徽标与正文的间距应等于设定的间隙")
        self.assertGreaterEqual(canvas.winfo_x(), app.editor.winfo_x(),
                                "徽标不能跑到编辑器外面")

    def test_label_tracks_content_width_setting(self) -> None:
        """行宽越大 → 正文越宽 → 左右留白越小 → 徽标越靠左。"""
        self._labels()
        # 默认窗口下编辑区通常只占屏幕 40% 上下，落在「5% 地板」那一档——那一档里
        # 正文块宽 = 编辑区宽 − 10%·屏幕，**和 line_width 无关**，这条就测不出来。
        # 钉住屏幕宽把用例挪到「正文块宽 = 行宽 × 屏幕宽」那一档（见 _pinning）。
        pin_screen_share(self.app, 0.9)
        self.app._on_setting_changed("line_width", "30")
        self.root.update()
        self.app._apply_editor_geometry()
        self.root.update()
        at_narrow = self.app.heading_labels.winfo_x()
        self.app._on_setting_changed("line_width", "60")
        self.root.update()
        self.app._apply_editor_geometry()
        self.root.update()
        at_wide = self.app.heading_labels.winfo_x()
        self.assertLess(at_wide, at_narrow, "正文变宽后留白变小，徽标应向左移动")
        # 无论怎么调，都不能越过正文左边缘
        content_left = self.app.editor.winfo_x() + self.app._current_pad
        self.assertLessEqual(at_wide + HEADING_LABEL_W, content_left)

    def test_label_hidden_when_margin_too_narrow(self) -> None:
        """留白放不下徽标时必须隐藏，而不是压住正文。

        真实窗口下编辑区只占屏幕 40% 上下，走的是「5% 地板」那一档，留白反而很宽
        （正文块宽 = 编辑区宽 − 10%·屏幕），徽标永远放得下——所以这条得先把屏幕宽
        钉成「编辑区几乎占满屏幕」，行宽拉到 80% 时留白才会被挤到只剩几个像素。
        """
        self._labels()
        pin_screen_share(self.app, 0.9)
        self.app._on_setting_changed("line_width", "80")
        self.root.update()
        self.app._apply_editor_geometry()
        self.root.update()
        self.assertEqual(self.app.heading_labels.winfo_manager(), "",
                         "留白不足以放下徽标时应隐藏，而不是压住正文")

    def test_label_repositions_when_editor_width_changes(self) -> None:
        """专注模式会隐藏左右两栏、编辑器变宽，徽标必须跟着重排而不是留在原地。"""
        self._labels()
        before = self.app.heading_labels.winfo_x()
        self.app.toggle_focus()
        self.root.update()
        self.app._apply_editor_geometry()
        self.root.update()
        after = self.app.heading_labels.winfo_x()
        self.assertEqual(self.app.heading_labels.winfo_manager(), "place")
        self.assertGreater(after, before, "编辑器变宽后留白变大，徽标应向右移动")
        content_left = self.app.editor.winfo_x() + self.app._current_pad
        self.assertEqual(content_left - (after + HEADING_LABEL_W), HEADING_LABEL_GAP)

    def test_label_vertical_alignment_follows_heading_baseline(self) -> None:
        self._labels()
        app = self.app
        info = app.editor.dlineinfo("1.0")
        self.assertIsNotNone(info)
        item = app.heading_labels.find_all()[0]
        _x, y = app.heading_labels.coords(item)
        self.assertAlmostEqual(y, info[1] + info[4] - px(app.settings["font_size"]) * 0.36,
                               places=3)

    def test_labels_are_not_at_the_same_height(self) -> None:
        """回归：dlineinfo 第 5 项是相对行顶的基线偏移，漏加 info[1] 会让所有徽标重叠。"""
        heights = [self.app.heading_labels.coords(item)[1] for item in self._label_items()]
        self.assertEqual(len(set(heights)), len(heights), f"徽标纵坐标不应重合：{heights}")
        self.assertEqual(heights, sorted(heights), "徽标应按文档顺序自上而下排列")

    # ---- 不影响编辑 ----

    def test_label_click_returns_focus_to_editor(self) -> None:
        self.assertEqual(self.app._on_heading_label_click(), "break")

    def test_labels_survive_font_change(self) -> None:
        self.app._on_font_changed("楷体")
        self.root.update()
        self.assertEqual(self._labels(), ["H1", "H2", "H3"])

    def test_labels_survive_font_size_change(self) -> None:
        self.app._on_setting_changed("font_size", "22")
        self.root.update()
        self.assertEqual(self._labels(), ["H1", "H2", "H3"])


if __name__ == "__main__":
    unittest.main()
