"""文件名跟随一级标题：标题识别、文件名清洗、以及「什么时候才该改名」。

这一批里最要紧的不是「能改名」，而是**不该改名的时候不改**。用户文稿里现成的
反例一抓一把：

  · `2026-09-13.md` 与 `2026-09-14.md` 的 H1 都是 `# 今日日记`；
  · `开发记录.md` 的 H1 是 `# 简记优化记录`；
  · `说明书/简记使用说明.md` 的 H1 是 `# 简记 · 使用说明`。

要是「一存盘就按 H1 改名」，第一次保存就会把这一片名字全换掉，前两个还当场撞车。
所以规则是**只有标题相对载入时真的被改过才改名**（`_title_at_load` 是基准点）。

另外两条被改名牵连的路径也要盯着：`move_document_to` / `delete_document` 都是
「先 `save_now` 再拿手里的 path 去干活」，改名会让那个 path 指向一个不存在的名字。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import storage  # noqa: E402
from main import (  # noqa: E402
    FILENAME_MAX_CHARS,
    first_h1_title,
    title_to_filename,
)

import tkinter as tk  # noqa: E402

try:
    _probe = tk.Tk()
    _probe.destroy()
    HAS_DISPLAY = True
except tk.TclError:  # pragma: no cover
    HAS_DISPLAY = False


class FirstH1TitleTests(unittest.TestCase):
    """只认第一行的一级标题，别的一概不认。"""

    def test_first_line_heading_is_the_title(self) -> None:
        self.assertEqual(first_h1_title("# 今日日记\n\n正文。\n"), "今日日记")

    def test_title_is_stripped(self) -> None:
        self.assertEqual(first_h1_title("#    今日日记   \n"), "今日日记")

    def test_heading_must_be_the_first_line(self) -> None:
        self.assertIsNone(first_h1_title("正文。\n\n# 后面的标题\n"))

    def test_a_leading_blank_line_disqualifies_it(self) -> None:
        """开头先空一行再写标题，那行标题在用户心里就不是标题了。"""
        self.assertIsNone(first_h1_title("\n# 今日日记\n"))

    def test_needs_a_space_after_the_hashes(self) -> None:
        """`#标题` 不是标题——和编辑区渲染用的是同一套判断（`parse_block`）。"""
        self.assertIsNone(first_h1_title("#今日日记\n"))

    def test_second_level_heading_is_not_a_title(self) -> None:
        self.assertIsNone(first_h1_title("## 二级\n"))
        self.assertIsNone(first_h1_title("###### 六级\n"))

    def test_empty_heading_gives_nothing(self) -> None:
        self.assertIsNone(first_h1_title("# \n"))
        self.assertIsNone(first_h1_title("#\n"))

    def test_empty_document_gives_nothing(self) -> None:
        self.assertIsNone(first_h1_title(""))

    def test_a_lone_carriage_return_is_content_not_a_line_break(self) -> None:
        """单独一个 `\\r` 是行内容，不是换行——切行只能用 `split_document_lines`。

        `re.split(r"\\r\\n|\\r|\\n")` 会在这里多切出一行，标题就会被读成 `# 今日日记`、
        后面那半截另算一行；Tk 的 Text 控件不是这么分行的。
        """
        self.assertEqual(first_h1_title("# 今日日记\r\n正文"), "今日日记")
        self.assertEqual(first_h1_title("# 今日日记\r# 第二行"), "今日日记\r# 第二行")
        # 那个 \r 洗成空格之后，文件名里不会留一个看不见的回车
        self.assertEqual(title_to_filename(first_h1_title("# 甲\r乙") or ""), "甲 乙")


class TitleToFilenameTests(unittest.TestCase):
    """标题 → 合法文件名主干。"""

    def test_ordinary_title_passes_through(self) -> None:
        self.assertEqual(title_to_filename("今日日记"), "今日日记")
        self.assertEqual(title_to_filename("简记 · 使用说明"), "简记 · 使用说明")

    def test_illegal_characters_become_fullwidth_lookalikes(self) -> None:
        self.assertEqual(title_to_filename("第1章/第2节"), "第1章／第2节")
        self.assertEqual(title_to_filename("真的吗?"), "真的吗？")
        self.assertEqual(title_to_filename("a:b*c"), "a：b＊c")
        self.assertEqual(title_to_filename('a"b<c>d|e\\f'), "a＂b＜c＞d｜e＼f")

    def test_whitespace_is_collapsed(self) -> None:
        self.assertEqual(title_to_filename("我的  日记\t第二行"), "我的 日记 第二行")

    def test_trailing_dots_and_spaces_are_removed(self) -> None:
        """Windows 会把主干末尾的句点和空格吃掉，留着只会让人以为名字没改。"""
        self.assertEqual(title_to_filename("标题."), "标题")
        self.assertEqual(title_to_filename("标题...  "), "标题")

    def test_nothing_left_returns_none(self) -> None:
        """清完为空必须给 None，不能给空串——不然文件会被叫成 `.md`。"""
        for title in ("   ", "...", "\t\n", ""):
            self.assertIsNone(title_to_filename(title), f"{title!r} 应该给 None")

    def test_reserved_device_names_get_an_underscore(self) -> None:
        self.assertEqual(title_to_filename("CON"), "CON_")
        self.assertEqual(title_to_filename("con"), "con_")
        self.assertEqual(title_to_filename("COM7"), "COM7_")
        self.assertEqual(title_to_filename("LPT1"), "LPT1_")

    def test_reserved_name_with_an_extension_is_still_reserved(self) -> None:
        """`NUL.md` 在 Windows 上一样是保留的，下划线要插进点之前那一段。"""
        self.assertEqual(title_to_filename("NUL.md"), "NUL_.md")
        self.assertEqual(title_to_filename("aux.txt"), "aux_.txt")

    def test_similar_but_valid_names_are_left_alone(self) -> None:
        self.assertEqual(title_to_filename("CONSOLE"), "CONSOLE")
        self.assertEqual(title_to_filename("COM10"), "COM10")

    def test_length_is_capped(self) -> None:
        cleaned = title_to_filename("字" * 200)
        self.assertEqual(len(cleaned), FILENAME_MAX_CHARS)
        self.assertEqual(cleaned, "字" * FILENAME_MAX_CHARS)

    def test_cap_does_not_leave_a_trailing_dot(self) -> None:
        """正好切在句点上时要再削一次，否则会留下 Windows 不接受的结尾。"""
        cleaned = title_to_filename("a" * (FILENAME_MAX_CHARS - 1) + "..b")
        self.assertEqual(cleaned, "a" * (FILENAME_MAX_CHARS - 1))
        self.assertFalse(cleaned.endswith("."))


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class TitleRenameUiTests(unittest.TestCase):
    """界面：存盘时到底改不改名。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        # 名字与标题故意对不上，抄的就是用户文稿里真实的样子
        (folder / "2026-09-13.md").write_text(
            "# 今日日记\n\n今天把留白那件事收尾了。\n", encoding="utf-8")
        (folder / "2026-09-14.md").write_text(
            "# 今日日记\n\n另一天的日记，标题跟上面一模一样。\n", encoding="utf-8")
        (folder / "开发记录.md").write_text(
            "# 简记优化记录\n\n这一篇名字是用户自己起的。\n", encoding="utf-8")
        (folder / "无标题.md").write_text(
            "第一行不是标题。\n\n# 后面才有标题\n", encoding="utf-8")
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

    # ---- 助手 ----

    def _open(self, name: str) -> Path:
        path = self.folder / name
        self.app.open_file(path)
        self.root.update()
        return path

    def _set_first_line(self, text: str) -> None:
        """把第一行换成 text（模拟用户改标题）。"""
        self.app.editor.delete("1.0", "1.end")
        self.app.editor.insert("1.0", text)
        self.root.update()
        if not self.app.dirty:                  # 隐藏桌面上虚拟事件偶尔不投递，兜一手
            self.app._on_editor_modified()

    def _touch_body(self) -> None:
        """只动正文，不碰第一行。"""
        self.app.editor.insert("end-1c", "补一句正文。")
        self.root.update()
        if not self.app.dirty:
            self.app._on_editor_modified()

    def _flush(self) -> bool:
        """把待存的那一次立刻存掉（顶掉 700ms 的自动保存）。"""
        if self.app.save_job:
            self.root.after_cancel(self.app.save_job)
            self.app.save_job = None
        return self.app.save_now()

    def _names(self) -> set[str]:
        return {path.name for path in self.folder.glob("*.md")}

    # ---- 载入时的基准点 ----

    def test_the_title_is_captured_when_the_document_is_loaded(self) -> None:
        self._open("2026-09-13.md")
        self.assertEqual(self.app._title_at_load, "今日日记")

    def test_a_document_without_a_leading_heading_has_no_baseline(self) -> None:
        self._open("无标题.md")
        self.assertIsNone(self.app._title_at_load)

    # ---- 不该改名的时候 ----

    def test_editing_only_the_body_never_renames(self) -> None:
        """名字跟 H1 对不上是常态，用户自己起的名字不该被软件换掉。"""
        for name in ("2026-09-13.md", "开发记录.md"):
            with self.subTest(name=name):
                before = self._names()
                self._open(name)
                self._touch_body()
                self.assertTrue(self._flush())
                self.assertEqual(self.app.current_path.name, name)
                self.assertEqual(self._names(), before)

    def test_two_documents_sharing_one_title_both_keep_their_names(self) -> None:
        """两篇日记的 H1 都是 `# 今日日记`——照 H1 改名会当场撞车。"""
        for name in ("2026-09-13.md", "2026-09-14.md"):
            self._open(name)
            self._touch_body()
            self._flush()
        self.assertTrue((self.folder / "2026-09-13.md").is_file())
        self.assertTrue((self.folder / "2026-09-14.md").is_file())
        self.assertFalse((self.folder / "今日日记.md").exists())
        self.assertFalse((self.folder / "今日日记-2.md").exists())

    def test_clearing_the_title_keeps_the_file_name(self) -> None:
        """标题被删空时保住原名，总比把文件叫成 `.md` 强。"""
        self._open("2026-09-13.md")
        self._set_first_line("# ")
        self.assertTrue(self._flush())
        self.assertEqual(self.app.current_path.name, "2026-09-13.md")
        self.assertTrue(self.app.current_path.is_file())

    def test_a_heading_on_a_later_line_does_not_rename(self) -> None:
        self._open("无标题.md")
        self._touch_body()
        self._flush()
        self.assertEqual(self.app.current_path.name, "无标题.md")

    def test_a_title_matching_the_current_name_is_a_no_op(self) -> None:
        """`开发记录.md` 的 H1 改成 `# 开发记录`：名字已经对了，不必动盘。"""
        self._open("开发记录.md")
        self._set_first_line("# 开发记录")
        self.assertTrue(self._flush())
        self.assertEqual(self.app.current_path.name, "开发记录.md")
        self.assertEqual(self._names() & {"开发记录-2.md"}, set())

    # ---- 该改名的时候 ----

    def test_editing_the_title_renames_the_file(self) -> None:
        self._open("2026-09-13.md")
        self._set_first_line("# 今天想通了")
        self.assertTrue(self._flush())

        renamed = self.folder / "今天想通了.md"
        self.assertEqual(self.app.current_path, renamed)
        self.assertTrue(renamed.is_file())
        self.assertFalse((self.folder / "2026-09-13.md").exists(), "旧名字不该留着")
        self.assertIn("今天把留白那件事收尾了。", renamed.read_text(encoding="utf-8"),
                      "正文不能丢")
        self.assertIn("今天想通了", self.app.status_label.cget("text"))

    def test_the_renamed_document_stays_open_and_editable(self) -> None:
        self._open("2026-09-13.md")
        self._set_first_line("# 今天想通了")
        self._flush()

        self._touch_body()
        self.assertTrue(self._flush())
        self.assertIn("补一句正文。", (self.folder / "今天想通了.md").read_text(encoding="utf-8"))
        self.assertEqual(self.app.current_path.name, "今天想通了.md", "第二次保存不该再改名")

    def test_the_disk_signature_still_matches_after_a_rename(self) -> None:
        """改名不动 mtime / size / digest，所以不该被当成「外部改动」。"""
        self._open("2026-09-13.md")
        self._set_first_line("# 今天想通了")
        self._flush()
        self.assertEqual(self.app.disk_signature, storage.signature(self.app.current_path))

    def test_the_document_list_follows_the_rename(self) -> None:
        self._open("2026-09-13.md")
        self._set_first_line("# 今天想通了")
        self._flush()
        self.assertIn(self.app.current_path, self.app.file_paths)
        self.assertNotIn(self.folder / "2026-09-13.md", self.app.file_paths)

    def test_the_new_title_becomes_the_baseline(self) -> None:
        self._open("2026-09-13.md")
        self._set_first_line("# 今天想通了")
        self._flush()
        self.assertEqual(self.app._title_at_load, "今天想通了")

    def test_illegal_characters_are_sanitized(self) -> None:
        self._open("2026-09-13.md")
        self._set_first_line("# 第1章/第2节: 真的吗?")
        self._flush()
        self.assertEqual(self.app.current_path.name, "第1章／第2节： 真的吗？.md")
        self.assertTrue(self.app.current_path.is_file())

    def test_a_collision_gets_a_number_and_never_overwrites(self) -> None:
        self._open("2026-09-13.md")
        self._set_first_line("# 开发记录")            # 已经有 开发记录.md 了
        self._flush()
        self.assertEqual(self.app.current_path.name, "开发记录-2.md")
        self.assertEqual(
            (self.folder / "开发记录.md").read_text(encoding="utf-8"),
            "# 简记优化记录\n\n这一篇名字是用户自己起的。\n",
            "被撞的那一份必须原样保留",
        )

    def test_switching_documents_applies_the_pending_rename(self) -> None:
        """没等 700ms 自动保存就点开另一篇，改名也不能丢。"""
        self._open("2026-09-13.md")
        self._set_first_line("# 换名字了")
        self._open("开发记录.md")                     # open_file 里会 _finish_pending_edit
        self.assertTrue((self.folder / "换名字了.md").is_file())
        self.assertEqual(self.app.current_path.name, "开发记录.md")

    def test_a_new_document_takes_its_title_as_the_name(self) -> None:
        """用户最常用的那条路：新建 → 把占位标题改成自己的 → 文件名跟上。"""
        self.app.new_document()
        self.root.update()
        created = self.app.current_path
        self.assertIsNotNone(created)

        self._set_first_line("# 健身计划调整")
        self.assertTrue(self._flush())

        renamed = self.folder / "健身计划调整.md"
        self.assertTrue(renamed.is_file())
        self.assertFalse(created.exists())
        self.assertEqual(self.app.current_path, renamed)

    # ---- 改名失败 ----

    def test_a_failed_rename_keeps_the_file_name_and_says_so(self) -> None:
        """失败只提示一句、不抛异常：正文已经存进去了，没什么可丢的。"""
        self._open("2026-09-13.md")
        ghost = self.folder / "不存在的文件夹" / "2026-09-13.md"
        returned = self.app._follow_title(ghost, "# 今天想通了")
        self.assertEqual(returned, ghost, "失败要原样返回，不能假装改过")
        self.assertIn("改名失败", self.app.status_label.cget("text"))

    def test_a_failed_rename_does_not_try_the_same_title_again(self) -> None:
        """失败也算认下了这个标题，免得每次自动保存都再试一遍、再提示一遍。"""
        self._open("2026-09-13.md")
        ghost = self.folder / "不存在的文件夹" / "2026-09-13.md"
        self.app._follow_title(ghost, "# 今天想通了")
        self.assertEqual(self.app._title_at_load, "今天想通了")

    # ---- 改名牵连的两条路径 ----

    def test_move_after_a_rename_uses_the_new_path(self) -> None:
        """`move_document_to` 先 save_now（可能改名）再移动，别拿旧路径去移。"""
        target = self.folder / "归档"
        target.mkdir()
        self._open("2026-09-14.md")
        self._set_first_line("# 归档的那一天")
        self._touch_body()

        self.app.move_document_to(self.app.current_path, target)
        self.root.update()

        self.assertEqual(self.app.current_path, target / "归档的那一天.md")
        self.assertTrue((target / "归档的那一天.md").is_file())
        self.assertFalse((self.folder / "2026-09-14.md").exists())

    def test_delete_after_a_rename_uses_the_new_path(self) -> None:
        """`delete_document` 也是先 save_now 再回收，别拿旧路径去回收。"""
        self._open("无标题.md")
        self._set_first_line("# 临时名字")
        self._touch_body()

        self.app.delete_document(self.app.current_path)
        self.root.update()

        self.assertIsNone(self.app.current_path)
        self.assertFalse((self.folder / "临时名字.md").exists(), "应该已经进了回收站")
        self.assertFalse((self.folder / "无标题.md").exists())
        self.assertTrue(
            any("临时名字.md" in entry.original_relative
                for entry in storage.list_trash(self.folder)),
            "要能在回收站里找到它",
        )


if __name__ == "__main__":
    unittest.main()
