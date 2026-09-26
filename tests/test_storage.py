from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
