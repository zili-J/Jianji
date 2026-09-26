"""性能相关行为的回归测试。

卡顿的根源不是 Python 逻辑，而是**跨语言调用**：每问一次 Tk
「这是第几行 / 这一行的显示范围是多少」都要十几微秒，一次重画问上几百次
就是几十毫秒。所以这里守住四件事：

1. 光标换行走轻量刷新，结果必须和整篇重解析**一模一样**（正确性底线）；
2. 换光标不该重解析文档，滚动时可见行只走一遍、且只按逻辑行问 Tk；
3. 几何没变时不重画，几何变了必须重画（不能为了省事画错）；
4. 几何没变时装饰与表格框连图元都不重建（不是「画得一样」，是「没画」）。
"""
from __future__ import annotations

import re
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))     # 让 `import _pinning` 找得到

import storage  # noqa: E402
from _pinning import pin_screen_share  # noqa: E402
from main import EDITOR_SIDE_RATIO  # noqa: E402

import tkinter as tk  # noqa: E402

try:
    _probe = tk.Tk()
    _probe.destroy()
    HAS_DISPLAY = True
except tk.TclError:  # pragma: no cover
    HAS_DISPLAY = False


class _CountingTk:
    """tkapp 的只读代理：转发一切，只把 `call` 记一笔。

    `_tkinter.tkapp.call` 是只读属性，打不了补丁；但每个控件的 `tk`
    属性是普通属性（tkinter 里就是 `self.tk = master.tk`），换掉它就行
    —— Tkinter 的每个方法最终都落到 `self.tk.call`。
    """

    __slots__ = ("_tk", "_counter", "_watch")

    def __init__(self, tkapp, counter, watch):
        self._tk = tkapp
        self._counter = counter
        self._watch = watch

    def call(self, *args):
        watch = self._watch
        # 命令名可能在第一位（'place' …）也可能在第二位（控件路径 + 'create' …）
        if watch is None or any(arg in watch for arg in args[:2]):
            self._counter.count += 1
        return self._tk.call(*args)

    def __getattr__(self, name):
        return getattr(self._tk, name)


class TclCallCounter:
    """统计一段代码里 Tk 跨语言调用的次数。

    用次数而不是耗时做断言：耗时随机器抖动，次数是稳定的结构指标，
    也正是卡顿的直接来源。

    `watch` 给定时只统计指定的 Tcl 命令，用来把「读」和「真的重建了东西」
    区分开——判断「有没有白画一遍」时只关心改动型命令。
    """

    def __init__(self, watch: set[str] | None = None) -> None:
        self.watch = watch
        self.count = 0
        self._saved: list[tuple[tk.Misc, object]] = []

    def __enter__(self) -> "TclCallCounter":
        root = tk._default_root
        stack = [root]
        widgets = [root]
        while stack:
            for child in stack.pop().winfo_children():
                widgets.append(child)
                stack.append(child)
        for widget in widgets:
            self._saved.append((widget, widget.tk))
            widget.tk = _CountingTk(widget.tk, self, self.watch)
        self.count = 0            # 装代理本身也走了几次 Tcl，别算进来
        return self

    def __exit__(self, *_exc) -> bool:
        for widget, tkapp in self._saved:
            widget.tk = tkapp
        self._saved.clear()
        return False


# 会改动画面的 Tcl 命令：几何没变时这些一个都不该出现
MUTATING = {"create", "delete", "place", "forget", "coords", "itemconfigure",
            "configure", "raise", "lower"}


@contextmanager
def spy_method(obj, name: str):
    """替换对象上的某个方法，产出调用记录，退出时还原。"""
    calls = []
    original = getattr(obj, name)

    def counted(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    setattr(obj, name, counted)
    try:
        yield calls
    finally:
        setattr(obj, name, original)


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class PerformanceTests(unittest.TestCase):
    DOC = (
        "# 性能测试文档\n\n"
        "## 第一节\n\n"
        "这是一段普通的正文，没有 Markdown 记号，用来代表最常见的行。\n\n"
        "- 列表项一\n"
        "- 列表项二\n"
        "  - 嵌套项\n"
        "- [ ] 待办\n"
        "- [x] 已完成\n\n"
        "1. 有序一\n"
        "2. 有序二\n\n"
        "> 引用第一行\n"
        "> 引用第二行\n\n"
        "| 项目 | 数量 |\n"
        "| --- | ---: |\n"
        "| 苹果 | 3 |\n"
        "| 香蕉 | 5 |\n\n"
        "带 **粗体**、*斜体*、`代码` 和 [链接](https://example.com) 的一行。\n\n"
        "```python\nprint('hi')\n```\n\n"
        "---\n\n"
        "### 结尾\n\n"
        "最后一段。\n"
    )

    # 光标换行会动到的标签：轻量刷新与整篇重解析必须在这几个上完全一致
    CURSOR_TAGS = ("syntax", "syntax_current", "table_pipe")

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        self.doc = folder / "性能.md"
        self.doc.write_text(self.DOC, encoding="utf-8")
        storage.set_default_folder(folder)

        from main import JianJiApp

        self.root = tk.Tk()
        self.root.geometry("1180x760")
        self.app = JianJiApp(self.root)
        self.root.update()
        self.app.open_file(self.doc)
        self.root.update()
        # 等编辑区真的拿到高度再开跑。`_visible_lines()` 正是按 `winfo_height()`
        # 算可见范围的（`max(1, height)`），窗口刚映射时高度可能还是 1，那就只看得到
        # 第 1 行。单独跑这个模块时窗口映射得快，全量跑时负载一高就会偶发——
        # 曾在 test_wrapped_quote_gets_continuation_lines 上莫名 StopIteration。
        self._wait_for_editor_height()
        self.app.editor.mark_set("insert", "1.0")
        self.app._apply_markdown_styles()
        self.root.update()

    def _wait_for_editor_height(self, minimum: int = 40) -> None:
        """等到编辑区有真实高度为止（最多约 1 秒）。

        等不到就直接报出来，别让它变成一个没有信息的 StopIteration。
        """
        for _ in range(50):
            if self.app.editor.winfo_height() >= minimum:
                return
            self.root.update()
            time.sleep(0.02)
        self.fail(f"编辑区始终没拿到高度（{self.app.editor.winfo_height()} px），"
                  "依赖「可见行」的断言做不了（窗口没被正常映射？）")

    def tearDown(self) -> None:
        storage.default_state_path = self._real_state
        self.root.destroy()

    # ---- 辅助 ----

    def _line_count(self) -> int:
        """文档的内容行数（不含 Tk 自己补出来的空收尾行）。"""
        text = self.app.editor.get("1.0", "end-1c").rstrip("\r\n")
        return len(re.split(r"\r\n|\r|\n", text))

    def _mark_state(self) -> dict[str, set[tuple[str, str]]]:
        state: dict[str, set[tuple[str, str]]] = {}
        for tag in self.CURSOR_TAGS:
            raw = self.app.editor.tag_ranges(tag)
            state[tag] = {(str(raw[i]), str(raw[i + 1])) for i in range(0, len(raw), 2)}
        return state

    def _in_tag(self, tag: str, index: str) -> bool:
        raw = self.app.editor.tag_ranges(tag)
        for i in range(0, len(raw), 2):
            if (self.app.editor.compare(index, ">=", raw[i])
                    and self.app.editor.compare(index, "<", raw[i + 1])):
                return True
        return False

    def _bullet_canvas(self) -> tk.Canvas:
        return next(c for c in self.app._decor_pool
                    if c.decor_spec is not None and c.decor_spec[0] == "bullet")

    def _first_bullet_x(self) -> int:
        canvas = self._bullet_canvas()
        return canvas.winfo_x() + canvas.winfo_width()

    # ---- 1. 轻量刷新必须与整篇重解析等价 ----

    def test_light_refresh_equals_full_reparse_on_every_line(self) -> None:
        """逐行把光标挪过去，轻量刷新的结果要和整篇重解析完全一样。

        这是整个优化里最容易出错的地方：只换两行的标签，万一漏了哪一行、
        或者光标行判断错了，记号就会显示/隐藏错乱。
        """
        total = self._line_count()
        self.assertGreater(total, 20, "这份文档要够长，才测得出换行的影响")
        for target in range(1, total + 1):
            self.app.editor.mark_set("insert", f"{target}.0")
            self.app._refresh_cursor_line()
            self.root.update()
            light = self._mark_state()

            self.app._apply_markdown_styles()
            self.root.update()
            full = self._mark_state()

            self.assertEqual(light, full, f"光标停在第 {target} 行时两种路径结果不一致")

    def test_light_refresh_only_touches_the_two_lines(self) -> None:
        """轻量刷新只该动「离开的那行」和「新到的那行」的标签。

        断言的是「哪些行的标签变了」，不是「调用了多少次」——前者才是
        优化真正要保证的不变量。
        """
        self.app.editor.mark_set("insert", "3.0")
        self.app._refresh_cursor_line()
        self.root.update()
        before = self._mark_state()

        self.app.editor.mark_set("insert", "7.0")       # 第 7 行是列表项，有记号
        self.app._refresh_cursor_line()
        after = self._mark_state()

        for tag in self.CURSOR_TAGS:
            for start, _end in before[tag] ^ after[tag]:
                line = int(start.split(".")[0])
                self.assertIn(line, (3, 7),
                              f"{tag} 动了不该动的第 {line} 行")

    def test_light_refresh_does_not_reparse_the_whole_document(self) -> None:
        """文本没变时换光标，一行都不该重解析。

        轻量刷新的价值全在这里：整篇重解析在长文档上要几十毫秒，
        而换光标每次敲键都发生。
        """
        import main

        original = main.parse_block
        calls = {"n": 0}

        def counted(line):
            calls["n"] += 1
            return original(line)

        self.app.editor.mark_set("insert", "3.0")
        self.app._refresh_cursor_line()
        self.root.update()

        main.parse_block = counted
        try:
            self.app.editor.mark_set("insert", "7.0")
            self.app._refresh_cursor_line()
            light = calls["n"]
            calls["n"] = 0
            self.app._apply_markdown_styles()
            full = calls["n"]
        finally:
            main.parse_block = original

        self.assertEqual(light, 0, "文本没变，换光标不该重解析任何一行")
        self.assertGreater(full, 20,
                           "整篇重解析才是逐行走的（这条用来证明上面那条有意义）")

    def test_cursor_line_shows_source_after_light_refresh(self) -> None:
        """轻量刷新后，光标行的记号要显示出来、别的行要藏起来。

        标题的 `# ` 现在和列表记号走同一条「露源码 + 补偿宽度」的路（露出来会把正文
        顶右、卡在折行边界上的标题会多折一行），所以露出来时挂的是 `syntax_marker`，
        不是 `syntax_current`。判据是「露出来了」，不绑具体标签名。
        """
        heading = 1
        self.app.editor.mark_set("insert", f"{heading}.0")
        self.app._refresh_cursor_line()
        self.root.update()
        self.assertTrue(
            self._in_tag("syntax_marker", f"{heading}.0")
            or self._in_tag("syntax_current", f"{heading}.0"),
            "光标在标题行，`# ` 应显示")
        self.assertFalse(self._in_tag("syntax_current", "3.0"),
                         "别的行不该显示源码")

    # ---- 2. 可见行只走一遍 ----

    def test_margins_walk_the_visible_lines_once(self) -> None:
        """一次重画只该走一遍可见行。

        以前行号、徽标、装饰、表格四个模块各走一遍，每次滚动问 Tk 几百次，
        正是滚动卡顿的主因。
        """
        calls = {"n": 0}
        original = self.app._visible_lines

        def counted():
            calls["n"] += 1
            return original()

        self.app._visible_lines = counted
        try:
            self.app._redraw_margins()
        finally:
            self.app._visible_lines = original
        self.assertEqual(calls["n"], 1, "可见行应当只走一遍再分发给四个模块")

    def test_visible_lines_walk_logical_lines_only(self) -> None:
        """可见行只该按**逻辑行**问 Tk，不能按显示行逐条推算。

        旧实现用 index("N.0 + 1 display lines") 一条条挪显示行，
        一次重画要跑几十次，是滚动时最贵的一步。
        """
        warm = self.app._visible_lines()
        logical = {number for number, _offset, _info in warm}
        wrapped = {number for number in logical
                   if self.app._block_of(number).kind in ("quote", "table")}

        with spy_method(self.app.editor, "dlineinfo") as dline, \
                spy_method(self.app.editor, "index") as index:
            self.app._visible_lines()

        self.assertLessEqual(len(dline), len(logical) + 2,
                             "每个可见的逻辑行最多问一次 dlineinfo")
        display_queries = [args for args in index if "display lines" in str(args[0])]
        self.assertLessEqual(
            len(display_queries), len(wrapped) + 2,
            "只有引用和表格需要展开续行，别的行不该按显示行推算",
        )

    def test_visible_lines_are_cheap(self) -> None:
        with TclCallCounter() as counter:
            self.app._visible_lines()
        self.assertLess(counter.count, 70,
                        f"走一遍可见行不该问 Tk {counter.count} 次")

    # ---- 3. 可见行的边界 ----

    def test_trailing_blank_line_is_excluded(self) -> None:
        """文件以换行结尾时 Tk 会多留一行空的收尾行，它不该出现也不该编号。"""
        self.app.editor.see("end")
        self.root.update()
        visible = self.app._visible_lines()
        numbers = [number for number, _offset, _info in visible]
        self.assertTrue(numbers)
        self.assertEqual(max(numbers), self._line_count(),
                         "最后一行应当是正文的末行，不是 Tk 补出来的空行")

    def test_first_display_line_has_offset_zero(self) -> None:
        visible = self.app._visible_lines()
        self.assertTrue(visible)
        for number, offset, _info in visible:
            self.assertEqual(offset, 0, f"第 {number} 行的第一显示行偏移应为 0")

    def test_wrapped_quote_gets_continuation_lines(self) -> None:
        """引用要拿到换行的续行，竖条才能连成一条。"""
        self.app.editor.mark_set("insert", "1.0")
        self.app.editor.insert("4.0", "> " + "很长的引用内容，" * 30 + "\n")
        self.app._apply_markdown_styles()
        self.root.update()
        visible = self.app._visible_lines()
        quote_line = next(
            (number for number, _o, _i in visible
             if self.app.editor.get(f"{number}.0", f"{number}.4").startswith("> 很长")),
            None,
        )
        # 别用裸 next()：找不到时只会抛一个没有信息的 StopIteration。
        self.assertIsNotNone(
            quote_line,
            f"可见行里找不到那条引用。可见行 {[n for n, _o, _i in visible]}，"
            f"视口 {self.app.editor.yview()}，"
            f"编辑区高 {self.app.editor.winfo_height()} px，"
            f"第 4 行内容 {self.app.editor.get('4.0', '4.8')!r}",
        )
        same_line = [entry for entry in visible if entry[0] == quote_line]
        self.assertGreater(len(same_line), 1, "换行的引用应展开出续行")

    # ---- 4. 几何缓存 ----

    def test_unchanged_geometry_skips_the_redraw(self) -> None:
        """留白、高度、视口起点都没变时，不该再画一遍。"""
        self.app._apply_editor_geometry()
        calls = {"n": 0}
        original = self.app._redraw_margins

        def counted():
            calls["n"] += 1
            return original()

        self.app._redraw_margins = counted
        try:
            self.app._apply_editor_geometry()
            self.app._apply_editor_geometry()
        finally:
            self.app._redraw_margins = original
        self.assertEqual(calls["n"], 0, "几何没变就不该重画")

    def test_changed_geometry_does_redraw(self) -> None:
        """视口变了就必须重画，不能因为缓存把画面留在旧位置。"""
        self.app._apply_editor_geometry()
        self.app.editor.yview_moveto(0.8)       # 视口立刻生效，不用等事件循环
        self.assertNotEqual(self.app._margin_state(), self.app._margin_signature,
                            "视口变了，绘制依据就该对不上")

        calls = {"n": 0}
        original = self.app._redraw_margins

        def counted():
            calls["n"] += 1
            return original()

        self.app._redraw_margins = counted
        try:
            self.app._apply_editor_geometry()
        finally:
            self.app._redraw_margins = original
        self.assertEqual(calls["n"], 1, "视口变了应当重画一次")

    def test_line_width_change_forces_redraw(self) -> None:
        """改内容行宽会挪留白，装饰必须跟着动。"""
        # 默认窗口下编辑区通常只占屏幕 40% 上下，落在「5% 地板」那一档——那一档里
        # 正文块宽 = 编辑区宽 − 10%·屏幕，**和 line_width 无关**，这条就测不出来。
        # 钉住屏幕宽把用例挪到「正文块宽 = 行宽 × 屏幕宽」那一档（见 _pinning）。
        pin_screen_share(self.app, 0.9)
        self.app._on_setting_changed("line_width", "30")
        self.root.update()
        self.app._apply_editor_geometry()
        self.root.update()
        before = self._first_bullet_x()
        self.app._on_setting_changed("line_width", "60")
        self.root.update()
        self.app._apply_editor_geometry()
        self.root.update()
        after = self._first_bullet_x()
        self.assertLess(after, before, "正文变宽、留白变小，装饰应整体左移")

    def test_geometry_requests_are_coalesced(self) -> None:
        """连续到来的几何变化要合并成一次，别每次都排一遍。"""
        calls = {"n": 0}
        original = self.app._apply_editor_geometry

        def counted():
            calls["n"] += 1
            return original()

        self.app._apply_editor_geometry = counted
        try:
            for _ in range(5):
                self.app._request_geometry()
            self.root.update()          # 让 after_idle 跑掉
        finally:
            self.app._apply_editor_geometry = original
        self.assertEqual(calls["n"], 1, "五次请求应当合并成一次重排")

    def test_focus_toggle_still_renders(self) -> None:
        """专注模式开关之后，装饰与徽标要画在新的位置上。

        专注模式藏起左栏，正文区域变宽；正文是限宽居中的，所以留白按比例
        变大，装饰跟着右移——不是「整体左移」，别想当然。
        """
        before = self._first_bullet_x()
        self.app.toggle_focus()
        self.root.update()
        self.app._apply_editor_geometry()
        self.root.update()
        after = self._first_bullet_x()
        self.assertGreater(after, before, "正文区域变宽，限宽居中的留白应变大")
        self.app.toggle_focus()
        self.root.update()
        self.app._apply_editor_geometry()
        self.root.update()
        self.assertAlmostEqual(self._first_bullet_x(), before, delta=2,
                               msg="退出专注模式后装饰应回到原位")

    def test_padding_follows_width_before_the_idle_phase(self) -> None:
        """宽度一变就要同步算好留白，不能等空闲阶段。

        Tk 的重折行排在空闲阶段的前头：留白晚一步，正文就先按旧留白折一遍、
        再按新留白折一遍，长文档下白花几十毫秒。
        """
        from main import px

        self.app.editor.configure(padx=px(24))      # 故意改错，看能不能纠回来
        self.app._current_pad = -1
        self.app._on_editor_configure()             # 模拟宽度变化时的 <Configure>

        width = self.app.editor.winfo_width()
        column = self.app.editor.master.winfo_width()
        screen = self.app._screen_width()
        ratio = self.app.settings["line_width"] / 100.0
        # 正文块目标宽 = min(行宽 × 屏幕宽, 编辑区宽 − 2×5%×屏幕宽)
        target = max(0, min(int(ratio * screen),
                            column - int(2 * EDITOR_SIDE_RATIO * screen)))
        expected = max(0, (width - target) // 2)
        self.assertEqual(self.app._current_pad, expected,
                         "留白应当同步跟上宽度")
        self.assertEqual(int(self.app.editor.cget("padx")), expected,
                         "留白要真的落到控件上，不能只记在变量里")
        # padx 对称 → 两侧留白合计是偶数，目标宽和控件宽奇偶性不同时只能差 1 像素
        self.assertLessEqual(abs((width - 2 * expected) - target), 1,
                             "正文块宽应当等于目标宽（允许 1 像素的取整差）")

    def test_overlay_canvases_sit_above_the_badge_canvas(self) -> None:
        """装饰与表格框必须压在标题徽标画布上面。

        以前靠每帧 lift 保证（一次重画给几十块画布各抬一次，光这一项就占
        整次重画的四成），现在改成靠创建顺序：徽标画布之后建的画布天然在它
        上面。这条守住这个前提，别哪天把创建顺序改了。
        """
        self.app._redraw_margins()
        self.root.update()
        parent = self.app.heading_labels.master
        order = [str(widget) for widget in parent.winfo_children()]
        badge = order.index(str(self.app.heading_labels))
        self.assertGreater(badge, order.index(str(self.app.editor)),
                           "徽标画布要压在正文上面才看得见")
        for pool in (self.app._decor_pool, self.app._table_pool):
            for canvas in pool:
                if canvas.winfo_manager():
                    self.assertGreater(
                        order.index(str(canvas)), badge,
                        "叠放顺序错了：这块画布会被徽标画布的整高白底盖住")

    def test_no_window_is_lifted_during_a_redraw(self) -> None:
        """重画时不该再抬窗口。

        `lift` 是重排窗口，比一次普通调用贵两个数量级；装饰画布靠创建顺序
        就已经在上面了，逐帧抬一次纯属白花。
        """
        self.app._redraw_margins()
        self.root.update()
        lifts = []
        original = tk.Misc.lift

        def counted(widget, aboveThis=None):
            lifts.append(str(widget))
            if aboveThis is None:
                return original(widget)
            return original(widget, aboveThis)

        tk.Misc.lift = counted
        try:
            self.app._redraw_margins()
        finally:
            tk.Misc.lift = original
        self.assertEqual(lifts, [], "重画不该抬窗口")

    # ---- 5. 装饰与表格框的几何缓存 ----

    def test_decoration_is_not_rebuilt_when_nothing_moved(self) -> None:
        """光标在同一屏内移动时装饰一动不动，就不该 delete/create 一遍。

        delete/place/create/lift 每个都是跨语言调用，省下来就是流畅度。
        """
        self.app._redraw_decorations()
        self.root.update()
        canvas = self._bullet_canvas()
        items = tuple(canvas.find_all())
        self.assertTrue(items, "装饰画布上应当有图元")

        with TclCallCounter(watch=MUTATING) as counter:
            self.app._redraw_decorations()
        self.assertEqual(counter.count, 0,
                         f"什么都没动却重建了 {counter.count} 次图元/位置")
        self.assertEqual(tuple(canvas.find_all()), items, "图元不该被重建")

    def test_decoration_moves_when_scrolling(self) -> None:
        """滚动时装饰的几何真的变了，必须重画到新位置。"""
        self.app._redraw_decorations()
        self.root.update()
        before = self._bullet_canvas().winfo_y()
        self.app.editor.yview_scroll(2, "units")
        self.root.update()
        self.app._redraw_decorations()
        self.root.update()
        self.assertNotEqual(self._bullet_canvas().winfo_y(), before,
                            "滚动后装饰应当换位置")

    def test_table_frame_is_not_rebuilt_when_nothing_moved(self) -> None:
        self.app._redraw_table_frames()
        self.root.update()
        canvases = [c for c in self.app._table_pool if c.winfo_manager()]
        self.assertTrue(canvases, "文档里有表格，应当画出边框")
        items = {id(c): tuple(c.find_all()) for c in canvases}

        with TclCallCounter(watch=MUTATING) as counter:
            self.app._redraw_table_frames()
        self.assertEqual(counter.count, 0,
                         f"什么都没动却重建了 {counter.count} 次边框")
        for canvas in canvases:
            self.assertEqual(tuple(canvas.find_all()), items[id(canvas)],
                             "边框图元不该被重建")

    def test_table_frame_moves_when_scrolling(self) -> None:
        self.app._redraw_table_frames()
        self.root.update()
        before = [c.winfo_y() for c in self.app._table_pool if c.winfo_manager()]
        self.app.editor.yview_scroll(2, "units")
        self.root.update()
        self.app._redraw_table_frames()
        self.root.update()
        after = [c.winfo_y() for c in self.app._table_pool if c.winfo_manager()]
        self.assertNotEqual(after, before, "滚动后表格框应当换位置")


if __name__ == "__main__":
    unittest.main()
