"""把 Tk 窗口截图存成 PNG，用于人工核验界面。

只用标准库：ctypes 调 GDI 抓像素，手写 PNG 编码，不依赖 Pillow 等第三方库。
用法：

    from capture_window import capture_widget
    capture_widget(app.root, "outputs/界面预览.png")

或命令行（会把前台窗口截下来）：

    python tools/capture_window.py 输出路径.png
"""
from __future__ import annotations

import ctypes
import struct
import sys
import zlib
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


# 必须声明 argtypes：64 位下句柄是 8 字节，不声明的话 ctypes 会按 C 的 int
# （4 字节）传参，句柄一大就抛 `OverflowError: int too long to convert`。
# 这个错误只在句柄恰好很大时出现，所以以前偶尔能跑通、偶尔炸。
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int,
                         wintypes.DWORD]
gdi32.BitBlt.restype = wintypes.BOOL
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                            wintypes.UINT, ctypes.c_void_p,
                            ctypes.POINTER(BITMAPINFO), wintypes.UINT]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def encode_png(width: int, height: int, bgra_bottom_up: bytes) -> bytes:
    """BGRA（自下而上）→ PNG 字节流。"""
    stride = width * 4
    rows: list[bytes] = []
    for y in range(height - 1, -1, -1):
        row = bytearray(bgra_bottom_up[y * stride : (y + 1) * stride])
        # BGRA -> RGBA：交换 B/R，并把 alpha 全部置为不透明
        row[0::4], row[2::4] = row[2::4], row[0::4]
        row[3::4] = b"\xff" * width
        rows.append(b"\x00" + bytes(row))
    raw = b"".join(rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw, 6))
        + _chunk(b"IEND", b"")
    )


def _dib_bytes(memdc, bitmap, width: int, height: int) -> bytes:
    """把已画好的位图读成 BGRA（自下而上）。"""
    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = height  # 正数 = 自下而上
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0
    buffer = ctypes.create_string_buffer(width * height * 4)
    if not gdi32.GetDIBits(memdc, bitmap, 0, height, buffer, ctypes.byref(info),
                           DIB_RGB_COLORS):
        raise OSError("GetDIBits 失败")
    return buffer.raw


def _blit(source_hdc, x: int, y: int, width: int, height: int) -> bytes:
    """从某个 DC 的 (x, y) 起拷 width×height 像素，返回 BGRA（自下而上）。"""
    memdc = gdi32.CreateCompatibleDC(source_hdc)
    bitmap = gdi32.CreateCompatibleBitmap(source_hdc, width, height)
    gdi32.SelectObject(memdc, bitmap)
    try:
        gdi32.BitBlt(memdc, 0, 0, width, height, source_hdc, x, y, SRCCOPY)
        return _dib_bytes(memdc, bitmap, width, height)
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memdc)


def grab_hwnd(hwnd: int) -> tuple[int, int, bytes]:
    """抓取指定窗口的客户区像素，返回 (宽, 高, BGRA 自下而上)。"""
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise OSError("GetClientRect 失败")
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise OSError(f"窗口尺寸无效：{width}x{height}")

    hdc = user32.GetDC(hwnd)
    try:
        return width, height, _blit(hdc, 0, 0, width, height)
    finally:
        user32.ReleaseDC(hwnd, hdc)


def grab_screen_rect(x: int, y: int, width: int, height: int) -> tuple[int, int, bytes]:
    """抓屏幕上的一块区域（屏幕坐标），返回 (宽, 高, BGRA 自下而上)。

    和 grab_hwnd 的区别是走屏幕 DC（GetDC(0)）：菜单这类弹出窗口没有可抓的
    客户区（`winfo_id()` 抓到的是 1×1），只能连它背后的窗口一起从屏幕上截。
    """
    if width <= 0 or height <= 0:
        raise OSError(f"区域尺寸无效：{width}x{height}")
    hdc = user32.GetDC(None)          # NULL = 整个屏幕
    try:
        return width, height, _blit(hdc, x, y, width, height)
    finally:
        user32.ReleaseDC(None, hdc)


def capture_hwnd(hwnd: int, out_path: str | Path) -> tuple[int, int]:
    width, height, data = grab_hwnd(hwnd)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_png(width, height, data))
    return width, height


def capture_screen_rect(out_path: str | Path, box: tuple[int, int, int, int]) -> tuple[int, int]:
    """抓屏幕上一块区域（x, y, 宽, 高，屏幕坐标）。菜单/提示框只能这样抓。"""
    x, y, width, height = box
    cw, ch, data = grab_screen_rect(x, y, width, height)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_png(cw, ch, data))
    return cw, ch


def is_blank(bgra: bytes) -> bool:
    """整幅是不是**一种颜色**。

    隐藏桌面（`tools/quiet_desktop.py`）上从**屏幕 DC** 抓回来的图是纯色
    （实测纯黑）：`GetDC(NULL)` 给的是「当前桌面的屏幕」，而那个桌面上什么都没有。
    **窗口 DC**（`grab_hwnd` / `capture_widget_region`）不受影响。
    截原生菜单只能走屏幕 DC，所以那两个脚本必须在当前桌面上跑——靠这个函数兜底报警，
    免得静默产出一张黑图还当成核验通过。
    """
    if len(bgra) < 4:
        return True
    # 整串直接比，不要在 Python 里逐像素循环（128 万个像素会慢得没意义）
    return bgra == bytes(bgra[:4]) * (len(bgra) // 4)


def capture_screen_rect_checked(
        out_path: str | Path,
        box: tuple[int, int, int, int]) -> tuple[int, int, bool]:
    """`capture_screen_rect` 的带检查版，多返回一个「这幅图是不是一片纯色」。"""
    x, y, width, height = box
    cw, ch, data = grab_screen_rect(x, y, width, height)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_png(cw, ch, data))
    return cw, ch, is_blank(data)


def capture_widget(widget, out_path: str | Path) -> tuple[int, int]:
    """截取控件所属的顶层窗口（对 Toplevel 就是它自己，对子控件就是主窗口）。"""
    top = widget.winfo_toplevel()
    top.update_idletasks()
    top.update()
    return capture_hwnd(int(top.winfo_id()), out_path)


def crop_bgra(w: int, h: int, data: bytes, box: tuple[int, int, int, int]) -> tuple[int, int, bytes]:
    """从自下而上的 BGRA 缓冲里裁一块，坐标按屏幕方向（左上为原点）。

    data 自下而上：索引 k 对应屏幕行 (h - 1 - k)。输出同样自下而上，
    所以从裁剪区底行 (y2 - 1) 往上取。
    """
    x1, y1, x2, y2 = box
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    cw, ch = x2 - x1, y2 - y1
    if cw <= 0 or ch <= 0:
        raise ValueError(f"裁剪区域无效：{box} 于 {w}x{h}")
    stride = w * 4
    rows = []
    for screen_y in range(y2 - 1, y1 - 1, -1):
        off = (h - 1 - screen_y) * stride + x1 * 4
        rows.append(data[off : off + cw * 4])
    return cw, ch, b"".join(rows)


def upscale_bgra(w: int, h: int, data: bytes, factor: int) -> tuple[int, int, bytes]:
    """最近邻放大（自下而上 BGRA），用来放大细节看边缘是否锐利。"""
    if factor <= 1:
        return w, h, data
    stride = w * 4
    out_rows = []
    for y in range(h):
        row = data[y * stride : (y + 1) * stride]
        wide = b"".join(row[i * 4 : i * 4 + 4] * factor for i in range(w))
        out_rows.append(wide * factor)
    return w * factor, h * factor, b"".join(out_rows)


def capture_widget_region(widget, out_path: str | Path, factor: int = 2) -> tuple[int, int]:
    """截取顶层窗口，再裁出指定控件所在区域并放大，用于细节核验。

    返回放大后的尺寸。
    """
    top = widget.winfo_toplevel()
    top.update_idletasks()
    top.update()
    w, h, data = grab_hwnd(int(top.winfo_id()))
    x = widget.winfo_rootx() - top.winfo_rootx()
    y = widget.winfo_rooty() - top.winfo_rooty()
    cw, ch, cdata = crop_bgra(w, h, data, (x, y, x + widget.winfo_width(), y + widget.winfo_height()))
    zw, zh, zdata = upscale_bgra(cw, ch, cdata, factor)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_png(zw, zh, zdata))
    return zw, zh


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "outputs/前台窗口.png"
    hwnd = user32.GetForegroundWindow()
    size = capture_hwnd(hwnd, target)
    print(f"SCREENSHOT {Path(target).resolve()} {size[0]} x {size[1]}")
