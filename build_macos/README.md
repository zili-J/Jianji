# 在 macOS 上打包「简记」

这一目录里是**只能在 macOS 上跑**的打包链路。Windows 那台机器写得出这些脚本，
但**验证不了产物** —— dmg 里装的必须是 `.app`，而 `.app` 只有在 macOS 上才生成得出来
（要拷 Python framework、重链 dylib、跑 `codesign`）。

## 最快的两条路

### 一、没有 Mac？用 GitHub Actions

推到 GitHub 之后（工程根的 `push_backup.cmd`），到仓库的 **Actions** 页选
「构建 macOS dmg」→ **Run workflow**。跑完在页面底部下载：

| artifact | 架构 | 给谁用 |
| --- | --- | --- |
| `jianji-dmg-arm64` | Apple Silicon（M1/M2/M3…） | 2020 年之后的 Mac |
| `jianji-dmg-x86_64` | Intel | 2020 年之前的 Mac |

打 `v*` 形式的 tag 也会自动跑一次。workflow 在 `.github/workflows/build-macos.yml`。

### 二、手边就有 Mac

```bash
git clone https://github.com/zili-J/Jianji.git
cd Jianji
bash build_macos/build_dmg.sh
```

产物在 `dist/` 下：`简记.app` 和 `JianJi-<版本>.dmg`。
只想出 `.app`、不装 dmg：`bash build_macos/build_dmg.sh --app-only`。

脚本会自己找一个**带 tkinter** 的 python3、在工程根建一个 `.build-venv`
（**不碰你的全局环境**）、装 PyInstaller、打包、签名、装 dmg。
没有 `create-dmg` 也能跑，会退回 `hdiutil`：

```bash
brew install create-dmg     # 可选，装出来的 dmg 好看得多
```

### 三、不打 dmg，直接跑源码

双击工程根的 **`run_jianji.command`** 就行（第一次可能要在终端里 `chmod +x`）。
简记是**零第三方依赖**的，从源码跑不需要装任何东西，只要 python3 带 tkinter：

```bash
brew install python-tk      # Homebrew 的 python 默认不带 tkinter
```

## 首次打开被拦住了怎么办

打出来的 dmg **没有 Apple 开发者签名**（用的是 ad-hoc 签名 `-`），所以在**别的机器**上
第一次打开会看到「无法打开，因为 Apple 无法检查其是否包含恶意软件」。

这是 Gatekeeper 的正常反应，不是文件坏了。三种放行办法，任选一种：

1. **右键 →「打开」**，在弹窗里再点一次「打开」。（只需一次，之后正常双击。）
2. **系统设置 → 隐私与安全性**，滚到底部点「仍要打开」。
3. **终端里去掉隔离标记**（把 dmg 拖进终端就会自动补全路径）：
   ```bash
   xattr -dr com.apple.quarantine "/Volumes/简记 1.0.0/简记.app"
   ```
   已经拖进「应用程序」了的话：
   ```bash
   xattr -dr com.apple.quarantine /Applications/简记.app
   ```

想要彻底不弹这个框，得花 99 美元/年买 Apple Developer Program，用 Developer ID 证书
签名并做公证（notarization）。`build_dmg.sh` 里已经留了口子：

```bash
JIANJI_SIGN_ID="Developer ID Application: 你的名字 (TEAMID)" bash build_macos/build_dmg.sh
```

（真要分发的话还得补一步 `xcrun notarytool submit` + `xcrun stapler staple`。）

## 图标

放一张 1024×1024 的 PNG，然后：

```bash
build_macos/make_icns.sh 你的图标.png
```

会在 `build_macos/JianJi.icns` 生成图标，`jianji.spec` 下次打包自动捡到。
没有这个文件也能打包，只是用系统默认图标。

## 从 Windows 版过来，有几处不一样

这些不是 bug，是平台差异；对应的实现都在 `app/platform_support.py` 和两个后端里。

| 事情 | Windows | macOS |
| --- | --- | --- |
| 快捷键 | `Ctrl+S` 等 | **`⌘S` 和 `Ctrl+S` 都能用** |
| 「在…中显示」 | 资源管理器 | 访达（`open -R`） |
| 回收站 | 系统回收站 | `~/.Trash`（跨卷时退回 Finder 删除） |
| 设置存哪 | `%LOCALAPPDATA%\JianJi` | `~/Library/Application Support/JianJi` |
| 朗读引擎 | SAPI5 | `/usr/bin/say` |
| **朗读音量** | 可调 | **不生效**（`say` 没有音量参数） |
| 界面缩放 | 自己声明 DPI 感知 | Tk 原生按点渲染，不额外缩放 |
| 输入法组字字体 | 调 imm32 | 系统自己管 |
| 默认界面字体 | 微软雅黑 | 苹方 |
| 代码块等宽字体 | Consolas | Menlo |

**「导出音频」的音色**：macOS 上会自动挑一个 `zh_CN` 音色（`say -v ?` 里第一个），
因为 `say` 的默认音色多半是英文的，拿它念中文会变成一串怪音节。
想换音色在「系统设置 → 辅助功能 → 朗读内容 → 系统声音」里改。

**写作字体**：Windows 版那批候选（SimSun / KaiTi / SimHei）在 macOS 上都不存在，
所以 `FONT_CANDIDATES` 换成了 macOS 自带的那几款 + 霞鹜文楷 / 思源宋体 / 更纱黑体。
想要和 Windows 上一模一样的观感，装一下**霞鹜文楷**（开源，OFL 许可）即可。

**ffmpeg**：从访达启动的 `.app` 拿不到登录 shell 的 `PATH`，Homebrew 装在
`/opt/homebrew/bin` 的东西**看不见**。所以「导出音频（MP3）」要么装到
`/usr/local/bin`，要么设环境变量 `JIANJI_FFMPEG=/opt/homebrew/bin/ffmpeg`。
不装也没事，转 MP3 本来就是可选的一步，WAV 照常导出。
