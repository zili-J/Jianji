"""各级标题排版的测试。

要求：一/二/三级标题的字号与正文完全一致，层级只靠**加粗**和**上下留白**区分，
不再逐级放大。
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
from tkinter import font as tkfont  # noqa: E402

try:
    _probe = tk.Tk()
    _probe.destroy()
    HAS_DISPLAY = True
except tk.TclError:  # pragma: no cover
    HAS_DISPLAY = False

HEADING_TAGS = ("h1", "h2", "h3")


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class HeadingStyleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        self.doc = folder / "标题测试.md"
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
        self.app.open_file(self.doc)
        self.root.update()

    def tearDown(self) -> None:
        storage.default_state_path = self._real_state
        self.root.destroy()

    # ---- 辅助 ----

    def _font(self, spec) -> tkfont.Font:
        return tkfont.Font(root=self.root, font=spec)

    def _tag_font(self, tag: str) -> tkfont.Font:
        return self._font(self.app.editor.tag_cget(tag, "font"))

    def _body_font(self) -> tkfont.Font:
        return self._font(self.app.editor.cget("font"))

    def _linespace(self, tag: str) -> int:
        """渲染出来的行高，用于「看起来一样大」的近似校验。

        注意：**加粗**字面的度量框比常规字面高 1px 左右，所以行高只能作近似，
        判断「字号一致」必须看下面 _spec_px() 里的像素字号。
        """
        return self._tag_font(tag).metrics("linespace")

    def _spec_px(self, spec) -> int:
        """从字体描述里取出像素字号（Tk 用负值表示像素尺寸）。"""
        if isinstance(spec, str):
            spec = self.root.tk.splitlist(spec)
        elif not isinstance(spec, (list, tuple)):
            spec = (spec,)
        for part in spec:
            try:
                value = int(part)
            except (TypeError, ValueError):
                continue
            if value < 0:
                return -value
        self.fail(f"字体描述中没有像素字号：{spec!r}")

    def _body_px(self) -> int:
        return self._spec_px(self.app.editor.cget("font"))

    def _tag_px(self, tag: str) -> int:
        return self._spec_px(self.app.editor.tag_cget(tag, "font"))

    # ---- 字号一致 ----

    def test_headings_match_body_size(self) -> None:
        """字号必须与正文完全相同（像素值），不靠行高代理。"""
        body_px = self._body_px()
        body_linespace = self._body_font().metrics("linespace")
        for tag in HEADING_TAGS:
            self.assertEqual(self._tag_px(tag), body_px, f"{tag} 字号应与正文一致")
            # 加粗字面可能高 1px，但不应更明显，否则说明字号被改过
            self.assertLessEqual(
                abs(self._linespace(tag) - body_linespace), 1,
                f"{tag} 渲染行高与正文相差过大，字号可能被放大",
            )

    def test_heading_levels_are_identical(self) -> None:
        """三级标题之间也应彼此一致，不再逐级放大。"""
        signatures = {
            (self._tag_font(tag).actual("family"),
             self._tag_font(tag).metrics("linespace"),
             self._tag_font(tag).actual("weight"))
            for tag in HEADING_TAGS
        }
        self.assertEqual(len(signatures), 1, f"各级标题应完全一致，实际：{signatures}")

    def test_headings_follow_font_size_setting(self) -> None:
        self.app._on_setting_changed("font_size", "22")
        self.root.update()
        large_body_px = self._body_px()
        large_linespace = self._body_font().metrics("linespace")
        for tag in HEADING_TAGS:
            self.assertEqual(self._tag_px(tag), large_body_px, f"{tag} 应跟随字号设置")
            self.assertLessEqual(abs(self._linespace(tag) - large_linespace), 1,
                                 f"{tag} 渲染行高应跟随正文字号")

        self.app._on_setting_changed("font_size", "12")
        self.root.update()
        small_body_px = self._body_px()
        small_linespace = self._body_font().metrics("linespace")
        self.assertLess(small_body_px, large_body_px, "字号调小后应确实变小")
        for tag in HEADING_TAGS:
            self.assertEqual(self._tag_px(tag), small_body_px, f"{tag} 应跟随字号设置")
            self.assertLessEqual(abs(self._linespace(tag) - small_linespace), 1,
                                 f"{tag} 渲染行高应跟随正文字号")

    # ---- 层级仍然可辨认 ----

    def test_headings_are_bold(self) -> None:
        for tag in HEADING_TAGS:
            self.assertEqual(self._tag_font(tag).actual("weight"), "bold",
                             f"{tag} 应保持加粗，否则与正文无法区分")

    def test_body_is_not_bold(self) -> None:
        self.assertEqual(self._body_font().actual("weight"), "normal")

    def test_headings_keep_extra_spacing(self) -> None:
        """同字号后仍需靠留白把标题与正文分开。"""
        for tag in HEADING_TAGS:
            spacing1 = int(self.app.editor.tag_cget(tag, "spacing1"))
            self.assertGreater(spacing1, 0, f"{tag} 上方应有留白")

    def test_heading_family_follows_writing_font(self) -> None:
        family = self._body_font().actual("family")
        for tag in HEADING_TAGS:
            self.assertEqual(self._tag_font(tag).actual("family"), family)

    # ---- 不影响其它标签 ----

    def test_code_tag_still_smaller_monospace(self) -> None:
        from main import MONO_FAMILY

        code = self._font(self.app.editor.tag_cget("code", "font"))
        self.assertEqual(code.actual("family"), MONO_FAMILY)
        self.assertLess(code.metrics("linespace"), self._body_font().metrics("linespace"))

    def test_italic_keeps_body_size(self) -> None:
        italic = self._font(self.app.editor.tag_cget("italic", "font"))
        self.assertEqual(italic.actual("slant"), "italic")
        self.assertEqual(self._spec_px(self.app.editor.tag_cget("italic", "font")),
                         self._body_px())

    def test_heading_tags_still_applied_to_document(self) -> None:
        """标题样式仍要真正作用到文本上（不能只配置了标签）。"""
        text = self.app.editor.get("1.0", "end-1c")
        self.assertIn("一级标题", text)
        ranges = self.app.editor.tag_ranges("h1")
        self.assertTrue(ranges, "一级标题应被标注 h1")
        self.assertTrue(self.app.editor.tag_ranges("h2"), "二级标题应被标注 h2")
        self.assertTrue(self.app.editor.tag_ranges("h3"), "三级标题应被标注 h3")


if __name__ == "__main__":
    unittest.main()
