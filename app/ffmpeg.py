"""找 ffmpeg，用它把 WAV 转成 MP3。**可选的外部工具，找不到就安静降级。**

## 为什么这里允许起外部进程

`speech.py` 绕了一大圈用 ctypes 直调 COM，就是为了不起进程、不闪控制台窗口。
这里破了一次例，理由要说清楚：

- 转 MP3 **没有**纯标准库的办法。Python 标准库没有任何 MP3 编码器，
  自己实现一个 LAME 是另一个量级的工程。
- 它是**可选的一步**：只在用户把导出文件存成 `.mp3` 时才走。找不到 ffmpeg
  就只提供 WAV，其余功能一点不受影响。
- 用户自己装了 ffmpeg（本机在 `D:\\OpenToUseSW\\ffmpeg-master-latest-win64-gpl-shared`），
  这是明确的用户意图，不是我们偷偷塞的依赖。
- **不闪黑框**：`CREATE_NO_WINDOW` 起进程，输出全部走管道收回来。

## 为什么不能只靠 `shutil.which`

`shutil.which("ffmpeg")` 只看 PATH 里每一层**本身**有没有 `ffmpeg.exe`。
可 ffmpeg 的 Windows 包解压出来是 `ffmpeg-xxx/bin/ffmpeg.exe`，很多人图省事
把**解压出来的那个目录**（不是 `bin`）加进 PATH —— 本机就是这样：
PATH 里有 `D:\\OpenToUseSW\\ffmpeg-master-latest-win64-gpl-shared`，而 exe 在它下面的
`bin\\` 里，于是 `where ffmpeg` 找不到，但东西其实就在那儿。所以 `_locate()`
在 PATH 每一层**再往下探一层 `bin\\`**。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

__all__ = ["FFmpegError", "find", "is_available", "to_mp3"]

#: 起进程时不弹控制台窗口（Windows）。别的平台上是 0。
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

#: 可以用这个环境变量直接指到某个 ffmpeg.exe，省得去找。
ENV_VAR = "JIANJI_FFMPEG"

#: PATH 上都没有时，再看这几个常见位置。
_COMMON = (
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
    r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
)

#: 默认的 MP3 品质（libmp3lame 的 VBR `-q:a`，0 最好、9 最差）。
#: 2 ≈ 190 kbps。朗读不需要更高，再往上只是白占地方。
DEFAULT_QUALITY = 2

_cached: Path | None = None
_resolved = False


class FFmpegError(RuntimeError):
    """找不到 ffmpeg，或者它转换失败。调用方只需要弹个框。"""


def _locate() -> Path | None:
    override = os.environ.get(ENV_VAR)
    if override:
        candidate = Path(override)
        if candidate.is_file():
            return candidate

    found = shutil.which("ffmpeg")
    if found:
        return Path(found)

    # 见模块开头：PATH 里那一项常常是解压目录本身，exe 在它下面的 bin\ 里
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        for relative in ("ffmpeg.exe", os.path.join("bin", "ffmpeg.exe")):
            candidate = Path(entry) / relative
            if candidate.is_file():
                return candidate

    for raw in _COMMON:
        candidate = Path(raw)
        if candidate.is_file():
            return candidate
    return None


def find() -> Path | None:
    """ffmpeg.exe 的完整路径；找不到返回 `None`。

    **结果会缓存**：`is_available()` 在导出对话框打开前会被问一次，转换时又问一次，
    每次都把 PATH 翻一遍太浪费。找不到时也缓存（不然每次都要把整个 PATH 走两遍）。
    装了 ffmpeg 之后要重启软件才会认——这个取舍是有意的，比每次去摸盘便宜。
    """
    global _cached, _resolved
    if not _resolved:
        _cached = _locate()
        _resolved = True
    return _cached


def is_available() -> bool:
    return find() is not None


def _reset_cache() -> None:
    """忘掉上次找到的结果，重新找一遍。

    给测试用；装上 ffmpeg 之后不想重启软件，也可以拿它重来一次。
    """
    global _cached, _resolved
    _cached = None
    _resolved = False


def to_mp3(source: Path, target: Path, *, quality: int = DEFAULT_QUALITY,
           timeout: float = 600.0) -> int:
    """把 WAV 转成 MP3，返回写出的字节数。失败抛 `FFmpegError`。

    `-map_metadata -1` 是**必须的**：不加的话 ffmpeg 会把输入文件里的元数据
    （含我们的临时文件名）带进 MP3，播放器里会显示一串莫名其妙的东西。
    """
    binary = find()
    if binary is None:
        raise FFmpegError("没有找到 ffmpeg，无法转成 MP3。")
    source = Path(source)
    target = Path(target)
    if not source.is_file():
        raise FFmpegError(f"找不到要转换的文件：{source}")
    target.parent.mkdir(parents=True, exist_ok=True)

    command = [str(binary), "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(source), "-codec:a", "libmp3lame", "-q:a", str(quality),
               "-map_metadata", "-1", str(target)]
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout,
                                creationflags=_CREATE_NO_WINDOW)
    except OSError as error:
        raise FFmpegError(f"无法运行 ffmpeg：{error}") from error
    except subprocess.TimeoutExpired as error:
        raise FFmpegError("ffmpeg 转换超时。") from error
    if result.returncode != 0:
        detail = (result.stderr or b"").decode("utf-8", "replace").strip()
        raise FFmpegError(f"ffmpeg 返回 {result.returncode}：{detail[-400:]}")
    if not target.is_file():
        raise FFmpegError("ffmpeg 没有写出文件。")
    return target.stat().st_size
