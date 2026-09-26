"""核验「在隐藏桌面上跑测试」真的不打扰前台。

用户的原话是「测试界面可否在虚拟桌面，或后台进行，我需要继续在前台工作」。
所以要证明的不是「跑通了」，而是**跑的时候当前桌面什么都没发生**：

* 当前桌面上**没有冒出任何新的 Tk 顶层窗口**——屏幕上一个窗口都没闪；
* 前台窗口**从没落到被测进程头上**——焦点一次都没被抢。

第二条必须按**进程**判，不能只比句柄：用户本来就在前台干活，跑测试这一两分钟里
他自己切窗口是正常的，只比「句柄有没有变」会把用户的操作算到测试头上（第一次跑就误判了）。
所以每次采样都记下前台窗口属于哪个 PID，最后看被测进程的 PID 有没有出现在里面。

做法：本进程留在用户桌面上当「观察者」，每 0.2 秒采一次样，同时用 `Popen` 起
`tools/run_tests.py`（默认走隐藏桌面），跑完比对。

用法：

    python tools/_check_quiet_desktop.py                 # 观察全量测试
    python tools/_check_quiet_desktop.py -k sort         # 只跑一小撮，快
    python tools/_check_quiet_desktop.py --foreground    # 反面对照：故意在前台跑
                                                         # （应该能抓到窗口冒出来）
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]

user32 = ctypes.WinDLL("user32", use_last_error=True)
# 64 位句柄一定要声明类型，否则 ctypes 按 C int 传会随机 OverflowError
user32.GetForegroundWindow.restype = wintypes.HWND
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL,
                                                  wintypes.HWND,
                                                  wintypes.LPARAM),
                               wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                            ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

#: 采样间隔。**必须够密**：测试里每个用例的窗口只活一百多毫秒
#: （`-k sort` 31 个用例约 5 秒），0.2 秒一次会大片漏掉——
#: 漏掉的表现就是「反面对照也报干净」，那这个核验就白做了。
SAMPLE_INTERVAL = 0.05


def _window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _tk_windows() -> dict[int, str]:
    """当前桌面上可见的 Tk 顶层窗口：句柄 -> 标题。"""
    found: dict[int, str] = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, name, 256)
        if name.value.startswith("Tk"):
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            found[hwnd] = title.value
        return True

    user32.EnumWindows(visit, 0)
    return found


def _watch(stop: threading.Event, samples: list) -> None:
    while not stop.is_set():
        hwnd = user32.GetForegroundWindow()
        samples.append((hwnd, _window_pid(hwnd), _tk_windows()))
        stop.wait(SAMPLE_INTERVAL)


def main() -> int:
    arguments = sys.argv[1:]
    foreground = bool(arguments) and arguments[0] == "--foreground"
    if foreground:
        command = [sys.executable, str(PROJECT / "tools" / "run_tests.py"),
                   "--foreground"] + arguments[1:]
        label = "前台（对照）"
    else:
        command = [sys.executable, str(PROJECT / "tools" / "run_tests.py"),
                   *arguments]
        label = "隐藏桌面"

    before_foreground = user32.GetForegroundWindow()
    before_pid = _window_pid(before_foreground)
    before_windows = _tk_windows()
    print(f"跑法：{label}")
    print(f"开始前：前台窗口 {before_foreground}（pid {before_pid}），"
          f"当前桌面 Tk 窗口 {len(before_windows)} 个")

    samples: list = []
    stop = threading.Event()
    watcher = threading.Thread(target=_watch, args=(stop, samples), daemon=True)
    watcher.start()

    started = time.time()
    child = subprocess.Popen(command, cwd=PROJECT, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace")
    child_output, _ = child.communicate()
    elapsed = time.time() - started
    child_pid = child.pid

    stop.set()
    watcher.join(timeout=2)

    after_foreground = user32.GetForegroundWindow()
    after_windows = _tk_windows()

    foreground_pids = {pid for _, pid, _ in samples}
    seen_windows: dict[int, str] = {}
    for _, _, windows in samples:
        seen_windows.update(windows)
    new_windows = {hwnd: title for hwnd, title in seen_windows.items()
                   if hwnd not in before_windows}

    print(f"被测进程 pid {child_pid}，跑了 {elapsed:.1f} 秒，采样 {len(samples)} 次")
    print(f"前台窗口：开始 {before_foreground}（pid {before_pid}）"
          f" → 结束 {after_foreground}（pid {_window_pid(after_foreground)}）")
    print(f"期间出现过 {len(foreground_pids)} 个不同的前台进程："
          f"{sorted(foreground_pids)}")
    print(f"当前桌面上的 Tk 窗口：开始 {len(before_windows)} 个，"
          f"期间冒出过 {len(new_windows)} 个新的")
    for hwnd, title in sorted(new_windows.items()):
        print(f"    新窗口 {hwnd}  「{title}」")

    stole_focus = child_pid in foreground_pids
    if stole_focus:
        print(f"**焦点被抢过**：前台窗口出现过被测进程 pid {child_pid}。")

    if foreground:
        print(f"（对照）前台跑：预期会看到新窗口，实际 {len(new_windows)} 个、"
              f"抢焦点 {'是' if stole_focus else '否'} —— "
              f"{'符合预期' if new_windows or stole_focus else '没抓到，观察窗口太短？'}")
        return 0

    if not new_windows and not stole_focus:
        print("结论：前台完全没被打扰（屏幕没闪窗口、焦点没被抢）。")
        return 0
    print("结论：**被打扰了**，隐藏桌面没生效。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
