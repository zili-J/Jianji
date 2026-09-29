"""按文件名时间戳回填创建时间：命名识别、四道闸、改与还原。

这个工具**会动用户真实文稿的文件时间**，所以判据比功能本身更值得测：

- 认得出两种命名，且**认不出「只有日期」的戳**（那些数字常常是内容里的日期）；
- 四道闸一条都不能松：不往未来改、不改成晚于修改时间、没有改进就不动、
  重跑必须幂等；
- 改之前有备份，能从备份整体还原。
"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))
sys.path.insert(0, str(PROJECT / "tools"))

import backfill_created as bf  # noqa: E402
import storage  # noqa: E402

#: 一个稳定的「现在」，免得测试跟真实时钟较劲
NOW = datetime(2026, 9, 29, 8, 0, 0).timestamp()


def moment(*args) -> float:
    return datetime(*args).timestamp()


class StampFromNameTests(unittest.TestCase):
    """文件名 → 创建时刻。"""

    def test_the_new_note_convention_is_recognised(self) -> None:
        parsed = bf.stamp_from_name("新建-2026-09-13-161624")
        self.assertIsNotNone(parsed)
        stamp, label = parsed
        self.assertEqual(stamp, moment(2026, 9, 13, 16, 16, 24))
        self.assertEqual(label, "新建-")

    def test_it_is_found_inside_a_longer_name(self) -> None:
        """老文稿的名字前面常常还挂着一串备份前缀。"""
        parsed = bf.stamp_from_name("20260913-170127 新建-2026-09-13-161624")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[0], moment(2026, 9, 13, 16, 16, 24))

    def test_the_date_space_time_convention_is_recognised(self) -> None:
        parsed = bf.stamp_from_name("2026-09-09 222022")
        self.assertIsNotNone(parsed)
        stamp, label = parsed
        self.assertEqual(stamp, moment(2026, 9, 9, 22, 20, 22))
        self.assertEqual(label, "日期 时刻")

    def test_a_date_only_name_is_deliberately_ignored(self) -> None:
        """这些数字常常是**内容里提到的日期**，不是创建时间。猜错比不猜更糟。"""
        for name in ("今日日记20260914", "20260927广州草莓音乐节", "2026-09-13"):
            self.assertIsNone(bf.stamp_from_name(name), name)

    def test_a_name_without_any_digits_is_ignored(self) -> None:
        self.assertIsNone(bf.stamp_from_name("关于情感问题的思考"))

    def test_an_impossible_date_is_ignored(self) -> None:
        self.assertIsNone(bf.stamp_from_name("新建-2026-13-45-999999"))

    def test_the_backup_prefix_alone_is_not_taken(self) -> None:
        """`20260913-170127` 这种前缀是**备份时刻**，不能当创建时间。

        判据很硬：拿它回填会把创建时间改到修改时间**之后**，见
        `test_a_stamp_after_the_modification_time_is_refused`。
        """
        self.assertIsNone(bf.stamp_from_name("20260913-170127 我的日记"))


class PlanGuardTests(unittest.TestCase):
    """四道闸。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def _make(self, name: str, *, created: float, modified: float) -> Path:
        path = self.tmp / name
        path.write_text("正文", encoding="utf-8")
        self.assertTrue(storage.set_creation_time(path, created))
        import os
        os.utime(path, (modified, modified))
        return path

    def _row(self, path: Path) -> dict:
        rows = [r for r in bf.plan(self.tmp, now=NOW) if r["path"] == path]
        self.assertEqual(len(rows), 1, "这篇应当出现在预演里")
        return rows[0]

    def test_a_late_creation_time_is_fixed(self) -> None:
        """这正是要修的：创建时间被保存冲成了后面的时刻。"""
        path = self._make("2026-09-09 202027.md",
                          created=moment(2026, 9, 12, 8, 50, 13),
                          modified=moment(2026, 9, 12, 8, 50, 13))
        self.assertIsNone(self._row(path)["skip"])

    def test_a_stamp_after_the_modification_time_is_refused(self) -> None:
        """创建不可能晚于最后修改。撞上这条说明那个戳不是创建时间。"""
        path = self._make("新建-2026-09-25-112829.md",
                          created=moment(2026, 9, 10, 20, 56, 39),
                          modified=moment(2026, 9, 10, 20, 56, 39))
        self.assertEqual(self._row(path)["skip"], "晚于修改时间")

    def test_a_future_stamp_is_refused(self) -> None:
        path = self._make("新建-2027-01-01-000000.md",
                          created=moment(2026, 9, 1),
                          modified=moment(2026, 9, 1))
        self.assertEqual(self._row(path)["skip"], "戳在未来")

    def test_a_tiny_improvement_is_not_worth_touching_the_file(self) -> None:
        """差几秒是噪声。放过它同时也保证了重跑幂等。"""
        target = moment(2026, 9, 13, 16, 16, 24)
        path = self._make("新建-2026-09-13-161624.md",
                          created=target + bf.MIN_GAIN_SECONDS / 2,
                          modified=target + 3600)
        self.assertEqual(self._row(path)["skip"], "没有改进")

    def test_a_name_without_a_stamp_is_left_alone_entirely(self) -> None:
        self._make("关于情感问题的思考.md", created=moment(2026, 9, 28, 21, 26),
                   modified=moment(2026, 9, 28, 21, 26))
        self.assertEqual(bf.plan(self.tmp, now=NOW), [])

    def test_non_markdown_files_are_ignored(self) -> None:
        path = self.tmp / "2026-09-09 202027.txt"
        path.write_text("x", encoding="utf-8")
        self.assertEqual(bf.plan(self.tmp, now=NOW), [])


class ApplyAndRestoreTests(unittest.TestCase):
    """真的写、以及整体还原。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.backup = Path(tempfile.mkdtemp()) / "bak.json"
        self.path = self.tmp / "2026-09-09 202027.md"
        self.path.write_text("正文", encoding="utf-8")
        self.original = moment(2026, 9, 12, 8, 50, 13)
        storage.set_creation_time(self.path, self.original)

    def _plan(self) -> list[dict]:
        return [r for r in bf.plan(self.tmp, now=NOW) if r["skip"] is None]

    def test_it_writes_the_stamp_from_the_name(self) -> None:
        todo = self._plan()
        self.assertEqual(len(todo), 1)
        bf.apply(todo, self.backup)
        self.assertAlmostEqual(storage.creation_time(self.path),
                               moment(2026, 9, 9, 20, 20, 27), delta=1.0)

    def test_it_leaves_the_modification_time_alone(self) -> None:
        """只改创建时间。正文的「最后改过」不能被动。"""
        before = self.path.stat().st_mtime
        bf.apply(self._plan(), self.backup)
        self.assertAlmostEqual(self.path.stat().st_mtime, before, delta=1.0)

    def test_running_twice_changes_nothing_the_second_time(self) -> None:
        bf.apply(self._plan(), self.backup)
        self.assertEqual(self._plan(), [], "第二次不该还有要改的")

    def test_it_backs_up_the_old_value_before_touching_anything(self) -> None:
        import json

        bf.apply(self._plan(), self.backup)
        saved = json.loads(self.backup.read_text(encoding="utf-8"))
        self.assertAlmostEqual(saved[str(self.path)], self.original, delta=0.001)

    def test_the_backup_can_put_everything_back(self) -> None:
        bf.apply(self._plan(), self.backup)
        bf.restore(self.backup)
        self.assertAlmostEqual(storage.creation_time(self.path), self.original,
                               delta=1.0)

    def test_restoring_survives_a_deleted_file(self) -> None:
        bf.apply(self._plan(), self.backup)
        self.path.unlink()
        self.assertEqual(bf.restore(self.backup), 0)

    def test_a_dry_run_does_not_touch_the_file(self) -> None:
        """预演就是预演——没给 `--apply` 就一个字都不能写。"""
        bf.main([str(self.tmp)])
        self.assertAlmostEqual(storage.creation_time(self.path), self.original,
                               delta=1.0)

    def test_the_command_line_writes_only_with_apply(self) -> None:
        code = bf.main([str(self.tmp), "--apply", "--backup", str(self.backup)])
        self.assertEqual(code, 0)
        self.assertAlmostEqual(storage.creation_time(self.path),
                               moment(2026, 9, 9, 20, 20, 27), delta=1.0)

    def test_a_missing_folder_is_reported_not_crashed(self) -> None:
        self.assertEqual(bf.main([str(self.tmp / "没有这个文件夹")]), 1)


if __name__ == "__main__":
    unittest.main()
