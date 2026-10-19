# 简记 · 项目长期笔记

> 只放「不照做就会犯错」的规则与落点。**动手前先读 `DETAIL.md` 的同名章节**：实测数据、踩坑经过、被否决的
> 写法全在那儿。逐日进展 `memory/YYYY-MM-DD.md`；设计 `.3things/build-log.md`。
> 技能：`tk-text-incremental-highlight`、`long-image-export`、`windows-gui-quiet-capture`、`windows-batch-and-git-remote`。

## 平台移植（macOS / dmg）

- **dmg 只能在 macOS 上产出**，Windows 侧只能写脚本、验证不了产物。唯一不需 Mac 的验证是 GitHub Actions
  `build-macos`（Intel + Apple 芯片 + 启动冒烟）。
- **平台差异唯一落点 `app/platform_support.py`**（只 import 标准库）。`app/` 其余模块**不许**出现裸
  `sys.platform` / `os.name` / `ctypes.windll`；例外只有 `image_backend_gdi.py` / `image_backend_cg.py` /
  `speech.py` 的两套后端。
- 对应关系：创建时间 `st_ctime`→`st_birthtime`；写时间 `SetFileTime`→`setattrlist`；删除→`~/.Trash`；
  状态目录→`~/Library/Application Support/JianJi`；定位 `explorer /select,`→`open -R`；
  `CREATE_NO_WINDOW`→**0**（macOS 传 `creationflags` 直接 `ValueError`）。
- **CoreGraphics**：原点左下 → `Translate(0,H)`+`Scale(1,-1)`；翻完必须 `CGContextSetTextMatrix(1,0,0,-1,0,0)`
  （否则**字形上下颠倒且不报错**）；位图用 `kCGImageAlphaNoneSkipFirst|kCGBitmapByteOrder32Little`（内存
  B,G,R,X 与 GDI DIB 相同）；**量文字必须走 CTLine**；`kCTFontAttributeName` 用 `c_void_p.in_dll(...).value` 读。
- **`say`**：`/usr/bin/say` 与 ffmpeg **必须绝对路径**（`.app` 只有 `/usr/bin:/bin:/usr/sbin:/sbin`）；正文走
  **`-f 文件`**；静音 `[[slnc N]]`；`-r` 是**绝对值 WPM**（175 / ±20 / 下限 90）；`-v` 必须 `zh_CN`；
  **没有音量参数 → `volume` 被忽略**。
- **`speech.py` 的 macOS 段必须排在 `NATURAL_RATE`/`*_BREAK_MS`/`_SENTENCE_END_RE` 之后**（默认参数在 `def`
  时求值，放前面 `NameError` → import 失败）。非 Windows 靠 `_UnavailableDll` 顶替 `WinDLL`；`winreg` 条件导入。
- **`main.py`**：`FAMILY` 微软雅黑 UI / 苹方；字体表拆 `_WINDOWS_FONTS`/`_MACOS_FONTS`/`_COMMON_FONTS`，
  **macOS 表不许出现 SimSun/KaiTi/SimHei/YaHei/Consolas**（静默退到 Tk 默认字体）。**快捷键主键统一走
  `_shortcut_modifiers()`，绑定与解绑必须共用**（否则换一次主题就叠绑，⌘S 存两次）。菜单文案走
  `_reveal_label()`。`__version__` 在模块顶部（打包脚本正则读）。`install_shortcut.py` 非 Windows 直接
  `ImportError`。macOS 不需要 DPI 声明与输入法字体强制（都早退）。
- **打包**：`build_macos/{jianji.spec,build_dmg.sh,make_icns.sh,README.md}`、`run_jianji.command`、
  `.github/workflows/build-macos.yml`。`BUNDLE(name="JianJi.app")` 保持 ASCII，脚本再改名「简记.app」。
- **判据错时，被检查的东西往往没错**：「源码不许出现 windll」要用 `code_only()`（AST 去注释）；一条判据对
  全部样本都失败就先怀疑判据；**导入时算好的常量（`FILE_MANAGER_LABEL`）`_PlatformPatch` 换不掉**，别写假断言。

## 运行 / 测试 / 交付物

- 运行 `run_jianji.cmd` / `python app/main.py`（系统 Python 3.12，零第三方依赖）。
- **`python` 走 PATH 是托管 3.13，没有 tkinter**。测试/同步一律用全路径
  `C:\Users\22910\AppData\Local\Programs\Python\Python312\python.exe`。
- 测试 `python tools/run_tests.py`（**默认隐藏桌面**；`--foreground` 才回当前桌面，**截原生菜单必须用它**；
  `-k` 按文件名子串过滤）。报告边跑边写进 `outputs/test-output.txt`；**0 用例 = 退出码 3**。裸
  `unittest discover` 必须带 `-t tests` 且先重定向到文件。**不要同时跑两套 GUI 用例**（挂过一次 51 分钟）。
- 给用户看的文档写进文档文件夹（本机 `C:\Users\22910\Documents\wb01`），**不写工程根**。
- **说明书每次更新都要同步并重出长图**（用户固定要求）：改 `<工程根>/简记使用说明.md` → 跑
  `tools/quiet_desktop.py <py> tools/_sync_manual.py`。`说明书\` 里那份是**产物**，改它白改。
- **`简记使用说明.md` 是测试基准**：`EditorParityTests` 拿它逐行对账，并断言覆盖全部 10 种块级语法；
  `hr` 靠章节间 `---`（前面要有空行）。**自指的张数（「分成了三张」）改完必须重新导出核对**。
- **写 `.md` 给本应用渲染：一段话必须写成一行**（按源行分块）。连续两行 `> xxx` 会裂成两个引用块（除非首行
  行尾两个空格）。**表格别放超长单元格**（等比压缩下限写死 `body_size*2.2`）。**别改 `_measure`/`char_widths`**。
- **原子写盘会冲掉创建时间**：`atomic_write_markdown` 是「临时文件 + `os.replace`」，替换后要用
  `set_creation_time` 写回。**别删这步**。
- **文件名时间戳**：`新建-YYYY-MM-DD-HHMMSS` 与 `YYYY-MM-DD HHMMSS` 可信；`YYYYMMDD-HHMMSS ` 前缀是备份/导入
  时刻，**不能用**；只有日期的也别认。回填 `tools/backfill_created.py`。
- **扫文稿夹必须用 `storage.list_markdown(folder, recursive=True)`**，别用 `rglob`（它跳过 `.简记回收站`）。
  「字面路径 exists=False、rglob 却找得到」这种矛盾先去查目录，别怀疑编码。前提错了就**退回原状再报告**。
- **`tools/` 只留 20 个**（清单在 `.3things/project.md`）。一次性探针**用完即弃**。删探针看**活文件有没有引用**。
- **有 git 仓库**（`main`，`8f63f1e` 起）：回退用 `git checkout -- <文件>`。`core.autocrlf=false`；身份是占位值。
  已忽略 `outputs/`、`__pycache__/`。**行尾**：绝大多数 LF，CRLF 只有 `app/image_export.py` 与
  `.workbuddy-ai/memory/` 下的文件。

## 备份 / 远端

- `origin` = `https://github.com/zili-J/Jianji.git`；**备份入口是双击 `push_backup.cmd`**。
- **沙箱里推不上去**（`GIT_TERMINAL_PROMPT=0`、凭据会挂住、进程树被 SIGTERM）——**认证必须在用户窗口里点一次**。
- `cmdkey /list` 查不到 ≠ 没凭据，要 `git credential-manager get` 才算数。
- **行尾**：`.cmd`/`.bat` 必须 CRLF，`.sh`/`.command` 必须 LF。`.gitattributes` 里 `*.cmd` 那条要排在 `*` 之后。
- **Git Bash 会改坏 `ref:path`**：用 `git ls-tree origin/main <path>` 或 `MSYS_NO_PATHCONV=1`，**报缺失前先复核**。

## 核验必须在用户真实设置下做

- 设置现读 `%LOCALAPPDATA%\JianJi\state.json`，**用户会自己调**，别照抄任何一行。探针要把用户 settings 原样
  写进临时 state.json 再构造 `JianJiApp`（设置只在启动时读一次）；测试要覆盖非默认设置。
  **判据里不要出现绝对像素**，一律 `px(设置项)`。用户的 state.json 与文档夹都别写。
- 探针三坑：`mark_set` 后必须显式跑 `_refresh_cursor_line()`；「光标在别处」停在**紧邻目标行的正文行**；
  `open_file(同一路径)` 直接短路，换用例要换文件名。

## 界面约定 · 反退化

- **编辑区必须 `wrap="char"`，不能改回 `word`**（用户第 11 轮）。各级标题**字号与正文一致**，靠加粗 / 颜色 /
  留白 / 左侧 `H1`–`H6` 徽标分层。尺寸一律 `px()`。
- **内容行宽基准是显示器宽**：`T = min(r·S, C − 2×5%·S)`，`pad = (控件宽 − T)//2`；窗口下限 =
  `NAV_W + CARDS_W + 40%·S`（**值没变必须提前返回**，否则 `minsize` 来回弹）。**三栏都开时落第二档
  `T = C − 10%·S`，那一档 `line_width` 不起作用**——`test_the_setting_is_inert_...` 钉住，**别修掉**。
  测试用 `pin_screen_share()`；对称 `padx` 只能居中到 ±1px。
- **光标所在行显示源码**；**唯一例外是有序列表序号**（画布画、永远 elide、**不记进 `_line_marks`**）。别扩大。
  `_line_marks` 是 **5 元组**。**行首记号露源码必须补偿**：`syntax_marker` + `lmargin1/2` + `wrap="char"` 三件
  一起做，标签**必须建在 `li{n}` 之后**。**`spacing1` 不生效**。标题走 `_tag_marker`，别改回 `_tag_syntax`。
- **底部留白 0.25**：给**最后一行**挂 `spacing3`，**必须挂在末尾那个空行上**（挂正文行会被选中高亮涂蓝）→
  `_load_editor_text` 调 `_ensure_trailing_newline`（拼进那一次 `insert`，在 `edit_reset()` 前）。判据必须
  **量标签真实范围**（字符串比大小是错的）。**两条否决过的路**：留白挪出正文区；「删了就补回来」。
- **装饰画布走 `_display_line_box()`**：只看宽度；空行也接受；重测用 `index + 1 display chars`。
  **不透明画布会盖住光标**；**一行全 elide 时 Tk 把光标退回行首** → `_style_line` 有序分支在
  `body_empty and 光标在本行` 时把行尾空格留在 elide 之外。**别改回「画布整体左移」**。
- **文件夹树是多层嵌套**（2026-09-29 推翻「只有一级」）：顶层挂树根，子夹挂父节点。**`_tree_nodes` 不能当父
  节点查找表**（它把 `self.folder` 映射到 `"__all__"`），用局部 `folder_iids`。`_open_iids()` 记展开状态。
  点文件夹只显示它自己那层；「全部文稿」递归。已取消「今天」入口。
- **文稿排序**：`sort_key` 按时间**倒序**。**`created` 必须走 `storage.creation_time()`**（macOS 上 `st_ctime`
  是元数据变更时间）。入口在文稿列表右键菜单，**别放回标题栏**。
- **搜索**（2026-10-19）：逻辑在 `app/search.py`（纯函数），界面在 `main.py`「搜索」一节。**条子与结果面板都是
  `place` 浮层、绝不进 grid**（进 grid 会改编辑区宽度 → 正文重排）；条子放**顶栏那一行**。主键走
  `_shortcut_modifiers()`，**编辑器和根窗口各绑一次**（控件级返回 `"break"`）。**全局范围方向键只换选中项、
  回车才打开**；`_goto_hit` 只在 `hit.path == current_path` 时贴 `search_current` / `see()`。搜索标签建在
  `_configure_editor_tags` **最后**、**只设 background/foreground，不设 `elide`**。正文一改就重贴。**Esc 先关搜索**。
- **顶部栏常显**，字数**悬停才显示**（`count_label` 初始 `grid_remove()`，**别用 `grid_forget()`**）。判据按
  指针真实位置算（`_pointer_is_on_topbar`），**不能只看 Enter/Leave**。`_counts_job` 要进 `_cancel_after_jobs`。
- **粗体/斜体走暖色**，改色要**三处一起**（编辑器标签 / `export_palette()` / `Palette` 默认值）。**深色主题** =
  `apply_theme()` + 整块 `_rebuild_ui()`；`THEMES["light"]` 导入时快照 → **导出长图永远浅色**；重建前
  `root.unbind()` 防叠绑。**预览卡片字体固定**；**文稿列表不放滚动条**；**点卡片按 `_card_bounds` 查表**。
- **有序列表回车续号**；**空项回车结束列表**；程序化 `insert` 后手动 `see("insert")`。滚轮：编辑区
  `delta/3 × 6` 且**必须返回 `"break"`**；卡片与树 `LIST_WHEEL_UNITS=3`。**Canvas 只在滚动区比视口高时夹原点**
  （判据 `canvasy(0) < 0`，此时 `yview()` 说谎）。

## 其余硬约束

- **共用函数**：`_load_editor_text` 的 `normal → delete → insert → edit_reset → edit_modified(False)` **顺序不能换**；
  `_build_ui()` = window → layout → typography → shortcuts，换主题调它之前**必须先 `root.unbind()`**。
- **文件名跟随一级标题**：`_follow_title()` 只在 `save_now()` 里、**落盘之后**改名；判据只有一条「第一行是 H1
  且名字不同就改」，**别改回「只在你改标题时才改」**。撞名走 `unique_path`；禁字符换**全角同形字**；截断 80。
  `_rename_failed_for` 的 key **必须带 stem**。`move_document_to` / `delete_document` 改名后要
  `if was_current: path = self.current_path`。`_sync_manual.py` **不许写死说明文件名**。
- **导出音频**：`speech.py`；念的文本走 `speakable_text()`，复用 `build_export_rows`。合成在后台线程 +
  `root.after` 轮询（`_speech_job`/`_speech_poll` 登记进 `_cancel_after_jobs`）。**语速只走 `voice.Rate` /
  `say -r`，绝不进 `<prosody rate>`**。节奏 450 / 260 / 160ms，逗号不加；`_gap(0)` 返回空串。
- **长图导出**：宽 1080；**缩进续行必须并进上一段**；**分张上限是软约束**（别改成按 `span` 卡）；
  **`DEFAULT_BODY_SIZE = 46`**（一行正好 20 汉字，47 就退成 19）。**行内强调**：`_inline_spans` 与 `_tag_inline`
  必须一字不差；**用 `search` + 手动推进，不用 `finditer`**（反引号里的 `**` 会被当真）。**行内记号必须开闭
  在同一源行**。`INLINE_CODE_RE` 只认单反引号。
- **列表缩进**：`LIST_MAX_LEVEL=4`、`LIST_INDENT_STEP=2`（必须和 `_indent_level` 的 `空格数//2` 一致）。
  `_on_editor_tab` 按「范围内有没有列表行」分派，**不能按「有没有移动」**；`<Tab>` 必须返回 `"break"`。
  **增量重解析**先走 `_apply_markdown_styles_incremental()`，`False` 才整篇；两条路径共用 `_style_line`，顺序
  `_drop_line_marks`（旧行号）→ `_shift_line_maps`（delta）→ 抹标签 → 上色。**卡片阴影要有自己的 tag**。
- **`import main` 必须在第一次 `Tk()` 之前**（它导入时声明 DPI 感知）。反了 `tk scaling` 变成 1.332，所有量几何
  的用例一起红，单独跑那些模块却全绿。兜底在 `run_tests.py` 的 `run_in_process()`，**别删**。
- **隐藏桌面**：`event_generate("<Return>")` 不投递按键 → 只能断言绑定脚本；截选区前必须 `focus_force()`；
  **别在 sleep 里等模态框**（没人点确定，会挂到被杀且日志为空）。**截原生菜单**：`post()` 不返回 → 先起线程
  抓屏再 `post()`，抓完 `os._exit(0)`；先等 `winfo_viewable()`。
- **量几何**：`count(...,"ypixels")` 与 `yview()` 都不是稳定值；用 `dlineinfo`/`bbox`；量位移用**中位数**；
  `yview_moveto(1.0)` 前要 `update_idletasks()`。**`dlineinfo` 含 `spacing1/3`**。**Tk 用 `\n` 分行、行尾 `\r`
  是行内容** → 切行只能用 `split_document_lines()`（`splitlines()` 会在 `\x0b`/U+2028 断行）。**`end-1c` 是最后
  一个真字符**。
- **`os.utime` 改不了创建时间**；「按创建时间排序」的测试**不能写死顺序**（相等时 `sorted` 保持原序）。**输入法
  字号归 Windows 管**（`imm32`）。**「两块颜色分不分得开」不能比对比度**（要看 `max(|Δr|,|Δg|,|Δb|) >= 12`）。
  **数叠绑不能数 `info commands`**（`unbind` 不删命令），要读 `bind()` 脚本里函数名出现几次。
- **`shutil.which("ffmpeg")` 找不到 ≠ 没装**（`_locate()` 会往 PATH 每层再探一层 `bin\`）。**起外部进程要
  `CREATE_NO_WINDOW`**。**本机 SAPI5 只有 `TTS_MS_ZH-CN_HUIHUI_11.0`**；OneCore 音色 SAPI5 看不见。
  **审计后故意留着的「伪冗余」**：`_card_index_at(self,x,y)` 的 `x`、回调的 `_event`/`_args`。
- **`⌘` / `⇧` 在长图与编辑器里是豆腐块**（雅黑没有 U+2318，GDI 导出不做字体回退）——文档里写 `Command+N`。
