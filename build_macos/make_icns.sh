#!/usr/bin/env bash
#
# 把一张 1024×1024 的 PNG 转成 macOS 的 .icns 图标。**只能在 macOS 上跑。**
#
# 用法：
#     build_macos/make_icns.sh 路径/图标.png
# 产物：build_macos/JianJi.icns —— `jianji.spec` 会自动捡到它（没有也能打包，
#       只是用系统默认图标）。
#
# 为什么需要它：`.icns` 不是「换扩展名的 PNG」，它是带索引的容器，里面按
# 16/32/128/256/512 各存 @1x 和 @2x 两套。`iconutil` 就是干这个的。
#
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="${1:-}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "✗ iconutil / sips 是 macOS 自带的，这个脚本只能在 macOS 上跑。" >&2
  exit 1
fi

if [[ -z "$SOURCE" || ! -f "$SOURCE" ]]; then
  echo "用法：build_macos/make_icns.sh 路径/图标.png（建议 1024×1024，正方形，带透明通道）" >&2
  exit 1
fi

# 源图必须够大：最小的 @2x 也要 1024（512pt @2x），不够的话系统会拉伸变糊。
WIDTH="$(sips -g pixelWidth "$SOURCE" | awk '/pixelWidth/ {print $2}')"
if [[ "${WIDTH:-0}" -lt 1024 ]]; then
  echo "✗ 源图只有 ${WIDTH}px 宽，至少要 1024px。" >&2
  exit 1
fi

ICONSET="$(mktemp -d)/JianJi.iconset"
mkdir -p "$ICONSET"

# `iconutil` 认的是这套**固定文件名**，少一个就会报「图标集不完整」。
# 每个尺寸都要 @1x 和 @2x 两份（@2x 是它的两倍像素）。
for SIZE in 16 32 128 256 512; do
  sips -z "$SIZE" "$SIZE" "$SOURCE" --out "$ICONSET/icon_${SIZE}x${SIZE}.png" >/dev/null
  DOUBLE=$((SIZE * 2))
  sips -z "$DOUBLE" "$DOUBLE" "$SOURCE" --out "$ICONSET/icon_${SIZE}x${SIZE}@2x.png" >/dev/null
done

iconutil -c icns "$ICONSET" -o "$PROJECT/build_macos/JianJi.icns"
rm -rf "$(dirname "$ICONSET")"

echo "✓ build_macos/JianJi.icns"
