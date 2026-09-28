"""文字转音频导出：「导出音频」菜单项、念出来的文本、后台合成流程。

用户要求：「新增将文字转换成音频导出功能。功能出现在点击文档预览右键菜单。」

这一组用例守三件事：

① **念出来的文本和长图是同一套解析**——`speakable_text()` 复用 `build_export_rows`，
   Markdown 记号一个都不许漏出来（漏了会真的被念成「井号」「星号」）；
② **导出不阻塞界面**——合成很慢（每秒约 3.5 个字），必须走后台线程；
③ 没有语音引擎 / 没有内容 / 用户取消，三种情况都不能崩。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
import xml.etree.ElementTree as ET
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


class SsmlTests(unittest.TestCase):
    """`build_ssml()`：这是「让朗读别那么机械」的全部手段，得钉死。

    停顿是照着 `speakable_text()` 的结构来的——空行分段、段内单换行分行。
    实测（一段两行的样例）：加停顿之后音频比纯文本长 1.15 秒，而按常量推算
    是 1.19 秒，**说明 `<break>` 真的被引擎执行了**，不是被当成文本念出来。
    """

    def setUp(self) -> None:
        import speech

        self.speech = speech

    def test_the_result_is_well_formed_xml(self) -> None:
        ET.fromstring(self.speech.build_ssml("第一段。\n\n第二段。"))

    def test_a_blank_line_becomes_a_longer_pause(self) -> None:
        ssml = self.speech.build_ssml("第一段。\n\n第二段。")
        self.assertIn(f"<break time='{self.speech.PARAGRAPH_BREAK_MS}ms'/>", ssml)

    def test_a_single_newline_becomes_a_shorter_pause(self) -> None:
        ssml = self.speech.build_ssml("第一项\n第二项")
        self.assertIn(f"<break time='{self.speech.LINE_BREAK_MS}ms'/>", ssml)

    def test_sentence_enders_get_a_short_pause(self) -> None:
        # 「甲。乙！丙？」三处句末，最后那处在段尾会被去掉（不跟段间停顿叠加）
        ssml = self.speech.build_ssml("甲。乙！丙？")
        self.assertEqual(ssml.count(f"<break time='{self.speech.SENTENCE_BREAK_MS}ms'/>"), 2)

    def test_a_comma_does_not_get_a_pause(self) -> None:
        """逗号上 SAPI 本来就会停一下，再插一个会把句子切得一頓一頓。"""
        self.assertNotIn("<break", self.speech.build_ssml("甲，乙，丙"))

    def test_zero_turns_every_pause_off(self) -> None:
        """核验时要能拿到「和纯文本一模一样」的对照。"""
        ssml = self.speech.build_ssml("第一段。\n\n第二段！", paragraph_break_ms=0,
                                      sentence_break_ms=0, line_break_ms=0)
        self.assertNotIn("<break", ssml)

    def test_a_break_at_the_end_of_a_paragraph_is_dropped(self) -> None:
        """段尾句号那个停顿会和段间停顿叠一起，去掉一个。"""
        self.assertNotIn("<break", self.speech.build_ssml("只有一段。"))

    def test_markup_in_the_text_is_escaped(self) -> None:
        """正文里真的出现 `<` 时不能被当成标签，否则 SAPI 会报解析错。"""
        ssml = self.speech.build_ssml('a < b & c > d "e"')
        ET.fromstring(ssml)
        self.assertIn("&lt;", ssml)
        self.assertIn("&amp;", ssml)
        self.assertIn("&gt;", ssml)
        self.assertNotIn("< b", ssml)

    def test_the_ampersand_is_escaped_first(self) -> None:
        """先换 `<` 再换 `&` 的话，换出来的 `&lt;` 会被二次转义成 `&amp;lt;`。"""
        ssml = self.speech.build_ssml("<")
        self.assertIn("&lt;", ssml)
        self.assertNotIn("&amp;lt;", ssml)

    def test_the_rate_is_not_put_into_the_ssml(self) -> None:
        """语速只走 `voice.Rate`。

        `<prosody rate='0'>` 交给 SAPI 会把时长彻底搞乱——实测同一段文本
        （纯文本 16.9 秒）加上 `<prosody rate='0'>` 之后变成 **50.9 秒**。
        两个地方各设一次语速还会叠乘，账对不上。所以 SSML 里只放停顿。
        """
        self.assertNotIn("prosody", self.speech.build_ssml("一句话。"))

    def test_the_language_is_declared(self) -> None:
        self.assertIn("xml:lang='zh-CN'", self.speech.build_ssml("中文。"))

    def test_whitespace_only_text_still_gives_valid_xml(self) -> None:
        ET.fromstring(self.speech.build_ssml("   \n\n  "))


class FFmpegModuleTests(unittest.TestCase):
    """`app/ffmpeg.py`：找得到就转，找不到就安静地不转（不能崩、不能抛）。"""

    def setUp(self) -> None:
        import ffmpeg

        self.ffmpeg = ffmpeg
        self._saved_path = os.environ.get("PATH", "")
        self._saved_override = os.environ.pop(ffmpeg.ENV_VAR, None)
        ffmpeg._reset_cache()

    def tearDown(self) -> None:
        os.environ["PATH"] = self._saved_path
        if self._saved_override is not None:
            os.environ[self.ffmpeg.ENV_VAR] = self._saved_override
        self.ffmpeg._reset_cache()

    def test_the_env_var_wins(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        binary = tmp / "my-ffmpeg.exe"
        binary.write_bytes(b"")
        os.environ[self.ffmpeg.ENV_VAR] = str(binary)
        self.ffmpeg._reset_cache()
        self.assertEqual(self.ffmpeg.find(), binary)

    def test_a_path_entry_that_is_the_extract_folder_is_looked_into(self) -> None:
        """本机就是这种：PATH 里放的是**解压出来的目录本身**，exe 在它下面的 `bin\\`。

        用户这台机器 `D:\\OpenToUseSW\\ffmpeg-master-latest-win64-gpl-shared` 在 PATH 上，
        而 `ffmpeg.exe` 在 `...\\bin\\` 里，所以 `where ffmpeg` 找不到，东西其实就在。
        """
        tmp = Path(tempfile.mkdtemp())
        binary = tmp / "bin" / "ffmpeg.exe"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"")
        os.environ["PATH"] = str(tmp)
        self.ffmpeg._reset_cache()
        self.assertEqual(self.ffmpeg.find(), binary)

    def test_the_result_is_cached(self) -> None:
        os.environ["PATH"] = tempfile.mkdtemp()
        self.ffmpeg._reset_cache()
        first = self.ffmpeg.find()
        # 换了 PATH 也不重新找——缓存是有意的（导出对话框会连着问两次）
        os.environ["PATH"] = self._saved_path
        self.assertIs(self.ffmpeg.find(), first)

    def test_nothing_found_is_not_an_error(self) -> None:
        """没有 ffmpeg 只是「不能导 MP3」，不是故障。"""
        if any(Path(raw).is_file() for raw in self.ffmpeg._COMMON):
            self.skipTest("这台机器在常见位置装了 ffmpeg，构造不出「找不到」")
        os.environ["PATH"] = tempfile.mkdtemp()
        self.ffmpeg._reset_cache()
        self.assertIsNone(self.ffmpeg.find())
        self.assertFalse(self.ffmpeg.is_available())

    def test_to_mp3_refuses_cleanly_without_ffmpeg(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        source = tmp / "in.wav"
        source.write_bytes(b"RIFF")
        self.ffmpeg.find = lambda: None
        with self.assertRaises(self.ffmpeg.FFmpegError):
            self.ffmpeg.to_mp3(source, tmp / "out.mp3")

    def test_to_mp3_builds_the_expected_command(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        source = tmp / "in.wav"
        source.write_bytes(b"RIFF")
        target = tmp / "out.mp3"
        seen: dict = {}

        class _Shim:
            """换掉整个 `subprocess` 模块，免得动到真的那一个。"""
            TimeoutExpired = subprocess.TimeoutExpired

            @staticmethod
            def run(command, **kwargs):
                seen["command"] = command
                seen["kwargs"] = kwargs
                target.write_bytes(b"ID3")
                return subprocess.CompletedProcess(command, 0, b"", b"")

        self.ffmpeg.find = lambda: Path(r"C:\fake\ffmpeg.exe")
        original = self.ffmpeg.subprocess
        self.ffmpeg.subprocess = _Shim
        try:
            size = self.ffmpeg.to_mp3(source, target)
        finally:
            self.ffmpeg.subprocess = original

        self.assertEqual(size, 3)
        command = seen["command"]
        self.assertEqual(command[0], r"C:\fake\ffmpeg.exe")
        self.assertIn("libmp3lame", command)
        self.assertIn("-map_metadata", command)
        self.assertEqual(command[command.index("-map_metadata") + 1], "-1")
        self.assertEqual(command[-1], str(target))
        # 不能弹黑框
        self.assertEqual(seen["kwargs"]["creationflags"], self.ffmpeg._CREATE_NO_WINDOW)

    def test_to_mp3_reports_a_nonzero_exit(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        source = tmp / "in.wav"
        source.write_bytes(b"RIFF")

        class _Shim:
            TimeoutExpired = subprocess.TimeoutExpired

            @staticmethod
            def run(command, **_kwargs):
                return subprocess.CompletedProcess(command, 1, b"", b"boom")

        self.ffmpeg.find = lambda: Path(r"C:\fake\ffmpeg.exe")
        original = self.ffmpeg.subprocess
        self.ffmpeg.subprocess = _Shim
        try:
            with self.assertRaises(self.ffmpeg.FFmpegError) as caught:
                self.ffmpeg.to_mp3(source, tmp / "out.mp3")
        finally:
            self.ffmpeg.subprocess = original
        self.assertIn("boom", str(caught.exception))


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


class _ExportHarness:
    """`AudioExportTests` 和 `Mp3ExportTests` 共用的两个替身。

    写成**普通 mixin**（不继承 `TestCase`）是有意的：直接让它继承 `SpeakableTextTests`
    的话，unittest 会把父类那一批用例在每个子类里各跑一遍。
    """

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


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class AudioExportTests(_ExportHarness, SpeakableTextTests):

    def test_the_menu_has_an_audio_item(self) -> None:
        """菜单上要看得见「导出音频」，后面括注有哪几种格式。

        括注是跟着**这台机器有没有 ffmpeg** 变的，所以只断言前缀，
        不去钉死整串（钉死了换台机器就红）。
        """
        menu = self.app._doc_menu(self.doc)
        labels = [menu.entrycget(i, "label")
                  for i in range(menu.index("end") + 1)
                  if menu.type(i) != "separator"]
        self.assertTrue(any(label.startswith("导出音频") for label in labels),
                        f"菜单里没有导出音频：{labels}")

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
        """送给引擎的必须是**去掉记号**的文本（包在 SSML 里），不是原始 Markdown。"""
        import xml.etree.ElementTree as ET

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
        payload = seen[0]
        self.assertTrue(payload.startswith("<speak"), "应当送 SSML，不是裸正文")
        ET.fromstring(payload)                       # 不合法会在这里抛
        self.assertNotIn("#", payload)
        self.assertIn("今天的想法", payload)
        self.assertIn("<break", payload, "应当带停顿，否则跟原来一样机械")

    def test_synthesis_uses_the_natural_rate_and_xml_mode(self) -> None:
        """语速和「按 SSML 解析」这两件事必须真的传下去。"""
        import speech

        seen: list[dict] = []
        target = self.tmp / "参数.wav"

        def capture(text, path, **kwargs):
            seen.append(kwargs)
            Path(path).write_bytes(b"RIFF")
            return 4

        opened, restore = self._stub(target)
        speech.synthesize = capture
        try:
            self.app.export_audio(self.doc)
            self._wait_for_the_job()
        finally:
            restore()

        self.assertEqual(seen, [{"rate": speech.NATURAL_RATE, "xml": True}])

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


def _write_wav(_text, path, **_kwargs) -> int:
    """替身：写一个 0.1 秒的真 WAV，好让 `wav_seconds()` 读得出时长。"""
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(22050)
        handle.writeframes(b"\x00\x00" * 2205)
    return Path(path).stat().st_size


@unittest.skipUnless(HAS_DISPLAY, "需要图形界面环境")
class Mp3ExportTests(_ExportHarness, SpeakableTextTests):
    """选了 `.mp3` 时：先合成 WAV、再让 ffmpeg 转一道，临时文件不许留在盘上。"""

    def _stub_ffmpeg(self, *, available: bool = True) -> dict:
        import ffmpeg

        original = {"available": ffmpeg.is_available, "to_mp3": ffmpeg.to_mp3}
        ffmpeg.is_available = lambda: available
        return original

    def _restore_ffmpeg(self, original: dict) -> None:
        import ffmpeg

        ffmpeg.is_available = original["available"]
        ffmpeg.to_mp3 = original["to_mp3"]

    def test_choosing_mp3_synthesises_to_a_temp_wav_then_converts(self) -> None:
        import ffmpeg
        import speech

        target = self.tmp / "朗读.mp3"
        converted: list[tuple[Path, Path]] = []

        def fake_to_mp3(source, destination, **_kwargs):
            converted.append((Path(source), Path(destination)))
            Path(destination).write_bytes(b"ID3")
            return 3

        opened, restore = self._stub(target)
        original = self._stub_ffmpeg()
        speech.synthesize = _write_wav
        ffmpeg.to_mp3 = fake_to_mp3
        try:
            self.app.export_audio(self.doc)
            self._wait_for_the_job()
        finally:
            self._restore_ffmpeg(original)
            restore()

        self.assertEqual(len(converted), 1)
        source, destination = converted[0]
        self.assertEqual(destination, target)
        self.assertEqual(source.suffix, ".wav")
        self.assertNotEqual(source.parent, target.parent,
                            "中间那个 WAV 不该放在用户选的目录里")
        self.assertFalse(source.parent.exists(), "临时目录要清掉，不能留一堆文件")
        self.assertFalse(source.exists(), "临时 WAV 要删掉")
        self.assertIn("已导出音频：朗读.mp3", self.app.status_label.cget("text"))
        self.assertIn("秒", self.app.status_label.cget("text"),
                      "时长要从合成出来的 WAV 上取，转成 MP3 之后就读不到了")
        self.assertEqual(opened, [target])

    def test_the_temp_wav_is_cleaned_up_even_when_ffmpeg_fails(self) -> None:
        import ffmpeg
        import main as app_main
        import speech

        target = self.tmp / "会炸.mp3"
        shown: list[tuple] = []
        original_error = app_main.messagebox.showerror
        app_main.messagebox.showerror = lambda *args, **kwargs: shown.append(args)

        def explode(_source, _destination, **_kwargs):
            raise ffmpeg.FFmpegError("转码炸了")

        opened, restore = self._stub(target)
        original = self._stub_ffmpeg()
        speech.synthesize = _write_wav
        ffmpeg.to_mp3 = explode
        try:
            self.app.export_audio(self.doc)
            self._wait_for_the_job()
        finally:
            self._restore_ffmpeg(original)
            restore()
            app_main.messagebox.showerror = original_error

        self.assertEqual(len(shown), 1)
        self.assertIn("转码炸了", str(shown[0][1]))
        self.assertEqual(self.app.status_label.cget("text"), "导出音频失败")

    def test_mp3_without_ffmpeg_is_refused_before_anything_starts(self) -> None:
        import main as app_main

        target = self.tmp / "要MP3.mp3"
        shown: list[tuple] = []
        original_error = app_main.messagebox.showerror
        app_main.messagebox.showerror = lambda *args, **kwargs: shown.append(args)
        opened, restore = self._stub(target)
        original = self._stub_ffmpeg(available=False)
        try:
            self.app.export_audio(self.doc)
        finally:
            self._restore_ffmpeg(original)
            restore()
            app_main.messagebox.showerror = original_error

        self.assertEqual(len(shown), 1)
        self.assertIn("ffmpeg", str(shown[0][1]))
        self.assertIsNone(self.app._speech_job, "不该起后台任务")
        self.assertEqual(opened, [])

    def test_the_dialog_offers_mp3_only_when_ffmpeg_is_there(self) -> None:
        import main as app_main

        seen: list[dict] = []
        original = app_main.filedialog.asksaveasfilename
        app_main.filedialog.asksaveasfilename = lambda **kwargs: seen.append(kwargs) or ""
        try:
            original_ff = self._stub_ffmpeg(available=True)
            try:
                self.app.export_audio(self.doc)
            finally:
                self._restore_ffmpeg(original_ff)
            self.assertEqual(seen[0]["defaultextension"], ".mp3")
            self.assertIn("*.mp3", [pattern for _label, pattern in seen[0]["filetypes"]])
            self.assertTrue(seen[0]["initialfile"].endswith(".mp3"))

            seen.clear()
            original_ff = self._stub_ffmpeg(available=False)
            try:
                self.app.export_audio(self.doc)
            finally:
                self._restore_ffmpeg(original_ff)
            self.assertEqual(seen[0]["defaultextension"], ".wav")
            self.assertEqual(len(seen[0]["filetypes"]), 1,
                             "没有 ffmpeg 就不该把 MP3 摆出来")
        finally:
            app_main.filedialog.asksaveasfilename = original


if __name__ == "__main__":
    unittest.main()
