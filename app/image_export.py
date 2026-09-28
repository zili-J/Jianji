"""把整篇文档渲染成一张长图。

只用标准库：ctypes 调 GDI 画字与图形，手写 PNG 编码，不依赖 Pillow。

为什么不用「开一个隐藏窗口再截图」：窗口截图要靠 GDI 从窗口 DC 取像素，
窗口被遮挡或跑到屏幕外都会拿到空白；而且超长的窗口根本画不出来。
这里直接建一块 32 位 DIB（内存位图）当画布，想画多长画多长，
整个过程不涉及任何窗口，不闪屏、不受遮挡影响。

调用方（`main.py`）负责把文档整理成「每行若干样式片段」——那是它的知识
（Markdown 解析、配色、标签）；本模块只负责排版和画。

坐标全部是**图片像素**，字号也是像素（正数）。调用方按
「正文目标字号 ÷ 编辑器字号」的比例把界面上的逻辑尺寸换算过来，
这样导出的图与屏幕上的观感一致。
"""
from __future__ import annotations

import ctypes
import struct
import zlib
from ctypes import wintypes
from dataclasses import dataclass, field, replace

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

# 手机屏宽一般是 1080，图按这个宽度出，不用缩放就能看
DEFAULT_WIDTH = 1080
DEFAULT_MARGIN = 72
DEFAULT_BODY_SIZE = 46          # 正文像素字号：一行正好 20 个汉字（见下）
# 用户要求「导出长图时每行显示 20 个字」。汉字在这几款中文字体里的步进**正好等于
# 字号**（实测 霞鹜文楷 / 微软雅黑：字号 32 → 单字宽 32px），所以
#   一行几个字 = 正文可用宽 ÷ 字号 = (1080 − 72×2) ÷ 46 = 936 ÷ 46 = 20.35
# 20 个字排下来 920px 放得进 936，21 个字 966px 放不进 ⇒ 正好 20 个一行。
# **别再往上调一档**：47 就只有 936÷47 = 19.91，20 个字要 940px 差 4px 放不下，
# 一行会退成 19 个字。
# 单张图的高度上限，超了自动分张。一屏 47 px 行高算下来能装六百多行，
# 日记正常写不到；再往上加，画布本身就要占几百 MB 内存，不值当。
# 这是**软**上限（判据见 `_split_chunks`）：宁可偶尔超几个百分点，
# 也不要为了卡死上限，把本来一张装得下的文档切成两张。
DEFAULT_MAX_HEIGHT = 30000


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


# ---------- 颜色与 PNG ----------

def colorref(hex_color: str) -> int:
    """`#RRGGBB` → GDI 的 COLORREF（内存里是 0x00BBGGRR）。"""
    value = hex_color.lstrip("#")
    r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    return r | (g << 8) | (b << 16)


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def encode_png(width: int, height: int, bgra_rows) -> bytes:
    """BGRA 行（自上而下，每行 width*4 字节）→ PNG 字节流。

    参数收的是**可迭代的一行行字节**而不是一整块缓冲区：长图动辄上百 MB，
    先拼出一份完整副本再编码会平白多占一倍内存，按行边读边压最省。

    不用行过滤器（filter 0）：文本图里大片是纯白，deflate 自己就能压得很好，
    而在 Python 里逐字节做 Sub/Up 过滤反而要慢一个量级。
    """
    compressor = zlib.compressobj(6)
    payload: list[bytes] = []
    opaque = b"\xff" * width
    for row in bgra_rows:
        line = bytearray(row)
        line[0::4], line[2::4] = line[2::4], line[0::4]     # BGRA → RGBA
        line[3::4] = opaque                                 # GDI 不填 alpha，统一补不透明
        payload.append(compressor.compress(b"\x00" + bytes(line)))
    payload.append(compressor.flush())
    # 压好的数据必须包进 IDAT 块：少了 length/tag/CRC 这三段，
    # 文件头看着还是 PNG，实际已经不是合法的分块结构了。
    return (PNG_SIGNATURE
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", b"".join(payload))
            + _chunk(b"IEND", b""))


# ---------- 字体与画布 ----------

FontKey = tuple          # (family, size, bold, italic, underline, strike)


class _Fonts:
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

    def __init__(self, fonts: _Fonts, width: int, height: int, background: str) -> None:
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


# ---------- 渲染输入 ----------

@dataclass
class Piece:
    """一段同样式文字。"""

    text: str
    family: str
    size: int                       # 像素字号（正数）
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    color: str = "#23211E"
    background: str | None = None

    @property
    def font(self) -> FontKey:
        return (self.family, self.size, self.bold, self.italic,
                self.underline, self.strike)


@dataclass
class Row:
    """一个逻辑行（Markdown 里的一行）。

    表格是唯一的例外：一个 Row 装整张表，`cells` 是「行 × 列 × 片段」。
    列宽必须整张表一起算，否则各行会各算各的、列对不齐。
    """

    pieces: list[Piece] = field(default_factory=list)
    kind: str = "text"              # text / blank / heading / quote / bullet /
                                    # task / ordered / hr / code / table
    level: int = 0
    checked: bool = False
    number: str = ""
    badge: str | None = None
    cells: list[list[list[Piece]]] = field(default_factory=list)
    alignments: list[str] = field(default_factory=list)
    table_id: int = -1


@dataclass
class Palette:
    """渲染要用的颜色。由调用方从自己的配色常量填进来，避免两处各写一份。"""

    background: str = "#FFFFFF"
    text: str = "#23211E"
    heading: tuple[str, ...] = ("#1F3864", "#2C5282", "#31688E",
                                "#3D7A87", "#4A8A7B", "#5C8A5C")
    bold: str = "#B3261E"
    italic: str = "#A87514"
    strike: str = "#A8A29A"
    highlight_bg: str = "#FFF3C4"
    highlight_text: str = "#8A6412"
    code_bg: str = "#F1F0EC"
    code_text: str = "#B5396B"
    codeblock_text: str = "#3F4A56"
    link: str = "#2F6BD8"
    image: str = "#7A6BB5"
    quote_bar: str = "#7BA7D9"
    quote_text: str = "#54657A"
    bullet: tuple[str, ...] = ("#3B6FE0", "#2E9E6B", "#D9822B", "#8E5BD0", "#3F9EB8")
    ordered: str = "#3B6FE0"
    task_box: str = "#B9B4AB"
    task_check: str = "#2E9E6B"
    hr: str = "#D5D0C8"
    badge: str = "#A9A49B"
    table_head_bg: str = "#EEF3FB"
    table_head_text: str = "#1F3864"
    table_stripe_bg: str = "#FAFAF8"
    table_border: str = "#D9DEE8"


@dataclass
class Options:
    """排版尺寸，全部是图片像素。调用方按「正文目标字号 ÷ 编辑器字号」换算。"""

    width: int = DEFAULT_WIDTH
    margin: int = DEFAULT_MARGIN
    line_height: int = 48
    body_size: int = DEFAULT_BODY_SIZE
    family: str = "Microsoft YaHei UI"      # 正文与徽标用的字体族
    mono_family: str = "Consolas"           # 代码用的等宽字体族
    mono_size: int = 29
    max_height: int = DEFAULT_MAX_HEIGHT
    list_indent: int = 24           # 一层列表缩进
    list_marker_gap: int = 12       # 标记右边缘与正文之间的空隙
    marker_radius: int = 4
    task_box: int = 15
    quote_bar_width: int = 4
    quote_bar_gap: int = 17         # 竖条相对引用正文左边缘往左退多少
    hr_thickness: int = 2
    badge_size: int = 12
    badge_gap: int = 10             # 徽标右边缘与正文左边缘的间距
    head_gap: int = 10              # 标题上下的额外留白
    table_padding: int = 16
    blank_gap: int = 0              # 空行额外的留白

    @property
    def content_width(self) -> int:
        return max(1, self.width - self.margin * 2)

    def list_left(self, level: int) -> int:
        """第 level 层列表正文的左边缘（与编辑区 list_indent 同一套算法）。"""
        return self.margin + self.list_indent * (min(level, 4) + 1)

    @property
    def body_font(self) -> FontKey:
        return (self.family, self.body_size, False, False, False, False)


# ---------- 排版 ----------

@dataclass
class _DisplayLine:
    """排好版的显示行：一个逻辑行可能被折成好几个显示行。"""

    top: int
    height: int
    baseline: int                   # 本行基线相对图片顶部的 y
    row: Row
    pieces: list[Piece]
    x: int                          # 正文起始 x
    wrap: bool                      # 是不是折行出来的续行
    background: str | None = None   # 整行底色（代码块）
    table: tuple | None = None      # 表格行：(列宽, 本行各格的折行结果, 内边距, 行序号)


def _is_wide(char: str) -> bool:
    """宽字符（中日韩）：可以在它前面直接断行，不用找空格。"""
    return ord(char) > 0x2E80


# 中文禁则：不能出现在**行首**的收尾类标点。中英文都收——行首挂着一个逗号、
# 句号或右括号，在任何语言里都是坏断行（英文里 `.`、`%` 同理，见 `_kinsoku`）。
_CLOSING = "，。、；：？！）》」』】〕〉…,.;:?!)]}%"
# 不能出现在**行尾**的开启类标点
_OPENING = "（《「『【〔〈([{“‘"


def _kinsoku(text: str, start: int, cut: int) -> int:
    """把断点挪到不违反中文禁则的位置（追い出し：把前一个字一起推到下一行）。

    中文排版里收尾类标点不能落在行首（「行首禁则」），开启类标点不能落在行尾
    （「行尾禁则」）。Tk 和浏览器不做这件事，所以导出图里会看到句号、逗号单独
    占一行——用户报的「在逗号后异常换行」就是这个。

    做法是**把断点往前挪一个字**，让那个标点跟着前一个字一起到下一行去：

        …写作区的字体设置变     →   …写作区的字体设
        。                         置变。

    不采用「标点悬挂」（让标点留在上一行、往右溢出一点）是因为溢出会撞上表格
    单元格的边框，而追い出し只让这一行短一个字，任何宽度下都安全；顺带还修好了
    `3.12` 被断成 `3.` / `12` 这种情况（前一个字「3」会跟着「.」一起下移）。

    **标点必须紧贴前一个字才挪**：前面是空格说明它本来就是个独立记号
    （`app/main.py` 的点、` .简记回收站` 的点），断在那儿是对的。

    `cut` 可以退到 `start` 之前吗？不行——`start` 是这一行的起点，退过去就把
    上一行的字吃掉了。真退不动就原样返回，调用方会退化成按字断。
    """
    while start < cut < len(text) and text[cut] in _CLOSING \
            and not text[cut - 1].isspace():
        cut -= 1
    while cut > start and text[cut - 1] in _OPENING:
        cut -= 1
    return cut


def _break_index(text: str, start: int, index: int, floor: int | None = None) -> int:
    """`text[index]` 放不下时，这一行该断在哪（返回绝对下标）。

    合法的断行点只有两种：**空格之后**、以及**宽字符（中日韩）旁边**——中文本来
    就能在任意字前断开。所以从 `index` 往回找**最近的一个**断行点就够了。

    不能一路退到「上一个空格」：中文行里那个空格可能在十几个字以前，退到那里等于
    白扔大半行（实测有一行只填到 55%，下一行还得接着排，看着就像凭空多了个换行）。
    也不该在宽字符前还继续往回退——直接断在它前面就是最紧的断法。

    往回退的时候还有一个坑：断点可能落在**行首的空白**里。Markdown 用行尾两个空格
    表示换行，编辑器里这种「续行」就带着两个前导空格；从后面回头找空格，找到的正是
    那两个前导空格，断在它后面就甩出一行纯空白——也就是用户看到的「凭空多了一个
    换行」。这种断点一律作废，退回按字断。

    `floor` 是回扫的**下界**，默认 `start`。调用方（`_wrap`）传的是「当前这个字所属
    片段在整行里的起点」，也就是**回扫不许跨片段**：跨过去的话，一段长代码标识符
    前面那个「、」会被当成断点，这一行就白扔 40% 的宽度（实测 project.md 里从 2 处
    涨到 37 处）。不跨片段、回扫不到就按字断，和只按片段折行的老行为一致。

    最后再走一遍 `_kinsoku`——注意它是**允许跨片段**的（`cut` 可以退到 `floor`
    之前，退到 `start` 为止），这正是「行内代码后面的 `）`」能修好的原因。
    """
    floor = start if floor is None else floor
    if text[index].isspace():
        return _kinsoku(text, start, index + 1)   # 放不下的正好是个空格：断在它后面
    if _is_wide(text[index]):
        return _kinsoku(text, start, index)       # 宽字符前可以直接断，且已是最紧的断法
    for position in range(index - 1, floor - 1, -1):
        char = text[position]
        if char.isspace() or _is_wide(char):
            cut = position + 1
            if text[start:cut].strip():
                return _kinsoku(text, start, cut)
            break                     # 断点前只剩空白：整段都是空白，没有可断的地方
    return _kinsoku(text, start, index)


def _slice(piece: Piece, text: str) -> Piece:
    return Piece(text=text, family=piece.family, size=piece.size, bold=piece.bold,
                 italic=piece.italic, underline=piece.underline, strike=piece.strike,
                 color=piece.color, background=piece.background)


def _trim_tail(pieces: list[Piece]) -> None:
    """去掉折行留下的行尾空白（断在空格后时，那个空格会留在上一行末尾）。"""
    for index in range(len(pieces) - 1, -1, -1):
        stripped = pieces[index].text.rstrip(" \t")
        if stripped == pieces[index].text:
            return
        if stripped:
            pieces[index] = _slice(pieces[index], stripped)
            return
        pieces.pop()


def _join(chars: list[tuple[str, "Piece", int]], a: int, b: int) -> list[Piece]:
    """把摊平的字列表 `chars[a:b]` 拼回片段，同一来源的连续字合成一段。

    第 3 项是字所属片段在原始列表里的序号（用序号而不是对象本身比较，
    免得同一个 Piece 对象在列表里出现两次时把两段不相邻的文字粘到一起）。
    """
    out: list[Piece] = []
    buf: list[str] = []
    source: Piece | None = None
    ordinal = -1
    for char, piece, owner in chars[a:b]:
        if owner != ordinal:
            if buf and source is not None:
                out.append(_slice(source, "".join(buf)))
            buf = []
            source = piece
            ordinal = owner
        buf.append(char)
    if buf and source is not None:
        out.append(_slice(source, "".join(buf)))
    return out


def _wrap(pieces: list[Piece], width: int, fonts: _Fonts) -> list[list[Piece]]:
    """把若干片段折成若干显示行，每行不超过 width。

    **先把所有片段摊成一串「字」再折行**，不能按片段各折各的：标点经常单独待在
    一个片段里（`**粗体**。` 的句号、行内代码后面的 `）`），按片段折的话断点落在
    片段开头，`_break_index` 在那个片段里没有任何回退余地，行首禁则就落空了
    （实测使用说明里 13 处行首标点，修完段内的还剩 5 处，全是这种）。

    断行点只有「空格后」和「宽字符旁」两种，放不下时由 `_break_index` 从当前位置
    往回找**最近的一个**（不是「上一个空格」——那会白扔大半行）。

    断在空格后时必须把 index **退回到断点**重新累加：断点之后的那些字虽然
    已经「走」过一遍，但它们的宽度还没算进新的一行里，不退回去新行就会
    悄悄超宽（屏幕上看着是文字被切掉，实际上是排版就算错了）。
    """
    chars: list[tuple[str, Piece, int]] = [
        (char, piece, ordinal)
        for ordinal, piece in enumerate(pieces) if piece.text
        for char in piece.text
    ]
    if not chars:
        return [[]]
    text = "".join(char for char, _piece, _ordinal in chars)
    widths = [fonts.width(char, piece.font) for char, piece, _ordinal in chars]
    # 每个字所属片段的第一个字在整行里的下标（回扫不许跨片段，见 `_break_index`）
    owner_floor: list[int] = []
    owner = -1
    begin = 0
    for position, (_char, _piece, ordinal) in enumerate(chars):
        if ordinal != owner:
            owner = ordinal
            begin = position
        owner_floor.append(begin)

    raw: list[list[Piece]] = []
    start = 0
    while start < len(chars):
        used = 0
        index = start
        while index < len(chars) and used + widths[index] <= width:
            used += widths[index]
            index += 1
        if index >= len(chars):
            raw.append(_join(chars, start, index))
            break
        cut = _break_index(text, start, index, max(start, owner_floor[index]))
        if cut <= start:
            cut = index
        if cut <= start:            # 连一个字都放不下：硬放一个字，否则原地打转
            cut = start + 1
        raw.append(_join(chars, start, cut))
        start = cut

    # 行尾空白（Markdown 用两个空格表示换行）不该占一行：裁掉，裁空了整行丢掉
    result: list[list[Piece]] = []
    for line in raw:
        _trim_tail(line)
        if line or not result:
            result.append(line)
    return result or [[]]


def _measure(pieces: list[Piece], fonts: _Fonts) -> int:
    return sum(fonts.width(piece.text, piece.font) for piece in pieces)


def _line_box(pieces: list[Piece], options: Options, fonts: _Fonts,
              fallback: FontKey) -> tuple[int, int, int]:
    """(显示行高, 段首留白, 基线相对行顶的偏移)。

    和 Tk 的做法一致：先取行内最高的那个字体（换行/等宽混排时以高的为准），
    把 line_height 与字体自然行高的差分成两半，一半补在段首
    （spacing1）、一半补在段尾，基线就落在 spacing1 + ascent 处。
    """
    keys = {piece.font for piece in pieces} or {fallback}
    ascent = max(fonts.metrics(key)[0] for key in keys)
    descent = max(fonts.metrics(key)[1] for key in keys)
    linespace = ascent + descent
    height = max(options.line_height, linespace)
    extra = height - linespace
    spacing1 = extra // 2
    return height, spacing1, spacing1 + ascent


def build_layout(rows: list[Row], options: Options, palette: Palette,
                 fonts: _Fonts) -> list[_DisplayLine]:
    """算出所有显示行、纵坐标与基线。返回的 top 从 0 起算，不含上下留白。"""
    out: list[_DisplayLine] = []
    y = 0
    fallback = options.body_font

    for row in rows:
        if row.kind == "table":
            y = _layout_table(row, options, palette, fonts, out, y)
            continue

        if row.kind == "heading":
            y += options.head_gap

        indent = options.margin
        if row.kind in ("bullet", "task", "ordered"):
            indent = options.list_left(row.level)
        elif row.kind == "quote":
            indent = options.margin + options.list_indent
        available = options.margin + options.content_width - indent

        if row.kind == "hr":
            height, _spacing, baseline = _line_box([], options, fonts, fallback)
            out.append(_DisplayLine(top=y, height=height, baseline=y + baseline,
                                    row=row, pieces=[], x=indent, wrap=False))
            y += height
            if options.head_gap:
                y += options.head_gap
            continue

        background = palette.code_bg if row.kind == "code" else None
        if row.kind == "blank":
            y += options.blank_gap

        wrapped = _wrap(row.pieces, max(40, available), fonts)
        for index, pieces in enumerate(wrapped):
            height, _spacing, baseline = _line_box(pieces, options, fonts, fallback)
            out.append(_DisplayLine(
                top=y, height=height, baseline=y + baseline, row=row, pieces=pieces,
                x=indent, wrap=index > 0, background=background,
            ))
            y += height

        if row.kind == "heading":
            y += options.head_gap
    return out


def _layout_table(row: Row, options: Options, palette: Palette, fonts: _Fonts,
                  out: list[_DisplayLine], y: int) -> int:
    """整张表一起排版：先量出各列的公共宽度，再逐行逐格折行算高度。"""
    columns = max((len(cells) for cells in row.cells), default=0)
    if not columns:
        return y
    padding = options.table_padding
    available = options.content_width

    natural: list[int] = []
    for column in range(columns):
        widest = 0
        for cells in row.cells:
            pieces = cells[column] if column < len(cells) else []
            widest = max(widest, _measure(pieces, fonts))
        natural.append(widest + padding * 2)
    total = sum(natural)
    if total > available:
        # 太宽就等比压到能放下，同时留一个最小列宽，别把字压成一条缝
        floor = int(options.body_size * 2.2)
        room = available - floor * columns
        if room <= 0:
            widths = [available // columns] * columns
        else:
            factor = room / max(1, total - floor * columns)
            widths = [floor + int((value - floor) * factor) for value in natural]
            widths[-1] += available - sum(widths)
    else:
        widths = natural

    # 整张表共用一个格内行高与基线，各格才在同一根基准线上
    every = [piece for cells in row.cells for cell in cells for piece in cell]
    cell_height, _spacing, cell_baseline = _line_box(
        every, options, fonts, options.body_font)

    for index, cells in enumerate(row.cells):
        wrapped_cells: list[list[list[Piece]]] = []
        tallest = 1
        for column in range(columns):
            pieces = cells[column] if column < len(cells) else []
            wrapped = _wrap(pieces, max(20, widths[column] - padding * 2), fonts)
            wrapped_cells.append(wrapped)
            tallest = max(tallest, len(wrapped))
        height = tallest * cell_height + padding * 2
        out.append(_DisplayLine(
            top=y, height=height, baseline=y + padding + cell_baseline,
            row=row, pieces=[], x=options.margin, wrap=False,
            background=palette.table_head_bg if index == 0 else
            (palette.table_stripe_bg if index % 2 == 0 else None),
            table=(widths, wrapped_cells, padding, cell_height, cell_baseline),
        ))
        y += height
    return y


# ---------- 绘制 ----------

def _draw_display_line(surface: Surface, line: _DisplayLine, options: Options,
                       palette: Palette, fonts: _Fonts) -> None:
    row = line.row
    top = line.top
    bottom = top + line.height

    if row.kind == "hr":
        thickness = max(1, options.hr_thickness)
        centre = top + line.height / 2
        surface.fill(options.margin, centre - thickness / 2,
                     options.margin + options.content_width, centre + thickness / 2,
                     palette.hr)
        return

    if line.table is not None:
        _draw_table_line(surface, line, options, fonts)
        return

    if line.background:
        surface.fill(options.margin, top, options.margin + options.content_width,
                     bottom, line.background)

    if not line.wrap:
        _draw_marker(surface, line, options, palette, fonts)
    _draw_pieces(surface, line.pieces, line.x, line.baseline, fonts)


def _draw_marker(surface: Surface, line: _DisplayLine, options: Options,
                 palette: Palette, fonts: _Fonts) -> None:
    """左侧装饰：项目符号、复选框、序号、引用竖条、标题徽标。

    位置与编辑区同一套算法——标记的**右边缘**落在正文左边缘再往左 gap 处。
    """
    row = line.row
    top = line.top
    centre = line.baseline - options.body_size * 0.36

    if row.kind in ("bullet", "task", "ordered"):
        right = line.x - options.list_marker_gap
        if row.kind == "bullet":
            radius = options.marker_radius
            colors = palette.bullet or (palette.ordered,)
            color = colors[row.level % len(colors)]
            box = (right - radius * 2, centre - radius, right, centre + radius)
            if row.level == 0:
                surface.ellipse(*box, color, None)
            elif row.level == 1:
                surface.ellipse(*box, None, color, max(1, int(radius * 0.55)))
            else:
                surface.rect(*box, color, None)
        elif row.kind == "task":
            box = options.task_box
            box_top = centre - box / 2
            left = right - box
            color = palette.task_check if row.checked else palette.task_box
            surface.rect(left, box_top, left + box, box_top + box,
                         palette.task_check if row.checked else None, color,
                         max(1, int(options.body_size * 0.05)))
            if row.checked:
                surface.polyline([
                    (left + box * 0.22, box_top + box * 0.52),
                    (left + box * 0.43, box_top + box * 0.73),
                    (left + box * 0.80, box_top + box * 0.26),
                ], "#FFFFFF", max(1, int(options.body_size * 0.09)))
        else:
            family = row.pieces[0].family if row.pieces else options.family
            key: FontKey = (family, options.body_size, True, False, False, False)
            width = fonts.width(row.number, key)
            surface.text(right - width, centre + options.body_size * 0.34,
                         row.number, key, palette.ordered)
    elif row.kind == "quote":
        bar_x = options.margin + options.list_indent - options.quote_bar_gap
        surface.fill(bar_x, top, bar_x + max(1, options.quote_bar_width),
                     top + line.height, palette.quote_bar)
    elif row.kind == "heading" and row.badge:
        key: FontKey = (options.family, options.badge_size, True, False, False, False)
        width = fonts.width(row.badge, key)
        x = options.margin - options.badge_gap - width
        surface.text(x, centre + options.badge_size * 0.34,
                     row.badge, key, palette.badge)


def _draw_pieces(surface: Surface, pieces: list[Piece], x: float, baseline: float,
                 fonts: _Fonts) -> None:
    cursor = x
    for piece in pieces:
        width = fonts.width(piece.text, piece.font)
        if piece.background:
            ascent, descent = fonts.metrics(piece.font)
            inset = max(1, int(piece.size * 0.12))
            surface.fill(cursor, baseline - ascent + inset, cursor + width,
                         baseline + descent - inset, piece.background)
        surface.text(cursor, baseline, piece.text, piece.font, piece.color)
        cursor += width


def _draw_table_line(surface: Surface, line: _DisplayLine, options: Options,
                     fonts: _Fonts) -> None:
    widths, wrapped_cells, padding, cell_height, cell_baseline = line.table
    left = options.margin
    right = left + sum(widths)
    top = line.top
    if line.background:
        surface.fill(left, top, right, top + line.height, line.background)

    cursor = left
    for column, width in enumerate(widths):
        align = (line.row.alignments[column]
                 if column < len(line.row.alignments) else "none")
        for offset, pieces in enumerate(wrapped_cells[column]):
            baseline = top + padding + offset * cell_height + cell_baseline
            text_width = _measure(pieces, fonts)
            if align == "center":
                x = cursor + (width - text_width) / 2
            elif align == "right":
                x = cursor + width - padding - text_width
            else:
                x = cursor + padding
            _draw_pieces(surface, pieces, x, baseline, fonts)
        cursor += width


def _draw_table_rules(surface: Surface, lines: list[_DisplayLine],
                      options: Options, palette: Palette) -> None:
    """表格网格线最后统一画：相邻两行只画一条横线，外框也只画一圈。"""
    thickness = max(1, int(options.body_size * 0.045))
    index = 0
    total = len(lines)
    while index < total:
        line = lines[index]
        if line.table is None:
            index += 1
            continue
        run = [line]
        cursor = index + 1
        while cursor < total and lines[cursor].table is not None \
                and lines[cursor].row is line.row:
            run.append(lines[cursor])
            cursor += 1
        widths = run[0].table[0]
        left = options.margin
        right = left + sum(widths)
        top = run[0].top
        bottom = run[-1].top + run[-1].height
        surface.rect(left, top, right, bottom, None, palette.table_border, thickness)
        boundary = left
        for width in widths[:-1]:
            boundary += width
            surface.fill(boundary - thickness / 2, top, boundary + thickness / 2,
                         bottom, palette.table_border)
        for row_line in run[1:]:
            surface.fill(left, row_line.top - thickness / 2, right,
                         row_line.top + thickness / 2, palette.table_border)
        index = cursor


# ---------- 入口 ----------

def render(rows: list[Row], options: Options, palette: Palette | None = None,
           fonts: _Fonts | None = None) -> list[bytes]:
    """把整篇文档渲染成一张或多张 PNG。

    超过 `options.max_height` 时按显示行切开分张（切点落在行边界上，
    不会把一行字劈成两半，也不会把一张表劈开）。
    """
    palette = palette or Palette()
    own_fonts = fonts is None
    fonts = fonts or _Fonts()
    try:
        lines = build_layout(rows, options, palette, fonts)
        images: list[bytes] = []
        for chunk in _split_chunks(lines, options):
            placed = _shift_chunk(chunk, options)
            height = _chunk_height(placed, options)
            surface = Surface(fonts, options.width, height, palette.background)
            try:
                for line in placed:
                    _draw_display_line(surface, line, options, palette, fonts)
                _draw_table_rules(surface, placed, options, palette)
                images.append(surface.to_png())
            finally:
                surface.close()
        return images
    finally:
        if own_fonts:
            fonts.close()


def _split_chunks(lines: list[_DisplayLine], options: Options) -> list[list[_DisplayLine]]:
    """按高度上限把显示行切成若干块（每块自己带上下留白）。

    判据里 `used` 累加的是 `line.height`，**不含** `build_layout` 已经烘进
    `top` 的那些留白（标题与分隔线的 `head_gap`、表格内边距），所以真实高度
    （`span + 2×margin`，`span = 末行 top+height − 首行 top`）会比 `used`
    大出几个百分点——这是**有意留的余量**，不是漏算：宁可偶尔超一点，
    也不要为卡死上限，把本来一张装得下的文档切成两张。
    另一条余量是「同一逻辑块的显示行不切开」（`current[-1].row is not line.row`），
    所以一行特别高的块（比如整张表格）会把这一块整体顶到上限之上。
    量实际差额：`python tools/quiet_desktop.py <python> tools/_probe_chunks.py`。
    """
    room = max(200, options.max_height - options.margin * 2)
    chunks: list[list[_DisplayLine]] = []
    current: list[_DisplayLine] = []
    used = 0
    for line in lines:
        if current and used + line.height > room and current[-1].row is not line.row:
            chunks.append(current)
            current = []
            used = 0
        current.append(line)
        used += line.height
    if current or not chunks:
        chunks.append(current)
    return chunks


def _shift_chunk(chunk: list[_DisplayLine], options: Options) -> list[_DisplayLine]:
    """把一块里的行整体下移，让它自己的上留白从 0 开始对齐。

    `top` 和 `baseline` 必须一起挪——画底色/边框看 top，画字看 baseline，
    只挪一个就会出现「框走了字没走」。
    """
    if not chunk:
        return chunk
    delta = options.margin - chunk[0].top
    if not delta:
        return chunk
    return [replace(line, top=line.top + delta, baseline=line.baseline + delta)
            for line in chunk]


def _chunk_height(chunk: list[_DisplayLine], options: Options) -> int:
    body = max((line.top + line.height for line in chunk), default=0)
    return max(options.line_height + options.margin * 2, body + options.margin)
