from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from app import storage
from app.storage import (
    DEFAULT_SETTINGS,
    ExternalChangeError,
    SETTING_RANGES,
    atomic_write_markdown,
    conflict_copy_path,
    default_journal_folder,
    get_default_folder,
    get_settings,
    list_markdown,
    load_state,
    new_note_path,
    read_markdown,
    replace_with_retry,
    save_state,
    set_default_folder,
    set_settings,
)


class StorageTests(unittest.TestCase):
    def test_utf8_markdown_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "日记.md"
            saved = atomic_write_markdown(path, "# 标题\n\n今天很好。**加粗**", None)
            text, loaded = read_markdown(path)
            self.assertEqual(text, "# 标题\n\n今天很好。**加粗**")
            self.assertEqual(saved, loaded)
            self.assertFalse(path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_detects_external_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "2026-09-09.md"
            original = atomic_write_markdown(path, "原文", None)
            path.write_text("外部修改", encoding="utf-8")
            with self.assertRaises(ExternalChangeError):
                atomic_write_markdown(path, "简记编辑", original)
            self.assertEqual(path.read_text(encoding="utf-8"), "外部修改")

    def test_markdown_listing_filters_and_sorts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "a.txt").write_text("x", encoding="utf-8")
            (folder / "2026-09-08.md").write_text("x", encoding="utf-8")
            (folder / "2026-09-09.MD").write_text("x", encoding="utf-8")
            self.assertEqual([path.name for path in list_markdown(folder)],
                             ["2026-09-09.MD", "2026-09-08.md"])

    def test_conflict_copy_name_is_plain_markdown(self) -> None:
        path = Path("日记.md")
        copy = conflict_copy_path(path, datetime(2026, 9, 9, 21, 30, 0))
        self.assertEqual(copy.name, "日记.简记冲突-20260909-213000.md")

    def test_state_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            save_state({"folder": "C:/日记", "last_file": "C:/日记/今天.md"}, path)
            self.assertEqual(load_state(path)["folder"], "C:/日记")

    def test_default_folder_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "state.json"
            real = Path(temp) / "日记"
            real.mkdir()
            set_default_folder(real, state_path)
            self.assertEqual(get_default_folder(state_path), real)
            self.assertIn("default_folder", load_state(state_path))

    def test_default_folder_ignores_missing_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "state.json"
            save_state({"default_folder": str(Path(temp) / "不存在")}, state_path)
            self.assertIsNone(get_default_folder(state_path))
            self.assertIsNone(get_default_folder(Path(temp) / "no_state.json"))

    def test_default_journal_folder_lives_in_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            import app.storage as storage
            storage.Path.home = lambda: Path(temp)  # type: ignore[assignment]
            docs = Path(temp) / "Documents"
            docs.mkdir()
            self.assertEqual(default_journal_folder(), docs / "简记日记")

    def test_settings_round_trip_and_clamp(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "state.json"
            self.assertEqual(get_settings(state_path), DEFAULT_SETTINGS)
            set_settings({"line_width": 70, "font_size": 20, "line_height": 24}, state_path)
            saved = get_settings(state_path)
            self.assertEqual((saved["line_width"], saved["font_size"], saved["line_height"]), (70, 20, 24))
            set_settings({"line_width": 999, "font_size": 1, "line_height": -5}, state_path)
            clamped = get_settings(state_path)
            self.assertEqual(clamped["line_width"], 80)
            self.assertEqual(clamped["font_size"], 12)
            self.assertEqual(clamped["line_height"], 12)

    def test_setting_ranges_match_requirement(self) -> None:
        self.assertEqual(SETTING_RANGES["line_width"], (30, 80))
        self.assertEqual(SETTING_RANGES["font_size"], (12, 24))
        self.assertEqual(SETTING_RANGES["line_height"], (12, 40))

    def test_line_height_accepts_values_up_to_40(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "state.json"
            set_settings({"line_height": 40}, state_path)
            self.assertEqual(get_settings(state_path)["line_height"], 40)
            set_settings({"line_height": 41}, state_path)
            self.assertEqual(get_settings(state_path)["line_height"], 40)

    def test_new_note_path_has_md_extension(self) -> None:
        folder = Path("C:/diary")
        path = new_note_path(folder, datetime(2026, 9, 10, 20, 55, 3))
        self.assertEqual(path.name, "新建-2026-09-10-205503.md")
        self.assertTrue(path.parent == folder)


class ReplaceWithRetryTests(unittest.TestCase):
    """`replace_with_retry`：Windows 上的**瞬时**占用要重试，真错误要立刻抛。

    这一组守的是「用户什么都没做错，却看到保存失败」那条路。触发它的是实测过的
    一次全量测试：`save_state()` 在 `%TEMP%` 下抛 `PermissionError [WinError 5]`，
    单独重跑同一个模块 10 项全绿——典型的杀毒/索引器瞬时占用。
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.source = self.tmp / "state.tmp"
        self.target = self.tmp / "state.json"
        self.source.write_text("新的", encoding="utf-8")
        self.target.write_text("旧的", encoding="utf-8")

    def tearDown(self) -> None:
        # `storage.os` 就是全局的 os 模块，补丁必须还原（见 _patch_replace）
        pass

    def _patch_replace(self, function):
        """临时换掉 `os.replace`，返回一个「还原」的可调用对象。

        **注意 `storage.os` 是全局 `os` 模块本身**，这个补丁会影响整个进程——
        所以只在极短的用例里用，并且一定在 `finally` 里还原。
        """
        original = storage.os.replace
        storage.os.replace = function

        def restore() -> None:
            storage.os.replace = original

        return restore

    def test_a_transient_lock_is_retried_until_it_succeeds(self) -> None:
        calls: list[int] = []
        real = storage.os.replace

        def flaky(source, target):
            calls.append(1)
            if len(calls) <= 2:                     # 前两次「别人正好拿着」
                raise PermissionError(13, "拒绝访问", None, 5)
            return real(source, target)

        restore = self._patch_replace(flaky)
        try:
            replace_with_retry(self.source, self.target)
        finally:
            restore()

        self.assertEqual(len(calls), 3, "前两次瞬时失败之后应当再试一次")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "新的")
        self.assertFalse(self.source.exists(), "成功之后临时文件不该留着")

    def test_a_sharing_violation_is_also_retried(self) -> None:
        """WinError 32/33（共享冲突、锁冲突）同样属于「等一下就没事」。"""
        for winerror in (32, 33):
            with self.subTest(winerror=winerror):
                calls: list[int] = []
                real = storage.os.replace

                def flaky(source, target, _winerror=winerror):
                    calls.append(1)
                    if len(calls) == 1:
                        raise PermissionError(13, "冲突", None, _winerror)
                    return real(source, target)

                self.source.write_text("新的", encoding="utf-8")
                restore = self._patch_replace(flaky)
                try:
                    replace_with_retry(self.source, self.target)
                finally:
                    restore()
                self.assertEqual(len(calls), 2)

    def test_a_real_error_is_not_retried(self) -> None:
        """真的是只读文件/磁盘满时要立刻抛出去，不能拖成六次无谓的等待。"""
        calls: list[int] = []

        def broken(source, target):
            calls.append(1)
            raise PermissionError(13, "介质受写保护", None, 19)   # ERROR_WRITE_PROTECT

        restore = self._patch_replace(broken)
        try:
            with self.assertRaises(PermissionError):
                replace_with_retry(self.source, self.target)
        finally:
            restore()

        self.assertEqual(len(calls), 1, "非瞬时错误只该试一次")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "旧的", "失败时不许动目标")

    def test_a_missing_source_is_not_retried(self) -> None:
        """源文件不存在（winerror 为 None）也不是瞬时错误。"""
        calls: list[int] = []
        real = storage.os.replace

        def counting(source, target):
            calls.append(1)
            return real(source, target)

        restore = self._patch_replace(counting)
        try:
            with self.assertRaises(OSError):
                replace_with_retry(self.tmp / "没有这个.tmp", self.target)
        finally:
            restore()
        self.assertEqual(len(calls), 1)

    def test_giving_up_raises_the_last_error(self) -> None:
        """一直失败时，最终要把错误抛出来，不能静默吞掉。"""
        calls: list[int] = []

        def always_locked(source, target):
            calls.append(1)
            raise PermissionError(13, "拒绝访问", None, 5)

        restore = self._patch_replace(always_locked)
        try:
            with self.assertRaises(PermissionError):
                replace_with_retry(self.source, self.target)
        finally:
            restore()
        self.assertEqual(len(calls), storage._REPLACE_ATTEMPTS)

    def test_the_document_save_path_uses_the_retry(self) -> None:
        """`atomic_write_markdown` 必须走重试那条路——用户的正文就在这条路上。"""
        import inspect

        source = inspect.getsource(atomic_write_markdown)
        self.assertIn("replace_with_retry", source)
        self.assertNotIn("os.replace", source)

    def test_save_state_uses_the_retry(self) -> None:
        """设置落盘同理：写设置失败会弹一个用户完全看不懂的错误。"""
        import inspect

        source = inspect.getsource(save_state)
        self.assertIn("replace_with_retry", source)
        self.assertNotIn("os.replace", source)


class CreationTimeTests(unittest.TestCase):
    """原子写盘不能把创建时间冲掉。

    保存走的是「写临时文件 + `os.replace`」。Windows 上那是**用源文件顶掉目标
    文件**，目标文件连创建时间也一起变成了临时文件的创建时间——也就是这一次
    保存的时刻。

    这不是纸上推演：修之前，用户文件夹里 12 篇文稿的 `st_ctime` **全部等于**
    `st_mtime`。后果是「创建日期」永远显示成修改日期，而且「按创建时间排序」
    与「按修改时间排序」结果完全一样。
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "a.md"

    def _backdate_creation(self, days: float = 30.0) -> float:
        """把创建时间挪到 N 天前，返回那个时间戳。

        比 `time.sleep()` 等时间自然流逝靠谱得多——NTFS 的时间精度虽然到 100ns，
        但「等一秒再写」在慢机器上照样可能撞上同一秒，判据会飘。
        """
        target = time.time() - days * 86400
        self.assertTrue(storage.set_creation_time(self.path, target),
                        "这台机器上 SetFileTime 应当可用")
        return target

    @unittest.skipUnless(os.name == "nt", "创建时间是 Windows 才有的概念")
    def test_a_second_save_keeps_the_creation_time(self) -> None:
        atomic_write_markdown(self.path, "第一版", None)
        target = self._backdate_creation()
        atomic_write_markdown(self.path, "第二版", storage.signature(self.path))
        self.assertAlmostEqual(self.path.stat().st_ctime, target, delta=1.0,
                               msg="保存把创建时间冲成了「现在」")

    @unittest.skipUnless(os.name == "nt", "创建时间是 Windows 才有的概念")
    def test_the_modification_time_still_advances(self) -> None:
        """保住创建时间不能顺手把修改时间也冻住——那就本末倒置了。"""
        atomic_write_markdown(self.path, "第一版", None)
        before = self.path.stat().st_mtime
        self._backdate_creation()
        os.utime(self.path, (before - 60, before - 60))
        atomic_write_markdown(self.path, "第二版", storage.signature(self.path))
        self.assertGreater(self.path.stat().st_mtime, before - 60 + 30)

    @unittest.skipUnless(os.name == "nt", "创建时间是 Windows 才有的概念")
    def test_the_two_timestamps_end_up_different(self) -> None:
        """这正是用户要看的东西：创建 ≠ 修改。修之前它们永远相等。"""
        atomic_write_markdown(self.path, "第一版", None)
        target = self._backdate_creation()
        atomic_write_markdown(self.path, "第二版", storage.signature(self.path))
        info = self.path.stat()
        self.assertAlmostEqual(info.st_ctime, target, delta=1.0)
        self.assertGreater(info.st_mtime - info.st_ctime, 86400)

    @unittest.skipUnless(os.name == "nt", "创建时间是 Windows 才有的概念")
    def test_renaming_keeps_the_creation_time(self) -> None:
        """改名走的是同一条 `replace_with_retry`，也不能把创建时间弄丢。"""
        atomic_write_markdown(self.path, "正文", None)
        target = self._backdate_creation()
        renamed = self.tmp / "b.md"
        replace_with_retry(self.path, renamed)
        self.assertAlmostEqual(renamed.stat().st_ctime, target, delta=1.0)

    def test_a_brand_new_file_gets_the_current_time(self) -> None:
        """新建的文稿没有「旧创建时间」可保，就应该是现在——别被写成 0。"""
        before = time.time()
        atomic_write_markdown(self.path, "正文", None)
        self.assertAlmostEqual(storage.creation_time(self.path), before, delta=30)

    def test_creation_time_of_a_missing_file_is_none(self) -> None:
        self.assertIsNone(storage.creation_time(self.tmp / "没有这个文件.md"))

    def test_setting_the_time_on_a_missing_file_fails_quietly(self) -> None:
        """best-effort：拿不到句柄就返回 False，**不能抛异常打断保存**。"""
        self.assertFalse(storage.set_creation_time(self.tmp / "没有这个文件.md", 0.0))

    def test_the_save_path_does_not_swallow_the_preservation(self) -> None:
        """守住「以后别把这段删了」——删了功能会静默退化成假的。"""
        import inspect

        source = inspect.getsource(atomic_write_markdown)
        self.assertIn("creation_time", source)
        self.assertIn("set_creation_time", source)


if __name__ == "__main__":
    unittest.main()
