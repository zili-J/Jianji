"""裁一块矩形并整数放大，用来核对小细节（光标、图标、边框）。

用法：
    python tools/png_zoom.py 源图.png 目标图.png x y w h [放大倍数]

只处理**本软件自己写出的 PNG**（编码时 filter 全 0），别的工具生成的图不保证能解。
和 `png_crop.py` 的区别：那个只能按整幅宽度裁一段纵向（给长图用），这个能裁任意矩形。
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path


def decode(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG")
    pos, idat, width, height = 8, bytearray(), 0, 0
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height = struct.unpack(">II", body[:8])
        elif kind == b"IDAT":
            idat += body
    raw = zlib.decompress(bytes(idat))
    stride = width * 4
    pixels = bytearray(stride * height)
    for y in range(height):
        start = y * (stride + 1)
        if raw[start] != 0:
            raise ValueError(f"第 {y} 行用了过滤器 {raw[start]}，本工具只认 filter 0")
        pixels[y * stride:(y + 1) * stride] = raw[start + 1:start + 1 + stride]
    return width, height, bytes(pixels)


def encode(width: int, height: int, pixels: bytes) -> bytes:
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        raw += pixels[y * stride:(y + 1) * stride]

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))


def main() -> None:
    if len(sys.argv) < 7:
        print(__doc__)
        raise SystemExit(2)
    source, target = Path(sys.argv[1]), Path(sys.argv[2])
    x, y, w, h = (int(value) for value in sys.argv[3:7])
    zoom = int(sys.argv[7]) if len(sys.argv) > 7 else 1

    width, height, pixels = decode(source)
    x, y = max(0, x), max(0, y)
    w, h = min(w, width - x), min(h, height - y)
    if w <= 0 or h <= 0:
        raise SystemExit(f"裁剪区域超出图像范围（图 {width}×{height}）")

    scaled = bytearray()
    for row in range(h):
        start = ((y + row) * width + x) * 4
        line = pixels[start:start + w * 4]
        wide = bytearray()
        for col in range(w):
            wide += line[col * 4:col * 4 + 4] * zoom     # 横向放大
        for _ in range(zoom):
            scaled += wide                                # 纵向放大

    target.write_bytes(encode(w * zoom, h * zoom, bytes(scaled)))
    print(f"{target}  {w}×{h} → {w * zoom}×{h * zoom}（源图 {width}×{height}）")


if __name__ == "__main__":
    main()
