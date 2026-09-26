"""把文档移动到指定文件夹：底层移动、右键菜单、界面联动。

三条底线：
  · **绝不覆盖**——目标里已有同名文件时自动加 -2、-3…；
  · 只能在当前日记文件夹内移动（源和目标都不许越界）；
  · 移动的若是当前打开的文档，编辑器要跟着走，不能丢内容或指向旧路径。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import storage  # noqa: E402
from storage import move_to_folder, unique_path  # noqa: E402

import tkinter as tk  # noqa: E402

try:
    _probe = tk.Tk()
    _probe.destroy()
    HAS_DISPLAY = True
except tk.TclError:  # pragma: no cover
    HAS_DISPLAY = False


class MoveToFolderTests(unittest.TestCase):
    """底层：移动、重名、越界与各种不存在。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "简记日记"
        self.root.mkdir()
        self.sub = self.root / "读书"
        self.sub.mkdir()
        self.doc = self.root / "笔记.md"
        self.doc.write_text("# 笔记\n\n正文。\n", encoding="utf-8")

    def test_moves_file_and_keeps_content(self) -> None:
        moved = move_to_folder(self.doc, self.sub, self.root)
        self.assertEqual(moved, self.sub / "笔记.md")
        self.assertTrue(moved.exists())
        self.assertFalse(self.doc.exists(), "原位置不应再留着文件")
        self.assertEqual(moved.read_text(encoding="utf-8"), "# 笔记\n\n正文。\n")

    def test_same_folder_is_a_no_op(self) -> None:
        moved = move_to_folder(self.doc, self.root, self.root)
        self.assertEqual(moved, self.doc)
        self.assertTrue(self.doc.exists())

    def test_never_overwrites_existing_file(self) -> None:
        occupied = self.sub / "笔记.md"
        occupied.write_text("别人的内容", encoding="utf-8")
        moved = move_to_folder(self.doc, self.sub, self.root)
        self.assertEqual(moved.name, "笔记-2.md")
        self.assertEqual(occupied.read_text(encoding="utf-8"), "别人的内容",
                         "同名文件必须原样保留")

    def test_second_collision_gets_the_next_number(self) -> None:
        (self.sub / "笔记.md").write_text("a", encoding="utf-8")
        (self.sub / "笔记-2.md").write_text("b", encoding="utf-8")
        moved = move_to_folder(self.doc, self.sub, self.root)
        self.assertEqual(moved.name, "笔记-3.md")

    def test_unique_path_keeps_suffix_and_numbering(self) -> None:
        (self.sub / "a.md").write_text("x", encoding="utf-8")
        self.assertEqual(unique_path(self.sub, "a.md").name, "a-2.md")
        (self.sub / "a-2.md").write_text("x", encoding="utf-8")
        self.assertEqual(unique_path(self.sub, "a.md").name, "a-3.md")
        self.assertEqual(unique_path(self.sub, "b.md").name, "b.md")

    def test_refuses_source_outside_root(self) -> None:
        outside = self.tmp / "外面.md"
        outside.write_text("x", encoding="utf-8")
        with self.assertRaises(OSError):
            move_to_folder(outside, self.sub, self.root)
        self.assertTrue(outside.exists(), "拒绝之后不许动文件")

    def test_refuses_target_outside_root(self) -> None:
        outside_dir = self.tmp / "外面的文件夹"
        outside_dir.mkdir()
        with self.assertRaises(OSError):
            move_to_folder(self.doc, outside_dir, self.root)
        self.assertTrue(self.doc.exists())

    def test_refuses_missing_target_folder(self) -> None:
        with self.assertRaises(OSError):
            move_to_folder(self.doc, self.root / "不存在", self.root)
        self.assertTrue(self.doc.exists())

    def test_refuses_missing_source(self) -> None:
        with self.assertRaises(FileNotFoundError):
            move_to_folder(self.root / "没有这篇.md", self.sub, self.root)


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class MoveDocumentUiTests(unittest.TestCase):
    """界面：菜单项、目标列表、移动后的编辑器与列表状态。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        (folder / "读书").mkdir()
        (folder / "读书" / "随笔").mkdir()
        self.doc = folder / "第一篇.md"
        self.doc.write_text("# 第一篇\n\n正文内容。\n", encoding="utf-8")
        (folder / "第二篇.md").write_text("# 第二篇\n\n另一篇。\n", encoding="utf-8")
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

    # ---- 菜单 ----

    def _labels(self, menu) -> list[str]:
        """菜单里所有带文字的项（分隔线没有 label，跳过去）。"""
        return [menu.entrycget(i, "label")
                for i in range(menu.index("end") + 1)
                if menu.type(i) != "separator"]

    def _submenu(self, menu, label: str):
        index = next(i for i in range(menu.index("end") + 1)
                     if menu.type(i) == "cascade"
                     and menu.entrycget(i, "label") == label)
        return menu.nametowidget(menu.entrycget(index, "menu"))

    def test_menu_has_move_cascade(self) -> None:
        menu = self.app._doc_menu(self.doc)
        kinds = [menu.type(i) for i in range(menu.index("end") + 1)]
        self.assertIn("cascade", kinds, "文档菜单里应有「移动到」子菜单")
        self.assertIn("移动到", self._labels(menu))
        self.assertIn("删除文档（移到回收站）", self._labels(menu))

    def test_move_submenu_lists_top_level_folders(self) -> None:
        """子文件夹里的文档：能回根目录、能去别的顶层文件夹，就是没有自己这一层。

        文件夹层级只有一级（用户要求「当前文件夹层级提供一级」），所以 `读书/随笔`
        这种嵌套目录**不再出现**：界面上根本看不到它，菜单里却列出来，
        只会让人以为文档挪到哪儿去了。
        """
        (self.folder / "工作").mkdir()
        inside = self.folder / "读书" / "在里面.md"
        inside.write_text("# 在里面\n\n正文。\n", encoding="utf-8")

        labels = self._labels(self._submenu(self.app._doc_menu(inside), "移动到"))

        self.assertIn("全部文稿（根目录）", labels)
        self.assertIn("工作", labels, "别的顶层文件夹可以移进去")
        self.assertNotIn("读书", labels, "当前所在文件夹不该出现在菜单里")
        self.assertNotIn("读书/随笔", labels, "嵌套目录不再出现在菜单里")
        self.assertIn("新建文件夹并移动…", labels)

    def test_move_submenu_for_root_document_only_lists_subfolders(self) -> None:
        """根目录里的文档：不再列根目录（移过去等于没动），只列顶层子文件夹。"""
        labels = self._labels(self._submenu(self.app._doc_menu(self.doc), "移动到"))
        self.assertEqual(
            [label for label in labels if label != "新建文件夹并移动…"],
            ["读书"],
        )

    def test_move_targets_are_one_level_only(self) -> None:
        """移动目标和文件夹树一样只有一级——两边不一致比少一个选项更让人困惑。"""
        self.assertEqual(
            [label for label, _path in self.app._move_targets(self.doc)],
            ["读书"],
        )

    def test_move_submenu_skips_the_current_folder(self) -> None:
        targets = dict(self.app._move_targets(self.doc))
        self.assertNotIn("读书", [label for label in targets if targets[label] == self.folder])
        # 文档在根目录：不再列根目录，但子文件夹都在
        labels = list(self.app._move_targets(self.doc))
        self.assertNotIn("全部文稿（根目录）", labels)

        inside = self.folder / "读书" / "在里面.md"
        inside.write_text("# 在里面\n\n正文。\n", encoding="utf-8")
        labels = [label for label, _path in self.app._move_targets(inside)]
        self.assertNotIn("读书", labels, "当前所在文件夹不该出现")
        self.assertIn("全部文稿（根目录）", labels)
        self.assertNotIn("读书/随笔", labels, "嵌套目录不参与")

    def test_targets_never_include_the_trash(self) -> None:
        self.app.delete_document(self.folder / "第二篇.md")
        self.root.update()
        labels = [label for label, _path in self.app._move_targets(self.doc)]
        self.assertFalse(any("回收站" in label for label in labels))

    # ---- 移动 ----

    def test_move_current_document_keeps_it_open(self) -> None:
        from main import split_document_lines

        self.app.open_file(self.doc)
        self.root.update()
        self.assertEqual(self.app.current_path, self.doc)

        target = self.folder / "读书"
        self.app.move_document_to(self.doc, target)
        self.root.update()

        moved = target / "第一篇.md"
        self.assertFalse(self.doc.exists())
        self.assertTrue(moved.exists())
        self.assertEqual(self.app.current_path, moved, "编辑器要跟着文件走")
        self.assertEqual(
            split_document_lines(self.app.editor.get("1.0", "end-1c")),
            split_document_lines(moved.read_text(encoding="utf-8")),
            "编辑区里还是同一篇文档",
        )
        self.assertIn("读书", self.app.status_label.cget("text"))

    def test_moved_current_document_can_still_be_saved(self) -> None:
        """移动后磁盘签名仍有效，继续编辑不会误判成「外部改动」。"""
        self.app.open_file(self.doc)
        self.root.update()
        self.app.move_document_to(self.doc, self.folder / "读书")
        self.root.update()

        self.app.editor.insert("end-1c", "\n补一句。\n")
        self.root.update()          # 让 <<Modified>> 跑到，dirty 才会置位
        self.assertTrue(self.app.dirty, "前置条件：编辑后应处于未保存状态")
        self.assertTrue(self.app.save_now(), "移动后仍应能保存")
        moved = self.folder / "读书" / "第一篇.md"
        self.assertIn("补一句", moved.read_text(encoding="utf-8"))
        self.assertFalse(self.doc.exists())

    def test_move_other_document_leaves_current_alone(self) -> None:
        self.app.open_file(self.doc)
        self.root.update()
        other = self.folder / "第二篇.md"
        self.app.move_document_to(other, self.folder / "读书")
        self.root.update()
        self.assertEqual(self.app.current_path, self.doc)
        self.assertTrue((self.folder / "读书" / "第二篇.md").exists())

    def test_moved_document_leaves_the_scoped_list(self) -> None:
        self.app.scope_folder = self.folder
        self.app.refresh_files()
        self.root.update()
        self.assertIn(self.doc, self.app.file_paths)

        self.app.move_document_to(self.doc, self.folder / "读书")
        self.root.update()
        self.assertNotIn(self.doc, self.app.file_paths, "移走之后不该还在当前列表里")

    def test_rename_on_collision_is_reported(self) -> None:
        (self.folder / "读书" / "第一篇.md").write_text("别人的", encoding="utf-8")
        self.app.move_document_to(self.doc, self.folder / "读书")
        self.root.update()
        self.assertTrue((self.folder / "读书" / "第一篇-2.md").exists())
        self.assertEqual(
            (self.folder / "读书" / "第一篇.md").read_text(encoding="utf-8"), "别人的")

    def test_move_failure_shows_error_and_keeps_file(self) -> None:
        import main

        shown: list[str] = []
        original = main.messagebox.showerror
        main.messagebox.showerror = lambda *a, **k: shown.append(str(a))
        try:
            self.app.move_document_to(self.doc, self.folder / "不存在")
        finally:
            main.messagebox.showerror = original
        self.assertEqual(len(shown), 1, "失败要提示")
        self.assertTrue(self.doc.exists(), "失败时文件必须原地不动")

    # ---- 新建文件夹并移动 ----

    def test_new_folder_and_move(self) -> None:
        import main

        original = main.simpledialog.askstring
        main.simpledialog.askstring = lambda *a, **k: "九月"
        try:
            self.app.new_folder_and_move(self.doc)
        finally:
            main.simpledialog.askstring = original
        self.root.update()
        moved = self.folder / "九月" / "第一篇.md"
        self.assertTrue(moved.exists())
        self.assertFalse(self.doc.exists())
        self.assertIn(self.folder / "九月", self.app._tree_nodes, "新文件夹要进文件夹树")

    def test_new_folder_and_move_cancel_changes_nothing(self) -> None:
        import main

        original = main.simpledialog.askstring
        main.simpledialog.askstring = lambda *a, **k: None
        try:
            self.app.new_folder_and_move(self.doc)
        finally:
            main.simpledialog.askstring = original
        self.assertTrue(self.doc.exists())
        self.assertFalse((self.folder / "九月").exists())

    def test_invalid_folder_name_is_refused(self) -> None:
        import main

        original_ask = main.simpledialog.askstring
        original_error = main.messagebox.showerror
        warned: list[str] = []
        main.simpledialog.askstring = lambda *a, **k: "九/月"
        main.messagebox.showerror = lambda *a, **k: warned.append(str(a))
        try:
            self.app.new_folder_and_move(self.doc)
        finally:
            main.simpledialog.askstring = original_ask
            main.messagebox.showerror = original_error
        self.assertEqual(len(warned), 1, "非法名字要提示")
        self.assertTrue(self.doc.exists())


if __name__ == "__main__":
    unittest.main()
