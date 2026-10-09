# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把 `app/` 打成一个自包含的 `JianJi.app`。

**只能在 macOS 上跑。** `BUNDLE` 产出的是 macOS 的 `.app` 目录结构，
Windows / Linux 上的 PyInstaller 根本没有这个 target —— 这也是为什么
「在 Windows 上出一个 dmg」这条路走不通：dmg 里装的必须是 `.app`，
而 `.app` 只能在 macOS 上生成（要拷 Python framework、重链 dylib、跑 codesign）。

用法（在**工程根目录**，不是这个目录）：

    build_macos/build_dmg.sh              # 推荐：一条龙，含 venv + dmg
    pyinstaller --clean --noconfirm build_macos/jianji.spec   # 只出 .app

产物：`dist/JianJi.app`。

**为什么 bundle 叫 `JianJi` 而不是 `简记`**：PyInstaller 在非 ASCII 的应用名上
处理 `Contents/Frameworks` 的符号链接时出过问题，而 `.app` 的名字完全可以事后改
（`CFBundleExecutable` 仍是 `JianJi`，不跟着名字走）。所以这里坚持 ASCII，
由 `build_dmg.sh` 在装进 dmg 之前 `cp -R` 成 `简记.app` —— 访达里显示的就是简记。
"""
import re
import sys
from pathlib import Path

BUILD_DIR = Path(SPECPATH).resolve()            # build_macos/
PROJECT = BUILD_DIR.parent                       # 工程根
APP = PROJECT / "app"

if sys.platform != "darwin":
    raise SystemExit(
        "这个 spec 只能在 macOS 上跑：.app 是 macOS 专有的包格式。"
        "Windows 上请用 run_jianji.cmd 从源码运行。"
    )

# 版本号的唯一来源是 `app/main.py` 里的 `__version__`，别在这儿再抄一份。
_match = re.search(r'^__version__\s*=\s*"([^"]+)"',
                   (APP / "main.py").read_text(encoding="utf-8"), re.MULTILINE)
if not _match:
    raise SystemExit("在 app/main.py 里找不到 __version__，版本号无从确定。")
VERSION = _match.group(1)

# 图标是可选的：有 `build_macos/JianJi.icns` 就用，没有就交给系统默认图标。
# 生成办法见 `build_macos/make_icns.sh`。
ICON = BUILD_DIR / "JianJi.icns"

a = Analysis(
    [str(APP / "main.py")],
    # `app/main.py` 里是 `import storage` 这种**平级**导入（跑起来时 `app/` 在
    # sys.path 上），所以分析路径必须带上 `app/`，否则一堆模块找不到。
    pathex=[str(APP)],
    binaries=[],
    datas=[],
    # `image_export` 里那两个后端是**按平台条件导入**的。PyInstaller 的静态分析
    # 其实两条分支都会扫，但 macOS 上跑的时候 `platform_support.MACOS` 才是真，
    # 点名写出来是给自己留个明账：换后端时别忘了这里。
    hiddenimports=[
        "platform_support",
        "image_common",
        "image_backend_gdi",
        "image_backend_cg",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # tkinter **绝对不能排除** —— 整个界面就是它。这里只排掉明确用不到的重家伙。
    excludes=["numpy", "PIL", "setuptools", "pip", "pytest", "unittest"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="JianJi",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX 会改动二进制，签名会因此失效；macOS 上体积本来也不是问题。
    upx=False,
    console=False,          # 双击不弹终端窗口
    disable_windowed_traceback=False,
    argv_emulation=False,   # 不吃「把文件拖到图标上」那套 AppleEvent
    target_arch=None,       # 跟随构建机（CI 上是 arm64）
    codesign_identity=None, # 显式留空，由 build_dmg.sh 统一签
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="JianJi",
)

app = BUNDLE(
    coll,
    name="JianJi.app",
    icon=str(ICON) if ICON.is_file() else None,
    bundle_identifier="com.jianji.jianji",
    version=VERSION,
    info_plist={
        "CFBundleName": "简记",
        "CFBundleDisplayName": "简记",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "CFBundleGetInfoString": f"简记 {VERSION}",
        # Retina：不声明的话 macOS 会把窗口按 1× 渲染再放大，文字发糊。
        # 这一条和 Windows 那边的 `SetProcessDpiAwareness` 是一回事。
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSHumanReadableCopyright": "",
        # 让「用简记打开 .md」出现在访达的右键菜单里。
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "Markdown 文稿",
                "CFBundleTypeRole": "Editor",
                "LSHandlerRank": "Alternate",
                "LSItemContentTypes": ["net.daringfireball.markdown", "public.plain-text"],
            }
        ],
    },
)
