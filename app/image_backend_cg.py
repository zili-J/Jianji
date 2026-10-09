"""长图导出的 **macOS 后端**：ctypes 直调 CoreGraphics / CoreText 离屏画字。

和 `image_backend_gdi.py` **接口一字不差**（同名 `Fonts` / `Surface`、同签名方法），
`image_export.py` 只按平台挑一个，排版和绘制那六百行一行都不用改。

## 为什么是 ctypes 而不是 PyObjC / Pillow

这个项目的原则是**零第三方依赖**。macOS 上真正画字的底层就是 CoreGraphics，
它就在 `/System/Library/Frameworks/` 里，`ctypes.CDLL` 得动 —— 和 Windows 上
`ctypes.windll.gdi32` 是完全同一回事，不是「为了不用依赖而硬凑」。
PyObjC 在 macOS 上虽然是系统自带，但它会让打包出来的 `.app` 多一层解释器胶水，
而且真正的活（建位图上下文、量字、画字）一样要落到 CoreGraphics 上。

## 四个必须做对、做错了还不报错的地方

1. **坐标系是反的。** GDI 原点在左上、y 向下；CoreGraphics 原点在左下、y 向上。
   所以建完上下文立刻把 CTM 翻过来（`Translate(0,H)` + `Scale(1,-1)`），
   让上层代码继续按「左上角原点」写，两平台共用同一套排版数字。
2. **翻完 CTM 之后字是倒的。** 翻转会连字形一起镜像，必须再把文字矩阵翻回来
   （`CGContextSetTextMatrix` 设 `(1,0,0,-1,0,0)`）。漏了这一步图上的字**上下颠倒**，
   而且不报任何错 —— 这是最难自查的一种错。
3. **像素格式必须是 BGRA。** `encode_png` 收的是 BGRA 行（它会把 0/2 通道对调）。
   用 `kCGImageAlphaNoneSkipFirst | kCGBitmapByteOrder32Little` 建位图，
   内存排布正是 B,G,R,X —— 和 GDI 的 32 位 DIB 一模一样，编码器一份通用。
4. **`kCTFontAttributeName` 是变量不是函数。** 它声明成 `const CFStringRef`，
   是个全局指针变量，得用 `c_void_p.in_dll` 把**它指向的值**读出来，
   不是 `CDLL` 里找一个叫这个名字的函数。

## 与 Windows 版的两处已知差异（都是刻意的，不是漏了）

- **表格细线的抗锯齿。** GDI 的 `FillRect` 按整数像素填，半像素边界会向外取整成
  两像素实线；CoreGraphics 开着抗锯齿，同样的矩形会铺成一条更柔的线。
  观感上 macOS 这边反而更细腻，没有去对齐它。
- **下划线与删除线是自己画的。** GDI 把 `lfUnderline` / `lfStrikeOut` 交给字体引擎，
  CoreText 有下划线属性、**没有删除线**。为了两平台粗细位置一致，两条都改成
  在 `Surface.text()` 里按字号自己描一条线（见 `_draw_rules`）。
"""
from __future__ import annotations

import ctypes
from typing import Any

from image_common import encode_png

__all__ = ["Fonts", "Surface", "FontKey"]

#: 字体键：(family, size, bold, italic, underline, strike)
FontKey = tuple

# --------------------------------------------------------------- 位图格式常量

#: `kCGBitmapAlphaInfoMask` / `kCGImageAlphaNoneSkipFirst`（CoreGraphics 的枚举值）
_CG_ALPHA_NONE_SKIP_FIRST = 6
#: `kCGBitmapByteOrder32Little` —— 让 32 位字在内存里按小端排，得到 B,G,R,X
_CG_BYTE_ORDER_32_LITTLE = 2 << 12

#: `CTFontSymbolicTraits`
_CT_TRAIT_BOLD = 1 << 0
_CT_TRAIT_ITALIC = 1 << 1

_FRAMEWORKS = (
    ("cg", "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"),
    ("ct", "/System/Library/Frameworks/CoreText.framework/CoreText"),
    ("cf", "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"),
)


# --------------------------------------------------------------- 结构体

class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class CGRect(ctypes.Structure):
    """`CGRectMake` 在头文件里是 `static inline`，**没有导出**，只能自己拼结构。"""

    _fields_ = [("origin", CGPoint), ("size", CGSize)]


class CGAffineTransform(ctypes.Structure):
    """6 个 double，**按值**传参（声明进 argtypes 就行）。"""

    _fields_ = [("a", ctypes.c_double), ("b", ctypes.c_double),
                ("c", ctypes.c_double), ("d", ctypes.c_double),
                ("tx", ctypes.c_double), ("ty", ctypes.c_double)]


class _DictionaryCallbacks(ctypes.Structure):
    """`CFDictionaryKeyCallBacks` / `CFDictionaryValueCallBacks` 的占位。

    **只为了拿它在 CoreFoundation 里的地址**（`byref` 出来传给 `CFDictionaryCreate`），
    内容一个字段都不读。两种回调结构前几个字段都是「一个 CFIndex + 若干函数指针」，
    6 个指针宽度足够装下任何一种。
    """

    _fields_ = [("slots", ctypes.c_void_p * 6)]


# --------------------------------------------------------------- 惰性加载
#
# **框架在模块顶层加载会在非 macOS 上直接抛 OSError**，那样这个文件就没法在
# Windows 上被 import —— 而「两个后端接口必须一致」这件事，恰恰要在 Windows 上
# 才测得了（见 tests/test_image_backend.py）。所以推迟到第一次真正要用的时候。

_cg: Any = None
_ct: Any = None
_cf: Any = None
_ready = False
_key_callbacks: Any = None
_value_callbacks: Any = None
_font_attribute: int = 0


def _load() -> None:
    """加载三个框架并声明原型。**只有 macOS 上能成功。**"""
    global _cg, _ct, _cf, _ready, _key_callbacks, _value_callbacks, _font_attribute
    if _ready:
        return
    loaded: dict[str, Any] = {}
    for key, path in _FRAMEWORKS:
        try:
            loaded[key] = ctypes.CDLL(path, use_errno=True)
        except OSError as error:                     # pragma: no cover - 只在非 macOS 上
            raise OSError(
                f"长图导出需要 macOS 的 CoreGraphics（{path} 打不开）。"
            ) from error
    cg, ct, cf = loaded["cg"], loaded["ct"], loaded["cf"]
    pointer, size_t, cdouble, cint = ctypes.c_void_p, ctypes.c_size_t, ctypes.c_double, ctypes.c_int

    # ---- CoreFoundation ----
    cf.CFStringCreateWithCharacters.argtypes = [pointer, ctypes.POINTER(ctypes.c_uint16),
                                                ctypes.c_long]
    cf.CFStringCreateWithCharacters.restype = pointer
    cf.CFRelease.argtypes = [pointer]
    cf.CFDictionaryCreate.argtypes = [pointer, ctypes.POINTER(pointer),
                                      ctypes.POINTER(pointer), ctypes.c_long,
                                      pointer, pointer]
    cf.CFDictionaryCreate.restype = pointer
    cf.CFAttributedStringCreate.argtypes = [pointer, pointer, pointer]
    cf.CFAttributedStringCreate.restype = pointer

    # ---- CoreText ----
    ct.CTFontCreateWithName.argtypes = [pointer, cdouble, pointer]
    ct.CTFontCreateWithName.restype = pointer
    ct.CTFontCreateCopyWithSymbolicTraits.argtypes = [pointer, cdouble, pointer,
                                                     ctypes.c_uint32, ctypes.c_uint32]
    ct.CTFontCreateCopyWithSymbolicTraits.restype = pointer
    ct.CTFontGetAscent.argtypes = [pointer]
    ct.CTFontGetAscent.restype = cdouble
    ct.CTFontGetDescent.argtypes = [pointer]
    ct.CTFontGetDescent.restype = cdouble
    ct.CTLineCreateWithAttributedString.argtypes = [pointer]
    ct.CTLineCreateWithAttributedString.restype = pointer
    ct.CTLineGetTypographicBounds.argtypes = [pointer, ctypes.POINTER(cdouble),
                                              ctypes.POINTER(cdouble),
                                              ctypes.POINTER(cdouble)]
    ct.CTLineGetTypographicBounds.restype = cdouble
    ct.CTLineDraw.argtypes = [pointer, pointer]

    # ---- CoreGraphics ----
    cg.CGColorSpaceCreateDeviceRGB.restype = pointer
    cg.CGColorSpaceRelease.argtypes = [pointer]
    cg.CGBitmapContextCreate.argtypes = [pointer, size_t, size_t, size_t, size_t,
                                         pointer, ctypes.c_uint32]
    cg.CGBitmapContextCreate.restype = pointer
    cg.CGContextRelease.argtypes = [pointer]
    cg.CGContextTranslateCTM.argtypes = [pointer, cdouble, cdouble]
    cg.CGContextScaleCTM.argtypes = [pointer, cdouble, cdouble]
    cg.CGContextSetTextMatrix.argtypes = [pointer, CGAffineTransform]
    cg.CGContextSetTextPosition.argtypes = [pointer, cdouble, cdouble]
    cg.CGContextSetRGBFillColor.argtypes = [pointer, cdouble, cdouble, cdouble, cdouble]
    cg.CGContextSetRGBStrokeColor.argtypes = [pointer, cdouble, cdouble, cdouble, cdouble]
    cg.CGContextFillRect.argtypes = [pointer, CGRect]
    cg.CGContextStrokeRect.argtypes = [pointer, CGRect]
    cg.CGContextFillEllipseInRect.argtypes = [pointer, CGRect]
    cg.CGContextStrokeEllipseInRect.argtypes = [pointer, CGRect]
    cg.CGContextSetLineWidth.argtypes = [pointer, cdouble]
    cg.CGContextBeginPath.argtypes = [pointer]
    cg.CGContextMoveToPoint.argtypes = [pointer, cdouble, cdouble]
    cg.CGContextAddLineToPoint.argtypes = [pointer, cdouble, cdouble]
    cg.CGContextStrokePath.argtypes = [pointer]
    cg.CGContextFlush.argtypes = [pointer]
    cg.CGContextSetShouldAntialias.argtypes = [pointer, cint]
    cg.CGContextSetAllowsAntialiasing.argtypes = [pointer, cint]

    # `kCTFontAttributeName` 是个**全局指针变量**，读它的值才是那个 CFStringRef。
    _font_attribute = ctypes.c_void_p.in_dll(ct, "kCTFontAttributeName").value or 0
    _key_callbacks = _DictionaryCallbacks.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")
    _value_callbacks = _DictionaryCallbacks.in_dll(cf, "kCFTypeDictionaryValueCallBacks")

    _cg, _ct, _cf = cg, ct, cf
    _ready = True


# --------------------------------------------------------------- 小工具

#: 翻转 CTM 之后必须配套设的文字矩阵，否则字是上下颠倒的（见模块开头第 2 条）。
_TEXT_MATRIX = CGAffineTransform(1.0, 0.0, 0.0, -1.0, 0.0, 0.0)


def _rect(x0: float, y0: float, x1: float, y1: float) -> CGRect:
    return CGRect(CGPoint(float(x0), float(y0)),
                  CGSize(max(0.0, float(x1) - float(x0)), max(0.0, float(y1) - float(y0))))


def _rgba(hex_color: str) -> tuple[float, float, float, float]:
    """`#RRGGBB` → CoreGraphics 要的 0..1 浮点四元组。"""
    value = hex_color.lstrip("#")[:6]
    r, g, b = (int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return r, g, b, 1.0


def _cfstring(text: str) -> int:
    """Python 字符串 → CFStringRef（调用方负责 `_release`）。

    走 UTF-16 是**必须**的：`CFStringCreateWithCString` 认的是系统编码，
    而 `CFStringCreateWithCharacters` 收的是 UniChar 序列，中文、emoji 都不会出岔子。
    """
    data = text.encode("utf-16-le")
    count = len(data) // 2
    if count == 0:
        buffer = ctypes.create_string_buffer(b"\x00\x00")
    else:
        buffer = ctypes.create_string_buffer(data, len(data))
    return _cf.CFStringCreateWithCharacters(
        None, ctypes.cast(buffer, ctypes.POINTER(ctypes.c_uint16)), count)


def _release(ref: int) -> None:
    if ref:
        _cf.CFRelease(ref)


# --------------------------------------------------------------- 字体

class Fonts:
    """字体引用缓存 + 量文字宽度。

    量宽度**必须走 CTLine**，不能只问字体要 advance：CoreText 会在当前字体缺字时
    自动做字体级联（中文正文里混一个「→」或 emoji 就是这种情况），
    只查 advance 会量出 0 或者错的宽度，断行位置跟着全错。
    """

    def __init__(self) -> None:
        _load()
        self._fonts: dict[FontKey, int] = {}
        self._metrics: dict[FontKey, tuple[int, int]] = {}
        self._widths: dict[tuple[FontKey, str], int] = {}

    def handle(self, key: FontKey) -> int:
        ref = self._fonts.get(key)
        if ref is None:
            family, size, bold, italic, _underline, _strike = key
            name = _cfstring(family)
            try:
                ref = _ct.CTFontCreateWithName(name, float(size), None)
            finally:
                _release(name)
            if not ref:
                raise OSError(f"CoreText 建不出字体：{family}")
            # 粗体/斜体**一次只加一种**：`CTFontCreateCopyWithSymbolicTraits` 要求
            # 掩码里每一位都能满足，一次要 Bold|Italic 时，只要字体没有斜体变体就
            # 整个返回 NULL —— 连粗体也一起丢了。
            for trait in (_CT_TRAIT_BOLD if bold else 0, _CT_TRAIT_ITALIC if italic else 0):
                if not trait:
                    continue
                styled = _ct.CTFontCreateCopyWithSymbolicTraits(
                    ref, float(size), None, trait, trait)
                if styled:
                    _release(ref)
                    ref = styled
            self._fonts[key] = ref
        return ref

    def metrics(self, key: FontKey) -> tuple[int, int]:
        """(基线以上的高度, 基线以下的高度)。对应 GDI 的 tmAscent / tmDescent。"""
        cached = self._metrics.get(key)
        if cached is None:
            ref = self.handle(key)
            cached = (int(round(_ct.CTFontGetAscent(ref))),
                      int(round(_ct.CTFontGetDescent(ref))))
            self._metrics[key] = cached
        return cached

    def _line(self, text: str, key: FontKey) -> int:
        """建一条 CTLine（调用方负责 `_release`）。"""
        string = _cfstring(text)
        keys = (ctypes.c_void_p * 1)(_font_attribute)
        values = (ctypes.c_void_p * 1)(self.handle(key))
        dictionary = _cf.CFDictionaryCreate(
            None, keys, values, 1,
            ctypes.byref(_key_callbacks), ctypes.byref(_value_callbacks))
        try:
            attributed = _cf.CFAttributedStringCreate(None, string, dictionary)
        finally:
            _release(dictionary)
            _release(string)
        line = _ct.CTLineCreateWithAttributedString(attributed)
        _release(attributed)
        return line

    def width(self, text: str, key: FontKey) -> int:
        if not text:
            return 0
        cached = self._widths.get((key, text))
        if cached is not None:
            return cached
        line = self._line(text, key)
        try:
            measured = _ct.CTLineGetTypographicBounds(line, None, None, None)
        finally:
            _release(line)
        value = int(round(measured))
        if len(text) <= 64:
            self._widths[(key, text)] = value
        return value

    def char_widths(self, text: str, key: FontKey) -> list[int]:
        """逐字宽度，用来决定从哪里断行。"""
        return [self.width(char, key) for char in text]

    def close(self) -> None:
        for ref in self._fonts.values():
            _release(ref)
        self._fonts.clear()
        self._metrics.clear()
        self._widths.clear()


# --------------------------------------------------------------- 画布

class Surface:
    """一块 32 位 BGRA 位图上下文。坐标都是像素，原点在左上角（已翻转）。"""

    def __init__(self, fonts: Fonts, width: int, height: int, background: str) -> None:
        _load()
        self.fonts = fonts
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        self._space = _cg.CGColorSpaceCreateDeviceRGB()
        self._stride = self.width * 4
        # 位图内存归 Python 管：`create_string_buffer` 会清零，而且只要
        # `self._buffer` 还活着，CoreGraphics 就不会写到别人的内存上去。
        self._buffer = ctypes.create_string_buffer(self._stride * self.height)
        self.context = _cg.CGBitmapContextCreate(
            self._buffer, self.width, self.height, 8, self._stride, self._space,
            _CG_ALPHA_NONE_SKIP_FIRST | _CG_BYTE_ORDER_32_LITTLE)
        if not self.context:
            raise OSError(f"创建画布失败（{self.width}×{self.height} 可能超出内存）")
        # 见模块开头第 1、2 条：先把 y 轴翻过来，再把文字矩阵翻回去
        _cg.CGContextTranslateCTM(self.context, 0.0, float(self.height))
        _cg.CGContextScaleCTM(self.context, 1.0, -1.0)
        _cg.CGContextSetTextMatrix(self.context, _TEXT_MATRIX)
        _cg.CGContextSetShouldAntialias(self.context, 1)
        _cg.CGContextSetAllowsAntialiasing(self.context, 1)
        self.fill(0, 0, self.width, self.height, background)

    # ---- 基础绘制 ----

    def fill(self, x0: float, y0: float, x1: float, y1: float, color: str) -> None:
        if x1 <= x0 or y1 <= y0:
            return
        _cg.CGContextSetRGBFillColor(self.context, *_rgba(color))
        _cg.CGContextFillRect(self.context, _rect(x0, y0, x1, y1))

    def text(self, x: float, baseline: float, content: str, key: FontKey, color: str) -> None:
        """按**基线**画字。两个平台的 `text()` 都是「基线对齐」，这是共用契约。"""
        if not content:
            return
        line = self.fonts._line(content, key)
        try:
            _cg.CGContextSetTextPosition(self.context, float(x), float(baseline))
            _cg.CGContextSetRGBFillColor(self.context, *_rgba(color))
            _ct.CTLineDraw(line, self.context)
        finally:
            _release(line)
        self._draw_rules(x, baseline, content, key, color)

    def _draw_rules(self, x: float, baseline: float, content: str,
                    key: FontKey, color: str) -> None:
        """自己画下划线和删除线（理由见模块开头「已知差异」第 2 条）。"""
        underline, strike = bool(key[4]), bool(key[5])
        if not (underline or strike):
            return
        width = self.fonts.width(content, key)
        if width <= 0:
            return
        size = float(key[1])
        ascent, descent = self.fonts.metrics(key)
        thickness = max(1.0, size / 14.0)
        if underline:
            top = baseline + max(1.0, descent * 0.35)
            self.fill(x, top, x + width, top + thickness, color)
        if strike:
            top = baseline - ascent * 0.30
            self.fill(x, top, x + width, top + thickness, color)

    def ellipse(self, x0, y0, x1, y1, fill_color, outline_color, thickness: int = 1) -> None:
        box = _rect(x0, y0, x1, y1)
        if fill_color:
            _cg.CGContextSetRGBFillColor(self.context, *_rgba(fill_color))
            _cg.CGContextFillEllipseInRect(self.context, box)
        if outline_color:
            _cg.CGContextSetLineWidth(self.context, float(max(1, thickness)))
            _cg.CGContextSetRGBStrokeColor(self.context, *_rgba(outline_color))
            _cg.CGContextStrokeEllipseInRect(self.context, box)

    def rect(self, x0, y0, x1, y1, fill_color, outline_color, thickness: int = 1) -> None:
        if fill_color:
            _cg.CGContextSetRGBFillColor(self.context, *_rgba(fill_color))
            _cg.CGContextFillRect(self.context, _rect(x0, y0, x1, y1))
        if outline_color:
            # GDI 的 `Rectangle` 把边框画在矩形**边界内侧**；CoreGraphics 的
            # `StrokeRect` 以路径为中心往两边各铺一半。往里缩半个线宽，
            # 两平台的边框宽度才一样（不缩的话外沿会多出半条线）。
            inset = max(1, thickness) / 2.0
            _cg.CGContextSetLineWidth(self.context, float(max(1, thickness)))
            _cg.CGContextSetRGBStrokeColor(self.context, *_rgba(outline_color))
            _cg.CGContextStrokeRect(self.context, _rect(x0 + inset, y0 + inset,
                                                        x1 - inset, y1 - inset))

    def polyline(self, points: list[tuple[float, float]], color: str, thickness: int) -> None:
        if len(points) < 2:
            return
        _cg.CGContextSetLineWidth(self.context, float(max(1, thickness)))
        _cg.CGContextSetRGBStrokeColor(self.context, *_rgba(color))
        _cg.CGContextBeginPath(self.context)
        _cg.CGContextMoveToPoint(self.context, float(points[0][0]), float(points[0][1]))
        for x, y in points[1:]:
            _cg.CGContextAddLineToPoint(self.context, float(x), float(y))
        _cg.CGContextStrokePath(self.context)

    def to_png(self) -> bytes:
        """就地读像素编码成 PNG，**不整块复制**（长图可能上百 MB）。

        和 GDI 那边一样按行取：`self._buffer.raw` 会一次性复制整块内存，
        在 1080×30000 这种尺寸上就是白白多占一百多 MB。
        """
        _cg.CGContextFlush(self.context)
        address = ctypes.addressof(self._buffer)
        stride = self._stride

        def rows():
            for y in range(self.height):
                yield ctypes.string_at(address + y * stride, stride)

        return encode_png(self.width, self.height, rows())

    def close(self) -> None:
        if self.context:
            _cg.CGContextRelease(self.context)
            self.context = None
        if self._space:
            _cg.CGColorSpaceRelease(self._space)
            self._space = None
        self._buffer = None
