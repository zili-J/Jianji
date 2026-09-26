"""回归测试：文稿区右键必须能命中卡片并弹出删除菜单。

历史缺陷：卡片背景与文字是两层，早期只给文字绑定了事件，
导致在卡片空白处右键没有任何反应。
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


class FakeEvent:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y
        self.x_root = x
        self.y_root = y


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class CardContextMenuTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        (folder / "第一篇.md").write_text("# 第一篇\n\n正文内容。\n", encoding="utf-8")
        (folder / "第二篇.md").write_text("# 第二篇\n\n另一篇内容。\n", encoding="utf-8")
        storage.set_default_folder(folder)
        self.folder = folder

        from main import JianJiApp

        self.root = tk.Tk()
        self.root.geometry("1180x760")
        self.app = JianJiApp(self.root)
        self.root.update()

    def tearDown(self) -> None:
        storage.default_state_path = self._real_state
        self.root.destroy()

    def test_canvas_has_right_click_binding(self) -> None:
        bindings = self.app.cards_canvas.bind("<Button-3>")
        self.assertTrue(bindings, "文稿区画布必须绑定右键事件")

    def test_hit_test_covers_card_blank_area(self) -> None:
        canvas = self.app.cards_canvas
        width = canvas.winfo_width()
        # 卡片右侧空白处（超出文字宽度，但仍在卡片内）
        hit = self.app._card_at(width - px(25), 40)
        self.assertIsNotNone(hit, "点击卡片空白处也应命中该文档")
        self.assertIn(hit, self.app.file_paths)

    def test_hit_test_on_card_text(self) -> None:
        hit = self.app._card_at(40, 40)
        self.assertIsNotNone(hit, "点击卡片文字处也应命中该文档")

    def test_hit_test_outside_cards_returns_none(self) -> None:
        self.assertIsNone(self.app._card_at(4, 4000))

    def test_right_click_pops_menu_with_delete(self) -> None:
        popped: list[tuple[int, int]] = []
        original = tk.Menu.tk_popup

        def fake_popup(self, x, y, *args, **kwargs):  # noqa: ANN001
            popped.append((x, y))

        tk.Menu.tk_popup = fake_popup
        try:
            width = self.app.cards_canvas.winfo_width()
            self.app._on_cards_right_click(FakeEvent(width - px(25), 40))
        finally:
            tk.Menu.tk_popup = original
        self.assertEqual(len(popped), 1, "右键应弹出菜单")

    def test_doc_menu_contains_delete(self) -> None:
        path = self.folder / "第一篇.md"
        menu = self.app._doc_menu(path)
        labels = [
            menu.entrycget(index, "label")
            for index in range(menu.index("end") + 1)
            if menu.type(index) == "command"
        ]
        self.assertIn("删除文档（移到回收站）", labels)
        self.assertIn("打开", labels)

    def test_doc_menu_also_offers_sorting(self) -> None:
        """排序入口挂在文稿右键菜单里（原来占着标题栏，被用户要求挪走）。"""
        menu = self.app._doc_menu(self.folder / "第一篇.md")
        cascades = [menu.entrycget(index, "label")
                    for index in range(menu.index("end") + 1)
                    if menu.type(index) == "cascade"]
        self.assertTrue(any(label.startswith("排序：") for label in cascades),
                        f"文稿菜单里没有排序入口：{cascades}")

    def test_right_click_below_the_cards_also_pops_a_menu(self) -> None:
        """卡片下方的空白处也要能弹菜单——排序入口就在那儿。

        原来点空白是**什么都不发生**；现在文稿少的时候卡片之外全是空白，
        不给菜单就等于没有排序入口。
        """
        popped: list[tuple[int, int]] = []
        original = tk.Menu.tk_popup

        def fake_popup(self, x, y, *args, **kwargs):  # noqa: ANN001
            popped.append((x, y))

        tk.Menu.tk_popup = fake_popup
        try:
            self.app._on_cards_right_click(FakeEvent(px(60), 4000))
        finally:
            tk.Menu.tk_popup = original
        self.assertEqual(len(popped), 1, "卡片下方的空白处应弹出菜单")
        labels = [self.app._card_menu.entrycget(index, "label")
                  for index in range(self.app._card_menu.index("end") + 1)]
        self.assertTrue(any(label.startswith("排序：") for label in labels),
                        f"空白处的菜单应当只有排序：{labels}")
        self.assertNotIn("删除文档（移到回收站）", labels,
                         "空白处不该出现针对某一篇文档的操作")

    def test_delete_document_moves_to_trash_and_clears_editor(self) -> None:
        path = self.folder / "第一篇.md"
        self.app.open_file(path)
        self.root.update()
        self.assertEqual(self.app.current_path, path)

        self.app.delete_document(path)
        self.root.update()

        self.assertFalse(path.exists(), "原位置不应再保留该文件")
        self.assertIsNone(self.app.current_path)
        self.assertEqual(self.app.editor.get("1.0", "end-1c"), "")
        trash = self.folder / ".简记回收站"
        self.assertTrue(trash.is_dir())
        self.assertEqual(len(list(trash.glob("*.md"))), 1, "文档应进入应用内回收站")

    def test_delete_document_does_not_ask_for_confirmation(self) -> None:
        from main import messagebox as mb

        asked: list[str] = []
        original = mb.askyesno
        mb.askyesno = lambda *a, **k: asked.append("called") or True
        try:
            self.app.delete_document(self.folder / "第二篇.md")
        finally:
            mb.askyesno = original
        self.assertEqual(asked, [], "删除文档不应弹确认框")


if __name__ == "__main__":
    unittest.main()
