from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import storage  # noqa: E402
from app.storage import (
    count_files,
    create_folder,
    delete_to_recycle_bin,
    list_markdown,
    list_subfolders,
    reveal_in_explorer,
)

import tkinter as tk  # noqa: E402

try:
    _probe = tk.Tk()
    _probe.destroy()
    HAS_DISPLAY = True
except tk.TclError:  # pragma: no cover
    HAS_DISPLAY = False


class TreeListingTests(unittest.TestCase):
    def _sample(self, root: Path) -> None:
        (root / "2026-09-13.md").write_text("# 今天\n", encoding="utf-8")
        work = root / "工作"
        work.mkdir()
        (work / "会议.md").write_text("# 会议\n", encoding="utf-8")
        deep = work / "2026"
        deep.mkdir()
        (deep / "计划.md").write_text("# 计划\n", encoding="utf-8")
        hidden = root / ".obsidian"
        hidden.mkdir()
        (hidden / "secret.md").write_text("# 隐藏\n", encoding="utf-8")

    def test_non_recursive_listing_only_top_level(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._sample(root)
            names = [path.name for path in list_markdown(root)]
            self.assertEqual(names, ["2026-09-13.md"])

    def test_recursive_listing_includes_subfolders(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._sample(root)
            names = {path.name for path in list_markdown(root, recursive=True)}
            self.assertEqual(names, {"2026-09-13.md", "会议.md", "计划.md"})

    def test_recursive_listing_skips_hidden_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._sample(root)
            names = {path.name for path in list_markdown(root, recursive=True)}
            self.assertNotIn("secret.md", names)

    def test_subfolder_listing_is_one_level_by_default(self) -> None:
        """`list_subfolders` 不传参时只列直接子目录。

        界面上的文件夹树是**多层**的，走的是 `recursive=True`；这里钉的是函数
        自己的默认值——别因为「界面要多层」就顺手把默认改成递归，
        那会让所有「只要一层」的调用方跟着变。
        """
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._sample(root)
            relatives = [sub.relative_to(root).as_posix() for sub in list_subfolders(root)]
            self.assertEqual(relatives, ["工作"])

    def test_subfolder_listing_can_still_go_deep_on_request(self) -> None:
        """要递归的时候显式传 recursive=True，行为与原来一致。"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._sample(root)
            relatives = [sub.relative_to(root).as_posix()
                         for sub in list_subfolders(root, recursive=True)]
            self.assertEqual(relatives, ["工作", "工作/2026"])

    def test_subfolder_listing_excludes_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._sample(root)
            names = [sub.name for sub in list_subfolders(root)]
            self.assertNotIn(".obsidian", names)

    def test_count_files_counts_recursively(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._sample(root)
            self.assertEqual(count_files(root), 4)
            self.assertEqual(count_files(root / "工作"), 2)

    def test_create_folder_and_reject_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            created = create_folder(root, "随笔")
            self.assertTrue(created.is_dir())
            with self.assertRaises(FileExistsError):
                create_folder(root, "随笔")


class DeleteSafetyTests(unittest.TestCase):
    def test_delete_missing_path_raises(self) -> None:
        missing = Path(tempfile.gettempdir()) / "简记-不存在-zzz.md"
        with self.assertRaises(FileNotFoundError):
            delete_to_recycle_bin(missing)

    def test_reveal_missing_path_raises(self) -> None:
        missing = Path(tempfile.gettempdir()) / "简记-不存在-zzz"
        with self.assertRaises(FileNotFoundError):
            reveal_in_explorer(missing)


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class FolderTreeShapeTests(unittest.TestCase):
    """文件夹树**多层嵌套**：顶层与「全部文稿」并列，子文件夹挂在各自父节点下。

    （原来是「只有一级、且不可在文件夹下新建文件夹」，用户后来要求「多层文件夹预览」，
    那条限制随之放开——见 `test_a_subfolder_can_be_created_inside_a_folder`。）
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        (folder / "2026-09-13.md").write_text("# 今天\n", encoding="utf-8")
        work = folder / "工作"
        work.mkdir()
        (work / "会议.md").write_text("# 会议\n", encoding="utf-8")
        deep = work / "2026"
        deep.mkdir()
        (deep / "计划.md").write_text("# 计划\n", encoding="utf-8")
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

    def _top_level_labels(self) -> list[str]:
        return [self.app.folder_tree.item(iid, "text")
                for iid in self.app.folder_tree.get_children("")]

    def test_folders_sit_beside_all_documents(self) -> None:
        """文件夹是「全部文稿」的**兄弟**（同一层），不是它的子节点。"""
        labels = self._top_level_labels()
        self.assertIn("全部文稿", labels)
        self.assertIn("工作", labels)
        self.assertNotIn("工作",
                         [self.app.folder_tree.item(iid, "text")
                          for iid in self.app.folder_tree.get_children("__all__")],
                         "文件夹不该挂在「全部文稿」下面")

    def test_second_level_folder_is_a_tree_node_under_its_parent(self) -> None:
        """`工作/2026` 现在**是**一个节点，且挂在 `工作` 下面（多层文件夹预览）。"""
        all_iids = self._walk_iids()
        labels = [self.app.folder_tree.item(iid, "text") for iid in all_iids]
        self.assertIn("2026", labels, "二级文件夹应当作为节点出现")
        deep_iid = str(self.folder / "工作" / "2026")
        self.assertIn(deep_iid, all_iids)
        self.assertEqual(
            self.app.folder_tree.parent(deep_iid),
            str(self.folder / "工作"),
            "二级文件夹要挂在它的父文件夹下，而不是跟顶层文件夹并排",
        )

    def test_the_parent_folder_is_still_top_level(self) -> None:
        """加了嵌套之后，顶层文件夹**仍然**与「全部文稿」并列（不挂进它下面）。"""
        self.assertEqual(
            self.app.folder_tree.parent(str(self.folder / "工作")), "",
        )

    def test_deep_documents_are_still_listed_under_all_documents(self) -> None:
        """二级文件夹里的文稿照样能在「全部文稿」里找到。"""
        self.app.show_all()
        self.root.update()
        names = {path.name for path in self.app.file_paths}
        self.assertIn("计划.md", names, "二级文件夹里的文稿也应在全部文稿里")

    def test_clicking_a_folder_shows_only_its_own_documents(self) -> None:
        """点「工作」只显示直接放在它里面的，`工作/2026` 里的不混进来。"""
        self.app._apply_tree_selection(str(self.folder / "工作"))
        self.app.refresh_files()
        self.root.update()
        names = {path.name for path in self.app.file_paths}
        self.assertEqual(names, {"会议.md"})

    def test_a_deep_folder_can_be_scoped_too(self) -> None:
        """二级文件夹也点得进去。"""
        self.app._apply_tree_selection(str(self.folder / "工作" / "2026"))
        self.app.refresh_files()
        self.root.update()
        self.assertEqual({path.name for path in self.app.file_paths}, {"计划.md"})

    def test_collapse_state_survives_a_tree_rebuild(self) -> None:
        """收起来的文件夹在重建之后还是收着的。

        没有这条的话，`_rebuild_tree` 每次都无条件展开，「收起」等于白点。
        """
        work_iid = str(self.folder / "工作")
        self.app.folder_tree.item(work_iid, open=False)
        self.app._rebuild_tree()
        self.root.update()
        self.assertFalse(self.app.folder_tree.item(work_iid, "open"),
                         "用户收起来的文件夹不该自己张开")

    def test_a_subfolder_can_be_created_inside_a_folder(self) -> None:
        """文件夹的右键菜单里有「新建子文件夹…」，并且真的建在它里面。"""
        menu = self.app._tree_menu(str(self.folder / "工作"))
        labels = [menu.entrycget(index, "label")
                  for index in range(menu.index("end") + 1)
                  if menu.type(index) != "separator"]
        self.assertIn("新建子文件夹…", labels)
        self.assertNotIn("新建文件夹…", labels, "文件夹上给的是「子文件夹」那一条")

        self.app._ask_folder_name = lambda *_args, **_kwargs: "周报"
        self.app.new_folder(self.folder / "工作")
        self.root.update()
        self.assertTrue((self.folder / "工作" / "周报").is_dir())
        # 挂在「工作」下面，不是并排
        self.assertEqual(
            self.app.folder_tree.parent(str(self.folder / "工作" / "周报")),
            str(self.folder / "工作"),
        )

    def test_new_folder_entry_is_offered_on_all_documents(self) -> None:
        menu = self.app._tree_menu("__all__")
        labels = [menu.entrycget(index, "label")
                  for index in range(menu.index("end") + 1)
                  if menu.type(index) != "separator"]
        self.assertIn("新建文件夹…", labels)

    def test_new_folder_is_created_at_the_top_level(self) -> None:
        """从「全部文稿」的入口新建的文件夹一律落在日记文件夹根下。"""
        self.app._ask_folder_name = lambda *_args, **_kwargs: "随笔"
        self.app.new_folder()
        self.root.update()
        self.assertTrue((self.folder / "随笔").is_dir())
        self.assertIn("随笔", self._top_level_labels())

    def _walk_iids(self) -> list[str]:
        tree = self.app.folder_tree
        found: list[str] = []

        def visit(parent: str) -> None:
            for iid in tree.get_children(parent):
                found.append(iid)
                visit(iid)

        visit("")
        return found


if __name__ == "__main__":
    unittest.main()
