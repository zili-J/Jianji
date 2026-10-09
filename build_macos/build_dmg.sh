#!/usr/bin/env bash
#
# 在 macOS 上把「简记」打成可分发的 .dmg。**这个脚本只能在 macOS 上跑。**
#
# 为什么不能在 Windows 上做：
#   · dmg 里装的必须是 `.app`，而 `.app` 只有在 macOS 上才生成得出来
#     （要拷 Python framework、重链 dylib、跑 codesign）；
#   · `hdiutil` / `codesign` / `iconutil` 都是 macOS 自带的命令。
#   所以 Windows 侧只能写脚本，验证得在 Mac 或 CI 上做。
#
# 用法（在**工程根目录**）：
#     build_macos/build_dmg.sh                # 出 dmg
#     build_macos/build_dmg.sh --app-only     # 只出 .app，不装 dmg
#
# 产物：dist/简记.app、dist/JianJi-<版本>.dmg
#
set -euo pipefail

# 切到工程根目录（脚本自己可能在 build_macos/ 里）
PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT"

# ---------------------------------------------------------------- 0. 平台检查

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "✗ 这个脚本只能在 macOS 上跑（当前是 $(uname -s)）。" >&2
  echo "  Windows 上请直接双击 run_jianji.cmd 从源码运行。" >&2
  exit 1
fi

APP_ONLY=0
[[ "${1:-}" == "--app-only" ]] && APP_ONLY=1

# ------------------------------------------------------------ 1. 找 Python 3

# 必须是有 tkinter 的那个：Homebrew 的 python 和系统自带的 /usr/bin/python3
# 都可能缺 tkinter，而简记整个界面就是 tkinter。
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
  echo "✗ 找不到带 tkinter 的 python3。" >&2
  echo "  Homebrew 的写法：brew install python-tk" >&2
  echo "  或者去 python.org 装官方安装包（自带 tkinter）。" >&2
  exit 1
fi
echo "· Python: $PYTHON ($("$PYTHON" --version 2>&1))"

# ------------------------------------------------- 2. 版本号（唯一来源 main.py）

VERSION="$("$PYTHON" - <<'PY'
import re, pathlib
text = pathlib.Path("app/main.py").read_text(encoding="utf-8")
match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
if not match:
    raise SystemExit("在 app/main.py 里找不到 __version__")
print(match.group(1))
PY
)"
echo "· 版本: $VERSION"

# ----------------------------------------------------- 3. 建一个干净的构建环境

# **别往用户的全局环境里装东西**：PyInstaller 只在打包时需要，
# 而简记本身是零第三方依赖的。venv 建在工程根，`.gitignore` 里已经忽略。
VENV="$PROJECT/.build-venv"
if [[ ! -x "$VENV/bin/python" ]]; then
  echo "· 建构建环境 $VENV"
  "$PYTHON" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip
# 钉一个大版本：PyInstaller 的 spec 格式跨大版本会变，别让 CI 某天自己升级后炸掉。
"$VENV/bin/python" -m pip install --quiet --upgrade "pyinstaller>=6.0,<7.0"

# ------------------------------------------------------------- 4. 打成 .app

echo "· PyInstaller 打包中…"
rm -rf build dist
"$VENV/bin/pyinstaller" --clean --noconfirm build_macos/jianji.spec

if [[ ! -d "dist/JianJi.app" ]]; then
  echo "✗ 没打出 dist/JianJi.app，看看上面的 PyInstaller 输出。" >&2
  exit 1
fi

# 访达里显示的名字来自**文件（夹）名**，不是 CFBundleDisplayName。
# PyInstaller 那边坚持用 ASCII（见 spec 的说明），所以在这里改成中文名。
# 改名不影响运行：真正被启动的可执行文件由 CFBundleExecutable 指定，仍是 JianJi。
rm -rf "dist/简记.app"
cp -R "dist/JianJi.app" "dist/简记.app"

# ------------------------------------------------------------- 5. 签名

# **ad-hoc 签名**（`-`）：没有 Apple 开发者证书时这是唯一能做的。
# 它让 App 在本机能启动，但**换一台机器下载下来仍会被 Gatekeeper 拦**，
# 用户要「右键 → 打开」一次（见 build_macos/README.md）。
# 有 Developer ID 的话把下面的 `-` 换成证书名，并加上 `--options runtime`。
SIGN_ID="${JIANJI_SIGN_ID:--}"
echo "· 签名（identity: $SIGN_ID）"
codesign --force --deep --sign "$SIGN_ID" "dist/简记.app"
codesign --verify --verbose=1 "dist/简记.app" || true

echo "✓ dist/简记.app"

if [[ "$APP_ONLY" == "1" ]]; then
  exit 0
fi

# --------------------------------------------------------------- 6. 装成 dmg

DMG="dist/JianJi-${VERSION}.dmg"
rm -f "$DMG"

if command -v create-dmg >/dev/null 2>&1; then
  # create-dmg 会摆好图标位置和「拖进应用程序」那个箭头，观感比裸 hdiutil 好得多。
  # 装法：brew install create-dmg
  echo "· create-dmg 装 dmg…"
  # create-dmg 在「同名卷已挂载」时会返回非零，但 dmg 其实已经出好了，
  # 所以这里不 set -e 地放过它，靠下面那句存在性检查兜底。
  create-dmg \
    --volname "简记 ${VERSION}" \
    --window-size 560 360 \
    --icon-size 110 \
    --icon "简记.app" 150 130 \
    --app-drop-link 400 130 \
    --no-internet-enable \
    "$DMG" "dist/简记.app" || true
else
  echo "· 没有 create-dmg，用 hdiutil 兜底（没装 create-dmg 就先 brew install create-dmg）"
  STAGE="$(mktemp -d)"
  trap 'rm -rf "$STAGE"' EXIT
  cp -R "dist/简记.app" "$STAGE/"
  ln -s /Applications "$STAGE/Applications"
  hdiutil create -volname "简记 ${VERSION}" -srcfolder "$STAGE" \
    -ov -format UDZO "$DMG"
fi

if [[ ! -f "$DMG" ]]; then
  echo "✗ dmg 没生成出来。" >&2
  exit 1
fi

echo
echo "✓ 完成"
echo "  App: $PROJECT/dist/简记.app"
echo "  DMG: $PROJECT/$DMG"
echo
echo "  首次在别的机器上打开会被 Gatekeeper 拦一次，放行办法见 build_macos/README.md。"
