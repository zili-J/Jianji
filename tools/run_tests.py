"""跑全量测试，并把完整输出落盘。

为什么要包一层：unittest 只在结尾的 traceback 段落里打印失败用例名，
终端里滚过去就没了（本项目的测试套件约两分钟、四百多项，输出很长）。
之前就吃过一次亏——只看到「Ran 338 tests / FAILED (failures=1, errors=1)」
这一行统计，具体是哪两项再也没法知道。

为什么默认在**隐藏桌面**上跑：测试要建真实的 Tk 窗口才能量几何
（`dlineinfo` / `winfo_width` 在 `withdraw()` 的窗口上量不出来），
于是屏幕上会闪几百次窗口、焦点被抢走几十次——用户正在前台干活，很烦人。
换个桌面（`tools/quiet_desktop.py`）之后，窗口只存在于那个桌面上，
**当前桌面的前台窗口完全不受影响**，也不会闪任何东西。

用法：

    python tools/run_tests.py                  # 全量，隐藏桌面，摘要打到屏幕
    python tools/run_tests.py -k export        # 只跑文件名含 export 的测试
    python tools/run_tests.py --foreground     # 在当前（可见）桌面上跑
    python tools/run_tests.py --in-process     # 就在本进程里跑，不开子进程

`--foreground` 什么时候必须用：要截**原生菜单**的脚本（`_shot_doc_menu.py`、
`_shot_sort_menu.py`）只能从屏幕 DC 抓图，而隐藏桌面上的屏幕 DC 是全黑的。
跑测试本身不需要它。

裸命令是 `python -m unittest discover -s tests -t tests`——**必须带 `-t tests`**，
`-t .` 会报 `Start directory is not importable`，因为 `tests/` 不是包；
但裸命令失败时用例名只在滚屏里出现，**一定要先重定向到文件**再 grep。

报告是**边跑边写**的（见 `_FlushingStream`）：万一某个用例在隐藏桌面上卡死，
`outputs/test-output.txt` 末尾那行就是正在跑的那个用例，不用干等一整套跑完。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "outputs" / "test-output.txt"

#: 屏幕上要再摘一遍的行（完整输出永远在 REPORT 里）
SUMMARY_PREFIXES = ("FAIL: ", "ERROR: ", "Ran ", "OK", "FAILED")


def _fail(message: str) -> None:
    """报错并以 **2** 退出。

    别写成 `raise SystemExit("...")`——那样退出码是 1，和「测试有失败」撞在一起，
    调用方分不清是「参数写错了」还是「用例挂了」。
    """
    print(f"{message}（见本文件开头的用法）", file=sys.stderr)
    raise SystemExit(2)


def _parse_arguments(arguments: list[str]) -> tuple[str, str, bool]:
    """解析命令行，返回 `(pattern, mode, quiet)`。

    `mode` 是 `"hidden"` / `"foreground"` / `"in-process"`；
    `quiet` 表示只写文件、不往屏幕打（隐藏桌面那一路给子进程用，
    打印由父进程统一做，免得同一份输出打两遍）。
    """
    pattern = "test_*.py"
    mode = "hidden"
    quiet = False
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in ("-k", "--pattern"):
            if index + 1 >= len(arguments):
                _fail("`-k` 后面要跟一个模式，例如 `-k export`")
            pattern = f"*{arguments[index + 1]}*.py"
            index += 2
            continue
        if argument == "--pattern-glob":
            # 内部用：隐藏桌面那一路已经拿到包好的 glob 了，别再包一层。
            # 包两层会变成 `**sort*.py*.py`，一个文件都匹配不上，而 unittest
            # 对「0 个用例」是判 OK 的——不写成显式参数就会静默空跑。
            if index + 1 >= len(arguments):
                _fail("`--pattern-glob` 后面要跟一个 glob")
            pattern = arguments[index + 1]
            index += 2
            continue
        if argument == "--hidden":
            mode = "hidden"
        elif argument == "--foreground":
            mode = "foreground"
        elif argument == "--in-process":
            mode = "in-process"
        elif argument == "--quiet":
            quiet = True
        else:
            _fail(f"未知参数：{argument}")
        index += 1
    return pattern, mode, quiet


class _FlushingStream:
    """把 unittest 的输出**边跑边落盘**。

    为什么不用 `io.StringIO`：那样报告只在整套跑完才写文件，而 Tk 测试在
    隐藏桌面上是有可能卡死的（模态对话框没人能关、`wait_window` 等不到事件）。
    实测踩过一次——跑了 50 分钟没动静，磁盘上**一个字都没有**，只能靠猜。
    包一层、每次 `write` 就 flush，卡住时报告末尾那行就是正在跑的那个用例名。

    注意 `verbosity=2` 打的是 `test_xxx (...) `（**没有换行**）＋ `ok`，
    所以必须逐次 write 就 flush，光靠行缓冲是刷不出来的。
    """

    def __init__(self, handle):
        self._handle = handle

    def write(self, text: str) -> int:
        written = self._handle.write(text)
        self._handle.flush()
        return written

    def flush(self) -> None:
        self._handle.flush()


def _summary_lines(output: str) -> list[str]:
    return [line for line in output.splitlines()
            if line.startswith(SUMMARY_PREFIXES)]


def _print_summary(output: str) -> None:
    """把要点再摘一遍，免得滚屏之后什么都看不到。"""
    print("-" * 60)
    for line in _summary_lines(output):
        print(line)
    print(f"完整输出：{REPORT.relative_to(ROOT)}")


def run_in_process(pattern: str, *, echo: bool = True) -> int:
    """在本进程里跑一遍测试，返回退出码。

    **为什么隐藏桌面这一路必须是「在本进程里跑」**：`CreateDesktopW` 建的桌面
    只对**直接**继承它的进程生效；`quiet_desktop.run_hidden` 起的子进程如果自己
    再 `CreateProcess`，孙进程就回到默认桌面去了。所以是
    「父进程开桌面 → 子进程用 `--in-process` 原地跑」，测试窗口才真在隐藏桌面上。
    """
    # 裸 `python -m unittest` 是把 cwd 放进 sys.path 的；本进程跑要自己补，
    # 因为这时 sys.path[0] 是 tools/ 而不是工程根。
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    REPORT.parent.mkdir(exist_ok=True)
    suite = unittest.TestLoader().discover("tests", pattern=pattern,
                                           top_level_dir="tests")
    # 报告边跑边写（见 `_FlushingStream`）：卡死时最后一行就是当前用例。
    with REPORT.open("w", encoding="utf-8") as handle:
        result = unittest.TextTestRunner(stream=_FlushingStream(handle),
                                         verbosity=2).run(suite)

    output = REPORT.read_text(encoding="utf-8")
    if echo:
        print(output)
        _print_summary(output)

    if result.testsRun == 0:
        # unittest 把「一个用例都没跑」也判成成功，于是一个写错的 pattern
        # 会以「OK / 退出码 0」收场——最坏的那种失败：看起来全绿。
        print(f"一个用例都没跑到（pattern={pattern}），八成是模式写错了。",
              file=sys.stderr)
        return 3
    return 0 if result.wasSuccessful() else 1


def run_on_hidden_desktop(pattern: str) -> int:
    """在隐藏桌面上跑一遍，把完整输出打回当前屏幕。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from quiet_desktop import run_hidden  # 只在这一路需要，免得给别处添依赖

    print("在隐藏桌面上跑测试（当前桌面不受影响，不会有窗口闪出来）……",
          flush=True)

    # 先把旧报告删掉：万一子进程压根没跑起来（参数写错、Tk 起不来），
    # 留着上一次的报告就会被当成这次的结果打出来——「看起来全绿」而其实什么都没跑。
    REPORT.unlink(missing_ok=True)

    code = run_hidden(
        [sys.executable, str(Path(__file__).resolve()),
         "--in-process", "--quiet", "--pattern-glob", pattern],
        cwd=ROOT,
    )

    if not REPORT.exists():
        print(f"没有拿到测试输出（子进程退出码 {code}）——它可能没启动起来。",
              file=sys.stderr)
        return code or 1
    output = REPORT.read_text(encoding="utf-8")
    print(output)
    _print_summary(output)
    return code


def main() -> int:
    pattern, mode, quiet = _parse_arguments(sys.argv[1:])
    if mode == "hidden":
        try:
            return run_on_hidden_desktop(pattern)
        except OSError as error:
            # 建不出桌面（权限、会话类型）就退到当前桌面，但要说清楚，
            # 免得用户以为「没闪窗口」而其实窗口全闪在前台了。
            print(f"开不了隐藏桌面（{error}），退到当前桌面跑。", file=sys.stderr)
            mode = "foreground"
    return run_in_process(pattern, echo=not quiet)


if __name__ == "__main__":
    raise SystemExit(main())
