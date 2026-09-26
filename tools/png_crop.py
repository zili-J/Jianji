"""裁切并放大 PNG 的某一块，用来核验长图。

长图动辄两三万像素高，看图工具会把它缩到看不清；这个工具把关心的那一段
裁出来（可选放大若干倍）另存一张小图，就能按接近 1:1 看细节。

只处理**本软件自己写出的 PNG**：编码时用的是 filter 0（不做行过滤），
所以解码只要剥掉每行开头的过滤字节即可。别的工具生成的 PNG 不保证能用。

用法：

    python tools/png_crop.py 源图.png 目标图.png 起点y 高度 [放大倍数]
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path


def decode_png(path: str | Path) -> tuple[int, int, bytearray]:
    """(宽, 高, RGBA 原始像素)。"""
    data = Path(path).read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("不是 PNG 文件")
    position = 8
    width = height = 0
    payload = bytearray()
    while position + 8 <= len(data):
        length = struct.unpack(">I", data[position:position + 4])[0]
        tag = data[position + 4:position + 8]
        body = data[position + 8:position + 8 + length]
        if tag == b"IHDR":
            width, height, depth, color = struct.unpack(">IIBB", body[:10])
            if depth != 8 or color != 6:
                raise ValueError(f"只支持 8 位 RGBA，实际是 {depth} 位 / 类型 {color}")
        elif tag == b"IDAT":
            payload += body
        elif tag == b"IEND":
            break
        position += 12 + length
    raw = zlib.decompress(bytes(payload))
    stride = width * 4
    pixels = bytearray(stride * height)
    for y in range(height):
        start = y * (stride + 1)
        if raw[start] != 0:
            raise ValueError(f"第 {y} 行用了过滤器 {raw[start]}，本工具只认 filter 0")
        pixels[y * stride:(y + 1) * stride] = raw[start + 1:start + 1 + stride]
    return width, height, pixels


def encode_png(width: int, height: int, rgba: bytes) -> bytes:
    def chunk(tag: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

    rows = bytearray()
    for y in range(height):
        rows += b"\x00" + rgba[y * width * 4:(y + 1) * width * 4]
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
            + chunk(b"IEND", b""))


def crop(source: str | Path, target: str | Path, top: int, height: int,
         factor: int = 1) -> tuple[int, int]:
    width, image_height, pixels = decode_png(source)
    top = max(0, min(top, image_height - 1))
    height = max(1, min(height, image_height - top))
    if factor <= 1:
        out = bytearray()
        for y in range(top, top + height):
            out += pixels[y * width * 4:(y + 1) * width * 4]
        result = encode_png(width, height, bytes(out))
        size = (width, height)
    else:
        zoom_width, zoom_height = width * factor, height * factor
        out = bytearray(zoom_width * zoom_height * 4)
        for y in range(height):
            row = pixels[(top + y) * width * 4:(top + y + 1) * width * 4]
            line = bytearray()
            for x in range(width):
                line += row[x * 4:x * 4 + 4] * factor
            for repeat in range(factor):
                start = ((y * factor + repeat) * zoom_width) * 4
                out[start:start + len(line)] = line
        result = encode_png(zoom_width, zoom_height, bytes(out))
        size = (zoom_width, zoom_height)
    Path(target).write_bytes(result)
    return size


def main() -> None:
    if len(sys.argv) < 5:
        print(__doc__)
        raise SystemExit(2)
    source, target = sys.argv[1], sys.argv[2]
    top, height = int(sys.argv[3]), int(sys.argv[4])
    factor = int(sys.argv[5]) if len(sys.argv) > 5 else 1
    width, image_height, _ = decode_png(source)
    size = crop(source, target, top, height, factor)
    print(f"源图 {width}×{image_height} → {target} {size[0]}×{size[1]}")


if __name__ == "__main__":
    main()
