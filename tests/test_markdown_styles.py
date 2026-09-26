"""行内 Markdown 记号的识别测试。

只测正则本身（不依赖图形界面），实际渲染效果见 test_markdown_syntax.py。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

from main import EMPHASIS_PATTERNS, HEADING_RE, INLINE_CODE_RE, LINK_RE  # noqa: E402


def _match(tag: str, text: str):
    """按顺序找第一条命中的规则，返回 (标签, 内层文字)。"""
    for name, compiled in EMPHASIS_PATTERNS:
        if name != tag:
            continue
        match = compiled.search(text)
        if match:
            return name, match.group(1)
    return None


class InlinePatternTests(unittest.TestCase):
    def test_common_inline_patterns_keep_plain_markdown_source(self) -> None:
        source = "这是 **加粗**、*斜体* 和 `代码`。"
        self.assertEqual(_match("bold", source), ("bold", "加粗"))
        self.assertEqual(_match("italic", source), ("italic", "斜体"))
        self.assertEqual(INLINE_CODE_RE.search(source).group(1), "代码")
        self.assertEqual(source, "这是 **加粗**、*斜体* 和 `代码`。")

    def test_underscore_variants(self) -> None:
        self.assertEqual(_match("bold", "__加粗__"), ("bold", "加粗"))
        self.assertEqual(_match("italic", "_斜体_"), ("italic", "斜体"))

    def test_underscores_inside_words_are_not_emphasis(self) -> None:
        self.assertIsNone(_match("bold", "变量 some_long_name 而已"))
        self.assertIsNone(_match("italic", "变量 some_long_name 而已"))

    def test_strikethrough_and_highlight(self) -> None:
        self.assertEqual(_match("strike", "~~删掉~~"), ("strike", "删掉"))
        self.assertEqual(_match("highlight", "==高亮=="), ("highlight", "高亮"))

    def test_bold_does_not_leak_into_italic(self) -> None:
        """**加粗** 里的星号不应被斜体规则吃掉。"""
        self.assertIsNone(_match("italic", "**加粗**"))

    def test_emphasis_requires_non_space_inside(self) -> None:
        self.assertIsNone(_match("bold", "** 空的 **"))
        self.assertIsNone(_match("strike", "~~ ~~"))

    def test_heading_pattern_covers_six_levels(self) -> None:
        self.assertEqual(len(HEADING_RE.match("## 今天").group(1)), 2)
        self.assertEqual(len(HEADING_RE.match("###### 六级").group(1)), 6)
        self.assertIsNone(HEADING_RE.match("####### 七级太深"))
        self.assertIsNone(HEADING_RE.match("#没有空格"))

    def test_link_and_image_patterns(self) -> None:
        link = LINK_RE.search("[说明](https://example.com/a)")
        self.assertEqual(link.group(1), "")
        self.assertEqual(link.group(2), "说明")
        image = LINK_RE.search("![图](a.png)")
        self.assertEqual(image.group(1), "!")
        self.assertEqual(image.group(2), "图")

    def test_empty_link_label_is_allowed(self) -> None:
        self.assertIsNotNone(LINK_RE.search("[](a.png)"))


if __name__ == "__main__":
    unittest.main()
