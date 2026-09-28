# 简记 · 项目长期笔记

> 只留「不照做就会犯错」的约定与落点。细节见 `memory/YYYY-MM-DD.md`、`.3things/build-log.md`、
> 技能 `tk-text-incremental-highlight`（Tk 硬约束全清单）、`long-image-export`、`quiet-desktop-gui-run`。

## 备份 / 远端

- 远端 `origin` = `https://github.com/zili-J/Jianji.git`。**备份入口是双击 `push_backup.cmd`**
  （工程根，纯 ASCII + `chcp 65001`，先打印远端/分支/待推提交再 `git push -u origin <当前分支>`）。
- **沙箱里推不上去**：环境全局设了 `GIT_TERMINAL_PROMPT=0`；显式打开后
  `git credential-manager get` 会**超时挂住**（GCM 的登录窗弹不出来）；
  有时整个进程树被 **SIGTERM 硬杀**（连 `echo` 都不执行）——那是沙箱级 kill，别当 git 错误查。
  本机 Windows 凭据管理器里没有 github.com 条目，所以**认证必须在用户自己的窗口里点一次**；
  点过之后凭据进凭据管理器，助手也就能直接推了。
- **`.cmd` / `.bat` 必须是 CRLF**（`.gitattributes` 里 `*.cmd text eol=crlf`，**排在 `*` 之后**）。
  `* text=auto eol=lf` 会连入口文件一起转成 LF，cmd.exe 对 LF 批处理容忍度有限
  （`goto :label` 会失效）。**改属性后要手动把工作区文件转回 CRLF**——git 只在检出时改写。
  判据 `git ls-files --eol <file>` = `i/lf  w/crlf  attr/text eol=crlf`。
- **备份已通**（2026-09-28）：远端 `refs/heads/main` = 本地 `main` = `71cf719`，73 个文件，
  上游 `origin/main` 已设。以后推只要 `git push`（凭据已在 Windows 凭据管理器里）。
- **`cmdkey /list` 查不到 github 条目 ≠ 没有凭据**：GCM 照样能取到。
  判断有没有凭据要直接跑 `printf "protocol=https\nhost=github.com\n\n" | git credential-manager get`。
- **Git Bash 会把 `ref:path` 参数改坏**：`git cat-file -e "origin/main:.gitattributes"` 会报
  `Not a valid object name origin\main;.gitattributes`（`:`→`;`、`/`→`\`，MSYS 路径转换），
  而且**时灵时不灵**，能骗出「文件缺失」的假结论。改用 `git ls-tree origin/main <path>`
  或加 `MSYS_NO_PATHCONV=1`。**报缺失前先用另一种写法复核**。

## 交付物 / 运行 / 测试

- 给用户看的文档写进**文档文件夹**（`state.json` 的 `default_folder`，本机 `C:\Users\22910\Documents\wb01`），
  **不写工程根目录**（根目录 `.md` 会让文件夹树混进 `app`/`tools`/`tests`）。
- **使用说明每次更新都同步到 `C:\Users\22910\Documents\wb01\说明书\` 并重出长图**（用户固定要求）：
  `python tools/quiet_desktop.py <python 全路径> tools/_sync_manual.py`。
- **`简记使用说明.md` 是测试基准文件，不是普通文档**：`tests/test_export.py::EditorParityTests`
  拿它整篇做「编辑器 vs 导出」逐行对账，并断言它**覆盖全部 10 种块级语法**
  （`heading/text/blank/bullet/ordered/task/quote/code/hr/table`）。重写它必须保留这 10 种。
  `hr` 靠章节之间那些 `---`，**前面必须有空行**（紧贴正文会变成 setext 标题）。
- **写 `.md` 给这个应用渲染时，一段话必须写成一行。** 编辑器和导出都**按源行分块**
  （只有**行首缩进**的续行会被并进上一段），手工断行会让每行各自成块 ——
  长图里就出现「写成 `2. `，」这种只剩半行的残句。**引用同理**：连续两行 `> xxx` 会裂成
  两个引用块（中间一道缝），除非第一行以**行尾两个空格**结尾。
- **表格里别放超长单元格**：`_layout_table` 在 `total > content_width` 时**等比压缩**，
  下限是**写死的** `floor = body_size * 2.2`，不是「本列最长不可断词宽度」——
  长格会把整表压窄，逼得另一列的长 token 从中间断开（实测 `Ctrl+Shift+F` → `Ctrl+Shi`/`ft+F`）。
  绕开办法是把那一格移出表格改成表下一行说明。**别去改 `_measure` / `char_widths`**：
  实测整串宽度与逐字之和差 0，不是它们的问题。
- **`python` 走 PATH 是托管 3.13，没有 tkinter**（`import tkinter` 直接
  `ModuleNotFoundError`）。跑测试/同步一律用**系统 3.12 全路径**：
  `C:\Users\22910\AppData\Local\Programs\Python\Python312\python.exe`。
- 运行 `run_jianji.cmd` / `python app/main.py`（系统 Python 3.12，无第三方依赖）。
- 测试 `python tools/run_tests.py`（**默认隐藏桌面**；`--foreground` 才回当前桌面，**截原生菜单必须用它**；
  `-k` 按文件名过滤）。输出落 `outputs/test-output.txt`，屏幕只摘 `FAIL:`/`ERROR:`/`Ran`/`OK`；**0 用例 = 退出码 3**。
  裸 `unittest discover` **必须带 `-t tests`** 且**先重定向到文件**。
- 报告是**边跑边写**的（`_FlushingStream`）——卡死时看 `outputs/test-output.txt` 末尾那行就知道卡在哪个用例，
  **别干等**。**不要同时跑两套 GUI 用例**（隐藏桌面上抢资源/留模态状态，实测挂过一次 51 分钟）。
- **`tools/` 只留 20 个**（5 个正式工具 + 15 个被点名的核验入口，**清单在 `.3things/project.md` 的「工具」一节**）。
  一次性探针**用完即弃**；旧的 62 个归档在 `outputs/tools-探针归档-2026-09-26.zip`。
  要删探针，判据是「**活文件**（`app/` `tests/` `tools/` 技能 `MEMORY.md` 说明书）有没有引用」——
  **历史日志里提到过不算理由**；`_bench_reparse.py` 依赖 `_bench_perf.build_document`，两个得一起留。
- **有 git 仓库了**（2026-09-26 建，分支 `main`，首个提交 `8f63f1e`，64 个文件）——回退直接
  `git checkout -- <文件>` / `git revert`，不用再靠临时改代码数残留。仓库级配置
  `core.autocrlf=false`（行尾原样存）、`core.quotepath=false`（中文路径不转义）；
  身份是占位值 `JianJi <jianji@localhost>`（**仅本仓库**，全局没配）。
  已忽略 `outputs/`（~12MB 长图与核验截图，可重生成；只跟踪 `tools-探针归档-2026-09-26.zip`）、
  `.idea/`、`__pycache__/`。
- **行尾实测**（建仓时逐文件量的，**别按印象写脚本**）：绝大多数是 **LF**；CRLF 的只有
  `app/image_export.py`、`tools/_probe_editor_wrap.py` 和 `.workbuddy-ai/memory/` 下的
  `MEMORY.md` / `09-22` / `09-23` / `09-25` / `09-26`——其中 `2026-09-22.md` 是**单文件内混用**
  （62 CRLF + 48 LF）。`09-09` / `09-13` / `09-20` / `09-21` / `09-24` 是 LF。
  「日志都是 CRLF」是错的。`core.autocrlf=false` 保证 git 不做任何转换（已验证磁盘与 blob 逐字节相同）。

## 核验必须在**用户真实设置**下做（踩得很惨）

- 设置现读 `%LOCALAPPDATA%\JianJi\state.json`（本机 `line_width 50 / font_size 18 / line_height 34 /
  霞鹜文楷 / sort_key modified / theme light`；`DEFAULT_SETTINGS` 是 `50/15/22/name`）。
  **这几个值用户会自己调**（`line_width` 60→50、字体从微软雅黑换成霞鹜文楷都发生过），
  所以**别照抄这一行，现读文件**——探针原样读 `settings` 就行。
  **探针要把用户 `settings` 原样写进临时 state.json 再构造 `JianJiApp`**（设置只在启动时读一次）；
  **测试要覆盖非默认设置**（`TallLineHeightWrappedLineGeometryTests` 的 `SETTINGS` 钩子）。
- **`SCALE` 由 `main` 在导入时算出来（本机 1.5，150% 缩放），且它会钉住整个进程的 `tk scaling`**：
  必须在**任何 `Tk()` 之前** `import main`（它顺带声明进程 DPI 感知）。顺序反了 `tk scaling`
  会停在 1.332 而不是 1.998，`_current_pad` 80→48、徽标画布压根不放置、折行填充率 0.971→0.898，
  **后面所有量几何的用例一起红**。踩过一次（见「技能都没收的坑」）。
- **判据里不要出现绝对像素**，一律相对设置项（`px(line_height)`）写。
- 用户的 `state.json` 与文档文件夹都别写（核验用临时 state.json；`set_folder()` 只在文件夹里没 `.md` 时才建文稿）。
- 探针三坑：`mark_set` 后必须显式跑 `_refresh_cursor_line()`；「光标在别处」停在**紧邻目标行的普通正文行**
  （扔到末尾会把目标滚出视口）；`open_file(同一路径)` **直接短路**，换用例要换文件名。

## 界面约定

- 各级标题**字号与正文一致**，靠加粗、颜色、上下留白、左侧 `H1`–`H6` 徽标分层。
- **光标所在行显示源码**（灰）；**唯一例外是有序列表序号**——永远由画布绘制（蓝、与正文同号），记号永远 elide，
  **不记进 `_line_marks`**。别扩大这个例外（项目符号/复选框仍露源码）。
- **编辑区控件必须是 `wrap="char"`，不能改回 `word`**（用户第 11 轮）：Tk 的 word 折行把「空格分隔的一串字符」
  当成**不可拆的词**。中文整行没空格时那个词就是整行，放不下只能按字符断 → 反而填得满；但只要行里有**一个空格**
  （自己敲的，或中英混排带出来的），空格后面那串中文就成了一个词 → **整串挪到下一显示行**，本行右边空掉一大块。
  实测（可用宽 571px）：「短 后面这一整串中文…」第一显示行只填 **4.7%**，引用/列表项只填 **41.9%**，整段还多折一行；
  改 char 后分别 96.0% / 100.0%，显示行还少一行。**这同时解释了用户说的「显示比例不对」**——行只填到 4.7% 时
  看起来就像正文块比设置窄得多（正文块本身实测 = 编辑区控件宽的 60.2%，和设置一致，逻辑没坏）。
  代价：英文单词跨行会被从中间断开（中文优先可接受；导出长图本来就按宽字符断行，两边这才对得上）。
  核验 `tools/_probe_editor_wrap.py`（0 处 vs 改回 word 的 4 处）+ `SpaceDelimitedWrapTests`。
  **顺带一个大简化**：char 下 Tk 对「行首 elide 的显示行」也直接给整条显示行的盒子，**空盒子整个消失**
  （word 下 `dlineinfo("2.0")` 是 `(189,51,0,16,8)`，char 下是 `(189,51,570,51,36)`），`is_real_display_box`
  从此只是保险；`bbox` 给 chunk 盒子那条坑**仍然成立**，别混。
- **「内容行宽」的比例基准是显示器宽，不是窗口也不是控件**（用户第 12 轮）：
  `T = min(r·S, C − 2×5%·S)`（S=显示器宽、C=编辑区宽、r=行宽设置），`pad = (控件宽 − T) // 2`。
  窗口下限 = `NAV_W + CARDS_W + 40%·S`（`_apply_min_window_width`；值没变必须提前返回，
  否则 `minsize` 触发 `<Configure>` 会来回弹；`_build_window` 的初始宽度也不能小于它）。
  **副作用**：三栏都开时 C 通常只有 40%·S，默认窗口就落在第二档 `T = C − 10%·S`——
  那一档里 `line_width` **不起作用**。这是规则本身，测试 `test_the_setting_is_inert_...`
  专门钉住，别「修掉」。测试里要用 `tests/_pinning.py` 的 `pin_screen_share(app, share)`
  钉住屏幕宽，否则「调行宽 → 留白变」那几条断言根本观测不到。
  **对称 `padx` 只能把正文块居中到 ±1px**（两侧留白合计必须是偶数），判据写 `abs(差) <= 1`。
- **行首记号露源码必须补偿**，落点 `syntax_marker` 标签：`lmargin1 = 行缩进 − 记号宽度`、`lmargin2 = 行缩进`、
  **`wrap="char"`**，三件一起做。标签**必须建在 `li{n}` 循环之后**，每次 `tag_add` 前用 `_marker_options(indent)` 重配。
  **只补宽度没用**（当年控件是 `word`，记号一露正文那个词被整个挪走 → 多一行）；控件改 char 后这条冗余但仍留着当回退哨兵。
  已接受的残留：点击后下方整体上移 `px(行高) − 字体行距`（用户设置 16px）；**`spacing1` 这个标签选项实测不生效**，别再试。
  `_line_marks` 是 **5 元组** `(off_tag, on_tag, start, end, indent)`，`indent` 为 `None` 或 `(lmargin1, lmargin2)`。
- **行内记号（`**`、`` ` ``）露出来会加宽整行** → 当年控件是 `word` 时把一个词整个挤到下一显示行 =「选中该行就换行」
  （**控件改 `char` 后这个机制消失**，`_inline_reveal_fits` 退化成哨兵，判据与落点保持原样不动）。
  修法 `_inline_reveal_fits(line, text)`：**整行带记号量出来仍能放进一个显示行**才露。落点三处：`_swap_cursor_marks`
  的 `reveal` 门（跳过 `off_tag=="syntax" and indent is None`）、`_style_line` 在 `_tag_inline` 后调 `_hide_inline_marks`、
  标题改走 `_tag_marker`（负 `lmargin1` 把 `# ` 挂进左留白）。**标题原来走 `_tag_syntax`** → 条目 `indent is None`
  会被行内门一起压掉，所以测试里「`syntax_marker` 标签存在」才抓得住回退。
- **装饰（序号/徽标/竖条）画布**统一走 `_display_line_box()`：① 判据**只看宽度**（`box[2] > 0`）——空盒子的高 =
  行高 − 字体行距，随行高设置变，「高度 < 6px」这种绝对阈值会失效；② 量到好盒子**直接用**，别与空盒子合并；
  ③ 空行/只有记号的行也是宽 0 但盒子对 → 接受（`box[2] > 0 or box[3] >= px(行高)`）。重新量用
  **`index + 1 display chars`**。定高 `max(box[3], px(行高))`（视口下沿那一行的 `box[3]` 会被 Tk 裁一刀）。
  **空盒子本身只在 `word` 下出现**（见上面 `wrap="char"` 那条），`char` 下这段判据只是保险。
- **不透明画布会盖住光标**；**一行全被 elide 时 Tk 把光标退回行首（忽略 `lmargin`）**——空的有序项回车续号后
  光标落在序号画布下。修法在 `_style_line` 有序分支：`body_empty and line_number == cursor_line` 时把**行尾空格
  留在 elide 之外**（`marker_end -= 1`）。**别再改成「画布整体左移」**（被撤掉的写法）。
- **顶部栏（编辑状态 + 专注模式）常显**（`grid_propagate(False)`）。「自动收起」被撤回（连编辑状态一起没了）。
  **中间那个字数是悬停才显示的**（`count_label` 在 `column=1`，初始 `grid_remove()`——**别用
  `grid_forget()`**，前者留着 grid 参数，`grid()` 一下就能回原位）。判据**不能看 `<Enter>/<Leave>`
  事件**：指针从 topbar 挪到它自己的子控件上时 topbar 也会收到 `<Leave>`，当场隐藏会闪。
  正确做法是 `_on_topbar_leave` 排一个 `after(COUNT_HIDE_DELAY_MS)`，到点用
  `_pointer_is_on_topbar()`（`winfo_containing` + 往上找 `master`）算真实位置。topbar 和它的
  **三个子控件都要绑**。`_counts_job` 要进 `_cancel_after_jobs`，`_rebuild_ui` 里也要显式取消。
  两个数：`document_counts()`（模块级纯函数）→ 字数 = 中日韩逐字 + 西文按词（标点/记号不计），
  字符数 = 除换行外全部字符。
- **粗体/斜体走暖色**（`BOLD_COLOR #B3261E`、`ITALIC_COLOR #A87514`）。改色要**三处一起**：编辑器标签、
  `export_palette()`、`image_export.Palette` 默认值。测试按**色相角差 + 相对亮度差**判。
- **深色主题**：`apply_theme(name)` 把颜色常量 `globals().update()` 再 `_rebuild_ui()` 整块重建；`THEMES["light"]`
  **导入时快照** → **导出长图永远浅色**。重建前 `root.unbind()` 防叠绑；新颜色要进 `THEME_KEYS` 和 `_DARK_COLORS`。
- **预览卡片**：字体固定 16 号微软雅黑（`CARD_FONT_*`，走 `px()`），**不跟编辑区联动**；间距唯一入口 `_card_gap()`；
  摘录**去换行、用空格连成一行**；**第一行是一级标题也照常显示**；`CARD_EXCERPT_ROWS = 4` 按**显示行**算。
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
- **留白必须挂在「末尾那个空行」上**：Tk 画选中高亮（和标签 `background`）时把**整个显示行盒子连
  `spacing3` 一起涂色** —— 挂在**正文行**上就会出现「选中最后一行 → 一大块蓝盖住留白」。
  所以 `_load_editor_text` 调 `_ensure_trailing_newline(text)` 给**不以换行结尾**的正文补一个
  （字符串级、拼进那一次 `insert`，别分成两次 insert：会多发一次 `<<Modified>>` 白跑一遍重解析；
  补在 `edit_reset()` 前，不进撤销栈、不算脏）。**已实测：标签怎么摆都躲不开**——空区间 `end..end`
  会被 Tk 丢掉（留白整段失效）、`end-1c..end` 只盖住换行符留白同样失效、标签 `background` 也盖住留白。
  **两条已否决的路，别再走**：① 留白挪出正文区（`grid_configure(pady=(0,pad))`）结构上免疫，
  但编辑区**常驻**少 25% 视口；② 在 `_apply_bottom_pad` 里「删了就补回来」——末尾空行会变成
  删不掉（Backspace 静默失效），还会让 20 个 `test_list_editing` 用例红。
  残留：用户删掉末尾换行后高亮又会盖住留白，重开文稿自愈。
  （补的时候若用 `insert("end","\n")`，**`insert` 标记会被推到新空行上**，得自己存下光标还原。）
- **文稿排序**：`sort_key`（`name`/`created`/`modified`），`list_markdown(..., sort_key=)` 按时间戳**倒序**；
  `_sort_key_of` 文件刚移走/删掉时退到 `0.0`。**Windows 上 `st_ctime` 就是创建时间**。入口在**文稿列表右键菜单**
  （`_add_sort_cascade`；点卡片下方空白也能弹）。**不要再放回标题栏**（grid 一列宽 = 该列最宽的行，卡片区会少一块）。
- **文件夹树只有一级**（子文件夹与「全部文稿」并列，`_tree_menu` 只在 `__all__` 给「新建文件夹…」）；
  已取消「今天」入口与 `Ctrl+D`。
- **有序列表回车自动续号**（`_on_editor_return`）：沿用缩进与分隔符数字 +1；**空项回车结束列表**；
  **光标不在行尾或有选区时不管**。程序化 `insert` 后要手动 `see("insert")`。
- 尺寸一律 `px()`。滚轮三处一起调：编辑区控件级绑定（`delta/3` × `EDITOR_WHEEL_FACTOR` = **6**，
  **必须返回 `"break"`**）；卡片与树走 `LIST_WHEEL_UNITS` = **3**。量法 `tools/_check_wheel_speed.py`。
- **Canvas 只在「滚动区比视口高」时才夹住原点**：判据必须用 `canvasy(0) < 0`（`yview()` 这时说谎），
  夹回用 `yview_moveto(0.0)`（`_clamp_cards_scroll`）。**Treeview 与 Text 自己夹得住，不要给它们也加一层。**

## 改这几处要走共用函数（2026-09-26 审查后新添，别在调用点重抄）

- **整篇换正文** `_load_editor_text(text, cursor=None)`：`configure(normal) → delete →
  insert → edit_reset → edit_modified(False)` 的**顺序不能换**（`edit_reset` 要在 `insert` 后，
  `edit_modified(False)` 要最后）。只读场景由调用方之后自己 `configure(state="disabled")`。
  三处调用：打开文档、打开回收站条目、换主题重建。
- **对话框居中** `_place_dialog(window, width, height)`：内部先 `root.update_idletasks()`
  再算位置（不先跑一遍 `winfo_width()` 是旧值）。设置窗口与字体选择器共用。
- **构建界面** `_build_ui()`：`_build_window → _build_layout → _apply_typography →
  _bind_shortcuts`。`__init__` 与 `_rebuild_ui` 共用；**换主题那条路在调它之前必须先
  `root.unbind()`**（那是重建独有的步骤，不在 `_build_ui` 里）。
- **围栏扫描** `scan_fences(lines, fence)`：整篇重解析与增量重解析共用。
  `in_code` 判的是「**进入**本行时已在围栏里」**或**「本行自己就是分隔线」，
  所以开栅栏行与闭栅栏行都按代码块渲染。**`build_export_rows` 与卡片摘录里的围栏循环
  形状不同（一个要 `set[int]` 行号、一个逐行短路），故意没合**——合了会给卡片摘录
  那条已优化过的热路径添分配。
- 审计后仍**故意留着**的「伪冗余」：`_card_index_at(self, x, y)` 的 `x`（横向不参与判定）、
  Tk 回调的 `_event` / `_args` 参数。**别再当垃圾删**。

## 2026-09-28 加的三样（文件名 / 音频 / 列表缩进）

- **文件名跟随一级标题**：`first_h1_title(text)` + `title_to_filename(title)` + `JianJiApp._follow_title()`，
  入口只在 `save_now()` 里（正文落盘**之后**改名，改名失败不丢内容）。
  **判据只有一条：第一行是一级标题、且清出来的名字与现在的文件名主干不同就改**（2026-09-28 晚改的，
  原来还有一道「标题相对载入变过没有」的门，`_title_at_load` 已删）。
  **别再改回「只在你改了标题时才改」**：用户文稿里那批 `新建-2026-09-28-160555.md` 全建在改名功能
  上线之前，H1 早就是 `# All in One 软件思考`、名字一直没跟上；用户把标题重新敲一遍（内容一字未变），
  旧规则判「没变过」→ 文件名不动，在用户眼里就是「改了标题名字没跟着改」（他报了两次）。
  **只在存盘时改名**：光打开翻看不写盘，文件名一动不动。只认**第一行**；标题清空/首行不是 H1 → 保持原名。
  撞名走 `unique_path` → 两篇 `# 今日日记` 落成 `今日日记.md` / `今日日记-2.md`（**不覆盖**）。
  禁字符 `\ / : * ? " < > |` 换**全角同形字**（删掉会把 `第1章/第2节` 粘成 `第1章第2节`）；
  保留设备名看**第一个点之前**那段（`NUL.md` 一样保留，下划线要插进那一段 → `NUL_.md`）；
  截断 `FILENAME_MAX_CHARS = 80`。失败记忆 `_rename_failed_for = (normcase(path), stem)`——
  **key 里必须带 stem**，否则换个标题也被上次的失败连坐；成功要清成 `None`。
  **改名会牵连两条老路径**：`move_document_to` / `delete_document` 都是「先 `save_now` 再拿手里的
  `path` 去干活」，改名后那个 `path` 已经不存在 → 必须 `if was_current: path = self.current_path`。
  核验 `tools/_probe_title_rename.py`（10 节端到端）+ `tests/test_title_rename.py`（41 项）。
  **`tools/_sync_manual.py` 不许再写死说明文件名**：说明书 H1 是 `# 简记 · 使用说明`，用户在软件里
  动一下就会变成 `简记 · 使用说明.md` → 走 `resolve_stem()`（已存在的那份优先，其次按 H1 推算），
  并清掉另一种主干留下的孤儿 png。
- **导出音频**：`app/speech.py` 是**纯 ctypes 驱动 SAPI**（`SAPI.SpVoice` + `SAPI.SpFileStream`，
  `IDispatch` 晚绑定，不走 PowerShell / .NET，无第三方依赖），入口 `JianJiApp.export_audio()`，
  菜单项在 `_doc_menu`。念的文本走 `speakable_text()`，**复用 `build_export_rows`**——
  屏幕/长图/耳朵必须是同一套解析，记号一个都不许漏（漏了会被念成「井号」「星号」）。
  合成很慢（中文 ≈3.5 字/秒）→ **后台线程 + 主线程 `root.after` 轮询**（`_speech_job` / `_speech_poll`，
  已在 `_cancel_after_jobs` 里登记）。**系统默认音色本来就是中文**（`Microsoft Huihui Desktop`），
  所以不做音色选择；`default_voice_name()` 读注册表。踩坑全在 `speech.py` 的模块 docstring 里。
- **列表缩进 Tab/Shift+Tab**：`LIST_MAX_LEVEL = 4`（5 级）**模型早就支持**，缺的只是按键绑定。
  `_shift_list_level(delta)` 按 `LIST_INDENT_STEP = 2` 重写行首缩进（**必须和 `_indent_level` 的
  `空格数 // 2` 一致**）。`_on_editor_tab` 按**「范围内有没有列表行」**分派，**不能按「有没有移动」**——
  第 4 级再按 Tab 什么都没动，会掉进「插入两个空格」那个分支。Tk 的 `<Tab>` 默认插 `\t`，
  而 `\t` 被 `_indent_level` 算成 4 空格 = 2 级，所以必须拦下来返回 `"break"`。

## 三块大实现（细节在技能里，这里只留落点）

- **增量重解析**：打字先走 `_apply_markdown_styles_incremental()`，`False` 才退回整篇；**两条路径共用 `_style_line`**。
  顺序不能反：`_drop_line_marks`（按**旧**行号）→ `_shift_line_maps`（按 delta）→ 抹标签 → 重新上色。
  验证 `tests/test_incremental.py`、`tools/_check_incremental.py`、`_bench_reparse.py`（数 `tag` 调用次数防退化）。
- **长图导出**：`app/image_export.py`（纯 ctypes+GDI 离屏，默认宽 1080）+ 适配层 `build_export_rows`/
  `export_palette`/`_export_pieces`。**折行断点（`_break_index`）、禁则（`_kinsoku`）、对齐坑的细节全在技能
  `long-image-export` 里**，这里只留三条硬约束：
  - **缩进续行必须并进上一段**（`build_export_rows` 的 `absorber`；`text` 行 + 行首空白 + 上一行**非硬换行**
    → 并入上一段并去掉行首缩进；空行/标题/分隔线/代码块/表格之后清空；缩进的 `- ` 不吞；两侧都非中日韩才补空格）。
    **不并就会把列表项从中间截断**——第一行末尾常常正好是逗号 = 用户报的「逗号后面换到了新的一行」。
    所以判据**不能**再写「一源行 = 一块」。
  - **分张上限是软约束**（使用说明第一张实测 31455 > 30000）：判据累加 `line.height`，**没算** `build_layout`
    烘进 `top` 的 `head_gap`（真实高度 = `span + 2×margin`），再加「同一块的显示行不切开」。
    **有意留的余量，别改成按 `span` 卡**——会把一张装得下的文档切成两张。量法 `tools/_probe_chunks.py`。
  - **别再拿「行宽太窄」当解释去收边距**：1080px + 32px 正文 ⇒ 每行最多 **33 个汉字**（要边距收到 12px）；
    实测边距 72→12 只把短尾巴从 45 处降到 41 处，**基本没用**。要少折行只能加宽画布或缩字号。
  - **行内强调的两条判据**（`_inline_spans` 与 `_tag_inline` **必须一字不差**，注释里互相点名）：
    ① 判「这段强调能不能用」**只看那两对记号**，不看内容——拿整个 `start..end` 去比 `blocked`，会让
    `**未压缩的 \`.wav\`**` 整段失效（行内代码先 claim 了 `.wav`），四个星号露在图上；
    ② 强调循环**用 `search` + 手动推进，不用 `finditer`**——`finditer` 匹配不重叠，反引号里当例子写的
    `**` 会**开启**一段匹配、把后面真那对星号当收尾，那段又因开头落在代码区间里被丢掉，
    于是**真的那对加粗从没被匹配过**。被挡下时 `search_from = start + 1`（不是 `match.end()`）。
    验证：`InlineStyleTests`、`InlineParityTests`（`_FakeEditor` + `_TagHarness`，不建 Tk）、`GuideMarkupTests`。
  - **行内记号必须开闭在同一源行**（解析器逐行工作，增量高亮的前提）。跨行的话**屏幕、长图、音频三处
    一致地**把 `**` 原样显示——所以**别去改 `_append_continuation` 重解析合并后的段落**，那会打破
    「屏幕 / 长图 / 音频同一套文字」这条不变量。这是**写文档的规则**，已写进说明书「补充说明」。
    写说明书时自己别踩：`GuideMarkupTests` 会渲染整篇 + 扫源文件把落单的 `**`/`~~`/`==` 揪出来。
  - `INLINE_CODE_RE` **只认单反引号**（`` `([^`\n]+?)` ``）：说明书里用双反引号写 `` `代码` `` 会露反引号。
    写「反引号」三个字绕开，别为它加语法。
  - **正文像素字号 `DEFAULT_BODY_SIZE = 46` = 一行正好 20 个汉字**（用户要求）。汉字在这几款中文字体里的
    **步进正好等于字号**（实测三款字体、字号 32 → 单字宽 32px），所以
    `一行几个字 = (1080 − 72×2) ÷ 字号 = 936 ÷ 46 = 20.35`，20 字 920px 放得进、21 字 966px 放不进。
    **别再往上调一档**：47 → 19.91，20 个字要 940px 差 4px，一行退成 19 个字。
    改字号会连带 `_export_options` 按 `ratio` 推出来的行距一起长（用户设置下 60 → 87px），
    使用说明长图因此从 1 张变 2 张。
- **点选卡顿**：元凶 `_trim_card_text`（占 `refresh_files` 85%）→ **探测长度从 32 起成倍增长再在末段二分** + 缓存；
  `_cards_signature()` 没变时不重建，只 `_retint_cards()`（只改两张）。**卡片阴影要有自己的 tag**，否则会被一起染色。
  实测 `refresh_files` 787→5.2 ms、点选 823.8→80.7 ms。**滚动**成本 ∝ 装饰画布数量；根治要改成 `text image_create`，还没做。

## 技能都没收的坑

- **「两块颜色分不分得开」不能比对比度**（对比度只看明度），要逐通道比 `max(|Δr|,|Δg|,|Δb|) >= 12`；
  「文字压在底色上够不够清楚」才用 WCAG 对比度（正文 ≥ 4.5:1）。
- **数「有没有叠绑」不能数 `info commands`**（`unbind` 不删那个 Tcl 命令），要读 `bind()` 的脚本数函数名出现几次。
- **输入法组字字号归 Windows 管**，要自己调 `imm32` 的 `ImmSetCompositionFontW`。
- **`os.utime` 改不了创建时间**；「按创建时间排序」的测试**不能写死期望顺序**（ctime 相等时 `sorted` 保持原顺序
  → 随机变红），要跟 `st_ctime` 真实值对。
- **`event_generate("<Return>")` 在隐藏桌面不投递按键**（没键盘焦点），只能断言绑定脚本里挂了处理函数 + 返回 `"break"`。
- **`count(...,"ypixels")` 与 `yview()` 都不是稳定值**（Tk 只为视口附近排版，远处挂「估计高度」）。量几何用
  `dlineinfo`/`bbox`；量位移用**行 y 位移的中位数**；`yview_moveto(1.0)` 前必须 `update_idletasks()`。
- **`bbox` 给的是「那一段 chunk 自己的盒子」，不是显示行的盒子**：elide 掉的字符会返回**幻影显示行**，
  按 y 切显示行会被骗成「断点变了」。**量显示行要用 `dlineinfo`**（过滤 `is_real_display_box`）。踩过一次。
- **`dlineinfo` 的盒子包含 `spacing1/3`**（和行高一样），**标签自己的 `background` 也照样盖住 spacing**。
  所以「把底部留白挂在正文行上、再用自绘背景假装选区」这条路是死的——选框高度和标签底色都会长成
  「留白 + 正文行高」（用户设置下实测 310px，其中留白 267px）。
  留白只能挂在**行尾那个空行**上（见「界面约定」里的 `_ensure_trailing_newline`）；挂对了阴影只剩行自身高（实测 51px），
  两者相差 ≈ 留白值——**判据写「阴影比留白小」，别写绝对像素**。
- **Tk `Text` 用 `\n` 分行，行尾那个 `\r` 是行内容**。切行只能用 `split_document_lines()`（`re.split` 遇单独 `\r`
  会多切一行，`str.splitlines()` 会在 `\x0b`/U+2028 断行，都会让行号错位）。
- **Windows 上 Tk 菜单是原生菜单，`menu.post()`/`tk_popup()` 本身不返回**（模态循环）。截菜单要**先起后台线程
  排好抓屏**（`capture_screen_rect` 是纯 ctypes GDI，子线程安全）**再进 `post()`**，抓完 `os._exit(0)`；一个进程只能摆
  一个菜单；**窗口还没映射时 `post()` 可能立刻返回** → 摆之前先等 `winfo_viewable()`。见 `tools/_shot_doc_menu.py`。
- **隐藏桌面上窗口拿不到键盘焦点，Tk 就不画选区底色** —— 截图里正文一点蓝色都没有，会误判成「问题不存在」。
  **截选区前必须 `root.focus_force()` + `editor.focus_force()`**（用 `focus_get() is editor` 确认）。
- **`end-1c` 是最后一个真字符**，末尾那个 `\n` 是 Tk 幻影换行：`get("end-3c","end")` 会比正文多一个 `\n`，
  `delete("end-1c")` 删的是最后一个真字符。**想造「末尾没有换行」的状态就直接 `insert` 原始正文**，
  别「载入完再删一个字符」（会静默删掉正文里的「。」）。
- **`import main` 必须排在第一次 `Tk()` 之前**（`main` 导入时声明进程 DPI 感知，而 Tk 在第一次 `Tk()` 时
  按当时的 DPI 定 `tk scaling`）。顺序反了 `tk scaling` = 1.332 而不是 1.998，`_current_pad` 80→48、
  徽标画布**压根不放置**、折行填充率 0.971→0.898——**后面所有量几何的用例一起红**，而且单独跑那些模块全绿，
  极难定位。测试侧的兜底在 `tools/run_tests.py` 的 `run_in_process()`（发现测试模块之前先 `import main`）；
  复现 `tools/_probe_scale_order.py`。**别把这道兜底删了**，也别在测试模块里把模块级探针写到 `import main` 前面。
- **`time.sleep` / 轮询循环里千万别等模态框**：隐藏桌面上没人能点「确定」，会一直挂到被杀，
  而且**日志一个字都没有**（子进程 stdout 是块缓冲，`-u` 或 `flush=True` 才有输出）。
  探针里要把 `messagebox` 换成记录器（`tools/_probe_title_rename.py` 有现成写法）。

## 音频导出（SAPI / ffmpeg）

- **语速只走 `voice.Rate`，绝不放 `<prosody rate>` 进 SSML**。实测 `<prosody rate='0'>` 会让同一段
  文本从 16.9 秒变成 **50.9 秒**（SAPI 对它的解释和 `voice.Rate = 0` 完全对不上）；两处各设一次还会叠乘。
  已钉进 `test_the_rate_is_not_put_into_the_ssml`。
- **让它不机械的杠杆只有节奏**：`speech.build_ssml()` 出带 `<break>` 的 SSML，`Speak` 带
  `SVSF_IS_XML`(=8)。段间 450ms / 段内换行 260ms / 句末 160ms；**逗号不加**（引擎本来就会停）。
  语速 `NATURAL_RATE = -1`。`_gap(0)` 返回空串——「关掉停顿」必须真的不留标签，否则对照核验是假的。
  量法：同段样例带停顿比纯文本长 **+1.15s**（理论 +1.19s）→ 说明 `<break>` 真被执行了。
  「更自然」没法机器判定，`tools/_probe_audio_naturalness.py` 出 A/B 样音给人听。
- **本机 SAPI5 只有 `TTS_MS_ZH-CN_HUIHUI_11.0` 一个中文音色**；更自然的 OneCore 音色
  （`MSTTTS_V110_zhCN_*`）注册在 `Speech_OneCore\Voices`，**SAPI5 看不见**，要管理员权限搬 token。
- **`shutil.which("ffmpeg")` 找不到 ≠ 没装**：Windows 的 ffmpeg 包解压出来是 `ffmpeg-xxx/bin/ffmpeg.exe`，
  很多人把**解压目录本身**加进 PATH（本机 `D:\OpenToUseSW\ffmpeg-master-latest-win64-gpl-shared` 就是），
  于是 `where ffmpeg` 失败但东西就在。`app/ffmpeg.py::_locate()` 会在 PATH 每一层**再往下探一层 `bin\`**。
- **起外部进程要 `creationflags=CREATE_NO_WINDOW`**（0x08000000），否则闪黑框——这正是
  `speech.py` 当初绕开外部脚本引擎的原因。转 MP3 是**可选一步**，找不到 ffmpeg 就只提供 WAV。
  临时 WAV 放 `mkdtemp()`，`finally` 里 `rmtree`；**别放用户选的目录**（中途失败会留下同名垃圾）。
