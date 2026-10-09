"""平台差异的**唯一落点**。

简记原本是纯 Windows 程序：创建时间靠 `SetFileTime`、彻底删除靠 `SHFileOperationW`、
状态目录靠 `%LOCALAPPDATA%`、音频靠 SAPI、长图靠 GDI。要上 macOS 就得给每一处
找一个对应物；而 `os.name != "nt"` 这种东西一旦散落到七八个模块里，
「哪些地方还没移植」就再也数不清了 —— 所以全部收到这里。

**约定**：`app/` 下别的模块不许再出现裸的 `sys.platform` / `os.name` / `ctypes.windll`。
要判断平台就 import 这里的常量，要平台专属动作就调这里的函数。
唯二的例外是 `image_export.py` 与 `speech.py` 里的后端实现 —— 那两个文件本来就是
「一个平台一个实现」的形状，平台分支写在它们自己内部。

## macOS 上几个关键对应物

| 这里做的事 | Windows | macOS |
|---|---|---|
| 读创建时间 | `st_ctime`（Windows 上它就是创建时间） | `st_birthtime` |
| 写创建时间 | `SetFileTime` | `setattrlist`（`ATTR_CMN_CRTIME`） |
| 彻底删除 | `SHFileOperationW`（进回收站） | 移进 `~/.Trash` |
| 状态目录 | `%LOCALAPPDATA%\\JianJi` | `~/Library/Application Support/JianJi` |
| 在文件管理器里显示 | `explorer /select,` | `open -R` |
| 不弹控制台窗口 | `CREATE_NO_WINDOW` | 无此问题，恒为 0 |

`setattrlist` 是这里唯一「非显然」的一步：标准库没有写创建时间的接口
（`os.utime` 只管访问时间和修改时间），macOS 也没有 `SetFileTime` 那样的现成函数，
只能自己拼一个 `struct attrlist` 交给 libc。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

__all__ = [
    "WINDOWS", "MACOS", "LINUX", "SUPPORTED",
    "PLATFORM_LABEL", "FILE_MANAGER_LABEL", "MODIFIER", "MODIFIER_LABEL",
    "app_dir", "state_dir", "default_state_path",
    "creation_time", "set_creation_time", "supports_creation_time",
    "reveal_in_file_manager", "move_to_system_trash",
    "NO_WINDOW", "run_quiet",
]

WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"
LINUX = sys.platform.startswith("linux")

#: 有移植实现的平台。别的平台上程序仍会启动（缺的功能安静降级），但不在支持范围。
SUPPORTED = WINDOWS or MACOS

PLATFORM_LABEL = "Windows" if WINDOWS else ("macOS" if MACOS else "Linux")

#: 界面文案里「那个打开文件夹的程序」叫什么。
FILE_MANAGER_LABEL = "资源管理器" if WINDOWS else ("访达" if MACOS else "文件管理器")

#: 快捷键的修饰键。macOS 上用户按的是 ⌘，不是 Ctrl —— 绑 Ctrl 也能用，
#: 但那在 macOS 上是「右键菜单键」的语义，属于一眼就不对的移植。
MODIFIER = "Command" if MACOS else "Control"
MODIFIER_LABEL = "⌘" if MACOS else "Ctrl"

APP_DIR_NAME = "JianJi"


# ------------------------------------------------------------------ 目录

def app_dir() -> Path:
    """放状态文件的目录（不保证存在）。

    macOS 上**不能用** `~/.jianji`：那是 Unix 老习惯，macOS 的规矩是
    `~/Library/Application Support`，备份工具与「访达 → 前往」都认这个位置。
    """
    if WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif MACOS:
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_DIR_NAME


def state_dir() -> Path:
    return app_dir()


def default_state_path() -> Path:
    return app_dir() / "state.json"


# ------------------------------------------------------------------ 创建时间
#
# 保存走的是「写临时文件 + `os.replace`」，而 `os.replace` 是**用源文件顶掉目标
# 文件** —— 连创建时间也变成这一次保存的时刻。所以每次替换之后要把旧的写回去。
#
# 注意：**这只能救回「从今往后」的创建时间**。已经丢掉的没法从文件系统上找回，
# 因为替换掉的旧文件对象已经不存在了。

_FILETIME_EPOCH = 11_644_473_600   # 1601-01-01 → 1970-01-01 的秒数
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_ALL = 0x00000007       # READ | WRITE | DELETE，别把别人的句柄挤掉
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x80

#: `struct attrlist` 的 bitmapcount 字段。macOS 头文件里是 `ATTR_BIT_MAP_COUNT`。
_ATTR_BIT_MAP_COUNT = 5
#: `ATTR_CMN_CRTIME` —— 「公共属性」组里的创建时间。
_ATTR_CMN_CRTIME = 0x00000200

_kernel32: Any = None              # 惰性加载；`False` 表示「试过了，这台机器上没有」
_libc: Any = None


def _load_kernel32():
    """拿到配好原型的 kernel32。非 Windows 或加载失败返回 None。"""
    global _kernel32
    if _kernel32 is None:
        if not WINDOWS:
            _kernel32 = False
            return None
        try:
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            # `CreateFileW` 返回的是句柄，**必须显式声明 `restype`**：ctypes 默认
            # 按 32 位 int 处理返回值，句柄高位被砍掉，接着就是一个无效句柄。
            kernel32.CreateFileW.restype = wintypes.HANDLE
            kernel32.CreateFileW.argtypes = [
                wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
            ]
            kernel32.SetFileTime.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                ctypes.c_void_p, ctypes.c_void_p,
            ]
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            _kernel32 = kernel32
        except (ImportError, AttributeError, OSError, ValueError):
            _kernel32 = False
    return _kernel32 or None


class _AttrList(ctypes.Structure):
    """macOS 的 `struct attrlist`（24 字节）。

    字段顺序照抄 `/usr/include/sys/attr.h`；`attrgroup_t` 是 `u_int32_t`，
    `bitmapcount` 与 `reserved` 各占 2 字节 —— 少写一个字段 libc 就会读到垃圾，
    而且是**静默失败**（返回非 0 而已），所以别凭印象改这个结构。
    """

    _fields_ = [
        ("bitmapcount", ctypes.c_ushort),
        ("reserved", ctypes.c_ushort),
        ("commonattr", ctypes.c_uint32),
        ("volattr", ctypes.c_uint32),
        ("dirattr", ctypes.c_uint32),
        ("fileattr", ctypes.c_uint32),
        ("forkattr", ctypes.c_uint32),
    ]


class _TimeSpec(ctypes.Structure):
    """`struct timespec`。64 位 macOS 上 `time_t` 与 `long` 都是 8 字节。"""

    _fields_ = [("tv_sec", ctypes.c_long), ("tv_nsec", ctypes.c_long)]


def _load_libc():
    """macOS 上拿到能调 `setattrlist` 的 libc。别处返回 None。"""
    global _libc
    if _libc is None:
        if not MACOS:
            _libc = False
            return None
        try:
            # 先试显式路径；失败再退回「主程序自己」（Python 就链着 libSystem，
            # 所以 `CDLL(None)` 也能解析到 setattrlist）。
            try:
                libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
            except OSError:
                libc = ctypes.CDLL(None, use_errno=True)
            libc.setattrlist.argtypes = [
                ctypes.c_char_p, ctypes.POINTER(_AttrList), ctypes.c_void_p,
                ctypes.c_size_t, ctypes.c_ulong,
            ]
            libc.setattrlist.restype = ctypes.c_int
            _libc = libc
        except (AttributeError, OSError, ValueError):
            _libc = False
    return _libc or None


def supports_creation_time() -> bool:
    """这个平台上能不能**读**创建时间。"""
    if WINDOWS:
        return True
    if MACOS:
        return hasattr(os.stat_result, "st_birthtime")
    return False


def creation_time(path: Path) -> float | None:
    """文件的**创建**时间；拿不到返回 None。

    Windows 上 `st_ctime` 就是创建时间；POSIX 上 `st_ctime` 是**元数据变更时间**
    （改权限、改名都会动它），所以那边必须用 `st_birthtime`，取不到就老实返回 None，
    不假装自己有这个信息。
    """
    try:
        info = Path(path).stat()
    except OSError:
        return None
    if WINDOWS:
        return info.st_ctime
    birth = getattr(info, "st_birthtime", None)
    return float(birth) if birth is not None else None


def set_creation_time(path: Path, timestamp: float) -> bool:
    """把创建时间写回文件。**best-effort**：失败返回 False，绝不打断保存。

    创建时间是给人看的信息，为它让保存失败不划算。
    """
    if WINDOWS:
        return _set_creation_time_windows(Path(path), timestamp)
    if MACOS:
        return _set_creation_time_macos(Path(path), timestamp)
    return False


def _set_creation_time_windows(path: Path, timestamp: float) -> bool:
    kernel32 = _load_kernel32()
    if kernel32 is None:
        return False
    from ctypes import wintypes
    ticks = int(timestamp * 10_000_000) + _FILETIME_EPOCH * 10_000_000
    created = wintypes.FILETIME(ticks & 0xFFFFFFFF, ticks >> 32)
    handle = kernel32.CreateFileW(
        str(path), _GENERIC_WRITE, _FILE_SHARE_ALL, None,
        _OPEN_EXISTING, _FILE_ATTRIBUTE_NORMAL, None,
    )
    if handle == ctypes.c_void_p(-1).value:      # INVALID_HANDLE_VALUE
        return False
    try:
        return bool(kernel32.SetFileTime(handle, ctypes.byref(created), None, None))
    finally:
        kernel32.CloseHandle(handle)


def _set_creation_time_macos(path: Path, timestamp: float) -> bool:
    libc = _load_libc()
    if libc is None:
        return False
    attributes = _AttrList()
    attributes.bitmapcount = _ATTR_BIT_MAP_COUNT
    attributes.commonattr = _ATTR_CMN_CRTIME
    seconds = int(timestamp)
    when = _TimeSpec(seconds, int(round((timestamp - seconds) * 1_000_000_000)))
    try:
        raw = os.fsencode(path)
    except (UnicodeEncodeError, OSError):
        return False
    result = libc.setattrlist(
        raw, ctypes.byref(attributes), ctypes.byref(when), ctypes.sizeof(when), 0
    )
    return result == 0


# ------------------------------------------------------------------ 文件管理器

def reveal_in_file_manager(path: Path) -> None:
    """在系统文件管理器里**选中**这个文件（文件夹则直接打开）。失败抛 OSError。"""
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"目标不存在：{target}")
    if WINDOWS:
        if target.is_dir():
            os.startfile(str(target))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["explorer", f"/select,{target}"])
        return
    if MACOS:
        # `open -R` = Reveal in Finder，正是「选中它」的语义；
        # 对文件夹用 `-R` 也行（会在父目录里选中它），但用户要的是「打开」，
        # 所以文件夹走不带 -R 的那条。
        command = ["open", str(target)] if target.is_dir() else ["open", "-R", str(target)]
        subprocess.Popen(command)
        return
    raise OSError(f"当前系统不支持打开{FILE_MANAGER_LABEL}。")


def move_to_system_trash(path: Path) -> None:
    """把文件或文件夹移进**系统**回收站；不做永久删除，失败即报错。

    macOS 上没有 `SHFileOperationW` 那样的现成函数。分两步走：
    先试「改名进 `~/.Trash`」—— 这是绝大多数情况，快且不弹任何东西；
    失败再退回 Finder（外置卷上的文件改名进 `~/.Trash` 会跨卷失败，
    而 Finder 自己知道该往哪儿放）。
    """
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"目标不存在：{target}")
    if not MACOS:
        raise OSError("当前系统不支持移到回收站，已取消删除。")

    trash = Path.home() / ".Trash"
    if trash.is_dir():
        candidate = trash / target.name
        counter = 1
        while candidate.exists():
            candidate = trash / f"{target.stem}-{counter}{target.suffix}"
            counter += 1
        try:
            os.rename(target, candidate)
            return
        except OSError:
            pass                        # 跨卷之类，交给 Finder

    script = 'tell application "Finder" to delete POSIX file {}'.format(
        _applescript_string(str(target))
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OSError(f"移到回收站失败：{error}") from error
    if result.returncode != 0:
        detail = (result.stderr or b"").decode("utf-8", "replace").strip()
        raise OSError(f"移到回收站失败：{detail[-200:] or 'Finder 拒绝了这次删除'}")


def _applescript_string(text: str) -> str:
    """把一段文本包成 AppleScript 字符串字面量（转义引号和反斜杠）。

    **别省这一步**：文件名里带个 `"` 就会把脚本截断成语法错误，
    而报错信息是「Finder 出了点问题」，跟文件名一点关系都看不出来。
    """
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


# ------------------------------------------------------------------ 起进程

#: 起外部进程时不弹控制台窗口（Windows）。别的平台上没有这回事，恒为 0。
NO_WINDOW = 0x08000000 if WINDOWS else 0


def run_quiet(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    """`subprocess.run` 的一层包装：在 Windows 上顺带屏蔽黑框。

    macOS 上传 `creationflags` 会直接抛 `ValueError`，所以这个参数**只能**在这里加，
    调用点不许自己传。
    """
    kwargs.setdefault("capture_output", True)
    if WINDOWS:
        kwargs.setdefault("creationflags", NO_WINDOW)
    return subprocess.run(command, **kwargs)


def popen_quiet(command: list[str], **kwargs: Any) -> subprocess.Popen:
    """`subprocess.Popen` 的同款包装（不等待的场景用，比如拉起文件管理器）。"""
    if WINDOWS:
        kwargs.setdefault("creationflags", NO_WINDOW)
    return subprocess.Popen(command, **kwargs)
