"""文字转音频导出：「导出音频」菜单项、念出来的文本、后台合成流程。

用户要求：「新增将文字转换成音频导出功能。功能出现在点击文档预览右键菜单。」

这一组用例守三件事：

① **念出来的文本和长图是同一套解析**——`speakable_text()` 复用 `build_export_rows`，
   Markdown 记号一个都不许漏出来（漏了会真的被念成「井号」「星号」）；
② **导出不阻塞界面**——合成很慢（每秒约 3.5 个字），必须走后台线程；
③ 没有语音引擎 / 没有内容 / 用户取消，三种情况都不能崩。
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import storage  # noqa: E402

# **这一行必须在下面的探针之前。** `main` 导入时会声明进程 DPI 感知
# （`SetProcessDpiAwareness`），而 Tk 是在**第一次 `Tk()`** 时按当时的 DPI 定下
# `tk scaling` 的。这个模块的名字排在最前，探针又是模块级的——只要顺序反了，
# **整个测试进程**后面所有量几何的用例都会歪（实测 `tk scaling` 1.332 vs 1.998、
# `_current_pad` 48 vs 80、折行填充率 0.898 vs 0.971）。详见 `tools/run_tests.py`
# 里那道兜底。这里只 import 不取符号：`main` 单独导入不需要图形环境。
import main  # noqa: E402,F401

import tkinter as tk  # noqa: E402

try:
    _probe = tk.Tk()
    _probe.destroy()
    HAS_DISPLAY = True
except tk.TclError:  # pragma: no cover
    HAS_DISPLAY = False

SAMPLE = """# 今天的想法

普通一段正文，里面有**粗体**和*斜体*，还有`行内代码`。

## 二级标题

- 无序第一项
- 无序第二项

> 引用的一段话。

| 名字 | 数量 |
| --- | --- |
| 苹果 | 三个 |

1. 有序第一项

```python
def hello(name):
    return name
```

---

最后一段收尾。
"""


class SpeechModuleTests(unittest.TestCase):
    """`app/speech.py` 里不碰 COM 的那部分（所以没有图形环境也能跑）。"""

    def setUp(self) -> None:
        import speech

        self.speech = speech

    def test_empty_text_is_refused(self) -> None:
        """空文本要在碰 COM 之前就拒绝，别去开一个空文件。"""
        with self.assertRaises(self.speech.SpeechError):
            self.speech.synthesize("   \n  ", Path(tempfile.gettempdir()) / "空.wav")

    def test_wav_seconds_reads_the_real_header(self) -> None:
        """时长必须读头部，不能拿「字节数 ÷ 44100」估——那假设了固定格式。"""
        target = Path(tempfile.mkdtemp()) / "一秒.wav"
        rate, frames = 8000, 8000
        with wave.open(str(target), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes(b"\x00\x00" * frames)
        self.assertAlmostEqual(self.speech.wav_seconds(target), 1.0, places=3)

    def test_wav_seconds_returns_none_for_a_broken_file(self) -> None:
        target = Path(tempfile.mkdtemp()) / "坏的.wav"
        target.write_bytes(b"this is not a wav")
        self.assertIsNone(self.speech.wav_seconds(target))

    def test_default_voice_name_never_raises(self) -> None:
        """读注册表拿不到就返回 None，不许抛异常（状态栏要用它）。"""
        name = self.speech.default_voice_name()
        self.assertTrue(name is None or isinstance(name, str))

    def test_the_module_imports_without_touching_com(self) -> None:
        """导入本身不能初始化 COM / 不能有副作用。"""
        source = (PROJECT / "app" / "speech.py").read_text(encoding="utf-8")
        self.assertNotIn("CoInitializeEx(None", source.split("def ")[0],
                         "模块顶层不该调 CoInitializeEx")


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class SpeakableTextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real_state = storage.default_state_path
        storage.default_state_path = lambda: self.tmp / "state.json"

        folder = self.tmp / "简记日记"
        folder.mkdir()
        self.doc = folder / "朗读.md"
        self.doc.write_bytes(SAMPLE.encode("utf-8"))
        storage.set_default_folder(folder)

        from main import JianJiApp

        self.root = tk.Tk()
        self.root.geometry("1180x760")
        self.app = JianJiApp(self.root)
        self.app.open_file(self.doc)
        self.root.update()

    def tearDown(self) -> None:
        storage.default_state_path = self._real_state
        self.root.destroy()

    def test_markdown_markers_never_leak_into_the_spoken_text(self) -> None:
        """记号漏出来会被真的念成「井号」「星号」「竖线」。"""
        spoken = self.app.speakable_text(SAMPLE)
        for mark in ("#", "*", "`", "> ", "- ", "|", "---", "1. "):
            self.assertNotIn(mark, spoken, f"{mark!r} 漏进了朗读文本")

    def test_every_kind_of_content_is_still_spoken(self) -> None:
        spoken = self.app.speakable_text(SAMPLE)
        for phrase in ("今天的想法", "普通一段正文", "粗体", "斜体", "行内代码",
                       "二级标题", "无序第一项", "引用的一段话", "苹果",
                       "有序第一项", "hello", "最后一段收尾"):
            self.assertIn(phrase, spoken, f"{phrase!r} 被漏掉了")

    def test_table_cells_are_separated_so_the_ear_can_tell_them_apart(self) -> None:
        spoken = self.app.speakable_text(SAMPLE)
        self.assertIn("名字、数量", spoken)
        self.assertIn("苹果、三个", spoken)

    def test_a_horizontal_rule_is_not_spoken(self) -> None:
        self.assertEqual(self.app.speakable_text("---\n").strip(), "")

    def test_an_empty_document_speaks_nothing(self) -> None:
        self.assertEqual(self.app.speakable_text("").strip(), "")
        self.assertEqual(self.app.speakable_text("\n\n\n").strip(), "")

    def test_blank_lines_survive_as_pauses(self) -> None:
        spoken = self.app.speakable_text("第一段。\n\n第二段。")
        self.assertIn("\n", spoken, "空行要留着当停顿")

    def test_the_default_audio_path_swaps_the_suffix(self) -> None:
        self.assertEqual(self.app._export_default_audio_path(self.doc),
                         self.doc.with_suffix(".wav"))


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class AudioExportTests(SpeakableTextTests):
    def _stub(self, target: Path | str, *, available: bool = True):
        """换掉存盘对话框、打开文件夹、语音引擎，返回记录容器与还原函数。"""
        import main as app_main
        import speech

        opened: list[Path] = []
        original = {
            "save": app_main.filedialog.asksaveasfilename,
            "reveal": app_main.reveal_in_explorer,
            "available": speech.is_available,
            "synthesize": speech.synthesize,
        }
        app_main.filedialog.asksaveasfilename = lambda **_kwargs: str(target)
        app_main.reveal_in_explorer = opened.append
        speech.is_available = lambda: available

        def restore() -> None:
            app_main.filedialog.asksaveasfilename = original["save"]
            app_main.reveal_in_explorer = original["reveal"]
            speech.is_available = original["available"]
            speech.synthesize = original["synthesize"]

        return opened, restore

    def _wait_for_the_job(self, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        while self.app._speech_job is not None and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.02)
        self.root.update()
        self.assertIsNone(self.app._speech_job, "后台任务没在超时内结束")

    def test_the_menu_has_an_audio_item(self) -> None:
        menu = self.app._doc_menu(self.doc)
        labels = [menu.entrycget(i, "label")
                  for i in range(menu.index("end") + 1)
                  if menu.type(i) != "separator"]
        self.assertIn("导出音频", labels)

    def test_synthesis_runs_in_the_background(self) -> None:
        """合成很慢，`export_audio` 必须立刻返回，不能冻住界面。"""
        import speech

        target = self.tmp / "慢.wav"
        started = threading.Event()

        def slow_synthesize(_text, path, **_kwargs):
            started.set()
            time.sleep(0.4)
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(22050)
                handle.writeframes(b"\x00\x00" * 2205)      # 0.1 秒
            return path.stat().st_size

        opened, restore = self._stub(target)
        speech.synthesize = slow_synthesize
        try:
            before = time.monotonic()
            self.app.export_audio(self.doc)
            elapsed = time.monotonic() - before
            self.assertLess(elapsed, 0.2, "export_audio 应当立刻返回（合成在后台）")
            self.assertIsNotNone(self.app._speech_job)
            self.assertTrue(started.wait(5), "后台线程没有启动")

            self._wait_for_the_job()
            self.assertIn("已导出音频", self.app.status_label.cget("text"))
            self.assertEqual(opened, [target], "导出完应当打开所在文件夹并选中它")
        finally:
            restore()

    def test_the_spoken_text_is_what_gets_synthesized(self) -> None:
        """传给引擎的必须是**去掉记号**的文本，不是原始 Markdown。"""
        import speech

        seen: list[str] = []
        target = self.tmp / "内容.wav"

        def capture(text, path, **_kwargs):
            seen.append(text)
            Path(path).write_bytes(b"RIFF")
            return 4

        opened, restore = self._stub(target)
        speech.synthesize = capture
        try:
            self.app.export_audio(self.doc)
            self._wait_for_the_job()
        finally:
            restore()

        self.assertEqual(len(seen), 1)
        self.assertNotIn("#", seen[0])
        self.assertIn("今天的想法", seen[0])

    def test_an_empty_document_says_so_and_asks_nothing(self) -> None:
        import main as app_main
        import speech

        empty = self.tmp / "简记日记" / "空的.md"
        empty.write_bytes(b"")
        asked: list[str] = []
        original = app_main.filedialog.asksaveasfilename
        app_main.filedialog.asksaveasfilename = lambda **kwargs: asked.append("asked")
        try:
            self.app.export_audio(empty)
        finally:
            app_main.filedialog.asksaveasfilename = original
        self.assertEqual(asked, [], "没有内容时不该弹存盘对话框")
        self.assertEqual(self.app.status_label.cget("text"), "没有内容可以朗读")
        self.assertIsNone(self.app._speech_job)

    def test_cancelling_the_dialog_starts_nothing(self) -> None:
        opened, restore = self._stub("")
        try:
            self.app.export_audio(self.doc)
        finally:
            restore()
        self.assertIsNone(self.app._speech_job)
        self.assertEqual(opened, [])

    def test_a_missing_voice_engine_shows_a_dialog_and_stops(self) -> None:
        import main as app_main

        shown: list[tuple] = []
        original_error = app_main.messagebox.showerror
        app_main.messagebox.showerror = lambda *args, **kwargs: shown.append(args)
        opened, restore = self._stub(self.tmp / "没有引擎.wav", available=False)
        try:
            self.app.export_audio(self.doc)
        finally:
            restore()
            app_main.messagebox.showerror = original_error

        self.assertEqual(len(shown), 1, "应当提示没有可用的语音引擎")
        self.assertIn("语音引擎", shown[0][1])
        self.assertIsNone(self.app._speech_job)
        self.assertEqual(opened, [])

    def test_a_failure_in_the_engine_is_reported_not_raised(self) -> None:
        import main as app_main
        import speech

        def explode(*_args, **_kwargs):
            raise speech.SpeechError("合成炸了")

        shown: list[tuple] = []
        original_error = app_main.messagebox.showerror
        app_main.messagebox.showerror = lambda *args, **kwargs: shown.append(args)
        opened, restore = self._stub(self.tmp / "会炸.wav")
        speech.synthesize = explode
        try:
            self.app.export_audio(self.doc)
            self._wait_for_the_job()
        finally:
            restore()
            app_main.messagebox.showerror = original_error

        self.assertEqual(len(shown), 1)
        self.assertIn("合成炸了", str(shown[0][1]))
        self.assertEqual(self.app.status_label.cget("text"), "导出音频失败")

    def test_a_second_click_while_busy_does_not_start_another_job(self) -> None:
        import speech

        target = self.tmp / "并发.wav"
        calls: list[str] = []

        def slow(text, path, **_kwargs):
            calls.append(text)
            time.sleep(0.3)
            Path(path).write_bytes(b"RIFF")
            return 4

        opened, restore = self._stub(target)
        speech.synthesize = slow
        try:
            self.app.export_audio(self.doc)
            self.app.export_audio(self.doc)          # 第二次点击
            self._wait_for_the_job()
        finally:
            restore()
        self.assertEqual(len(calls), 1, "同一时间只能有一个合成任务")


if __name__ == "__main__":
    unittest.main()
