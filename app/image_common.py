"""长图导出里与平台无关的两块：颜色换算、PNG 编码。

单独拆成一个模块，是因为 GDI 后端和 CoreGraphics 后端都要用它，而它自己谁都不依赖。
放进 `image_export.py` 会绕成循环导入（后端 ← image_export ← 后端）。
"""
from __future__ import annotations

import struct
import zlib

__all__ = ["colorref", "encode_png", "PNG_SIGNATURE"]


def colorref(hex_color: str) -> int:
    """`#RRGGBB` → GDI 的 COLORREF（内存里是 0x00BBGGRR）。

    名字和含义都是 GDI 的，macOS 后端不用它（CoreGraphics 收的是 RGBA 浮点）。
    留着是因为 `tests/test_export.py` 按这个名字引用它。
    """
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

    **两个后端都按这个格式交像素**，不是巧合：
    GDI 的 32 位 DIB 在内存里就是 B,G,R,保留；
    CoreGraphics 用 `kCGImageAlphaNoneSkipFirst | kCGBitmapByteOrder32Little`
    建出来的位图，内存排布一模一样。所以这里一份编码器两边通用。

    不用行过滤器（filter 0）：文本图里大片是纯白，deflate 自己就能压得很好，
    而在 Python 里逐字节做 Sub/Up 过滤反而要慢一个量级。
    """
    compressor = zlib.compressobj(6)
    payload: list[bytes] = []
    opaque = b"\xff" * width
    for row in bgra_rows:
        line = bytearray(row)
        line[0::4], line[2::4] = line[2::4], line[0::4]     # BGRA → RGBA
        line[3::4] = opaque                                 # 两个后端都不填 alpha，统一补不透明
        payload.append(compressor.compress(b"\x00" + bytes(line)))
    payload.append(compressor.flush())
    # 压好的数据必须包进 IDAT 块：少了 length/tag/CRC 这三段，
    # 文件头看着还是 PNG，实际已经不是合法的分块结构了。
    return (PNG_SIGNATURE
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", b"".join(payload))
            + _chunk(b"IEND", b""))
