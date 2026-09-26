"""行高作用于显示行、行号不重复的回归测试。

历史缺陷：
1. 行高只加在逻辑段落之间，段落内自动换行的显示行间距不变；
2. 行号按显示行重复输出，一段换行成多行会出现多个相同行号。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import tkinter as tk  # noqa: E402

import storage  # noqa: E402
from main import px  # noqa: E402

try:
    _probe = tk.Tk()
    _probe.destroy()
    HAS_DISPLAY = True
except tk.TclError:  # pragma: no cover
    HAS_DISPLAY = False


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class LineMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        long_paragraph = "这是一段会换行的长文字，" * 20
        self.doc = folder / "长文.md"
        self.doc.write_text(
            f"# 标题\n\n{long_paragraph}\n\n第二段很短。\n", encoding="utf-8"
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

    # ---------- 行高作用于显示行 ----------

    def test_line_height_makes_every_display_row_equal(self) -> None:
        app = self.app
        for line_height in (24, 32, 40):
            app._on_setting_changed("line_height", str(line_height))
            self.root.update()
            # 行高是逻辑像素，实际生效值要经 px() 换算（150% 缩放下会放大）
            expected_line_height = px(line_height)
            linespace = app._font_linespace(app.settings["font_size"])
            spacing1 = int(app.editor.cget("spacing1"))
            spacing2 = int(app.editor.cget("spacing2"))
            spacing3 = int(app.editor.cget("spacing3"))
            # 段内换行的相邻显示行距离 = 字体行高 + spacing2
            self.assertEqual(linespace + spacing2, max(expected_line_height, linespace))
            # 逻辑行首尾补白之和 = 同样的余量，保证整体均匀
            self.assertEqual(spacing1 + spacing3, max(0, expected_line_height - linespace))

    def test_wrapped_rows_are_taller_when_line_height_increases(self) -> None:
        app = self.app
        app._on_setting_changed("line_height", "16")
        self.root.update()
        small = int(app.editor.cget("spacing2"))
        app._on_setting_changed("line_height", "40")
        self.root.update()
        large = int(app.editor.cget("spacing2"))
        self.assertGreater(large, small, "行高调大后，段内换行间距也应变大")

    def test_line_height_never_below_font_linespace(self) -> None:
        app = self.app
        app._on_setting_changed("line_height", "12")
        self.root.update()
        self.assertGreaterEqual(int(app.editor.cget("spacing2")), 0)

    # ---------- 行号不重复 ----------

    def _gutter_numbers(self) -> list[str]:
        self.app._apply_markdown_styles()
        self.root.update()
        items = sorted(self.app.gutter.find_all())
        return [self.app.gutter.itemcget(item, "text") for item in items]

    def test_line_numbers_are_not_repeated(self) -> None:
        numbers = self._gutter_numbers()
        self.assertEqual(len(numbers), len(set(numbers)), f"行号出现重复：{numbers}")

    def test_only_first_display_line_gets_a_number(self) -> None:
        numbers = self._gutter_numbers()
        logical_lines = len(self.app.editor.get("1.0", "end-1c").splitlines())
        display_lines = int(self.app.editor.count("1.0", "end", "displaylines")[0])
        self.assertGreater(display_lines, logical_lines, "该用例需要文档确实发生换行")
        self.assertLessEqual(len(numbers), logical_lines)
        # 行号应是 1..n 连续前缀
        self.assertEqual(numbers, [str(i) for i in range(1, len(numbers) + 1)])

    def test_line_numbers_match_logical_line_count(self) -> None:
        numbers = self._gutter_numbers()
        logical_lines = len(self.app.editor.get("1.0", "end-1c").splitlines())
        self.assertEqual(len(numbers), logical_lines)

    def test_line_height_change_does_not_duplicate_numbers(self) -> None:
        for line_height in (16, 28, 40):
            self.app._on_setting_changed("line_height", str(line_height))
            self.root.update()
            numbers = self._gutter_numbers()
            self.assertEqual(len(numbers), len(set(numbers)), f"行高 {line_height} 时行号重复：{numbers}")


if __name__ == "__main__":
    unittest.main()
