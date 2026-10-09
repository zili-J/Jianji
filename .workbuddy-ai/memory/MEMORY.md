# 简记 · 项目长期笔记

> **只放「不照做就会犯错」的规则与落点。动手前先读 `DETAIL.md` 的同名章节**——实测数据、踩坑经过、被否决的
> 写法、为什么这么定，**全在那儿**（`DETAIL.md` 比这份长一倍，是它的展开版）。逐日进展 `memory/YYYY-MM-DD.md`；
> 设计 `.3things/build-log.md`。
> 技能：`tk-text-incremental-highlight`（Tk 硬约束全清单 + `references/tk-text-traps.md`）、`long-image-export`、
> `windows-gui-quiet-capture`（隐藏桌面跑 GUI + 截图）、`windows-batch-and-git-remote`。
> **`quiet-desktop-gui-run` / `windows-gui-screenshot` 已并入 `windows-gui-quiet-capture`**（2026-09-29）。

## 平台移植（2026-09-29 起：Windows → macOS，并出 dmg）

- **dmg 只能在 macOS 上产出**；Windows 侧只能写脚本，**产物本身验证不了**。唯一不需 Mac 的验证路径是 GitHub
  Actions 的 `build-macos`（Intel + Apple 芯片各一份 + 启动冒烟）。
- **平台差异唯一落点 `app/platform_support.py`**（只 import 标准库）。`app/` 其余模块**不许**出现裸
  `sys.platform` / `os.name` / `ctypes.windll`；例外只有 `image_backend_gdi.py`、`image_backend_cg.py` 和
  `speech.py` 的 SAPI / `say` 两套实现。
- 对应关系：创建时间 `st_ctime`→`st_birthtime`；写 `SetFileTime`→`setattrlist`(`ATTR_CMN_CRTIME`)；删除
  `SHFileOperationW`→移进 `~/.Trash`；状态目录 `%LOCALAPPDATA%\JianJi`→`~/Library/Application Support/JianJi`；
  定位文件 `explorer /select,`→`open -R`；`CREATE_NO_WINDOW`→**0**（macOS 传 `creationflags` 直接 `ValueError`）。
- **CoreGraphics 四坑**（细节在 `image_backend_cg.py` docstring）：原点在左下 → `Translate(0,H)` + `Scale(1,-1)`
  翻 CTM；翻完必须 `CGContextSetTextMatrix(1,0,0,-1,0,0)`（否则**字形上下颠倒且不报错**）；位图用
  `kCGImageAlphaNoneSkipFirst|kCGBitmapByteOrder32Little`（内存 **B,G,R,X 与 GDI DIB 逐字节相同** → 一套 PNG
  编码器通吃）；`kCTFontAttributeName` 是**全局指针变量**，用 `c_void_p.in_dll(ct,...).value` 读。**量文字必须走
  CTLine**（不能用字体步进，CoreText 给缺字做 fallback 会算错折行）；`CTFontCreateCopyWithSymbolicTraits`
  **一次只能加一个 trait**。两个后端在 `Surface`/`Fonts` 上都是「只画不读」，`BackendParityTests` 钉住。
- **`/usr/bin/say` 必须写绝对路径**（访达启动的 `.app` 只有 `/usr/bin:/bin:/usr/sbin:/sbin`）；**ffmpeg 同理**
  （Homebrew 的 `/opt/homebrew/bin` 看不见，`ffmpeg._COMMON` 是唯一兜底）。**正文要用 `-f 文件` 传给 `say`**，
  不能塞 argv。`say` 的静音是 `[[slnc N]]`、语速 `-r` 是**绝对值** WPM（`SAY_BASE_WPM=175`、每档 `+20`、下限
  `SAY_MIN_WPM=90`）、音色 `-v` **必须挑 `zh_CN`**；**`say` 没有音量参数 → macOS 上 `volume` 被忽略**。
- **`speech.py` 的 macOS 段落必须排在 `NATURAL_RATE`/`PARAGRAPH_BREAK_MS`/`SENTENCE_BREAK_MS`/`LINE_BREAK_MS`/
  `_SENTENCE_END_RE` 之后**（`build_say_script` 默认参数在 `def` 时求值，放前面 `NameError` → 模块 import 失败）。
  **非 Windows 靠 `_UnavailableDll` 顶替 `ctypes.WinDLL`** 让模块级 argtypes 照跑；`winreg` 条件导入。
- **`main.py`**：`FAMILY` = 微软雅黑 UI / 苹方，`MONO_FAMILY` = Consolas / Menlo；字体表拆 `_WINDOWS_FONTS`(11) /
  `_MACOS_FONTS`(7) / `_COMMON_FONTS`(3)。**macOS 表里不许出现 `SimSun`/`KaiTi`/`SimHei`/`Microsoft YaHei`/
  `Consolas`**（一个都命中不了 → 静默退到 Tk 默认字体）；两份表可共用中文本地化名，**拉丁名不许重叠**
  （`STKaiti`/`STSong`/`STZhongsong`/`STHeiti` 除外）。**快捷键主键统一走 `_shortcut_modifiers()`**（Windows
  `("Control",)`、macOS `("Command","Control")`）——**绑定与解绑必须共用它**，否则换一次主题就叠绑（⌘S 存两次）。
  菜单文案走 `_reveal_label(verb)`。`__version__ = "1.0.0"` 在模块顶部（两个打包脚本正则读它）。
  `install_shortcut.py` 非 Windows 直接 `ImportError`。**macOS 不需要 DPI 声明**（`enable_dpi_awareness()` 返回
  1.0）、**也不需要输入法字体强制**（`_apply_ime_font()` 早退）。
- **打包链路**：`build_macos/jianji.spec`（`BUNDLE(name="JianJi.app")` 保持 ASCII，脚本再改名「简记.app」）、
  `build_macos/build_dmg.sh`（`--app-only`、`JIANJI_SIGN_ID` 签名钩子、create-dmg 退 hdiutil）、
  `build_macos/make_icns.sh`、工程根 `run_jianji.command`、`.github/workflows/build-macos.yml`、`build_macos/README.md`。
- **判据错的时候，被检查的东西往往没错**：做「源码里不许出现 windll」这类断言要用 `code_only()`（`ast.parse` 去掉
  docstring/注释再 `unparse`），直接扫原文会被**解释 `st_ctime` 的注释**判红。**一条判据若对全部样本都失败，先怀疑
  判据**（`"kaiti"` 子串比对把苹果自带的 `Kaiti SC`/`STKaiti` 抓成 Windows 专有字体）。**导入时算好的常量（如
  `FILE_MANAGER_LABEL`）`_PlatformPatch` 换不掉**，别写那种假断言。

## 运行 / 测试 / 交付物

- 运行 `run_jianji.cmd` / `python app/main.py`（系统 Python 3.12，无第三方依赖）。
- **`python` 走 PATH 是托管 3.13，没有 tkinter**。跑测试/同步一律用**系统 3.12 全路径**：
  `C:\Users\22910\AppData\Local\Programs\Python\Python312\python.exe`。
- 测试 `python tools/run_tests.py`（**默认隐藏桌面**；`--foreground` 才回当前桌面，**截原生菜单必须用它**；`-k`
  按文件名子串过滤）。输出落 `outputs/test-output.txt`，屏幕只摘 `FAIL:`/`ERROR:`/`Ran`/`OK`；**0 用例 = 退出码 3**。
  裸 `unittest discover` **必须带 `-t tests`** 且**先重定向到文件**。报告**边跑边写**（`_FlushingStream`）——卡死时
  看 `outputs/test-output.txt` 末尾那行就知道卡在哪个用例。**不要同时跑两套 GUI 用例**（实测挂过一次 51 分钟）。
- 给用户看的文档写进**文档文件夹**（`state.json` 的 `default_folder`，本机 `C:\Users\22910\Documents\wb01`），
  **不写工程根目录**（根目录 `.md` 会让文件夹树混进 `app`/`tools`/`tests`）。
- **使用说明每次更新都同步到 `C:\Users\22910\Documents\wb01\说明书\` 并重出长图**（用户固定要求）：
  `python tools/quiet_desktop.py <python 全路径> tools/_sync_manual.py`。**要改说明书就改
  `<工程根>/简记使用说明.md`**——`说明书\` 里那份是**产物**，会被原样覆盖（踩过一次：改了产物，白改）。
- **`简记使用说明.md` 是测试基准文件**：`tests/test_export.py::EditorParityTests` 拿它整篇做「编辑器 vs 导出」逐行
  对账，并断言它**覆盖全部 10 种块级语法**（`heading/text/blank/bullet/ordered/task/quote/code/hr/table`）。重写必须
  保留这 10 种；`hr` 靠章节之间的 `---`，**前面必须有空行**。**文里自指的张数（「本说明就分成了三张」）改完必须
  重新导出核对**。
- **写 `.md` 给这个应用渲染时，一段话必须写成一行。** 编辑器和导出都**按源行分块**（只有**行首缩进**的续行会被
  并进上一段），手工断行会让每行各自成块。**引用同理**：连续两行 `> xxx` 会裂成两个引用块，除非第一行以**行尾
  两个空格**结尾。**表格里别放超长单元格**：`_layout_table` 在 `total > content_width` 时**等比压缩**，下限是
  **写死的** `floor = body_size * 2.2`；把那格移出表格改成表下一行说明即可。**别去改 `_measure`/`char_widths`**。
- **原子写盘会把创建时间冲掉**：`atomic_write_markdown` 是「临时文件 + `os.replace`」，Windows 上那是**用源文件
  顶掉目标文件**。现在每次替换后用 `storage.set_creation_time` 把旧创建时间写回（best-effort）。**别再把这步删掉**。
- **文件名里的时间戳分两种，别认错**：`新建-YYYY-MM-DD-HHMMSS`（简记自己打的，**就是创建时刻**）和
  `YYYY-MM-DD HHMMSS`（`文档\日记` 的老约定）可信；`YYYYMMDD-HHMMSS ` **前缀**是**备份/导入时刻**，**不能用**；
  **只有日期没有时刻的也别认**。回填工具 `tools/backfill_created.py`（默认预演，`--apply` 才写，`--restore` 可还原）。
- **扫用户文稿夹必须用 `storage.list_markdown(folder, recursive=True)`，别用裸 `folder.rglob("*.md")`**：前者走
  `_is_hidden_relative()`，**跳过 `.简记回收站`**，与界面列表一致。**碰到「字面路径 `exists: False`、`rglob` 却找得到、
  逐字符比还完全相同」这种矛盾，先去查目录，别怀疑编码。** 用户批准「改 N 篇」时若前提是错的，**退回原状再报告**。
- **`tools/` 只留 20 个**（5 个正式工具 + 15 个被点名的核验入口，清单在 `.3things/project.md` 的「工具」一节）。
  一次性探针**用完即弃**；旧的 62 个归档在 `outputs/tools-探针归档-2026-09-26.zip`。删探针的判据是「**活文件**
  （`app/` `tests/` `tools/` 技能 `MEMORY.md` 说明书）有没有引用」——**历史日志里提到过不算理由**。
- **有 git 仓库**（2026-09-26 建，分支 `main`，首个提交 `8f63f1e`）——回退直接 `git checkout -- <文件>` /
  `git revert`。仓库级 `core.autocrlf=false`、`core.quotepath=false`；身份是占位值 `JianJi <jianji@localhost>`
  （**仅本仓库**）。已忽略 `outputs/`、`.idea/`、`__pycache__/`。**行尾别按印象写脚本**：绝大多数是 **LF**，CRLF
  的只有 `app/image_export.py`、`tools/_probe_editor_wrap.py` 和 `.workbuddy-ai/memory/` 下的文件。

## 备份 / 远端

- 远端 `origin` = `https://github.com/zili-J/Jianji.git`。**备份入口是双击 `push_backup.cmd`**（工程根，纯 ASCII +
  `chcp 65001`，先打印远端/分支/待推提交再 `git push -u origin <当前分支>`）。
- **沙箱里推不上去**：环境全局设了 `GIT_TERMINAL_PROMPT=0`；显式打开后 `git credential-manager get` 会**超时挂住**；
  有时整个进程树被 **SIGTERM 硬杀**——那是沙箱级 kill，别当 git 错误查。**认证必须在用户自己的窗口里点一次**。
- **`cmdkey /list` 查不到 github 条目 ≠ 没有凭据**：要跑
  `printf "protocol=https\nhost=github.com\n\n" | git credential-manager get` 才算数。
- **行尾**：`.cmd`/`.bat` 必须 **CRLF**，`.sh`/`.command` 必须 **LF**（CRLF 报 `bad interpreter: ...bash^M`）。
  `.gitattributes` 里 `*.cmd text eol=crlf` **要排在 `*` 之后**，否则 `* text=auto eol=lf` 会把入口一起转成 LF，
  cmd.exe 的 `goto :label` 会失效。**改完属性要手动把工作区文件转回 CRLF**。判据 `git ls-files --eol <file>`。
- **Git Bash 会把 `ref:path` 参数改坏**：`git cat-file -e "origin/main:..."` 报 `Not a valid object name
  origin\main;...`（MSYS 路径转换，**时灵时不灵**），能骗出「文件缺失」的假结论。改用 `git ls-tree origin/main
  <path>` 或 `MSYS_NO_PATHCONV=1`。**报缺失前先复核**。

## 核验必须在**用户真实设置**下做（踩得很惨）

- 设置现读 `%LOCALAPPDATA%\JianJi\state.json`（`DEFAULT_SETTINGS` 是 `50/15/22/name`）。**这几个值用户会自己调**
  （`line_width`、字体、行高都改过），**别照抄任何一行，现读文件**。**探针要把用户 `settings` 原样写进临时
  state.json 再构造 `JianJiApp`**（设置只在启动时读一次）；**测试要覆盖非默认设置**
  （`TallLineHeightWrappedLineGeometryTests` 的 `SETTINGS` 钩子）。**判据里不要出现绝对像素**，一律相对设置项
  （`px(line_height)`）写。用户的 `state.json` 与文档文件夹都别写。
- 探针三坑：`mark_set` 后必须显式跑 `_refresh_cursor_line()`；「光标在别处」停在**紧邻目标行的普通正文行**；
  `open_file(同一路径)` **直接短路**，换用例要换文件名。

## 界面约定 · 反退化清单（细节全在 `DETAIL.md` 同名章节）

- **编辑区控件必须 `wrap="char"`，不能改回 `word`**（用户第 11 轮）。改 char 后三处机制一起消失/退化，**别当 bug
  修**：空显示行盒子消失、行首记号补宽度变冗余、`_inline_reveal_fits` 退化成哨兵。各级标题**字号与正文一致**，
  靠加粗、颜色、上下留白、左侧 `H1`–`H6` 徽标分层。尺寸一律 `px()`。
- **「内容行宽」基准是显示器宽**（用户第 12 轮）：`T = min(r·S, C − 2×5%·S)`，`pad = (控件宽 − T) // 2`。窗口下限 =
  `NAV_W + CARDS_W + 40%·S`（`_apply_min_window_width`；**值没变必须提前返回**，否则 `minsize` 触发 `<Configure>`
  会来回弹）。**三栏都开时落在第二档 `T = C − 10%·S`，那一档里 `line_width` 不起作用**——这是规则本身，
  `test_the_setting_is_inert_...` 钉住，**别「修掉」**。测试用 `tests/_pinning.py` 的 `pin_screen_share()` 钉屏幕宽；
  **对称 `padx` 只能居中到 ±1px**，判据写 `abs(差) <= 1`。
- **光标所在行显示源码**；**唯一例外是有序列表序号**（画布绘制、记号永远 elide、**不记进 `_line_marks`**）。**别扩大
  这个例外**。`_line_marks` 是 **5 元组** `(off_tag, on_tag, start, end, indent)`。**行首记号露源码必须补偿**：落点
  `syntax_marker` 标签，`lmargin1 = 行缩进 − 记号宽度`、`lmargin2 = 行缩进`、**`wrap="char"`** 三件一起做，标签
  **必须建在 `li{n}` 循环之后**。**`spacing1` 实测不生效**，别再试。标题露记号走 `_tag_marker`，**别改回 `_tag_syntax`**。
- **底部留白 `EDITOR_BOTTOM_PAD_RATIO = 0.25`**：给**最后一行**挂 `spacing3`。**生效条件：标签必须「包含最后一行的
  行首」**——**在空末行敲第一个字会把标签起点顶到新字之后 → 留白静默消失而行号不变**，所以判据
  `_bottom_pad_covers_last_line()` 必须**量标签真实范围**（**字符串比大小是错的**）。**留白必须挂在「末尾那个空行」
  上**（挂正文行会被选中高亮整块涂蓝）→ `_load_editor_text` 调 `_ensure_trailing_newline(text)`（拼进那一次
  `insert`，补在 `edit_reset()` 前）。**两条已否决的路别再走**：① 留白挪出正文区（编辑区常驻少 25% 视口）；
  ② 在 `_apply_bottom_pad` 里「删了就补回来」（末尾空行删不掉，20 个 `test_list_editing` 用例红）。
- **装饰（序号/徽标/竖条）画布**统一走 `_display_line_box()`：判据**只看宽度**（`box[2] > 0`）；量到好盒子**直接用**；
  空行也接受（`box[2] > 0 or box[3] >= px(行高)`）；重新量用 **`index + 1 display chars`**；定高
  `max(box[3], px(行高))`。**不透明画布会盖住光标**；**一行全被 elide 时 Tk 把光标退回行首（忽略 `lmargin`）** →
  `_style_line` 有序分支在 `body_empty and line_number == cursor_line` 时把**行尾空格留在 elide 之外**
  （`marker_end -= 1`）。**别再改成「画布整体左移」**（被撤掉的写法）。
- **文件夹树是「多层嵌套」的**（2026-09-29 用户要求，**推翻了原来的「只有一级」**，别再按「一级」改回去）：顶层挂
  **树根**（与「全部文稿」并列），子文件夹挂父节点下。**`_tree_nodes` 不能当父节点查找表用**（它把 `self.folder`
  映射到 `"__all__"`，会把顶层文件夹挂进「全部文稿」**里面**）；用局部 `folder_iids`。`_open_iids()` 在重建前记
  展开状态，**删了「收起」就白点**。「全部文稿」递归列全部；点某个文件夹**只显示它自己那一层**。已取消「今天」入口。
- **文稿排序**：`sort_key`（`name`/`created`/`modified`）按时间戳**倒序**。**`created` 必须走
  `storage.creation_time()`，不能直接读 `st_ctime`**——macOS/Linux 上那是**元数据变更时间**。入口在**文稿列表右键
  菜单**。**不要再放回标题栏**。
- **顶部栏常显**。**中间那个字数是悬停才显示的**（`count_label` 在 `column=1`，初始 `grid_remove()`——**别用
  `grid_forget()`**）。判据**不能看 `<Enter>/<Leave>` 事件**（指针挪到子控件上 topbar 也收到 `<Leave>`，会闪）：
  `_on_topbar_leave` 排 `after(COUNT_HIDE_DELAY_MS)`，到点用 `_pointer_is_on_topbar()` 算真实位置。topbar 和**三个
  子控件都要绑**。`_counts_job` 要进 `_cancel_after_jobs`。
- **粗体/斜体走暖色**（`BOLD_COLOR #B3261E`、`ITALIC_COLOR #A87514`）。改色要**三处一起**：编辑器标签、
  `export_palette()`、`image_export.Palette` 默认值。**深色主题** `apply_theme()` = `globals().update()` + 整块
  `_rebuild_ui()`；`THEMES["light"]` **导入时快照** → **导出长图永远浅色**；重建前 `root.unbind()` 防叠绑。
  **预览卡片**字体固定 16 号微软雅黑，**不跟编辑区联动**；**文稿列表不放滚动条**；**点卡片按 `_card_bounds` 查表**。
- **有序列表回车自动续号**：沿用缩进与数字 +1；**空项回车结束列表**；**光标不在行尾或有选区时不管**。程序化
  `insert` 后要手动 `see("insert")`。滚轮三处一起调：编辑区（`delta/3` × `EDITOR_WHEEL_FACTOR` = **6**，**必须返回
  `"break"`**）；卡片与树走 `LIST_WHEEL_UNITS` = **3**。**Canvas 只在「滚动区比视口高」时才夹住原点**：判据用
  `canvasy(0) < 0`（`yview()` 这时说谎），夹回用 `yview_moveto(0.0)`。**Treeview 与 Text 自己夹得住。**

## 其余硬约束（细节见 `DETAIL.md` 同名章节）

- **共用函数**：`_load_editor_text()` 的 `configure(normal) → delete → insert → edit_reset → edit_modified(False)`
  **顺序不能换**；`_place_dialog()` 内部先 `update_idletasks()`；`_build_ui()` = `_build_window → _build_layout →
  _apply_typography → _bind_shortcuts`，**换主题那条路调它之前必须先 `root.unbind()`**；`scan_fences()` 整篇与增量
  共用（**`build_export_rows` 与卡片摘录里的围栏循环故意没合**）。
- **文件名跟随一级标题**：`_follow_title()` 只在 `save_now()` 里调（落盘**之后**改名）。**判据只有一条：第一行是一级
  标题、且清出来的名字与现在的文件名主干不同就改**。**别再改回「只在你改了标题时才改」**（用户报过两次）。撞名走
  `unique_path`（**不覆盖**）；禁字符换**全角同形字**；截断 `FILENAME_MAX_CHARS = 80`。失败记忆
  `_rename_failed_for` 的 **key 必须带 stem**。`move_document_to` / `delete_document` 改名后必须
  `if was_current: path = self.current_path`。**`tools/_sync_manual.py` 不许写死说明文件名**（走 `resolve_stem()`）。
- **导出音频**：`app/speech.py`（Windows SAPI / macOS `say`，纯 ctypes）；念的文本走 `speakable_text()`，**复用
  `build_export_rows`**（屏幕/长图/耳朵同一套解析）。合成慢 → **后台线程 + 主线程 `root.after` 轮询**
  （`_speech_job`/`_speech_poll` 要登记进 `_cancel_after_jobs`）。**语速只走 `voice.Rate`（macOS 走 `say -r`），
  绝不放 `<prosody rate>` 进 SSML**（实测慢 3 倍，两处各设一次还会叠乘）。**让它不机械的杠杆只有节奏**：段间 450ms /
  段内换行 260ms / 句末 160ms，**逗号不加**，`NATURAL_RATE = -1`。`_gap(0)` / `_say_silence(0)` **返回空串**。
- **长图导出**：`image_export.py`（宽 1080）+ `build_export_rows`/`export_palette`/`_export_pieces`；**缩进续行必须
  并进上一段**（`absorber`，不并会把列表项从中间截断）；**分张上限是软约束**（**有意留的余量，别改成按 `span`
  卡**）；**`DEFAULT_BODY_SIZE = 46` = 一行正好 20 个汉字，别再往上调一档**（47 → 退成 19 个字）。折行/禁则坑在
  技能 `long-image-export` 里。**行内强调**：`_inline_spans` 与 `_tag_inline` **必须一字不差**；判「能不能用」**只看
  那两对记号**；循环**用 `search` + 手动推进，不用 `finditer`**（会把反引号里当例子的 `**` 当真记号）；被挡下时
  `search_from = start + 1`。**行内记号必须开闭在同一源行**——跨行则屏幕/长图/音频三处一致地原样显示，**别去改
  `_append_continuation`**。`INLINE_CODE_RE` **只认单反引号**。
- **列表缩进 Tab/Shift+Tab**：`LIST_MAX_LEVEL = 4`、`LIST_INDENT_STEP = 2`（**必须和 `_indent_level` 的
  `空格数 // 2` 一致**）。`_on_editor_tab` 按**「范围内有没有列表行」**分派，**不能按「有没有移动」**；Tk 的
  `<Tab>` 默认插 `\t`，必须拦下来返回 `"break"`。**增量重解析**先走 `_apply_markdown_styles_incremental()`，
  `False` 才退回整篇；**两条路径共用 `_style_line`**，顺序不能反：`_drop_line_marks`（按**旧**行号）→
  `_shift_line_maps`（按 delta）→ 抹标签 → 重新上色。**点选卡顿**的元凶是 `_trim_card_text` → 探测长度**从 32 起
  成倍增长再在末段二分** + 缓存；**卡片阴影要有自己的 tag**。
- **`import main` 必须排在第一次 `Tk()` 之前**（`main` 导入时声明进程 DPI 感知，Tk 在第一次 `Tk()` 时定
  `tk scaling`）。顺序反了 `tk scaling` = 1.332 而不是 1.998，**所有量几何的用例一起红**，单独跑那些模块却全绿。
  兜底在 `tools/run_tests.py` 的 `run_in_process()`，**别删**。
- **隐藏桌面**：`event_generate("<Return>")` 不投递按键，只能断言绑定脚本挂了处理函数 + 返回 `"break"`；
  **截选区前必须 `root.focus_force()` + `editor.focus_force()`**（否则没有蓝色，会误判成「问题不存在」）；
  **千万别在 `sleep`/轮询里等模态框**（没人点「确定」，会挂到被杀且**日志一个字都没有**）。**截原生菜单**：Windows
  上 `menu.post()`/`tk_popup()` **本身不返回**（模态循环）→ **先起后台线程排好抓屏再进 `post()`**，抓完
  `os._exit(0)`；一个进程只能摆一个菜单；**先等 `winfo_viewable()`**。见 `tools/_shot_doc_menu.py`。
- **量几何**：`count(...,"ypixels")` 与 `yview()` **都不是稳定值**；用 `dlineinfo`/`bbox`；量位移用**行 y 位移的中
  位数**；`yview_moveto(1.0)` 前必须 `update_idletasks()`。**`bbox` 给的是「那一段 chunk 自己的盒子」**；
  **`dlineinfo` 的盒子包含 `spacing1/3`**，**标签自己的 `background` 也照样盖住 spacing**。**Tk `Text` 用 `\n`
  分行，行尾那个 `\r` 是行内容**——切行只能用 `split_document_lines()`（`re.split` 遇单独 `\r` 会多切一行，
  `str.splitlines()` 会在 `\x0b`/U+2028 断行，都会让行号错位）。**`end-1c` 是最后一个真字符**，末尾那个 `\n` 是
  Tk 幻影换行；**想造「末尾没有换行」的状态就直接 `insert` 原始正文**。
- **`os.utime` 改不了创建时间**；「按创建时间排序」的测试**不能写死期望顺序**（ctime 相等时 `sorted` 保持原顺序 →
  随机变红），要跟 `st_ctime`（macOS 上 `st_birthtime`）真实值对。**输入法组字字号归 Windows 管**（`imm32` 的
  `ImmSetCompositionFontW`；macOS 上 Tk 自己管，跳过）。**「两块颜色分不分得开」不能比对比度**（对比度只看明度），
  要逐通道比 `max(|Δr|,|Δg|,|Δb|) >= 12`；「文字压在底色上够不够清楚」才用 WCAG 对比度（正文 ≥ 4.5:1）。**数「有
  没有叠绑」不能数 `info commands`**（`unbind` 不删那个 Tcl 命令），要读 `bind()` 的脚本数函数名出现几次。
- **`shutil.which("ffmpeg")` 找不到 ≠ 没装**：很多人把**解压目录本身**（`ffmpeg-xxx/`）加进 PATH，
  `app/ffmpeg.py::_locate()` 会在 PATH 每一层**再往下探一层 `bin\`**。**起外部进程要
  `creationflags=CREATE_NO_WINDOW`**（0x08000000），否则闪黑框。转 MP3 是**可选一步**；临时 WAV 放 `mkdtemp()`、
  `finally` 里 `rmtree`，**别放用户选的目录**。**本机 SAPI5 只有 `TTS_MS_ZH-CN_HUIHUI_11.0` 一个中文音色**；更自然的
  OneCore 音色注册在 `Speech_OneCore\Voices`，**SAPI5 看不见**，要管理员权限搬 token。**审计后仍故意留着的
  「伪冗余」**：`_card_index_at(self, x, y)` 的 `x`、Tk 回调的 `_event` / `_args`。
