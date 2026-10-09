"""平台层：常量、目录、起进程的包装，以及两个长图后端的接口一致性。

这一组守的是**移植的地基**，不是某一个功能：

① `app/platform_support.py` 是平台差异的唯一落点，所以「判断平台」这件事
   必须只有一套真值来源 —— 常量之间自相矛盾的话，后面每个调用点都会各错各的；
② 两个长图后端（GDI / CoreGraphics）**接口必须一字不差**，
   否则 `image_export.py` 里那六百行排版代码迟早要分平台，移植就白做了；
③ 排版代码本身不许再碰任何 Windows API —— 这是「后端拆分没漏」的判据。
"""
from __future__ import annotations

import ast
import inspect
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import platform_support  # noqa: E402


def code_only(module) -> str:
    """模块的源码，**去掉注释和 docstring**。

    「不许出现 `os.name`」这种判据必须只看代码：注释里正大光明地写着
    「Windows 上 `st_ctime` 就是创建时间」，那是给人看的说明，不是违规。
    直接对源码做字符串匹配的话，写得越清楚的模块越容易误报 —— 判据错了，
    而不是被检查的东西错了。
    """
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:]
    return ast.unparse(tree)


class _PlatformPatch:
    """临时把 `platform_support` 的平台常量改成别的平台。

    可以这么干是因为 `app_dir()` / `run_quiet()` 都是**调用时**去读模块全局的，
    不是导入时算死的。这样 macOS / Linux 的分支在 Windows 上也测得了。
    """

    def __init__(self, *, windows: bool, macos: bool, linux: bool = False) -> None:
        self.values = (windows, macos, linux)

    def __enter__(self):
        self.saved = (platform_support.WINDOWS, platform_support.MACOS,
                      platform_support.LINUX)
        (platform_support.WINDOWS, platform_support.MACOS,
         platform_support.LINUX) = self.values
        return self

    def __exit__(self, *_exc):
        (platform_support.WINDOWS, platform_support.MACOS,
         platform_support.LINUX) = self.saved
        return False


class PlatformConstantTests(unittest.TestCase):
    def test_at_most_one_platform_is_selected(self) -> None:
        flags = [platform_support.WINDOWS, platform_support.MACOS, platform_support.LINUX]
        self.assertLessEqual(sum(1 for flag in flags if flag), 1,
                             "同时命中两个平台说明常量算错了")

    def test_this_machine_is_recognised(self) -> None:
        if sys.platform == "win32":
            self.assertTrue(platform_support.WINDOWS)
        elif sys.platform == "darwin":
            self.assertTrue(platform_support.MACOS)

    def test_the_no_window_flag_follows_the_platform(self) -> None:
        """`CREATE_NO_WINDOW` 只在 Windows 上存在；别的平台必须是 0。"""
        if platform_support.WINDOWS:
            self.assertEqual(platform_support.NO_WINDOW, 0x08000000)
        else:
            self.assertEqual(platform_support.NO_WINDOW, 0)

    def test_the_modifier_key_follows_the_platform(self) -> None:
        """macOS 上用户按的是 ⌘。绑 Ctrl 在那边是「右键菜单键」，一眼就不对。"""
        self.assertEqual(platform_support.MODIFIER,
                         "Command" if platform_support.MACOS else "Control")
        self.assertEqual(platform_support.MODIFIER_LABEL,
                         "⌘" if platform_support.MACOS else "Ctrl")

    def test_the_file_manager_label_is_named(self) -> None:
        self.assertIn(platform_support.FILE_MANAGER_LABEL, ("资源管理器", "访达", "文件管理器"))


class AppDirectoryTests(unittest.TestCase):
    def test_windows_uses_localappdata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            saved = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = temp
            try:
                with _PlatformPatch(windows=True, macos=False):
                    self.assertEqual(platform_support.state_dir(),
                                     Path(temp) / "JianJi")
            finally:
                if saved is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = saved

    def test_macos_uses_application_support(self) -> None:
        """macOS 的规矩是 `~/Library/Application Support`，不是 Unix 老习惯的 `~/.jianji`。"""
        with _PlatformPatch(windows=False, macos=True):
            self.assertEqual(platform_support.state_dir(),
                             Path.home() / "Library" / "Application Support" / "JianJi")

    def test_linux_falls_back_to_xdg(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            saved = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = temp
            try:
                with _PlatformPatch(windows=False, macos=False, linux=True):
                    self.assertEqual(platform_support.state_dir(), Path(temp) / "JianJi")
            finally:
                if saved is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = saved

    def test_the_state_file_lives_in_the_app_directory(self) -> None:
        self.assertEqual(platform_support.default_state_path().name, "state.json")
        self.assertEqual(platform_support.default_state_path().parent,
                         platform_support.state_dir())


class RunQuietTests(unittest.TestCase):
    """起进程统一走平台层 —— 因为 `creationflags` 在 macOS 上会直接抛 ValueError。"""

    class _Shim:
        TimeoutExpired = subprocess.TimeoutExpired

        def __init__(self) -> None:
            self.seen: dict = {}

        def run(self, command, **kwargs):
            self.seen = dict(kwargs)
            self.seen["command"] = command
            return subprocess.CompletedProcess(command, 0, b"", b"")

    def _run_with_shim(self) -> dict:
        shim = self._Shim()
        original = platform_support.subprocess
        platform_support.subprocess = shim
        try:
            platform_support.run_quiet(["echo", "hi"])
        finally:
            platform_support.subprocess = original
        return shim.seen

    def test_creationflags_are_added_on_windows(self) -> None:
        with _PlatformPatch(windows=True, macos=False):
            seen = self._run_with_shim()
        self.assertEqual(seen.get("creationflags"), 0x08000000,
                         "Windows 上不加这个会闪黑框")

    def test_creationflags_are_not_passed_off_windows(self) -> None:
        with _PlatformPatch(windows=False, macos=True):
            seen = self._run_with_shim()
        self.assertNotIn("creationflags", seen,
                         "macOS 的 subprocess.run 收到 creationflags 会抛 ValueError")

    def test_output_is_captured_by_default(self) -> None:
        with _PlatformPatch(windows=True, macos=False):
            seen = self._run_with_shim()
        self.assertTrue(seen.get("capture_output"))


class BackendParityTests(unittest.TestCase):
    """两个长图后端必须接口一致 —— 这是「排版代码一行都不用分平台」的前提。

    这一组在**任何平台**上都跑得起来：CoreGraphics 后端把框架加载推迟到了
    第一次真正要用的时候，所以在 Windows 上 import 它不会炸，可以拿来做对照。
    """

    def setUp(self) -> None:
        import image_backend_cg
        import image_backend_gdi
        self.gdi = image_backend_gdi
        self.cg = image_backend_cg

    def test_both_backends_are_importable(self) -> None:
        self.assertTrue(callable(self.gdi.Surface))
        self.assertTrue(callable(self.cg.Surface))

    def test_the_macos_backend_imports_without_the_frameworks(self) -> None:
        """框架必须是**惰性加载**的。

        判据就是「它在这个平台上 import 成功了」：`_FRAMEWORKS` 里那三条路径
        只在 macOS 上存在，写在模块顶层的话，import 这一下就抛 OSError ——
        那样两个后端的接口就没法在 Windows 上对照了。
        """
        if platform_support.MACOS:
            self.skipTest("在 macOS 上这条没有区分度")
        self.assertIsNotNone(self.cg.Surface)

    def _method_names(self, cls) -> set[str]:
        return {name for name in vars(cls) if not name.startswith("__")}

    def test_surface_exposes_the_same_methods(self) -> None:
        expected = {"fill", "text", "ellipse", "rect", "polyline", "to_png", "close"}
        self.assertTrue(expected <= self._method_names(self.gdi.Surface))
        self.assertTrue(expected <= self._method_names(self.cg.Surface))

    def test_fonts_exposes_the_same_methods(self) -> None:
        expected = {"handle", "metrics", "width", "char_widths", "close"}
        self.assertTrue(expected <= self._method_names(self.gdi.Fonts))
        self.assertTrue(expected <= self._method_names(self.cg.Fonts))

    def test_the_shared_methods_have_identical_signatures(self) -> None:
        for name in ("fill", "text", "ellipse", "rect", "polyline", "to_png", "close"):
            self.assertEqual(
                str(inspect.signature(getattr(self.gdi.Surface, name))),
                str(inspect.signature(getattr(self.cg.Surface, name))),
                f"Surface.{name} 两个后端的签名不一致")
        for name in ("handle", "metrics", "width", "char_widths", "close"):
            self.assertEqual(
                str(inspect.signature(getattr(self.gdi.Fonts, name))),
                str(inspect.signature(getattr(self.cg.Fonts, name))),
                f"Fonts.{name} 两个后端的签名不一致")

    def test_the_constructors_have_identical_signatures(self) -> None:
        self.assertEqual(str(inspect.signature(self.gdi.Surface.__init__)),
                         str(inspect.signature(self.cg.Surface.__init__)))
        self.assertEqual(str(inspect.signature(self.gdi.Fonts.__init__)),
                         str(inspect.signature(self.cg.Fonts.__init__)))


class LayoutCodeIsPlatformFreeTests(unittest.TestCase):
    """排版与绘制那六百行不许再碰任何 Windows API —— 后端拆分有没有漏，看这里。"""

    def setUp(self) -> None:
        import image_export
        self.source = code_only(image_export)

    def test_no_windows_api_in_the_shared_layout_code(self) -> None:
        for token in ("windll", "gdi32", "wintypes", "user32", "SetFileTime"):
            self.assertNotIn(token, self.source,
                             f"排版代码里出现了 {token}，它该待在后端里")

    def test_it_still_re_exports_the_old_names(self) -> None:
        """`tests/test_export.py` 与两个工具按这些老名字引用，别把它们搬走就完事。"""
        import image_export
        for name in ("Surface", "_Fonts", "colorref", "encode_png", "render",
                     "build_layout", "Options", "Palette", "Piece", "Row"):
            self.assertTrue(hasattr(image_export, name), f"image_export 少了 {name}")


class CreationTimePlatformTests(unittest.TestCase):
    def test_a_missing_file_reports_no_creation_time(self) -> None:
        missing = Path(tempfile.mkdtemp()) / "没有这个文件.md"
        self.assertIsNone(platform_support.creation_time(missing))

    def test_setting_the_time_on_a_missing_file_fails_quietly(self) -> None:
        missing = Path(tempfile.mkdtemp()) / "没有这个文件.md"
        self.assertFalse(platform_support.set_creation_time(missing, 0.0))

    def test_the_creation_time_round_trips(self) -> None:
        if not platform_support.supports_creation_time():
            self.skipTest("这个平台读不到创建时间")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "文稿.md"
            path.write_text("正文", encoding="utf-8")
            target = 1_600_000_000.0                     # 2020-09-13
            self.assertTrue(platform_support.set_creation_time(path, target))
            read = platform_support.creation_time(path)
            self.assertIsNotNone(read)
            self.assertAlmostEqual(read, target, delta=2.0)

    def test_the_sort_key_uses_the_platform_creation_time(self) -> None:
        """`created` 排序必须走 `creation_time()`。

        直接读 `st_ctime` 在 Windows 上碰巧是对的，在 macOS/Linux 上却是
        **元数据变更时间** —— 那种错不会报错，只会让顺序悄悄不对。
        """
        import storage
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "文稿.md"
            path.write_text("正文", encoding="utf-8")
            expected = storage.creation_time(path) or 0.0
            self.assertAlmostEqual(storage._sort_key_of(path, "created"),
                                   expected, delta=0.001)


class StorageDelegationTests(unittest.TestCase):
    def test_the_state_path_comes_from_the_platform_layer(self) -> None:
        import storage
        self.assertEqual(storage.default_state_path(),
                         platform_support.default_state_path())

    def test_storage_has_no_platform_branches_left(self) -> None:
        """平台差异全部收到 `platform_support`，storage 里不该再有分支。"""
        import storage
        source = code_only(storage)
        for token in ("os.name", "sys.platform", "windll", "LOCALAPPDATA",
                      "ctypes", "subprocess"):
            self.assertNotIn(token, source, f"storage 里还留着 {token}")

    def test_ffmpeg_has_no_platform_branches_left(self) -> None:
        import ffmpeg
        source = code_only(ffmpeg)
        for token in ("os.name", "sys.platform", "creationflags"):
            self.assertNotIn(token, source, f"ffmpeg 里还留着 {token}")


class MainModulePlatformTests(unittest.TestCase):
    """`app/main.py` 的平台相关常量、快捷键和「在…中显示」文案。

    `main.py` 是唯一一个**导入就做实事**的模块（声明进程 DPI 感知），所以
    `import` 放在 `setUpClass` 里、只做一次。`tools/run_tests.py` 的
    `run_in_process()` 已经在发现测试模块之前 import 过它，这里拿的是缓存。
    """

    @classmethod
    def setUpClass(cls) -> None:
        import main

        cls.main = main

    def test_the_shortcut_modifier_is_control_on_windows(self) -> None:
        with _PlatformPatch(windows=True, macos=False):
            self.assertEqual(self.main._shortcut_modifiers(), ("Control",))

    def test_macos_binds_both_command_and_control(self) -> None:
        """⌘ 是主键，但 Ctrl 也得留着 —— 从 Windows 过来的人会去找 Ctrl。"""
        with _PlatformPatch(windows=False, macos=True):
            modifiers = self.main._shortcut_modifiers()
        self.assertIn("Command", modifiers)
        self.assertIn("Control", modifiers)

    def test_no_hardcoded_modifier_sequences_outside_the_helper(self) -> None:
        """绑定和解绑必须走 `_shortcut_modifiers()`。

        两边各抄一份的话，macOS 上很容易漏掉 Command 那一组 —— 表现是
        「换一次主题之后 ⌘S 会存两次」，而且很难看出来（绑了两层，看不出差别）。
        所以直接钉住：源码里不许再出现写死的 `<Control-x>`。
        """
        source = code_only(self.main)
        self.assertNotIn("Control-", source,
                         "main.py 里还有写死的 Control 序列，绑定/解绑要共用一个来源")
        self.assertIn("Control", source, "_shortcut_modifiers() 不见了？")

    def test_the_reveal_label_is_derived_from_the_file_manager_constant(self) -> None:
        """「在…中显示」的文案必须由 `platform_support.FILE_MANAGER_LABEL` 拼出来。

        这里**不能**用 `_PlatformPatch` 假装切到 macOS 去看「在访达中打开」：
        `FILE_MANAGER_LABEL` 是**导入时**算好的常量，`_PlatformPatch` 只换
        `WINDOWS/MACOS/LINUX` 那几个标志，换不动它 —— 那样写出来的断言永远是假的。
        真正要守的是**别把中文写死**：写死了之后 macOS 上会弹「在资源管理器中显示」，
        功能其实还能用（`open -R` 照样调得动），所以肉眼极难发现。
        """
        label = platform_support.FILE_MANAGER_LABEL
        self.assertEqual(self.main._reveal_label("显示"), f"在{label}中显示")
        self.assertEqual(self.main._reveal_label("打开"), f"在{label}中打开")
        self.assertNotIn("在资源管理器中", code_only(self.main),
                         "main.py 里写死了「在资源管理器中」，要改成 _reveal_label()")

    def test_the_file_manager_label_matches_the_platform(self) -> None:
        if platform_support.WINDOWS:
            self.assertEqual(platform_support.FILE_MANAGER_LABEL, "资源管理器")
        elif platform_support.MACOS:
            self.assertEqual(platform_support.FILE_MANAGER_LABEL, "访达")

    def test_the_active_font_table_matches_the_platform(self) -> None:
        table = self.main._WINDOWS_FONTS if platform_support.WINDOWS else self.main._MACOS_FONTS
        self.assertEqual(self.main.FONT_CANDIDATES[:len(table)], table)
        self.assertEqual(len(self.main.FONT_CANDIDATES),
                         len(table) + len(self.main._COMMON_FONTS))

    def test_the_macos_fonts_do_not_pretend_to_be_windows_fonts(self) -> None:
        """SimSun / KaiTi / SimHei 在 macOS 上**都不存在**。

        把它们留在 macOS 的候选表里，`font.families()` 一个都命中不了，最后会
        静默退到 Tk 默认字体 —— 界面能开，但字全变了，而且看不出是哪儿错了。

        判据必须是**整条别名**比对，不能拿名字去 substring。踩过一次：用
        `"kaiti" in macos_names` 抓 Windows 的 `KaiTi`，结果把苹果自带的
        `Kaiti SC` / `STKaiti`（**合法且应该留着**）一起抓了出来 —— 假阳性。
        凡是「每个样本都被判失败」的判据，可疑的是判据不是样本。
        """
        windows_only = {"SimSun", "NSimSun", "KaiTi", "SimKai", "SimHei",
                        "Microsoft YaHei", "Microsoft YaHei UI", "Consolas",
                        "DengXian", "FangSong", "YouYuan", "LiSu"}
        lowered = {name.lower() for name in windows_only}
        for label, aliases in self.main._MACOS_FONTS:
            for alias in aliases:
                self.assertNotIn(alias.lower(), lowered,
                                 f"macOS 候选表「{label}」里混进了 Windows 专有字体 {alias}")

    def test_the_two_font_tables_share_no_latin_alias(self) -> None:
        """两份表可以共用中文本地化名（宋体/楷体在两边都叫这个），

        但**拉丁名不许重叠** —— 重叠就说明有一份是抄过去的，而拉丁名恰好是
        系统之间最不可移植的那部分（`SimSun` 在 macOS 上就是查不到）。
        """
        def latin(names):
            return {a.lower() for a in names if a.isascii()}

        windows_latin = latin(a for _l, aliases in self.main._WINDOWS_FONTS for a in aliases)
        macos_latin = latin(a for _l, aliases in self.main._MACOS_FONTS for a in aliases)
        # STKaiti / STSong / STZhongsong / STHeiti 这几支「华文」系列两个平台都有，属正常。
        shared = (windows_latin & macos_latin) - {"stkaiti", "stsong", "stzhongsong",
                                                  "stheiti", "stfangso"}
        self.assertFalse(shared, f"两个平台的拉丁字体名重叠了：{sorted(shared)}")

    def test_every_font_candidate_has_an_alias_and_a_unique_label(self) -> None:
        labels = [label for label, _aliases in self.main.FONT_CANDIDATES]
        self.assertEqual(len(labels), len(set(labels)), "候选表里有重名的显示名")
        for label, aliases in self.main.FONT_CANDIDATES:
            self.assertTrue(aliases, f"{label} 没有任何别名，永远命中不了")

    def test_the_default_font_aliases_belong_to_this_platform(self) -> None:
        aliases = self.main.DEFAULT_FONT_ALIASES
        self.assertTrue(aliases)
        if platform_support.WINDOWS:
            self.assertIn("Microsoft YaHei UI", aliases)
        else:
            self.assertIn("PingFang SC", aliases)

    def test_the_ui_fonts_follow_the_platform(self) -> None:
        if platform_support.WINDOWS:
            self.assertEqual(self.main.FAMILY, "Microsoft YaHei UI")
            self.assertEqual(self.main.MONO_FAMILY, "Consolas")
        else:
            self.assertEqual(self.main.FAMILY, "PingFang SC")
            self.assertEqual(self.main.MONO_FAMILY, "Menlo")

    def test_main_has_no_bare_platform_checks(self) -> None:
        """判断平台只能问 `platform_support`。"""
        source = code_only(self.main)
        for token in ("sys.platform", "os.name"):
            self.assertNotIn(token, source, f"main.py 里还留着 {token}")

    def test_main_reports_a_version_for_the_packaging_scripts(self) -> None:
        """`build_macos/` 里两个脚本都靠正则抓这一行，格式不能变。"""
        self.assertRegex(self.main.__version__, r"^\d+\.\d+\.\d+$")

    def test_install_shortcut_refuses_to_load_off_windows(self) -> None:
        """它整个是 Windows 专属的（COM 的 IShellLink）。

        非 Windows 上必须在 `from ctypes import wintypes` **之前**就拦下来，
        否则报的是 `cannot import name 'wintypes'` —— 看不出是平台不对还是装坏了。
        """
        source = code_only(__import__("install_shortcut"))
        self.assertIn("platform_support.WINDOWS", source)
        self.assertNotIn("os.name", source)


if __name__ == "__main__":
    unittest.main()
