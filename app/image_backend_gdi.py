"""长图导出的 **Windows 后端**：ctypes 调 GDI 离屏画字。

只负责「字体」和「画布」两件事，排版和画什么由 `image_export.py` 决定。
接口与 `image_backend_cg.py` **一字不差**（同名 `Fonts` / `Surface`、同签名方法），
所以 `image_export.py` 只按平台挑一个，上层一行都不用改。

## 为什么不用「开一个隐藏窗口再截图」

窗口截图要靠 GDI 从窗口 DC 取像素，窗口被遮挡或跑到屏幕外都会拿到空白；
而且超长的窗口根本画不出来。这里直接建一块 32 位 DIB（内存位图）当画布，
想画多长画多长，整个过程不涉及任何窗口，不闪屏、不受遮挡影响。

坐标全部是**图片像素**，字号也是像素（正数），原点在左上角、y 向下。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from image_common import colorref, encode_png

__all__ = ["Fonts", "Surface", "FontKey"]

gdi32 = ctypes.windll.gdi32
user32 = ctypes.windll.user32      # FillRect 在 user32 里，不在 gdi32

BI_RGB = 0
DIB_RGB_COLORS = 0
TRANSPARENT = 1
FW_NORMAL = 400
FW_BOLD = 700
ANTIALIASED_QUALITY = 4
DEFAULT_CHARSET = 1
NULL_PEN = 8
HOLLOW_BRUSH = 5

#: 字体键：(family, size, bold, italic, underline, strike)
FontKey = tuple


class _LogFont(ctypes.Structure):
    _fields_ = [
        ("lfHeight", wintypes.LONG),
        ("lfWidth", wintypes.LONG),
        ("lfEscapement", wintypes.LONG),
        ("lfOrientation", wintypes.LONG),
        ("lfWeight", wintypes.LONG),
        ("lfItalic", wintypes.BYTE),
        ("lfUnderline", wintypes.BYTE),
        ("lfStrikeOut", wintypes.BYTE),
        ("lfCharSet", wintypes.BYTE),
        ("lfOutPrecision", wintypes.BYTE),
        ("lfClipPrecision", wintypes.BYTE),
        ("lfQuality", wintypes.BYTE),
        ("lfPitchAndFamily", wintypes.BYTE),
        ("lfFaceName", wintypes.WCHAR * 32),
    ]


class _TextMetric(ctypes.Structure):
    """TEXTMETRICW 的前半段。只用到 tmAscent / tmDescent / tmHeight。"""

    _fields_ = [
        ("tmHeight", wintypes.LONG),
        ("tmAscent", wintypes.LONG),
        ("tmDescent", wintypes.LONG),
        ("tmInternalLeading", wintypes.LONG),
        ("tmExternalLeading", wintypes.LONG),
        ("tmAveCharWidth", wintypes.LONG),
        ("tmMaxCharWidth", wintypes.LONG),
        ("tmWeight", wintypes.LONG),
        ("tmOverhang", wintypes.LONG),
        ("tmDigitizedAspectX", wintypes.LONG),
        ("tmDigitizedAspectY", wintypes.LONG),
        ("tmFirstChar", wintypes.WCHAR),
        ("tmLastChar", wintypes.WCHAR),
        ("tmDefaultChar", wintypes.WCHAR),
        ("tmBreakChar", wintypes.WCHAR),
        ("tmItalic", wintypes.BYTE),
        ("tmUnderlined", wintypes.BYTE),
        ("tmStruckOut", wintypes.BYTE),
        ("tmPitchAndFamily", wintypes.BYTE),
        ("tmCharSet", wintypes.BYTE),
    ]


class _BitmapInfoHeader(ctypes.Structure):
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


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("bmiHeader", _BitmapInfoHeader), ("bmiColors", wintypes.DWORD * 3)]


gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.POINTER(_BitmapInfo), wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.CreateFontIndirectW.argtypes = [ctypes.POINTER(_LogFont)]
gdi32.CreateFontIndirectW.restype = wintypes.HFONT
gdi32.GetTextMetricsW.argtypes = [wintypes.HDC, ctypes.POINTER(_TextMetric)]
gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.TextOutW.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int,
                           wintypes.LPCWSTR, ctypes.c_int]
gdi32.GetTextExtentPoint32W.argtypes = [wintypes.HDC, wintypes.LPCWSTR,
                                        ctypes.c_int, ctypes.POINTER(wintypes.SIZE)]
gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
user32.FillRect.restype = ctypes.c_int
gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.COLORREF]
gdi32.CreatePen.restype = wintypes.HPEN
gdi32.Ellipse.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, ctypes.c_int]
gdi32.Rectangle.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int,
                            ctypes.c_int, ctypes.c_int]
gdi32.MoveToEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
gdi32.LineTo.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.GetStockObject.argtypes = [ctypes.c_int]
gdi32.GetStockObject.restype = wintypes.HGDIOBJ


class Fonts:
    """字体句柄缓存 + 一个专门用来量文字宽度的内存 DC。

    量宽度和画画用不同的 DC：画布那块 DC 选着位图，量宽度不需要，
    分开之后量宽度不会踩到画布的状态。
    """

    def __init__(self) -> None:
        self.dc = gdi32.CreateCompatibleDC(None)
        self._handles: dict[FontKey, int] = {}
        self._metrics: dict[FontKey, tuple[int, int]] = {}
        self._current: int | None = None
        self._widths: dict[tuple[FontKey, str], int] = {}

    def handle(self, key: FontKey) -> int:
        handle = self._handles.get(key)
        if handle is None:
            family, size, bold, italic, underline, strike = key
            spec = _LogFont()
            spec.lfHeight = -int(size)          # 负数 = 字面高度（em）
            spec.lfWeight = FW_BOLD if bold else FW_NORMAL
            spec.lfItalic = 1 if italic else 0
            spec.lfUnderline = 1 if underline else 0
            spec.lfStrikeOut = 1 if strike else 0
            spec.lfCharSet = DEFAULT_CHARSET
            spec.lfQuality = ANTIALIASED_QUALITY
            spec.lfFaceName = family[:31]
            handle = gdi32.CreateFontIndirectW(ctypes.byref(spec))
            self._handles[key] = handle
        return handle

    def select(self, key: FontKey) -> int:
        handle = self.handle(key)
        if handle != self._current:
            gdi32.SelectObject(self.dc, handle)
            self._current = handle
        return handle

    def metrics(self, key: FontKey) -> tuple[int, int]:
        """(基线以上的高度, 基线以下的高度)。画字要按基线对齐，全靠它。"""
        cached = self._metrics.get(key)
        if cached is None:
            self.select(key)
            info = _TextMetric()
            gdi32.GetTextMetricsW(self.dc, ctypes.byref(info))
            cached = (int(info.tmAscent), int(info.tmDescent))
            self._metrics[key] = cached
        return cached

    def width(self, text: str, key: FontKey) -> int:
        if not text:
            return 0
        cached = self._widths.get((key, text))
        if cached is not None:
            return cached
        self.select(key)
        size = wintypes.SIZE()
        gdi32.GetTextExtentPoint32W(self.dc, text, len(text), ctypes.byref(size))
        if len(text) <= 64:
            self._widths[(key, text)] = size.cx
        return size.cx

    def char_widths(self, text: str, key: FontKey) -> list[int]:
        """逐字宽度，用来决定从哪里断行。

        逐字宽度之和与整串实测宽度会差几个像素（西文有字距调整），
        只影响断行位置，不影响最终绘制，够用了。
        """
        return [self.width(char, key) for char in text]

    def close(self) -> None:
        for handle in self._handles.values():
            gdi32.DeleteObject(handle)
        self._handles.clear()
        self._metrics.clear()
        self._widths.clear()
        if self.dc:
            gdi32.DeleteDC(self.dc)
            self.dc = None


class Surface:
    """一块 32 位 DIB 画布。坐标都是像素，原点在左上角。"""

    def __init__(self, fonts: Fonts, width: int, height: int, background: str) -> None:
        self.fonts = fonts
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        info = _BitmapInfo()
        info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
        info.bmiHeader.biWidth = self.width
        info.bmiHeader.biHeight = -self.height     # 负数 = 自上而下
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB
        self._bits = ctypes.c_void_p()
        self._bitmap = gdi32.CreateDIBSection(
            None, ctypes.byref(info), DIB_RGB_COLORS, ctypes.byref(self._bits), None, 0)
        if not self._bitmap:
            raise OSError(f"创建画布失败（{self.width}×{self.height} 可能超出内存）")
        self.dc = gdi32.CreateCompatibleDC(None)
        gdi32.SelectObject(self.dc, self._bitmap)
        self._font: FontKey | None = None
        self._brush = None
        self._brush_color: str | None = None
        self.fill(0, 0, self.width, self.height, background)

    # ---- 基础绘制 ----

    def _use_brush(self, color: str):
        if self._brush_color != color:
            if self._brush is not None:
                gdi32.DeleteObject(self._brush)
            self._brush = gdi32.CreateSolidBrush(colorref(color))
            self._brush_color = color
        return self._brush

    def fill(self, x0: float, y0: float, x1: float, y1: float, color: str) -> None:
        if x1 <= x0 or y1 <= y0:
            return
        rect = wintypes.RECT(int(x0), int(y0), int(x1 + 0.5), int(y1 + 0.5))
        user32.FillRect(self.dc, ctypes.byref(rect), self._use_brush(color))

    def text(self, x: float, baseline: float, content: str, key: FontKey, color: str) -> None:
        """按**基线**画字：GDI 的 TextOut 是顶对齐，所以自己减掉 ascent。"""
        if not content:
            return
        ascent = self.fonts.metrics(key)[0]
        handle = self.fonts.handle(key)
        if handle != self._font:
            gdi32.SelectObject(self.dc, handle)
            self._font = key
        gdi32.SetBkMode(self.dc, TRANSPARENT)
        gdi32.SetTextColor(self.dc, colorref(color))
        gdi32.TextOutW(self.dc, int(x), int(baseline - ascent), content, len(content))

    def _pen(self, color: str, thickness: int = 1):
        pen = gdi32.CreatePen(0, max(1, thickness), colorref(color))
        old = gdi32.SelectObject(self.dc, pen)
        return pen, old

    def _restore_pen(self, saved) -> None:
        pen, old = saved
        gdi32.SelectObject(self.dc, old)
        gdi32.DeleteObject(pen)

    def _shape(self, draw, x0, y0, x1, y1, fill_color, outline_color, thickness) -> None:
        brush = self._use_brush(fill_color) if fill_color else gdi32.GetStockObject(HOLLOW_BRUSH)
        old_brush = gdi32.SelectObject(self.dc, brush)
        saved = None
        old_pen = None
        if outline_color:
            saved = self._pen(outline_color, thickness)
        else:
            old_pen = gdi32.SelectObject(self.dc, gdi32.GetStockObject(NULL_PEN))
        draw(self.dc, int(x0), int(y0), int(x1 + 0.5), int(y1 + 0.5))
        if saved is not None:
            self._restore_pen(saved)
        else:
            gdi32.SelectObject(self.dc, old_pen)
        gdi32.SelectObject(self.dc, old_brush)

    def ellipse(self, x0, y0, x1, y1, fill_color, outline_color, thickness: int = 1) -> None:
        self._shape(gdi32.Ellipse, x0, y0, x1, y1, fill_color, outline_color, thickness)

    def rect(self, x0, y0, x1, y1, fill_color, outline_color, thickness: int = 1) -> None:
        self._shape(gdi32.Rectangle, x0, y0, x1, y1, fill_color, outline_color, thickness)

    def polyline(self, points: list[tuple[float, float]], color: str, thickness: int) -> None:
        if len(points) < 2:
            return
        saved = self._pen(color, thickness)
        gdi32.MoveToEx(self.dc, int(points[0][0]), int(points[0][1]), None)
        for x, y in points[1:]:
            gdi32.LineTo(self.dc, int(x), int(y))
        self._restore_pen(saved)

    def to_png(self) -> bytes:
        """就地读像素编码成 PNG，不整块复制一份位图（长图可能上百 MB）。"""
        stride = self.width * 4
        address = self._bits.value or 0

        def rows():
            for y in range(self.height):
                yield ctypes.string_at(address + y * stride, stride)

        return encode_png(self.width, self.height, rows())

    def close(self) -> None:
        # 先删 DC：位图和字体都还选在里面，删掉 DC 会自动解除选择，
        # 之后 DeleteObject 才真的释放得掉。
        if self.dc:
            gdi32.DeleteDC(self.dc)
            self.dc = None
        if self._bitmap:
            gdi32.DeleteObject(self._bitmap)
            self._bitmap = None
        if self._brush is not None:
            gdi32.DeleteObject(self._brush)
            self._brush = None
