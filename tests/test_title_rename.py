"""文件名跟随一级标题：标题识别、文件名清洗、以及「什么时候才该改名」。

规则只有一条：**第一行是一级标题，且它清出来的名字与现在的文件名不一样，就改。**
不看「相对载入有没有变过」——那条更严的规则会漏掉一种真实情况。用户文稿里躺着一批
`新建-2026-09-28-160555.md` 这样的名字，首行标题却是 `# All in One 软件思考`：它们
建在「文件名跟随标题」这个功能上线之前，名字与标题一直是错位的。用户再打开它们、
把标题重新敲一遍，按「相对载入变没变」判标题没变、文件名就不动，看起来就是
「我改了标题，文件名没跟着改」。

所以这一批里最要紧的是三件事：

  · **该改的时候真改**——只动正文也能把错位的名字扶正；
  · **不该改的时候别乱改**——第一行不是一级标题、标题清完为空，名字都一动不动；
  · **撞名绝不覆盖**——`2026-09-13.md` 与 `2026-09-14.md` 的 H1 都是 `# 今日日记`，
    先后改过来会落成 `今日日记.md` 与 `今日日记-2.md`，两份正文都在。

另外两条被改名牵连的路径也要盯着：`move_document_to` / `delete_document` 都是
「先 `save_now` 再拿手里的 path 去干活」，改名会让那个 path 指向一个不存在的名字。
"""
from __future__ import annotations

import os
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
        # 建在「文件名跟随标题」上线之前的那种名字：标题早就是自己的了，名字还是新建时的
        (folder / "新建-2026-09-28-160555.md").write_text(
            "# All in One 软件思考\n\n这一篇建在改名功能上线之前。\n", encoding="utf-8")
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

    # ---- 光翻看不改：一个字都不该落盘 ----

    def test_opening_a_document_never_touches_its_name(self) -> None:
        """改名只在**存盘**时发生。只是打开翻看，不写文件、也不动名字。"""
        before = self._names()
        self._open("开发记录.md")
        self._open("2026-09-13.md")
        self._open("新建-2026-09-28-160555.md")
        self.assertEqual(self._names(), before)

    # ---- 该改名的时候：名字与一级标题对不上 ----

    def test_editing_only_the_body_fixes_a_mismatched_name(self) -> None:
        """名字与 H1 对不上时，动一下正文就把名字扶正。

        这是「文件名＝一级标题」这条规则的代价，也正是要的效果：软件不再替用户
        判断「这个名字是不是你自己起的」。
        """
        self._open("开发记录.md")
        self._touch_body()
        self.assertTrue(self._flush())
        renamed = self.folder / "简记优化记录.md"
        self.assertEqual(self.app.current_path, renamed)
        self.assertTrue(renamed.is_file())
        self.assertFalse((self.folder / "开发记录.md").exists(), "旧名字不该留着")
        self.assertIn("这一篇名字是用户自己起的。", renamed.read_text(encoding="utf-8"))

    def test_an_old_document_gets_its_name_fixed_without_editing_the_title(self) -> None:
        """用户报的那一条：老文稿的名字与标题错位，把标题重新敲一遍也该扶正。

        `新建-2026-09-28-160555.md` 建在「文件名跟随标题」上线之前，首行标题
        `# All in One 软件思考` 一直没反映到文件名上。用户把标题重新敲一遍——
        内容一字未变——旧规则判「相对载入没变过」，文件名纹丝不动，于是看起来
        就是「我改了标题，文件名没跟着改」。这里把那条路径钉住。
        """
        self._open("新建-2026-09-28-160555.md")
        self._set_first_line("# All in One 软件思考")       # 与载入时一字不差
        self.assertTrue(self._flush())

        renamed = self.folder / "All in One 软件思考.md"
        self.assertEqual(self.app.current_path, renamed)
        self.assertFalse((self.folder / "新建-2026-09-28-160555.md").exists())
        self.assertIn("这一篇建在改名功能上线之前。", renamed.read_text(encoding="utf-8"))

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

    def test_two_documents_sharing_one_title_are_numbered_not_overwritten(self) -> None:
        """两篇日记的 H1 都是 `# 今日日记`：先后落成 今日日记.md 与 今日日记-2.md，
        两份正文都在，谁也没被覆盖。"""
        self._open("2026-09-13.md")
        self._touch_body()
        self.assertTrue(self._flush())
        self.assertEqual(self.app.current_path.name, "今日日记.md")

        self._open("2026-09-14.md")
        self._touch_body()
        self.assertTrue(self._flush())
        self.assertEqual(self.app.current_path.name, "今日日记-2.md")

        self.assertIn("今天把留白那件事收尾了。",
                      (self.folder / "今日日记.md").read_text(encoding="utf-8"))
        self.assertIn("另一天的日记，标题跟上面一模一样。",
                      (self.folder / "今日日记-2.md").read_text(encoding="utf-8"))

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
        # 只是打开、还没存盘，所以切过去的那一篇名字照旧（改名只发生在存盘时）
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

    # ---- 不该改名的时候 ----

    def test_clearing_the_title_keeps_the_file_name(self) -> None:
        """标题被删空时保住原名，总比把文件叫成 `.md` 强。"""
        self._open("2026-09-13.md")
        self._set_first_line("# ")
        self.assertTrue(self._flush())
        self.assertEqual(self.app.current_path.name, "2026-09-13.md")
        self.assertTrue(self.app.current_path.is_file())

    def test_a_heading_on_a_later_line_does_not_rename(self) -> None:
        """`# 后面才有标题` 在第三行——不算标题，名字不动。"""
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

    def test_a_title_whose_cleaned_name_is_empty_keeps_the_file_name(self) -> None:
        """标题只剩一串句点，清完为空——保住原名，别造出 `.md`。"""
        self._open("2026-09-13.md")
        self._set_first_line("# ...")
        self.assertTrue(self._flush())
        self.assertEqual(self.app.current_path.name, "2026-09-13.md")

    # ---- 改名失败 ----

    def test_a_failed_rename_keeps_the_file_name_and_says_so(self) -> None:
        """失败只提示一句、不抛异常：正文已经存进去了，没什么可丢的。"""
        self._open("2026-09-13.md")
        ghost = self.folder / "不存在的文件夹" / "2026-09-13.md"
        returned = self.app._follow_title(ghost, "# 今天想通了")
        self.assertEqual(returned, ghost, "失败要原样返回，不能假装改过")
        self.assertIn("改名失败", self.app.status_label.cget("text"))

    def test_a_failed_rename_does_not_try_the_same_title_again(self) -> None:
        """失败记一笔，同一个文件往同一个目标不再重试、也不再改写状态栏。"""
        self._open("2026-09-13.md")
        ghost = self.folder / "不存在的文件夹" / "2026-09-13.md"
        self.app._follow_title(ghost, "# 今天想通了")
        self.assertEqual(self.app._rename_failed_for,
                         (os.path.normcase(str(ghost)), "今天想通了"))

        self.app.status_label.configure(text="")
        self.assertEqual(self.app._follow_title(ghost, "# 今天想通了"), ghost)
        self.assertEqual(self.app.status_label.cget("text"), "", "第二次该悄悄放过")

    def test_a_failed_rename_still_allows_a_different_target(self) -> None:
        """换个标题（换个目标名字）要能重新试，别被上一次的失败连坐。"""
        self._open("2026-09-13.md")
        ghost = self.folder / "不存在的文件夹" / "2026-09-13.md"
        self.app._follow_title(ghost, "# 今天想通了")
        self.app._follow_title(ghost, "# 明天再想")
        self.assertEqual(self.app._rename_failed_for,
                         (os.path.normcase(str(ghost)), "明天再想"))

    def test_a_successful_rename_forgets_an_earlier_failure(self) -> None:
        self._open("2026-09-13.md")
        self.app._rename_failed_for = ("随便", "随便")
        self._set_first_line("# 今天想通了")
        self.assertTrue(self._flush())
        self.assertIsNone(self.app._rename_failed_for)

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
