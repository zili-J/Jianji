"""回收站测试：删除进回收站、可查看、可还原、可彻底删除。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import storage  # noqa: E402
from storage import (  # noqa: E402
    list_markdown,
    list_trash,
    move_to_trash,
    purge_trash_entry,
    restore_from_trash,
    trash_dir,
    trash_entry_path,
)

import tkinter as tk  # noqa: E402

try:
    _probe = tk.Tk()
    _probe.destroy()
    HAS_DISPLAY = True
except tk.TclError:  # pragma: no cover
    HAS_DISPLAY = False


class TrashStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "简记日记"
        self.root.mkdir()

    def _doc(self, relative: str, text: str = "# 标题\n\n正文。\n") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_move_to_trash_keeps_original_relative_path(self) -> None:
        doc = self._doc("工作/周报.md")
        entry = move_to_trash(doc, self.root, datetime(2026, 9, 13, 16, 45, 0))
        self.assertFalse(doc.exists())
        self.assertEqual(entry.original_relative, "工作/周报.md")
        self.assertTrue((trash_dir(self.root) / entry.trash_name).exists())

    def test_trash_folder_is_hidden_from_document_listing(self) -> None:
        doc = self._doc("随笔.md")
        move_to_trash(doc, self.root)
        self.assertEqual(list_markdown(self.root, recursive=True), [])

    def test_list_trash_returns_entries_newest_first(self) -> None:
        move_to_trash(self._doc("a.md"), self.root, datetime(2026, 9, 13, 10, 0, 0))
        move_to_trash(self._doc("b.md"), self.root, datetime(2026, 9, 13, 12, 0, 0))
        entries = list_trash(self.root)
        self.assertEqual([entry.original_relative for entry in entries], ["b.md", "a.md"])

    def test_restore_puts_file_back(self) -> None:
        doc = self._doc("工作/周报.md", "# 周报\n\n内容。\n")
        entry = move_to_trash(doc, self.root)
        restored = restore_from_trash(self.root, entry.trash_name)
        self.assertEqual(restored, doc)
        self.assertTrue(doc.exists())
        self.assertEqual(doc.read_text(encoding="utf-8"), "# 周报\n\n内容。\n")
        self.assertEqual(list_trash(self.root), [])

    def test_restore_does_not_overwrite_existing_file(self) -> None:
        doc = self._doc("周报.md", "旧内容")
        entry = move_to_trash(doc, self.root)
        doc.write_text("新内容", encoding="utf-8")
        restored = restore_from_trash(self.root, entry.trash_name)
        self.assertNotEqual(restored, doc)
        self.assertTrue(restored.name.startswith("周报-还原"))
        self.assertEqual(doc.read_text(encoding="utf-8"), "新内容")
        self.assertEqual(restored.read_text(encoding="utf-8"), "旧内容")

    def test_purge_removes_entry_and_cleans_index(self) -> None:
        doc = self._doc("临时.md")
        entry = move_to_trash(doc, self.root)
        calls: list[Path] = []
        original = storage.delete_to_recycle_bin
        storage.delete_to_recycle_bin = lambda path: calls.append(Path(path))
        try:
            purge_trash_entry(self.root, entry.trash_name)
        finally:
            storage.delete_to_recycle_bin = original
        self.assertEqual(calls, [trash_entry_path(self.root, entry.trash_name)])
        self.assertEqual(list_trash(self.root), [])

    def test_move_outside_root_is_rejected(self) -> None:
        outside = self.tmp / "外部.md"
        outside.write_text("x", encoding="utf-8")
        with self.assertRaises(OSError):
            move_to_trash(outside, self.root)

    def test_restore_missing_entry_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            restore_from_trash(self.root, "不存在.md")


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class TrashUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        (folder / "第一篇.md").write_text("# 第一篇\n\n正文。\n", encoding="utf-8")
        (folder / "第二篇.md").write_text("# 第二篇\n\n正文。\n", encoding="utf-8")
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

    def test_current_tree_iid_tracks_mode(self) -> None:
        self.assertEqual(self.app._current_tree_iid(), "__all__")
        self.app._apply_tree_selection("__trash__")
        self.assertEqual(self.app._current_tree_iid(), "__trash__")
        self.app.show_all()
        self.assertEqual(self.app._current_tree_iid(), "__all__")

    def test_tree_contains_trash_node(self) -> None:
        labels = [
            self.app.folder_tree.item(iid, "text")
            for iid in self.app.folder_tree.get_children("")
        ]
        self.assertIn("回收站", labels)

    def test_trash_node_shows_count_after_delete(self) -> None:
        self.app.delete_document(self.folder / "第一篇.md")
        self.root.update()
        labels = [
            self.app.folder_tree.item(iid, "text")
            for iid in self.app.folder_tree.get_children("")
        ]
        self.assertIn("回收站（1）", labels)

    def test_switch_to_trash_lists_deleted_documents(self) -> None:
        self.app.delete_document(self.folder / "第一篇.md")
        self.root.update()
        self.app._apply_tree_selection("__trash__")
        self.app.refresh_files()
        self.root.update()
        self.assertTrue(self.app.trash_mode)
        self.assertEqual(len(self.app.trash_entries), 1)
        self.assertEqual(self.app.trash_entries[0].original_relative, "第一篇.md")
        self.assertIn("回收站", self.app.cards_title.cget("text"))

    def test_preview_trash_entry_is_read_only(self) -> None:
        self.app.delete_document(self.folder / "第一篇.md")
        self.app._apply_tree_selection("__trash__")
        self.app.refresh_files()
        self.app.preview_trash_entry(self.app.trash_entries[0])
        self.root.update()
        self.assertEqual(self.app.editor.cget("state"), "disabled")
        self.assertIn("第一篇", self.app.editor.get("1.0", "end-1c"))
        self.assertIsNone(self.app.current_path, "预览不应触发自动保存")

    def test_restore_from_tree_returns_document(self) -> None:
        target = self.folder / "第一篇.md"
        self.app.delete_document(target)
        self.app._apply_tree_selection("__trash__")
        self.app.refresh_files()
        self.app.restore_trash_entry(self.app.trash_entries[0])
        self.root.update()
        self.assertTrue(target.exists())
        self.assertEqual(len(self.app.trash_entries), 0)

    def test_documents_are_editable_again_after_leaving_trash(self) -> None:
        self.app.delete_document(self.folder / "第一篇.md")
        self.app._apply_tree_selection("__trash__")
        self.app.refresh_files()
        self.app.preview_trash_entry(self.app.trash_entries[0])
        self.root.update()
        self.assertEqual(self.app.editor.cget("state"), "disabled")

        self.app.show_all()
        self.app.open_file(self.folder / "第二篇.md")
        self.root.update()
        self.assertEqual(self.app.editor.cget("state"), "normal")
        self.assertFalse(self.app.preview_mode)


if __name__ == "__main__":
    unittest.main()
