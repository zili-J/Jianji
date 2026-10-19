# 简记 · 详细资料（DETAIL.md）

> `MEMORY.md` 是精简版，只放约定与落点；**这里是它的展开版**：实测数据、踩坑经过、
> 被否决的写法、为什么这么定。两边的章节名一致，按名字对照即可。
> 逐日进展仍在 `memory/YYYY-MM-DD.md`。

---

> 只留「不照做就会犯错」的约定与落点。细节见 `memory/YYYY-MM-DD.md`、`.3things/build-log.md`。
> 技能：`tk-text-incremental-highlight`（Tk 硬约束全清单；控件内部的 `dlineinfo`/`bbox`/elide/`wrap` 坑
> 统一在它的 `references/tk-text-traps.md`）、`long-image-export`、`windows-gui-quiet-capture`、
> `windows-batch-and-git-remote`。
> **`quiet-desktop-gui-run` 与 `windows-gui-screenshot` 已合并进 `windows-gui-quiet-capture`**（2026-09-29），
> 那两个名字不再存在，别再去找。

## 平台移植（2026-09-29 起：Windows → macOS，并出 dmg）

- **dmg 只能在 macOS 上产出**（`hdiutil`/`codesign`/`py2app` 都是 mac 专属）。Windows 侧只能写
  `build_macos/` 脚本 + GitHub Actions workflow，**产物本身验证不了**。
- **平台差异的唯一落点是 `app/platform_support.py`**（只 import 标准库）。`app/` 下其余模块**不许**出现裸
  `sys.platform` / `os.name` / `ctypes.windll`；例外只有两个后端 `image_backend_gdi.py`（Windows GDI）与
  `image_backend_cg.py`（CoreGraphics），以及 `speech.py` 里的 SAPI / `say` 两套实现。
- 对应关系：读创建时间 `st_ctime`→`st_birthtime`；写 `SetFileTime`→`setattrlist`(`ATTR_CMN_CRTIME`)；
  永久删除 `SHFileOperationW`→移进 `~/.Trash`；状态目录 `%LOCALAPPDATA%\JianJi`→
  `~/Library/Application Support/JianJi`；定位文件 `explorer /select,`→`open -R`；
  `CREATE_NO_WINDOW`→**0**（macOS 上给 `subprocess` 传 `creationflags` 直接 `ValueError`）。
- `setattrlist`：`struct attrlist` **24 字节**、`bitmapcount = 5`，传 `struct timespec`（两个 8 字节 long）
  和它的 `sizeof`；**失败只返回非零，不抛 errno**。
- **CG 坐标/位图四坑**（都写在 `image_backend_cg.py` 里）：① 原点在左下 → `Translate(0,H)` + `Scale(1,-1)`
  翻 CTM，上游布局代码保持左上原点；② 翻完必须 `CGContextSetTextMatrix(1,0,0,-1,0,0)`，否则**字形上下颠倒
  且不报错**；③ 用 `kCGImageAlphaNoneSkipFirst|kCGBitmapByteOrder32Little`，内存里是 **B,G,R,X，与 GDI
  32 位 DIB 逐字节相同** → 一套 PNG 编码器通吃；④ `kCTFontAttributeName` 是**全局指针变量**，要用
  `ctypes.c_void_p.in_dll(ct, ...).value` 读，不是函数。
- `CGRectMake` 是 `static inline` 不导出 → 自己搭 `CGRect`/`CGPoint`/`CGSize` 结构体；
  `CGAffineTransform` 是 6 个 double **按值传**。
- `CTFontCreateCopyWithSymbolicTraits` 要求**每一位都能满足**：同时要 `Bold|Italic` 而字体没有斜体时返回
  NULL，**粗体也一起丢** → **一次只加一个 trait**。
- **量文字必须走 CTLine**，不能用字体步进：CoreText 会给缺字做 fallback（中文里的 `→`、emoji），
  步进量出来是 0 或错的，会算错折行。
- **两个后端在 Surface/Fonts 上都是「只画不读」**：`fill/text/ellipse/rect/polyline/to_png/close`、
  `handle/metrics/width/char_widths/close`。接口逐签名一致，`tests/test_platform.py::BackendParityTests` 钉住。
- `say` vs SAPI：静音 `[[slnc N]]` vs `<break time='Nms'/>`；语速 `-r` 是**绝对值** WPM
  （`SAY_BASE_WPM=175`、每档 `+20`、下限 `SAY_MIN_WPM=90`）vs `voice.Rate` 的相对 -10..10；
  音色 `-v`（**必须挑 `zh_CN`**，`say` 默认多半是英文，念中文会成怪音节）vs 系统默认；
  出 WAV 用 `-o f --file-format=WAVE --data-format=LEI16@22050`。**`say` 没有音量参数 → macOS 上 `volume` 被忽略**。
- **`/usr/bin/say` 必须写绝对路径**：访达启动的 `.app` 只有 `/usr/bin:/bin:/usr/sbin:/sbin`。
  **同一个坑也适用于 ffmpeg**——Homebrew 的 `/opt/homebrew/bin` 同样看不见，`ffmpeg._COMMON` 是唯一兜底。
- **正文要用 `-f 文件` 传给 `say`**，不能塞 argv：日记上千字会撞长度上限，且以 `-` 开头的正文会被当选项。
- **`speech.py` 的 macOS 段落必须排在 `NATURAL_RATE`/`PARAGRAPH_BREAK_MS`/`SENTENCE_BREAK_MS`/`LINE_BREAK_MS`/
  `_SENTENCE_END_RE` 之后**：`build_say_script` 的默认参数在 `def` 执行时求值，放前面直接 `NameError`，
  整个模块 import 失败（踩过一次）。
- **`speech.py` 在非 Windows 上靠 `_UnavailableDll` 顶替 `ctypes.WinDLL`**，让模块级的 argtypes 声明照跑；
  `winreg` 也要条件导入。`is_available()` 里原来那句 `os.name != "nt"` 早退已删（替身自己会抛）。
- **判据错的时候，被检查的东西往往没错**：`code_only()`（`ast.parse` 去掉 docstring/注释再 `unparse`）
  才能做「源码里不许出现 windll」这类断言——直接扫原文会被**解释 `st_ctime` 的注释**判红。
  同类：`py_compile(cfile=os.devnull)` 在 Windows 上必失败（`nul` 不是普通文件）；
  `python -c` 里的 `(.+)$` 被 bash 吃掉 `$` → 十条样本全报「没有名字」。
  **一条判据若对全部样本都失败，先怀疑判据，别怀疑样本。**
  同类假阳性（2026-10-09）：`test_the_macos_fonts_do_not_pretend_to_be_windows_fonts` 拿
  `"kaiti" in macos_names` 去抓 Windows 的 `KaiTi`，结果把苹果自带的 `Kaiti SC` / `STKaiti`
  **（合法且该留）** 一起抓出来。改成**整条别名**比对（`alias.lower() in {...}`）才对。
  另一类：`FILE_MANAGER_LABEL` 是**导入时**算好的常量，`_PlatformPatch` 只换 `WINDOWS/MACOS/LINUX`
  标志、换不动它 → 「切到 macOS 断言『在访达中显示』」这种测试**永远是假的**，只能断言
  `_reveal_label(v) == f"在{platform_support.FILE_MANAGER_LABEL}中{v}"`（真正守的是「别把中文写死」）。

### `app/main.py` 跨平台化（2026-10-09）

- `FAMILY` = 微软雅黑 UI / 苹方；`MONO_FAMILY` = Consolas / Menlo。
- 字体表拆三份：`_WINDOWS_FONTS`(11) / `_MACOS_FONTS`(7) / `_COMMON_FONTS`(3)，
  `FONT_CANDIDATES = 本平台表 + _COMMON_FONTS`；`DEFAULT_FONT_ALIASES` 也分平台。
  **macOS 表里不能出现 `SimSun`/`KaiTi`/`SimHei`/`Microsoft YaHei`/`Consolas`**（那些名字在
  macOS 上一个都命中不了，会静默退到 Tk 默认字体，界面能开但字全变）。两份表**共用中文本地化名**
  （宋体/楷体两边都叫这个）是允许的，**拉丁名不许重叠**（`STKaiti`/`STSong`/`STZhongsong`/`STHeiti`
  这几支「华文」系列除外，两个平台都自带）。
- **快捷键主键统一走 `_shortcut_modifiers()`**：Windows `("Control",)`，macOS `("Command","Control")`。
  绑定（`_bind_shortcuts`）与解绑（`_rebuild_ui`）**必须共用它**——两边各抄一份的话，换一次主题就叠绑，
  表现是「⌘S 存两次」，而且极难看出来（绑了两层看不出差别）。`code_only(main)` 里**不许出现 `Control-`**。
- 菜单文案走 `_reveal_label(verb)` → 「在资源管理器中显示」/「在访达中显示」。
- `__version__ = "1.0.0"` 放模块顶部，`jianji.spec` 和 `build_dmg.sh` 都用正则读它。
- **macOS 不需要 DPI 声明**（Tk 按点排版、系统负责 Retina）→ `enable_dpi_awareness()` 返回 1.0；
  **也不需要输入法字体强制** → `_apply_ime_font()` 早退。两者都保留函数与 docstring 说明差异，
  不假装做过。`_resolve_scale()` 自己不再判 `sys.platform`。
- `install_shortcut.py`（建 `.lnk` 的）**非 Windows 直接 `raise ImportError`**。

### 打包链路（2026-10-09）

- `build_macos/jianji.spec`（PyInstaller）：`BUNDLE(name="JianJi.app")` —— **bundle 名保持 ASCII**，
  因为 `.app` 内部结构对非 ASCII 名敏感，改名交给 `build_dmg.sh` 复制成「简记.app」。
  `info_plist` 里 `CFBundleName=简记`、`NSHighResolutionCapable=True`、`LSMinimumSystemVersion=11.0`。
- `build_macos/build_dmg.sh`：`set -euo pipefail`；`--app-only`；`find_python()` **要求能 `import tkinter`**；
  建 `.build-venv` 装 `pyinstaller>=6.0,<7.0`；`codesign --force --deep --sign "${JIANJI_SIGN_ID:--}"`
  （默认 ad-hoc `-`）；优先 `create-dmg`，没有就退 `hdiutil`。
- `build_macos/make_icns.sh`：`iconutil`/`sips`，源图必须 ≥1024px。
- `run_jianji.command`（工程根）：访达双击入口，找 Python 3、查 tkinter、`exec "$PYTHON" app/main.py`。
- `.github/workflows/build-macos.yml`：`workflow_dispatch` + `v*` tag；matrix `macos-14`(arm64) /
  `macos-13`(x86_64)，`fail-fast: false`；**冒烟测试**＝启动产物、`sleep 8`、进程还活着才算过。
- `.gitattributes` 加 `*.sh`/`*.command` = **LF**（CRLF 报 `bad interpreter: ...bash^M`），
  `*.icns`/`*.dmg` = binary。`.gitignore` 加 `.build-venv/`、`build/`、`dist/`、`*.dmg`。
- `build_macos/README.md`：三条装法 + Gatekeeper 首次打开的三种绕过 + Windows→macOS 差异表。

### 说明书里的 macOS 一节（2026-10-09）

`简记使用说明.md` 加了「## 十、在 macOS 上使用」（三种装法、Gatekeeper 绕过、无 Mac 也能出 dmg、
两个平台差异、导出长图）。**加完之后长图从 2 张变成 3 张**，所以第七节里那句自指的
「本说明就分成了两张」要同步改成「三张」——**这类自指句子改完必须重新导出核对张数**。

## 备份 / 远端

- 远端 `origin` = `https://github.com/zili-J/Jianji.git`。**备份入口是双击 `push_backup.cmd`**
  （工程根，纯 ASCII + `chcp 65001`，先打印远端/分支/待推提交再 `git push -u origin <当前分支>`）。
- **沙箱里推不上去**：环境全局设了 `GIT_TERMINAL_PROMPT=0`；显式打开后 `git credential-manager get`
  会**超时挂住**（GCM 的登录窗弹不出来）；有时整个进程树被 **SIGTERM 硬杀**（连 `echo` 都不执行）——
  那是沙箱级 kill，别当 git 错误查。**认证必须在用户自己的窗口里点一次**，之后助手才能直接推。
- **`cmdkey /list` 查不到 github 条目 ≠ 没有凭据**：要跑
  `printf "protocol=https\nhost=github.com\n\n" | git credential-manager get` 才算数。
- **`.cmd`/`.bat` 必须 CRLF**（`.gitattributes` 里 `*.cmd text eol=crlf`，**排在 `*` 之后**，否则
  `* text=auto eol=lf` 会把入口一起转成 LF，cmd.exe 的 `goto :label` 会失效）。
  **改完属性要手动把工作区文件转回 CRLF**——git 只在检出时改写。判据
  `git ls-files --eol <file>` = `i/lf  w/crlf  attr/text eol=crlf`。
- **Git Bash 会把 `ref:path` 参数改坏**：`git cat-file -e "origin/main:.gitattributes"` 报
  `Not a valid object name origin\main;.gitattributes`（`:`→`;`、`/`→`\`，MSYS 路径转换），
  **时灵时不灵**，能骗出「文件缺失」的假结论。改用 `git ls-tree origin/main <path>` 或加
  `MSYS_NO_PATHCONV=1`。**报缺失前先用另一种写法复核**。

## 运行 / 测试 / 交付物

- 运行 `run_jianji.cmd` / `python app/main.py`（系统 Python 3.12，无第三方依赖）。
- **`python` 走 PATH 是托管 3.13，没有 tkinter**。跑测试/同步一律用**系统 3.12 全路径**：
  `C:\Users\22910\AppData\Local\Programs\Python\Python312\python.exe`。
- 测试 `python tools/run_tests.py`（**默认隐藏桌面**；`--foreground` 才回当前桌面，**截原生菜单必须用它**；
  `-k` 按文件名子串过滤）。输出落 `outputs/test-output.txt`，屏幕只摘 `FAIL:`/`ERROR:`/`Ran`/`OK`；
  **0 用例 = 退出码 3**。裸 `unittest discover` **必须带 `-t tests`** 且**先重定向到文件**。
- 报告**边跑边写**（`_FlushingStream`）——卡死时看 `outputs/test-output.txt` 末尾那行就知道卡在哪个用例，
  **别干等**。**不要同时跑两套 GUI 用例**（隐藏桌面上抢资源/留模态状态，实测挂过一次 51 分钟）。
- 给用户看的文档写进**文档文件夹**（`state.json` 的 `default_folder`，本机 `C:\Users\22910\Documents\wb01`），
  **不写工程根目录**（根目录 `.md` 会让文件夹树混进 `app`/`tools`/`tests`）。
- **使用说明每次更新都同步到 `C:\Users\22910\Documents\wb01\说明书\` 并重出长图**（用户固定要求）：
  `python tools/quiet_desktop.py <python 全路径> tools/_sync_manual.py`。
  **要改说明书就改 `<工程根>/简记使用说明.md`**——`说明书\` 里那份是**产物**，会被原样覆盖
  （踩过一次：改了产物，白改）。同步脚本会先打印谁是源、谁是产物，并在「副本比源新、内容又不同」时警告。
- **`简记使用说明.md` 是测试基准文件**：`tests/test_export.py::EditorParityTests` 拿它整篇做
  「编辑器 vs 导出」逐行对账，并断言它**覆盖全部 10 种块级语法**
  （`heading/text/blank/bullet/ordered/task/quote/code/hr/table`）。重写必须保留这 10 种。
  `hr` 靠章节之间那些 `---`，**前面必须有空行**（紧贴正文会变成 setext 标题）。
- **写 `.md` 给这个应用渲染时，一段话必须写成一行。** 编辑器和导出都**按源行分块**（只有**行首缩进**的
  续行会被并进上一段），手工断行会让每行各自成块——长图里就出现「写成 `2. `」这种只剩半行的残句。
  **引用同理**：连续两行 `> xxx` 会裂成两个引用块，除非第一行以**行尾两个空格**结尾。
- **表格里别放超长单元格**：`_layout_table` 在 `total > content_width` 时**等比压缩**，下限是**写死的**
  `floor = body_size * 2.2`，不是「本列最长不可断词宽度」——长格会把整表压窄，逼得另一列的长 token
  从中间断开（实测 `Ctrl+Shift+F` → `Ctrl+Shi`/`ft+F`）。绕开办法是把那格移出表格、改成表下一行说明。
  **别去改 `_measure`/`char_widths`**：实测整串宽度与逐字之和差 0，不是它们的问题。
- **原子写盘会把创建时间冲掉**：`atomic_write_markdown` 是「临时文件 + `os.replace`」，Windows 上那是
  **用源文件顶掉目标文件**，目标连创建时间都变成保存时刻。修之前用户 12 篇文稿的 `st_ctime` **全部等于**
  `st_mtime`。现在每次替换后用 `storage.set_creation_time` 把旧创建时间写回（best-effort）。
  **别再把这步删掉**，删了「创建日期」会静默退化成假的。
- **文件名里的时间戳分两种，别认错**：`新建-YYYY-MM-DD-HHMMSS`（简记自己打的，**就是创建时刻**）和
  `YYYY-MM-DD HHMMSS`（`文档\日记` 的老约定）可信；`YYYYMMDD-HHMMSS ` **前缀**是**备份/导入时刻**，
  **不能用**（实测 `20260925-112829 2026-09-09.md` 的文件时间是 `2026-09-10`，创建不可能晚于修改 15 天）；
  **只有日期没有时刻的也别认**。回填工具 `tools/backfill_created.py`（默认预演，`--apply` 才写，`--restore` 可还原）。
- **扫用户文稿夹必须用 `storage.list_markdown(folder, recursive=True)`，别用裸 `folder.rglob("*.md")`**：
  前者走 `_is_hidden_relative()`（任一路径段以 `.` 开头就排除），**跳过 `.简记回收站`**，与界面列表一致。
  踩过一次：`wb01` 报「1 篇该改」，那篇其实在 `.简记回收站\` 下——字面路径 `exists: False`、`rglob` 却找得到、
  逐字符比还「完全相同」（**只有目录那一段不一样**）。**碰到这种矛盾先去查目录，别怀疑编码。**
  用户批准「改 N 篇」时若前提是错的，**退回原状再报告**。
- **`tools/` 只留 20 个**（5 个正式工具 + 15 个被点名的核验入口，清单在 `.3things/project.md` 的「工具」一节）。
  一次性探针**用完即弃**；旧的 62 个归档在 `outputs/tools-探针归档-2026-09-26.zip`。删探针的判据是
  「**活文件**（`app/` `tests/` `tools/` 技能 `MEMORY.md` 说明书）有没有引用」——**历史日志里提到过不算理由**；
  `_bench_reparse.py` 依赖 `_bench_perf.build_document`，两个得一起留。
- **有 git 仓库**（2026-09-26 建，分支 `main`，首个提交 `8f63f1e`）——回退直接 `git checkout -- <文件>` /
  `git revert`。仓库级 `core.autocrlf=false`（行尾原样存）、`core.quotepath=false`（中文路径不转义）；
  身份是占位值 `JianJi <jianji@localhost>`（**仅本仓库**）。已忽略 `outputs/`（~12MB，可重生成）、
  `.idea/`、`__pycache__/`。
- **行尾实测**（建仓时逐文件量的，**别按印象写脚本**）：绝大多数是 **LF**；CRLF 的只有
  `app/image_export.py`、`tools/_probe_editor_wrap.py` 和 `.workbuddy-ai/memory/` 下的
  `MEMORY.md`/`09-22`/`09-23`/`09-25`/`09-26`，其中 `2026-09-22.md` 是**单文件内混用**。
  「日志都是 CRLF」是错的。`core.autocrlf=false` 保证 git 不做任何转换。

## 核验必须在**用户真实设置**下做（踩得很惨）

- 设置现读 `%LOCALAPPDATA%\JianJi\state.json`（本机 `line_width 50 / font_size 18 / line_height 34 /
  霞鹜文楷 / sort_key modified / theme light`；`DEFAULT_SETTINGS` 是 `50/15/22/name`）。
  **这几个值用户会自己调**（60→50、微软雅黑→霞鹜文楷都发生过），**别照抄这一行，现读文件**。
  **探针要把用户 `settings` 原样写进临时 state.json 再构造 `JianJiApp`**（设置只在启动时读一次）；
  **测试要覆盖非默认设置**（`TallLineHeightWrappedLineGeometryTests` 的 `SETTINGS` 钩子）。
- **判据里不要出现绝对像素**，一律相对设置项（`px(line_height)`）写。
- 用户的 `state.json` 与文档文件夹都别写（核验用临时 state.json；`set_folder()` 只在文件夹里没 `.md` 时才建文稿）。
- 探针三坑：`mark_set` 后必须显式跑 `_refresh_cursor_line()`；「光标在别处」停在**紧邻目标行的普通正文行**
  （扔到末尾会把目标滚出视口）；`open_file(同一路径)` **直接短路**，换用例要换文件名。

## 界面约定

- 各级标题**字号与正文一致**，靠加粗、颜色、上下留白、左侧 `H1`–`H6` 徽标分层。
- **光标所在行显示源码**（灰）；**唯一例外是有序列表序号**——永远由画布绘制（蓝、与正文同号），记号永远
  elide，**不记进 `_line_marks`**。别扩大这个例外（项目符号/复选框仍露源码）。
- **编辑区控件必须是 `wrap="char"`，不能改回 `word`**（用户第 11 轮）：Tk 的 word 折行把「空格分隔的一串
  字符」当成**不可拆的词**。中文整行没空格时那个词就是整行，放不下只能按字符断 → 反而填得满；但只要行里
  有**一个空格**（自己敲的，或中英混排带出来的），空格后面那串中文就成了一个词 → **整串挪到下一显示行**，
  本行右边空掉一大块。实测（可用宽 571px）：「短 后面这一整串中文…」第一显示行只填 **4.7%**，引用/列表项
  只填 **41.9%**，整段还多折一行；改 char 后分别 96.0% / 100.0%，显示行还少一行。**这同时解释了用户说的
  「显示比例不对」**——行只填到 4.7% 时看起来就像正文块比设置窄得多（正文块本身实测 = 编辑区控件宽的
  60.2%，和设置一致，逻辑没坏）。代价：英文单词跨行会被从中间断开（中文优先可接受；导出长图本来就按宽
  字符断行，两边这才对得上）。核验 `tools/_probe_editor_wrap.py`（0 处 vs 改回 word 的 4 处）+ `SpaceDelimitedWrapTests`。
  **改 char 后有三处机制一起消失/退化，别当 bug 去「修」**：① 空显示行盒子整个消失（word 下
  `dlineinfo("2.0")` 是 `(189,51,0,16,8)`，char 下是 `(189,51,570,51,36)`），`is_real_display_box` 从此只是保险；
  ② 「行首记号露源码要补宽度」那条变冗余（仍留着当回退哨兵）；③ `_inline_reveal_fits` 退化成哨兵（判据与落点不动）。
  `bbox` 给 chunk 盒子那条坑**仍然成立**，别混。
- **「内容行宽」的比例基准是显示器宽，不是窗口也不是控件**（用户第 12 轮）：
  `T = min(r·S, C − 2×5%·S)`（S=显示器宽、C=编辑区宽、r=行宽设置），`pad = (控件宽 − T) // 2`。
  窗口下限 = `NAV_W + CARDS_W + 40%·S`（`_apply_min_window_width`；值没变必须提前返回，否则 `minsize`
  触发 `<Configure>` 会来回弹；`_build_window` 的初始宽度也不能小于它）。
  **副作用**：三栏都开时 C 通常只有 40%·S，默认窗口就落在第二档 `T = C − 10%·S`——那一档里 `line_width`
  **不起作用**。这是规则本身，测试 `test_the_setting_is_inert_...` 专门钉住，别「修掉」。测试里要用
  `tests/_pinning.py` 的 `pin_screen_share(app, share)` 钉住屏幕宽，否则「调行宽 → 留白变」那几条断言
  根本观测不到。**对称 `padx` 只能把正文块居中到 ±1px**（两侧留白合计必须是偶数），判据写 `abs(差) <= 1`。
- **行首记号露源码必须补偿**，落点 `syntax_marker` 标签：`lmargin1 = 行缩进 − 记号宽度`、`lmargin2 = 行缩进`、
  **`wrap="char"`**，三件一起做。标签**必须建在 `li{n}` 循环之后**，每次 `tag_add` 前用 `_marker_options(indent)`
  重配。已接受的残留：点击后下方整体上移 `px(行高) − 字体行距`（用户设置 16px）；**`spacing1` 这个标签选项
  实测不生效**，别再试。`_line_marks` 是 **5 元组** `(off_tag, on_tag, start, end, indent)`，`indent` 为
  `None` 或 `(lmargin1, lmargin2)`。
- **行内记号（`**`、`` ` ``）露出来会加宽整行** → 当年控件是 `word` 时把一个词整个挤到下一显示行 =
  「选中该行就换行」。修法 `_inline_reveal_fits(line, text)`：**整行带记号量出来仍能放进一个显示行**才露。
  落点三处：`_swap_cursor_marks` 的 `reveal` 门（跳过 `off_tag=="syntax" and indent is None`）、`_style_line`
  在 `_tag_inline` 后调 `_hide_inline_marks`、标题改走 `_tag_marker`（负 `lmargin1` 把 `# ` 挂进左留白）。
  **标题原来走 `_tag_syntax`** → 条目 `indent is None` 会被行内门一起压掉，所以测试里「`syntax_marker`
  标签存在」才抓得住回退。
- **装饰（序号/徽标/竖条）画布**统一走 `_display_line_box()`：① 判据**只看宽度**（`box[2] > 0`）——空盒子的高
  = 行高 − 字体行距，随行高设置变，「高度 < 6px」这种绝对阈值会失效；② 量到好盒子**直接用**，别与空盒子合并；
  ③ 空行/只有记号的行也是宽 0 但盒子对 → 接受（`box[2] > 0 or box[3] >= px(行高)`）。重新量用
  **`index + 1 display chars`**。定高 `max(box[3], px(行高))`（视口下沿那一行的 `box[3]` 会被 Tk 裁一刀）。
- **不透明画布会盖住光标**；**一行全被 elide 时 Tk 把光标退回行首（忽略 `lmargin`）**——空的有序项回车续号后
  光标落在序号画布下。修法在 `_style_line` 有序分支：`body_empty and line_number == cursor_line` 时把**行尾
  空格留在 elide 之外**（`marker_end -= 1`）。**别再改成「画布整体左移」**（被撤掉的写法）。
- **顶部栏（编辑状态 + 专注模式）常显**（`grid_propagate(False)`）。「自动收起」被撤回（连编辑状态一起没了）。
  **中间那个字数是悬停才显示的**（`count_label` 在 `column=1`，初始 `grid_remove()`——**别用 `grid_forget()`**，
  前者留着 grid 参数，`grid()` 一下就能回原位）。判据**不能看 `<Enter>/<Leave>` 事件**：指针从 topbar 挪到
  它自己的子控件上时 topbar 也会收到 `<Leave>`，当场隐藏会闪。正确做法是 `_on_topbar_leave` 排一个
  `after(COUNT_HIDE_DELAY_MS)`，到点用 `_pointer_is_on_topbar()`（`winfo_containing` + 往上找 `master`）
  算真实位置。topbar 和它的**三个子控件都要绑**。`_counts_job` 要进 `_cancel_after_jobs`，`_rebuild_ui`
  里也要显式取消。两个数：`document_counts()`（模块级纯函数）→ 字数 = 中日韩逐字 + 西文按词（标点/记号不计），
  字符数 = 除换行外全部字符。
- **粗体/斜体走暖色**（`BOLD_COLOR #B3261E`、`ITALIC_COLOR #A87514`）。改色要**三处一起**：编辑器标签、
  `export_palette()`、`image_export.Palette` 默认值。测试按**色相角差 + 相对亮度差**判。
- **深色主题**：`apply_theme(name)` 把颜色常量 `globals().update()` 再 `_rebuild_ui()` 整块重建；
  `THEMES["light"]` **导入时快照** → **导出长图永远浅色**。重建前 `root.unbind()` 防叠绑；
  新颜色要进 `THEME_KEYS` 和 `_DARK_COLORS`。
- **预览卡片**：字体固定 16 号微软雅黑（`CARD_FONT_*`，走 `px()`），**不跟编辑区联动**；间距唯一入口
  `_card_gap()`；摘录**去换行、用空格连成一行**；**第一行是一级标题也照常显示**；
  `CARD_EXCERPT_ROWS = 4` 按**显示行**算。
- **文稿列表不放滚动条**：`cards_holder` 只放 `cards_canvas`，滚轮是唯一滚动方式。**编辑区的滚动条不动**。
- **点卡片按 `_card_bounds` 查表**，不撞图元；命中前用 `canvasy()` 换算到**画布**坐标；上下各撑开半个间隙。
  **滚到当前卡片只在它露不全时才动**，按 `bbox(f"cardbg{index}")` 真实几何算（别用 `index/total`）。
- **「导出长图」在文稿右键菜单**；成功后自动打开文件夹并选中新图。行号与徽标只画在**记录行的第一显示行**上。
- **顶部留白**：编辑区 `Text` 的 `pady` 是 **0**，正文上方节奏由标题 `spacing` 负责。
- **底部留白 `EDITOR_BOTTOM_PAD_RATIO = 0.25`**：给**最后一行**挂 `spacing3`（显示层，不进正文/存盘/导出）。
  **生效条件：标签必须「包含最后一行的行首」**（`start <= "end-1c linestart" < stop`；空末行的范围是
  `end-1c`→`end`）。**在空末行敲第一个字会把标签起点顶到新字之后 → 留白静默消失而行号不变**，所以判据
  `_bottom_pad_covers_last_line()` 必须**量标签真实范围**（`tag_ranges` + `_index_pair`；字符串比大小是错的）。
  重挂后范围是 `行首..end`，后续字符落在范围内部 → **每个新空末行只重挂一次**，不影响打字性能。
  `open_file` 换整篇后必须 `force=True`；`_on_editor_modified` 里的调用要在 `current_path is None` 提前返回**之前**。
- **留白必须挂在「末尾那个空行」上**：Tk 画选中高亮（和标签 `background`）时把**整个显示行盒子连 `spacing3`
  一起涂色** —— 挂在**正文行**上就会出现「选中最后一行 → 一大块蓝盖住留白」。所以 `_load_editor_text` 调
  `_ensure_trailing_newline(text)` 给**不以换行结尾**的正文补一个（字符串级、拼进那一次 `insert`，别分成两次
  insert：会多发一次 `<<Modified>>` 白跑一遍重解析；补在 `edit_reset()` 前，不进撤销栈、不算脏）。
  **已实测：标签怎么摆都躲不开**——空区间 `end..end` 会被 Tk 丢掉（留白整段失效）、`end-1c..end` 只盖住换行符
  留白同样失效、标签 `background` 也盖住留白。**两条已否决的路，别再走**：① 留白挪出正文区
  （`grid_configure(pady=(0,pad))`）结构上免疫，但编辑区**常驻**少 25% 视口；② 在 `_apply_bottom_pad` 里
  「删了就补回来」——末尾空行会变成删不掉（Backspace 静默失效），还会让 20 个 `test_list_editing` 用例红。
  残留：用户删掉末尾换行后高亮又会盖住留白，重开文稿自愈。（补的时候若用 `insert("end","\n")`，
  **`insert` 标记会被推到新空行上**，得自己存下光标还原。）
- **文稿排序**：`sort_key`（`name`/`created`/`modified`），`list_markdown(..., sort_key=)` 按时间戳**倒序**；
  `_sort_key_of` 文件刚移走/删掉时退到 `0.0`。**`created` 必须走 `storage.creation_time()`，不能直接读
  `st_ctime`**——macOS/Linux 上那是**元数据变更时间**。入口在**文稿列表右键菜单**（`_add_sort_cascade`；
  点卡片下方空白也能弹）。**不要再放回标题栏**（grid 一列宽 = 该列最宽的行，卡片区会少一块）。
- **文件夹树是「多层嵌套」的**（2026-09-29 用户要求「多层文件夹预览」，**推翻了原来的「只有一级」**，
  别再按「一级」改回去）：顶层文件夹挂**树根**（与「全部文稿」并列），子文件夹挂父节点下；文件夹节点给
  「新建子文件夹…」，「全部文稿」给「新建文件夹…」。**`_tree_nodes` 不能当父节点查找表用**——它把
  `self.folder` 映射到 `"__all__"`（选中同步用），拿来查父节点会把顶层文件夹挂进「全部文稿」**里面**。
  父节点查找用局部 `folder_iids`。排序键 `(层级, 路径)`，保证父节点先插入。`_open_iids()` 在重建前记展开状态，
  **删了「收起」就白点**。「全部文稿」递归列全部；点某个文件夹**只显示它自己那一层**（用户明确选的，不是递归）。
  `_move_targets` 列全部层级、嵌套显示成相对路径。已取消「今天」入口与 `Ctrl+D`。
- **有序列表回车自动续号**（`_on_editor_return`）：沿用缩进与分隔符数字 +1；**空项回车结束列表**；
  **光标不在行尾或有选区时不管**。程序化 `insert` 后要手动 `see("insert")`。
- 尺寸一律 `px()`。滚轮三处一起调：编辑区控件级绑定（`delta/3` × `EDITOR_WHEEL_FACTOR` = **6**，
  **必须返回 `"break"`**）；卡片与树走 `LIST_WHEEL_UNITS` = **3**。量法 `tools/_check_wheel_speed.py`。
- **Canvas 只在「滚动区比视口高」时才夹住原点**：判据必须用 `canvasy(0) < 0`（`yview()` 这时说谎），
  夹回用 `yview_moveto(0.0)`（`_clamp_cards_scroll`）。**Treeview 与 Text 自己夹得住，不要给它们也加一层。**

## 改这几处要走共用函数（2026-09-26 审查后新添，别在调用点重抄）

- **整篇换正文** `_load_editor_text(text, cursor=None)`：`configure(normal) → delete → insert → edit_reset
  → edit_modified(False)` 的**顺序不能换**（`edit_reset` 要在 `insert` 后，`edit_modified(False)` 要最后）。
  只读场景由调用方之后自己 `configure(state="disabled")`。三处调用：打开文档、打开回收站条目、换主题重建。
- **对话框居中** `_place_dialog(window, width, height)`：内部先 `root.update_idletasks()` 再算位置
  （不先跑一遍 `winfo_width()` 是旧值）。设置窗口与字体选择器共用。
- **构建界面** `_build_ui()`：`_build_window → _build_layout → _apply_typography → _bind_shortcuts`。
  `__init__` 与 `_rebuild_ui` 共用；**换主题那条路在调它之前必须先 `root.unbind()`**（那是重建独有的步骤）。
- **围栏扫描** `scan_fences(lines, fence)`：整篇重解析与增量重解析共用。`in_code` 判的是「**进入**本行时已在
  围栏里」**或**「本行自己就是分隔线」，所以开栅栏行与闭栅栏行都按代码块渲染。**`build_export_rows` 与卡片
  摘录里的围栏循环形状不同（一个要 `set[int]` 行号、一个逐行短路），故意没合**——合了会给卡片摘录那条已
  优化过的热路径添分配。
- 审计后仍**故意留着**的「伪冗余」：`_card_index_at(self, x, y)` 的 `x`（横向不参与判定）、Tk 回调的
  `_event` / `_args` 参数。**别再当垃圾删**。

## 2026-09-28 加的三样（文件名 / 音频 / 列表缩进）

- **文件名跟随一级标题**：`first_h1_title(text)` + `title_to_filename(title)` + `JianJiApp._follow_title()`，
  入口只在 `save_now()` 里（正文落盘**之后**改名，改名失败不丢内容）。**判据只有一条：第一行是一级标题、
  且清出来的名字与现在的文件名主干不同就改**（2026-09-28 晚改的，原来还有一道「标题相对载入变过没有」的
  门，`_title_at_load` 已删）。**别再改回「只在你改了标题时才改」**：用户那批 `新建-2026-09-28-160555.md`
  全建在改名功能上线之前，H1 早就是 `# All in One 软件思考`、名字一直没跟上；用户把标题重新敲一遍
  （内容一字未变），旧规则判「没变过」→ 文件名不动，在用户眼里就是「改了标题名字没跟着改」（他报了两次）。
  **只在存盘时改名**：光打开翻看不写盘，文件名一动不动。只认**第一行**；标题清空/首行不是 H1 → 保持原名。
  撞名走 `unique_path` → 两篇 `# 今日日记` 落成 `今日日记.md` / `今日日记-2.md`（**不覆盖**）。
  禁字符 `\ / : * ? " < > |` 换**全角同形字**（删掉会把 `第1章/第2节` 粘成 `第1章第2节`）；保留设备名看
  **第一个点之前**那段（`NUL.md` 一样保留，下划线要插进那一段 → `NUL_.md`）；截断 `FILENAME_MAX_CHARS = 80`。
  失败记忆 `_rename_failed_for = (normcase(path), stem)`——**key 里必须带 stem**，否则换个标题也被上次的
  失败连坐；成功要清成 `None`。**改名会牵连两条老路径**：`move_document_to` / `delete_document` 都是
  「先 `save_now` 再拿手里的 `path` 去干活」，改名后那个 `path` 已不存在 → 必须
  `if was_current: path = self.current_path`。核验 `tools/_probe_title_rename.py`（10 节端到端）+
  `tests/test_title_rename.py`（41 项）。**`tools/_sync_manual.py` 不许再写死说明文件名**：说明书 H1 是
  `# 简记 · 使用说明`，用户在软件里动一下就会变成 `简记 · 使用说明.md` → 走 `resolve_stem()`（已存在的那份
  优先，其次按 H1 推算），并清掉另一种主干留下的孤儿 png。
- **导出音频**：`app/speech.py` 是**纯 ctypes 驱动 SAPI**（`SAPI.SpVoice` + `SAPI.SpFileStream`，`IDispatch`
  晚绑定，不走 PowerShell / .NET，无第三方依赖），入口 `JianJiApp.export_audio()`，菜单项在 `_doc_menu`。
  念的文本走 `speakable_text()`，**复用 `build_export_rows`**——屏幕/长图/耳朵必须是同一套解析，记号一个都
  不许漏（漏了会被念成「井号」「星号」）。合成很慢（中文 ≈3.5 字/秒）→ **后台线程 + 主线程 `root.after` 轮询**
  （`_speech_job` / `_speech_poll`，已在 `_cancel_after_jobs` 里登记）。**系统默认音色本来就是中文**
  （`Microsoft Huihui Desktop`），所以不做音色选择；`default_voice_name()` 读注册表。踩坑全在 `speech.py`
  的模块 docstring 里。
- **列表缩进 Tab/Shift+Tab**：`LIST_MAX_LEVEL = 4`（5 级）**模型早就支持**，缺的只是按键绑定。
  `_shift_list_level(delta)` 按 `LIST_INDENT_STEP = 2` 重写行首缩进（**必须和 `_indent_level` 的
  `空格数 // 2` 一致**）。`_on_editor_tab` 按**「范围内有没有列表行」**分派，**不能按「有没有移动」**——
  第 4 级再按 Tab 什么都没动，会掉进「插入两个空格」那个分支。Tk 的 `<Tab>` 默认插 `\t`，而 `\t` 被
  `_indent_level` 算成 4 空格 = 2 级，所以必须拦下来返回 `"break"`。

## 三块大实现（细节在技能里，这里只留落点）

- **增量重解析**：打字先走 `_apply_markdown_styles_incremental()`，`False` 才退回整篇；**两条路径共用
  `_style_line`**。顺序不能反：`_drop_line_marks`（按**旧**行号）→ `_shift_line_maps`（按 delta）→ 抹标签
  → 重新上色。验证 `tests/test_incremental.py`、`tools/_check_incremental.py`、`_bench_reparse.py`
  （数 `tag` 调用次数防退化）。
- **长图导出**：`app/image_export.py`（默认宽 1080）+ 适配层 `build_export_rows`/`export_palette`/
  `_export_pieces`；后端在 `image_backend_gdi.py` / `image_backend_cg.py`，公共部分在 `image_common.py`。
  **折行断点（`_break_index`）、禁则（`_kinsoku`）、对齐坑的细节全在技能 `long-image-export` 里**，
  这里只留四条硬约束：
  - **缩进续行必须并进上一段**（`build_export_rows` 的 `absorber`；`text` 行 + 行首空白 + 上一行**非硬换行**
    → 并入上一段并去掉行首缩进；空行/标题/分隔线/代码块/表格之后清空；缩进的 `- ` 不吞；两侧都非中日韩才补
    空格）。**不并就会把列表项从中间截断**——第一行末尾常常正好是逗号 = 用户报的「逗号后面换到了新的一行」。
    所以判据**不能**再写「一源行 = 一块」。
  - **分张上限是软约束**（使用说明第一张实测 31455 > 30000）：判据累加 `line.height`，**没算** `build_layout`
    烘进 `top` 的 `head_gap`（真实高度 = `span + 2×margin`），再加「同一块的显示行不切开」。**有意留的余量，
    别改成按 `span` 卡**——会把一张装得下的文档切成两张。量法 `tools/_probe_chunks.py`。
  - **别再拿「行宽太窄」当解释去收边距**：1080px + 32px 正文 ⇒ 每行最多 **33 个汉字**（要边距收到 12px）；
    实测边距 72→12 只把短尾巴从 45 处降到 41 处，**基本没用**。要少折行只能加宽画布或缩字号。
  - **正文像素字号 `DEFAULT_BODY_SIZE = 46` = 一行正好 20 个汉字**（用户要求）。汉字在这几款中文字体里的
    **步进正好等于字号**（实测三款字体、字号 32 → 单字宽 32px），所以
    `一行几个字 = (1080 − 72×2) ÷ 字号 = 936 ÷ 46 = 20.35`，20 字 920px 放得进、21 字 966px 放不进。
    **别再往上调一档**：47 → 19.91，20 个字要 940px 差 4px，一行退成 19 个字。改字号会连带
    `_export_options` 按 `ratio` 推出来的行距一起长（用户设置下 60 → 87px），使用说明长图因此从 1 张变 2 张。
  - **行内强调的两条判据**（`_inline_spans` 与 `_tag_inline` **必须一字不差**，注释里互相点名）：
    ① 判「这段强调能不能用」**只看那两对记号**，不看内容——拿整个 `start..end` 去比 `blocked`，会让
    `**未压缩的 \`.wav\`**` 整段失效（行内代码先 claim 了 `.wav`），四个星号露在图上；② 强调循环**用 `search`
    + 手动推进，不用 `finditer`**——`finditer` 匹配不重叠，反引号里当例子写的 `**` 会**开启**一段匹配、把后面
    真那对星号当收尾，那段又因开头落在代码区间里被丢掉，于是**真的那对加粗从没被匹配过**。被挡下时
    `search_from = start + 1`（不是 `match.end()`）。验证：`InlineStyleTests`、`InlineParityTests`
    （`_FakeEditor` + `_TagHarness`，不建 Tk）、`GuideMarkupTests`。
  - **行内记号必须开闭在同一源行**（解析器逐行工作，增量高亮的前提）。跨行的话**屏幕、长图、音频三处一致地**
    把 `**` 原样显示——所以**别去改 `_append_continuation` 重解析合并后的段落**，那会打破「屏幕 / 长图 /
    音频同一套文字」这条不变量。这是**写文档的规则**，已写进说明书「补充说明」。写说明书时自己别踩：
    `GuideMarkupTests` 会渲染整篇 + 扫源文件把落单的 `**`/`~~`/`==` 揪出来。
  - `INLINE_CODE_RE` **只认单反引号**（`` `([^`\n]+?)` ``）：说明书里用双反引号写 `` `代码` `` 会露反引号。
    写「反引号」三个字绕开，别为它加语法。
- **点选卡顿**：元凶 `_trim_card_text`（占 `refresh_files` 85%）→ **探测长度从 32 起成倍增长再在末段二分**
  + 缓存；`_cards_signature()` 没变时不重建，只 `_retint_cards()`（只改两张）。**卡片阴影要有自己的 tag**，
  否则会被一起染色。实测 `refresh_files` 787→5.2 ms、点选 823.8→80.7 ms。**滚动**成本 ∝ 装饰画布数量；
  根治要改成 `text image_create`，还没做。

## 技能都没收的坑

- **「两块颜色分不分得开」不能比对比度**（对比度只看明度），要逐通道比 `max(|Δr|,|Δg|,|Δb|) >= 12`；
  「文字压在底色上够不够清楚」才用 WCAG 对比度（正文 ≥ 4.5:1）。
- **数「有没有叠绑」不能数 `info commands`**（`unbind` 不删那个 Tcl 命令），要读 `bind()` 的脚本数函数名出现几次。
- **输入法组字字号归 Windows 管**，要自己调 `imm32` 的 `ImmSetCompositionFontW`（macOS 上 Tk 自己管，跳过）。
- **`os.utime` 改不了创建时间**；「按创建时间排序」的测试**不能写死期望顺序**（ctime 相等时 `sorted` 保持原顺序
  → 随机变红），要跟 `st_ctime`（macOS 上 `st_birthtime`）真实值对。
- **`event_generate("<Return>")` 在隐藏桌面不投递按键**（没键盘焦点），只能断言绑定脚本里挂了处理函数 +
  返回 `"break"`。
- **`count(...,"ypixels")` 与 `yview()` 都不是稳定值**（Tk 只为视口附近排版，远处挂「估计高度」）。量几何用
  `dlineinfo`/`bbox`；量位移用**行 y 位移的中位数**；`yview_moveto(1.0)` 前必须 `update_idletasks()`。
- **`bbox` 给的是「那一段 chunk 自己的盒子」，不是显示行的盒子**：elide 掉的字符会返回**幻影显示行**，
  按 y 切显示行会被骗成「断点变了」。**量显示行要用 `dlineinfo`**（过滤 `is_real_display_box`）。踩过一次。
- **`dlineinfo` 的盒子包含 `spacing1/3`**（和行高一样），**标签自己的 `background` 也照样盖住 spacing**。
  所以「把底部留白挂在正文行上、再用自绘背景假装选区」这条路是死的——选框高度和标签底色都会长成
  「留白 + 正文行高」（用户设置下实测 310px，其中留白 267px）。留白只能挂在**行尾那个空行**上
  （见「界面约定」里的 `_ensure_trailing_newline`）；挂对了阴影只剩行自身高（实测 51px），两者相差 ≈ 留白值
  ——**判据写「阴影比留白小」，别写绝对像素**。
- **Tk `Text` 用 `\n` 分行，行尾那个 `\r` 是行内容**。切行只能用 `split_document_lines()`（`re.split` 遇单独
  `\r` 会多切一行，`str.splitlines()` 会在 `\x0b`/U+2028 断行，都会让行号错位）。
- **Windows 上 Tk 菜单是原生菜单，`menu.post()`/`tk_popup()` 本身不返回**（模态循环）。截菜单要**先起后台
  线程排好抓屏**（`capture_screen_rect` 是纯 ctypes GDI，子线程安全）**再进 `post()`**，抓完 `os._exit(0)`；
  一个进程只能摆一个菜单；**窗口还没映射时 `post()` 可能立刻返回** → 摆之前先等 `winfo_viewable()`。
  见 `tools/_shot_doc_menu.py`。
- **隐藏桌面上窗口拿不到键盘焦点，Tk 就不画选区底色** —— 截图里正文一点蓝色都没有，会误判成「问题不存在」。
  **截选区前必须 `root.focus_force()` + `editor.focus_force()`**（用 `focus_get() is editor` 确认）。
- **`end-1c` 是最后一个真字符**，末尾那个 `\n` 是 Tk 幻影换行：`get("end-3c","end")` 会比正文多一个 `\n`，
  `delete("end-1c")` 删的是最后一个真字符。**想造「末尾没有换行」的状态就直接 `insert` 原始正文**，
  别「载入完再删一个字符」（会静默删掉正文里的「。」）。
- **`import main` 必须排在第一次 `Tk()` 之前**（`main` 导入时声明进程 DPI 感知，而 Tk 在第一次 `Tk()` 时按
  当时的 DPI 定 `tk scaling`）。顺序反了 `tk scaling` = 1.332 而不是 1.998（本机 150% 缩放），
  `_current_pad` 80→48、徽标画布**压根不放置**、折行填充率 0.971→0.898——**后面所有量几何的用例一起红**，
  而且单独跑那些模块全绿，极难定位。测试侧兜底在 `tools/run_tests.py` 的 `run_in_process()`（发现测试模块
  之前先 `import main`）；复现 `tools/_probe_scale_order.py`。**别把这道兜底删了**，也别在测试模块里把模块级
  探针写到 `import main` 前面。
- **`time.sleep` / 轮询循环里千万别等模态框**：隐藏桌面上没人能点「确定」，会一直挂到被杀，而且**日志一个字
  都没有**（子进程 stdout 是块缓冲，`-u` 或 `flush=True` 才有输出）。探针里要把 `messagebox` 换成记录器
  （`tools/_probe_title_rename.py` 有现成写法）。

## 音频导出（SAPI / ffmpeg）

- **语速只走 `voice.Rate`，绝不放 `<prosody rate>` 进 SSML**。实测 `<prosody rate='0'>` 会让同一段文本从
  16.9 秒变成 **50.9 秒**（SAPI 对它的解释和 `voice.Rate = 0` 完全对不上）；两处各设一次还会叠乘。
  已钉进 `test_the_rate_is_not_put_into_the_ssml`。
- **让它不机械的杠杆只有节奏**：`speech.build_ssml()` 出带停顿的标记（Windows `<break>` + `SVSF_IS_XML`(=8)；
  macOS `[[slnc]]`），段间 450ms / 段内换行 260ms / 句末 160ms；**逗号不加**（引擎本来就会停）。
  语速 `NATURAL_RATE = -1`。`_gap(0)` / `_say_silence(0)` 返回空串——「关掉停顿」必须真的不留标记，
  否则对照核验是假的。量法：同段样例带停顿比纯文本长 **+1.15s**（理论 +1.19s）→ 说明 `<break>` 真被执行了。
  「更自然」没法机器判定，`tools/_probe_audio_naturalness.py` 出 A/B 样音给人听。
- **本机 SAPI5 只有 `TTS_MS_ZH-CN_HUIHUI_11.0` 一个中文音色**；更自然的 OneCore 音色
  （`MSTTTS_V110_zhCN_*`）注册在 `Speech_OneCore\Voices`，**SAPI5 看不见**，要管理员权限搬 token。
- **`shutil.which("ffmpeg")` 找不到 ≠ 没装**：Windows 的 ffmpeg 包解压出来是 `ffmpeg-xxx/bin/ffmpeg.exe`，
  很多人把**解压目录本身**加进 PATH（本机 `D:\OpenToUseSW\ffmpeg-master-latest-win64-gpl-shared` 就是），
  于是 `where ffmpeg` 失败但东西就在。`app/ffmpeg.py::_locate()` 会在 PATH 每一层**再往下探一层 `bin\`**。
- **起外部进程要 `creationflags=CREATE_NO_WINDOW`**（0x08000000），否则闪黑框——这正是 `speech.py` 当初
  绕开外部脚本引擎的原因。转 MP3 是**可选一步**，找不到 ffmpeg 就只提供 WAV。临时 WAV 放 `mkdtemp()`，
  `finally` 里 `rmtree`；**别放用户选的目录**（中途失败会留下同名垃圾）。

## 搜索（2026-10-19）

- **为什么另开 `app/search.py`**：行号/列号/摘录窗口都是纯判据，可以不起窗口就测；`main.py` 已经五千多行，
  塞进去会让「界面」和「搜索规则」搅在一起。`main` 反过来不能 import `main` 里的 `first_h1_title`，
  所以标题解析在 `search.title_of` 里另写了一份（**比它宽松一格：跳过开头空行**——有人习惯先敲个回车）。
- **列号是字符偏移，不是像素**。行号 1 起、列号 0 起，`f"{line}.{column}"` 直接就是 Tk 索引。`text.splitlines()`
  天然处理 `\r\n`：**本应用写出的文件在 Windows 上就是 CRLF**（`write_text` 文本模式 + `atomic_write_markdown`
  原样写回编辑区内容），所以别用 `split("\n")`，否则每行末尾会多一个 `\r`，列号在行尾附近就错位。
- **搜索条必须 `place`，不能进 grid**。进 grid 等于从编辑区切走一截宽度，「内容行宽」按比例算的排版会整体重排
  ——开一次搜索正文就跳一次。`place` 之后编辑区宽度不变，`test_the_bar_does_not_change_the_editor_width` 钉住。
- **条子放顶栏那一行**（`y=px(3)`）：正文第一行往往就是 H1，浮层压上去比「临时盖住专注模式」难受得多。
- **键事件在隐藏桌面上送不到**：`event_generate("<Control-f>")` 要控件真有键盘焦点，`test_list_editing` 早就踩过。
  所以测试钉「绑定指向谁」（`bind()` 的脚本文本里有没有 `_on_find_shortcut`）+ 直接调处理函数，不发键事件。
- **全局范围下别挪光标**：一开始 `_goto_hit` 无脑 `mark_set` + `see()`，结果按一下方向键就把正文滚到另一篇文档的
  第 3 行——高亮还糊在无关的句子上（截图里「那」字被涂蓝就是它）。现在只有 `hit.path == current_path` 才贴
  `search_current` / `see()`；`move_cursor` 由调用方显式传（`_open_search_hit` 传 True）。
- **搜索标签只设 `background`/`foreground`，不设 `elide`**：Tk 的 elide 取「优先级最高且显式设了 elide 的标签」，
  搜索标签建在最后（优先级最高），一旦顺手设了 `elide=False`，行首记号就再也藏不住了。
- **正文一改，标签位置全废**：标签按 `行.列` 贴，前面插一个字后面全歪。`_on_editor_modified` 末尾调
  `_refresh_search_highlights()` 整段重贴（只扫文本，代价约 1ms）。
- **防抖定时器要真取消**：`_run_search` 里 `self._search_job = None` 只是丢句柄，定时器还挂着；直接调用
  `_run_search`（打开条子、换范围）时必须 `after_cancel`，否则同一次输入搜两遍。
- **`⌘` 在长图里是豆腐块**：Microsoft YaHei UI 没有 U+2318，GDI 导出不做字体回退。说明书里**一律写
  `Command+N`**，别用 ⌘/⇧ 这类符号（`⇧` 同样缺字）。编辑器里也一样——看到方块不是编码坏了。

## 从 MEMORY.md 精简时搬过来的零碎

- **`INCREMENTAL_WINDOW = 48`**：增量重解析只比对光标上下各 48 行；窗口内对不齐（一次粘几十行）就退回整篇。
- **`tools/` 20 个的清单**在 `.3things/project.md` 的「工具」一节；旧的 62 个探针归档在
  `outputs/tools-探针归档-2026-09-26.zip`。删探针的判据是「**活文件**（`app/` `tests/` `tools/` 技能
  `MEMORY.md` 说明书）有没有引用」——历史日志里提到过不算理由。
- **仓库细节**：分支 `main`，首个提交 `8f63f1e`；`core.autocrlf=false`、`core.quotepath=false`；身份是占位值
  `JianJi <jianji@localhost>`（仅本仓库）；已忽略 `outputs/`、`.idea/`、`__pycache__/`。
- **`push_backup.cmd`**：工程根，纯 ASCII + `chcp 65001`，先打印远端/分支/待推提交再 `git push -u origin <分支>`。
- **`_build_ui()` 的调用顺序**（`_build_window → _build_layout → _apply_typography → _bind_shortcuts`）**不能换**：
  `_build_layout` 要往 `_build_window` 建出来的控件里放东西，`_apply_typography` 得先拿到那些控件，
  `_bind_shortcuts` 最后绑。`__init__` 与 `_rebuild_ui` 都走这一串。
- **`scan_fences()` 整篇与增量共用**；**`build_export_rows` 与卡片摘录里的围栏循环故意没合**。
