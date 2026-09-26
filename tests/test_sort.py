"""文稿排序：按名称 / 按创建时间 / 按修改时间。

时间类一律「新的在前」——写作软件里最常想找刚写的那篇。排序方式存进
state.json，下次打开还是同一套顺序。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import storage  # noqa: E402
from main import SORT_LABELS, SORT_LABEL_BY_KEY, JianJiApp  # noqa: E402

import tkinter as tk  # noqa: E402

try:
    _probe = tk.Tk()
    _probe.destroy()
    HAS_DISPLAY = True
except tk.TclError:  # pragma: no cover
    HAS_DISPLAY = False


def _make(folder: Path, name: str, stamp: float) -> Path:
    """造一个文件并把创建/修改时间都设成 stamp。"""
    path = folder / name
    path.write_text(f"# {name}\n\n正文。\n", encoding="utf-8")
    os.utime(path, (stamp, stamp))
    return path


class ListMarkdownSortTests(unittest.TestCase):
    """`storage.list_markdown` 的三种排序。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.folder = self.tmp / "简记日记"
        self.folder.mkdir()
        # 名字顺序和修改时间顺序**故意不一致**，否则测不出区别
        now = time.time()
        self.old = _make(self.folder, "a-最老.md", now - 3000)
        self.mid = _make(self.folder, "b-中间.md", now - 2000)
        self.new = _make(self.folder, "c-最新.md", now - 1000)

    def test_default_sort_is_by_name(self) -> None:
        names = [p.name for p in storage.list_markdown(self.folder)]
        self.assertEqual(names, ["c-最新.md", "b-中间.md", "a-最老.md"])

    def test_sort_by_modified_is_newest_first(self) -> None:
        names = [p.name for p in storage.list_markdown(self.folder, sort_key="modified")]
        self.assertEqual(names, ["c-最新.md", "b-中间.md", "a-最老.md"])

    def test_sort_by_created_is_newest_first(self) -> None:
        """按创建时间排：新的在前。

        **不能写死成 `c, b, a`**：`os.utime` 改不了创建时间——Windows 上
        `st_ctime` 就是创建时间，只能由系统在写文件那一刻记下来，`_make` 里
        那三次 `utime` 对它毫无影响。三个文件的 ctime 是真实值、彼此只差几微秒，
        文件系统时间戳精度不够时就会相等，而相等时 `sorted` 保持原顺序，
        写死的期望值就会随机变红（本地就红过一次）。
        所以改成跟 `st_ctime` 的真实值对——测的是「按 ctime 倒序」这个契约。
        """
        names = [p.name for p in storage.list_markdown(self.folder, sort_key="created")]
        expected = sorted(
            (path.name for path in (self.old, self.mid, self.new)),
            key=lambda name: (self.folder / name).stat().st_ctime,
            reverse=True,
        )
        self.assertEqual(names, expected)
        self.assertEqual(sorted(names), sorted(expected))

    def test_created_and_modified_use_different_timestamps(self) -> None:
        """「按创建时间」取 `st_ctime`，「按修改时间」取 `st_mtime`——两者不能混。

        这条把上面那条测不到的「用哪个字段」直接钉住：把修改时间拨到很久以前，
        创建时间不动，于是两种排序必须给出不同结果。
        """
        long_ago = time.time() - 90000
        os.utime(self.new, (long_ago, long_ago))
        by_created = [p.name for p in storage.list_markdown(self.folder, sort_key="created")]
        by_modified = [p.name for p in storage.list_markdown(self.folder, sort_key="modified")]
        self.assertEqual(by_modified, ["b-中间.md", "a-最老.md", "c-最新.md"])
        self.assertNotEqual(by_created, by_modified,
                            "改了修改时间却不影响按创建时间的结果，说明用错了字段")
        self.assertEqual(storage._sort_key_of(self.new, "modified"), long_ago)
        self.assertNotEqual(storage._sort_key_of(self.new, "created"), long_ago)

    def test_modified_and_name_can_disagree(self) -> None:
        """把名字最新的那个改成最旧，两种排序就该给出不同结果。"""
        os.utime(self.new, (time.time() - 9000, time.time() - 9000))
        by_name = [p.name for p in storage.list_markdown(self.folder)]
        by_time = [p.name for p in storage.list_markdown(self.folder, sort_key="modified")]
        self.assertEqual(by_name, ["c-最新.md", "b-中间.md", "a-最老.md"])
        self.assertEqual(by_time, ["b-中间.md", "a-最老.md", "c-最新.md"])

    def test_unknown_sort_key_falls_back_to_name(self) -> None:
        names = [p.name for p in storage.list_markdown(self.folder, sort_key="乱写的")]
        self.assertEqual(names, ["c-最新.md", "b-中间.md", "a-最老.md"])

    def test_missing_folder_is_empty(self) -> None:
        self.assertEqual(storage.list_markdown(self.tmp / "没有这个文件夹"), [])

    def test_a_file_that_vanishes_mid_sort_does_not_break_the_list(self) -> None:
        """列表刚列出来文件就被移走（回收站、外部删除）时不能整个崩掉。"""
        items = list(self.folder.iterdir())
        missing = self.folder / "已经没了.md"
        items.append(missing)
        self.assertEqual(storage._sort_key_of(missing, "modified"), 0.0)


class SortSettingTests(unittest.TestCase):
    """排序方式要能存进 state.json 并读回来。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

    def tearDown(self) -> None:
        storage.default_state_path = self._real_state

    def test_default_is_by_name(self) -> None:
        self.assertEqual(storage.get_settings()["sort_key"], "name")

    def test_round_trip(self) -> None:
        storage.set_settings({"sort_key": "modified"})
        self.assertEqual(storage.get_settings()["sort_key"], "modified")
        storage.set_settings({"sort_key": "created"})
        self.assertEqual(storage.get_settings()["sort_key"], "created")

    def test_nonsense_value_is_ignored(self) -> None:
        storage.set_settings({"sort_key": "modified"})
        storage.set_settings({"sort_key": "按心情"})
        self.assertEqual(storage.get_settings()["sort_key"], "modified",
                         "乱七八糟的值不该把已存的排序方式冲掉")

    def test_settings_keys_match_the_labels(self) -> None:
        keys = {key for key, _label in SORT_LABELS}
        self.assertEqual(keys, set(storage.SORT_KEYS))


@unittest.skipUnless(HAS_DISPLAY, "没有图形环境")
class SortUiTests(unittest.TestCase):
    """卡片列的排序入口。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        now = time.time()
        self.first = _make(folder, "a-最老.md", now - 3000)
        _make(folder, "b-中间.md", now - 2000)
        self.last = _make(folder, "c-最新.md", now - 1000)
        storage.set_default_folder(folder)
        self.folder = folder

        self.root = tk.Tk()
        self.root.geometry("1180x760")
        self.app = JianJiApp(self.root)
        self.root.update()

    def tearDown(self) -> None:
        storage.default_state_path = self._real_state
        self.root.destroy()

    def _order(self) -> list[str]:
        return [p.name for p in self.app.file_paths]

    def test_label_shows_the_current_sort(self) -> None:
        self.assertEqual(self.app._sort_label_text(), "排序：按名称")

    def test_switch_to_modified_reorders_and_updates_the_label(self) -> None:
        self.app.set_sort_key("modified")
        self.root.update()
        self.assertEqual(self.app.settings["sort_key"], "modified")
        self.assertEqual(self.app._sort_label_text(), "排序：按修改时间")
        self.assertEqual(self._order(), ["c-最新.md", "b-中间.md", "a-最老.md"])

    def test_switch_to_created(self) -> None:
        self.app.set_sort_key("created")
        self.root.update()
        self.assertEqual(self.app._sort_label_text(), "排序：按创建时间")
        self.assertEqual(len(self._order()), 3)

    def test_choice_is_persisted(self) -> None:
        self.app.set_sort_key("created")
        self.assertEqual(storage.get_settings()["sort_key"], "created")

    def test_unknown_key_changes_nothing(self) -> None:
        self.app.set_sort_key("按心情")
        self.assertEqual(self.app.settings["sort_key"], "name")
        self.assertEqual(self.app._sort_label_text(), "排序：按名称")

    def test_current_document_stays_visible_after_resorting(self) -> None:
        """重排之后当前打开的那篇还要在视野里，不能被甩出屏幕。"""
        self.app.open_file(self.first)
        self.root.update()
        self.app.set_sort_key("modified")
        self.root.update()
        self.assertEqual(self.app.current_path, self.first)
        self.assertIn(self.first, self.app.file_paths)

    def test_sort_var_follows_the_setting(self) -> None:
        self.app.set_sort_key("created")
        self.assertEqual(self.app.sort_var.get(), "created")

    def test_menu_marks_the_current_choice(self) -> None:
        """菜单里当前那种要打勾，不然用户不知道现在按什么排。"""
        self.app.set_sort_key("modified")
        menu = self.app._sort_menu()
        self.assertEqual(menu.index("end"), len(SORT_LABELS) - 1)
        labels = [menu.entrycget(index, "label") for index in range(len(SORT_LABELS))]
        self.assertEqual(labels, [label for _key, label in SORT_LABELS])
        values = [menu.entrycget(index, "value") for index in range(len(SORT_LABELS))]
        self.assertEqual(values, [key for key, _label in SORT_LABELS])

    def test_trash_mode_is_not_resorted(self) -> None:
        """回收站有自己的顺序（按删除时间），换文稿排序不该动它。"""
        self.app.trash_mode = True
        self.app.file_paths = []
        self.app.set_sort_key("modified")
        self.assertEqual(self.app.file_paths, [])
        self.assertEqual(self.app.settings["sort_key"], "modified")

    def test_every_label_has_a_key(self) -> None:
        self.assertEqual(set(SORT_LABEL_BY_KEY), {key for key, _l in SORT_LABELS})


@unittest.skipUnless(HAS_DISPLAY, "没有图形环境")
class SortInContextMenuTests(unittest.TestCase):
    """排序入口在**右键菜单**里，而且**不占标题栏**。

    用户反馈：排序按钮原来挂在文稿列表标题右侧，把卡片区挤窄了一截，要求挪进
    预览栏的右键菜单并恢复宽度。所以这里既测「菜单里有排序」，也测「标题栏没占位」。
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        now = time.time()
        _make(folder, "a-最老.md", now - 3000)
        _make(folder, "b-中间.md", now - 2000)
        _make(folder, "c-最新.md", now - 1000)
        storage.set_default_folder(folder)
        self.folder = folder

        self.root = tk.Tk()
        self.root.geometry("1180x760")
        self.app = JianJiApp(self.root)
        self.root.update()

    def tearDown(self) -> None:
        storage.default_state_path = self._real_state
        self.root.destroy()

    def _labels(self, menu: tk.Menu) -> list[str]:
        """菜单里所有带文字项的标签，**跳过分隔线**（它没有 `-label` 选项）。"""
        return [menu.entrycget(index, "label")
                for index in range(menu.index("end") + 1)
                if menu.type(index) != "separator"]

    def _cascade(self, menu: tk.Menu, label: str) -> tk.Menu:
        for index in range(menu.index("end") + 1):
            if menu.type(index) == "cascade" and menu.entrycget(index, "label") == label:
                return self.root.nametowidget(menu.entrycget(index, "menu"))
        self.fail(f"菜单里没有「{label}」这一项：{self._labels(menu)}")

    def _sort_labels(self, menu: tk.Menu) -> list[str]:
        """找菜单里那个「排序：…」级联项，返回它子菜单里的选项。"""
        label = next((item for item in self._labels(menu) if item.startswith("排序：")), None)
        self.assertIsNotNone(label, f"菜单里没有排序入口：{self._labels(menu)}")
        return self._labels(self._cascade(menu, label))

    def test_doc_menu_has_the_sort_cascade(self) -> None:
        path = self.app.file_paths[0]
        labels = self._sort_labels(self.app._doc_menu(path))
        self.assertEqual(labels, [label for _key, label in SORT_LABELS])

    def test_sort_cascade_label_shows_the_current_choice(self) -> None:
        self.app.set_sort_key("created")
        menu = self.app._doc_menu(self.app.file_paths[0])
        self.assertIn("排序：按创建时间", self._labels(menu))

    def test_blank_area_menu_offers_sort_only(self) -> None:
        """点空白处也要能改排序——文稿少的时候卡片之外全是空白。"""
        labels = self._sort_labels(self.app._sort_only_menu())
        self.assertEqual(labels, [label for _key, label in SORT_LABELS])

    def test_picking_from_the_cascade_actually_resorts(self) -> None:
        menu = self.app._doc_menu(self.app.file_paths[0])
        label = next(item for item in self._labels(menu) if item.startswith("排序："))
        submenu = self._cascade(menu, label)
        index = [submenu.entrycget(i, "value")
                 for i in range(submenu.index("end") + 1)].index("modified")
        submenu.invoke(index)
        self.root.update()
        self.assertEqual(self.app.settings["sort_key"], "modified")
        self.assertEqual([p.name for p in self.app.file_paths],
                         ["c-最新.md", "b-中间.md", "a-最老.md"])

    def test_trash_mode_blank_area_has_no_sort_menu(self) -> None:
        """回收站有自己的顺序，空白处不该冒出排序入口。"""
        popped: list[tuple[int, int]] = []
        original = tk.Menu.tk_popup

        def fake_popup(self, x, y, *args, **kwargs):  # noqa: ANN001
            popped.append((x, y))

        self.app.trash_mode = True
        tk.Menu.tk_popup = fake_popup
        try:
            self.app._on_cards_right_click(_FakeEvent(px_mid(self.app), 4000))
        finally:
            tk.Menu.tk_popup = original
        self.assertEqual(popped, [], "回收站的空白处不该弹菜单")


class CardsPanelWidthTests(unittest.TestCase):
    """排序入口和滚动条都**不许**再占卡片区的宽度。

    这两条都是用户提的：排序按钮原来在标题栏占掉一整列（Tk 的 grid 是「一列宽 =
    该列所有行里最宽的那个」，于是下面卡片区被挤窄），滚动条又从右边切走一条。
    实测面板 459px 里卡片只剩 267px。现在卡片区应当拿到整幅面板宽度。
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        # 造够多的文稿把列表撑得比视口长，滚轮那条才滚得动
        for index in range(30):
            name = f"第{index:02d}篇.md"
            (folder / name).write_text(f"# {name}\n\n正文。\n", encoding="utf-8")
        storage.set_default_folder(folder)

        self.root = tk.Tk()
        self.root.geometry("1180x760")
        self.app = JianJiApp(self.root)
        self.root.update()

    def tearDown(self) -> None:
        storage.default_state_path = self._real_state
        self.root.destroy()

    def test_no_sort_button_anywhere_in_the_header(self) -> None:
        """标题那一行只该有标题——多一个控件就会挤掉卡片区的宽度。"""
        self.assertFalse(hasattr(self.app, "sort_button"),
                         "排序按钮该挪进右键菜单了，别留在标题栏")
        header = [w for w in self.app.cards_panel.winfo_children()
                  if w.winfo_manager() and w.grid_info().get("row") == 0]
        self.assertEqual(len(header), 1, f"标题行多了控件：{header}")
        self.assertIs(header[0], self.app.cards_title)

    def test_the_card_canvas_gets_the_whole_panel(self) -> None:
        panel = self.app.cards_panel.winfo_width()
        canvas = self.app.cards_canvas.winfo_width()
        self.assertGreater(panel, 100)
        self.assertEqual(canvas, panel,
                         f"卡片区 {canvas} 没拿到面板的整幅宽度 {panel}")

    def test_no_scrollbar_is_mapped(self) -> None:
        """预览栏不放滚动条（用户要求隐藏）。"""
        holder = self.app.cards_canvas.master
        mapped = [w for w in holder.winfo_children()
                  if isinstance(w, tk.Scrollbar) and w.winfo_manager()]
        self.assertEqual(mapped, [], f"预览栏还有滚动条：{mapped}")

    def test_the_wheel_still_scrolls(self) -> None:
        """没有滚动条也要能滚——滚轮是这一列唯一的滚动方式了。"""
        self.assertGreater(self.app.cards_canvas.bbox("all")[3],
                           self.app.cards_canvas.winfo_height(),
                           "内容没比视口长，滚轮那条测不出来")
        before = self.app.cards_canvas.yview()[0]
        self.app._on_cards_wheel(_FakeWheel(-120))
        self.root.update()
        self.assertGreater(self.app.cards_canvas.yview()[0], before,
                          "滚轮往下滚，列表应当往下走")


class _FakeEvent:
    """右键事件替身：只要 x/y 和 root 坐标。"""

    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y
        self.x_root = x
        self.y_root = y


class _FakeWheel:
    def __init__(self, delta: int) -> None:
        self.delta = delta


def px_mid(app) -> int:
    """横向正中间——一定落在卡片上，用来跟「空白处」区分。"""
    return max(1, app.cards_canvas.winfo_width() // 2)


if __name__ == "__main__":
    unittest.main()
