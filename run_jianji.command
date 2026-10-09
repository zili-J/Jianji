#!/usr/bin/env bash
#
# 双击我就能从源码启动简记（macOS）。和 Windows 那边的 run_jianji.cmd 是一对。
#
# 第一次双击可能被拦：macOS 对下载来的文件会打上隔离标记。
# 放行一次即可 —— 右键 → 打开 → 再点「打开」；或者跑
#     xattr -dr com.apple.quarantine run_jianji.command
#
# 如果双击没反应，先确认它有执行权限：
#     chmod +x run_jianji.command
#
cd "$(dirname "$0")" || exit 1

# 找一个**带 tkinter** 的 python3。简记的界面就是 tkinter，缺它连窗口都开不了。
# 注意 `/usr/bin/python3` 在有些 macOS 版本上是不带 tkinter 的，所以挨个试。
find_python() {
  local candidate
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 \
       && "$candidate" -c 'import tkinter' >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

if ! PYTHON="$(find_python)"; then
  echo "找不到带 tkinter 的 python3。"
  echo
  echo "  Homebrew：brew install python-tk"
  echo "  或到 python.org 装官方安装包（自带 tkinter）。"
  echo
  read -r -p "按回车关闭…" _
  exit 1
fi

echo "用 $PYTHON 启动简记…"
# 简记是**零第三方依赖**的，直接从源码跑就行，不需要装任何东西。
exec "$PYTHON" app/main.py
