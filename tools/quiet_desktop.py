"""把子进程放到一个**隐藏的 Windows 桌面**上跑。

为什么需要这个：测试和截图脚本都要建真实的 Tk 窗口，而 Tk 的几何量算
（`dlineinfo`、`winfo_width`）只有窗口**真的被映射**了才有意义，`withdraw()`
藏起来是量不出来的。于是每跑一次全量测试，屏幕上就闪几百次窗口、焦点被抢走
几十次——用户正在前台干活，这样很烦人。

试过但不够的办法：

* **把窗口挪到屏幕外**（`geometry("1180x760+-4000+-4000")`）：几何是对的，
  但**窗口不会绘制**，`GetDC(hwnd)+BitBlt` 抓回来是一片纯色，
  `PrintWindow` 只画得出边框、画不出正文（实测）。而且焦点照样被抢。
* **`WS_EX_NOACTIVATE`**：实测挡不住（Tk 映射窗口时会激活它）。

真正管用的是**换一个桌面**。Windows 的焦点是**按桌面**算的，别的桌面上的窗口
根本不可能抢走当前桌面的前台——屏幕上也不会出现任何东西。同一个窗口站
（WinSta0）里的另一个桌面照样有完整的 GDI 与合成能力，所以 `GetDC(NULL)`
拿到的是**那个桌面的**屏幕 DC，现有那些从屏幕 DC 抓图的脚本不用改。

用法：

    python tools/quiet_desktop.py <命令> [参数...]     # 在隐藏桌面上跑一条命令

作为模块用：

    from quiet_desktop import run_hidden
    code = run_hidden([sys.executable, "tools/run_tests.py", "--in-process"])

实现要点（ctypes 调 Win32 的老坑，踩过就别再踩）：

* **句柄一定要声明 `argtypes`/`restype`**，否则 ctypes 按 C int 传，
  句柄超过 2^31 时随机 `OverflowError`。
* `CreateProcessW` 的 `lpCommandLine` 必须是**可写缓冲区**（它可能就地改）。
* 桌面的生命周期跟句柄绑定：父进程要一直握着 `hDesktop`，子进程才活在那个桌面上；
  父进程一关，桌面就销毁了。
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --- 桌面 ---
user32.CreateDesktopW.argtypes = [
    wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
    wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
]
user32.CreateDesktopW.restype = wintypes.HANDLE
user32.CloseDesktop.argtypes = [wintypes.HANDLE]
user32.CloseDesktop.restype = wintypes.BOOL

DESKTOP_ALL_ACCESS = 0x000F01FF

# --- 进程 ---
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.GetExitCodeProcess.restype = wintypes.BOOL

GENERIC_WRITE = 0x40000000
CREATE_ALWAYS = 2
FILE_ATTRIBUTE_NORMAL = 0x80
INFINITE = 0xFFFFFFFF
STARTF_USESTDHANDLES = 0x00000100
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


class STARTUPINFO(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


kernel32.CreateProcessW.argtypes = [
    wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
    wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
    ctypes.POINTER(STARTUPINFO), ctypes.POINTER(PROCESS_INFORMATION),
]
kernel32.CreateProcessW.restype = wintypes.BOOL

DESKTOP_NAME = "JianJiQuiet"


def _quote(argument: str) -> str:
    """按 Windows 命令行规则给一个参数加引号。"""
    if argument and not any(char in argument for char in ' \t"'):
        return argument
    out = ['"']
    backslashes = 0
    for char in argument:
        if char == "\\":
            backslashes += 1
            continue
        if char == '"':
            out.append("\\" * (backslashes * 2 + 1))
            out.append('"')
        else:
            out.append("\\" * backslashes)
            out.append(char)
        backslashes = 0
    out.append("\\" * (backslashes * 2))
    out.append('"')
    return "".join(out)


def run_hidden(argv: list[str], cwd: str | Path | None = None,
               stdout_path: str | Path | None = None) -> int:
    """在隐藏桌面上跑 `argv`，返回退出码。

    `stdout_path` 给了就把子进程的 stdout/stderr 都写进那个文件
    （用可继承的文件句柄接过去），不给就继承当前进程的。
    """
    desktop = user32.CreateDesktopW(DESKTOP_NAME, None, None, 0,
                                    DESKTOP_ALL_ACCESS, None)
    if not desktop:
        raise OSError(f"CreateDesktopW 失败：{ctypes.get_last_error()}")

    security = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), None, True)
    handle = None
    try:
        startup = STARTUPINFO()
        startup.cb = ctypes.sizeof(STARTUPINFO)
        startup.lpDesktop = DESKTOP_NAME

        if stdout_path is not None:
            path = Path(stdout_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = kernel32.CreateFileW(str(path), GENERIC_WRITE, 0,
                                          ctypes.byref(security), CREATE_ALWAYS,
                                          FILE_ATTRIBUTE_NORMAL, None)
            if handle == INVALID_HANDLE_VALUE:
                raise OSError(f"CreateFileW 失败：{ctypes.get_last_error()}")
            startup.dwFlags |= STARTF_USESTDHANDLES
            startup.hStdOutput = handle
            startup.hStdError = handle
            startup.hStdInput = None

        command = ctypes.create_unicode_buffer(" ".join(_quote(a) for a in argv))
        info = PROCESS_INFORMATION()
        ok = kernel32.CreateProcessW(
            None, command, None, None, True, 0, None,
            str(cwd) if cwd else None,
            ctypes.byref(startup), ctypes.byref(info),
        )
        if not ok:
            raise OSError(f"CreateProcessW 失败：{ctypes.get_last_error()}")

        try:
            kernel32.WaitForSingleObject(info.hProcess, INFINITE)
            code = wintypes.DWORD()
            kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
            return code.value
        finally:
            kernel32.CloseHandle(info.hThread)
            kernel32.CloseHandle(info.hProcess)
    finally:
        if handle is not None:
            kernel32.CloseHandle(handle)
        user32.CloseDesktop(desktop)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__.strip().splitlines()[0])
        print("用法：python tools/quiet_desktop.py <命令> [参数...]")
        return 2
    command = sys.argv[1:]
    print(f"在隐藏桌面「{DESKTOP_NAME}」上运行：{' '.join(command)}", flush=True)
    code = run_hidden(command, cwd=PROJECT)
    print(f"退出码 {code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
