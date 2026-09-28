from __future__ import annotations

import ctypes
import os
import re
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font, messagebox, simpledialog, ttk

import speech
from image_export import (
    DEFAULT_BODY_SIZE as EXPORT_BODY_SIZE,
    DEFAULT_MARGIN as EXPORT_MARGIN,
    DEFAULT_MAX_HEIGHT as EXPORT_MAX_HEIGHT,
    DEFAULT_WIDTH as EXPORT_WIDTH,
    Options,
    Palette,
    Piece,
    Row,
    render as render_long_image,
)

from storage import (
    DEFAULT_FONT_FAMILY,
    ExternalChangeError,
    FileSignature,
    SETTING_RANGES,
    atomic_write_markdown,
    conflict_copy_path,
    count_files,
    create_folder,
    default_journal_folder,
    delete_to_recycle_bin,
    get_default_folder,
    get_settings,
    list_markdown,
    list_subfolders,
    list_trash,
    move_to_folder,
    move_to_trash,
    new_note_path,
    purge_trash_entry,
    read_markdown,
    replace_with_retry,
    restore_from_trash,
    reveal_in_explorer,
    set_default_folder,
    set_settings,
    signature,
    trash_entry_path,
    unique_path,
)


FAMILY = "Microsoft YaHei UI"   # 界面字体：导航、按钮、设置窗口等固定不变
MONO_FAMILY = "Consolas"        # 代码块等宽字体


class LOGFONTW(ctypes.Structure):
    """Windows 逻辑字体结构，imm32 用它描述输入法组字的字体。"""

    _fields_ = [
        ("lfHeight", ctypes.c_long),
        ("lfWidth", ctypes.c_long),
        ("lfEscapement", ctypes.c_long),
        ("lfOrientation", ctypes.c_long),
        ("lfWeight", ctypes.c_long),
        ("lfItalic", ctypes.c_byte),
        ("lfUnderline", ctypes.c_byte),
        ("lfStrikeOut", ctypes.c_byte),
        ("lfCharSet", ctypes.c_byte),
        ("lfOutPrecision", ctypes.c_byte),
        ("lfClipPrecision", ctypes.c_byte),
        ("lfQuality", ctypes.c_byte),
        ("lfPitchAndFamily", ctypes.c_byte),
        ("lfFaceName", ctypes.c_wchar * 32),
    ]


def _declare_imm32_types() -> None:
    """imm32 里组字相关的函数同样要先声明句柄类型（理由见 _declare_win32_types）。"""
    imm32 = ctypes.windll.imm32
    imm32.ImmGetContext.argtypes = [ctypes.c_void_p]
    imm32.ImmGetContext.restype = ctypes.c_void_p
    imm32.ImmReleaseContext.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    imm32.ImmReleaseContext.restype = ctypes.c_int
    imm32.ImmSetCompositionFontW.argtypes = [ctypes.c_void_p, ctypes.POINTER(LOGFONTW)]
    imm32.ImmSetCompositionFontW.restype = ctypes.c_int


def _declare_win32_types() -> None:
    """给这里用到的 Win32 函数声明参数类型。

    64 位下句柄是 8 字节，不声明的话 ctypes 会按 C 的 int（4 字节）传参，
    句柄一大就抛 `OverflowError: int too long to convert`。句柄是系统分配的，
    所以这是个「偶尔启动就崩」的隐患——必须显式声明，不能靠运气。
    """
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.GetDC.argtypes = [ctypes.c_void_p]
    user32.GetDC.restype = ctypes.c_void_p
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.ReleaseDC.restype = ctypes.c_int
    gdi32.GetDeviceCaps.argtypes = [ctypes.c_void_p, ctypes.c_int]
    gdi32.GetDeviceCaps.restype = ctypes.c_int


def enable_dpi_awareness() -> float:
    """声明进程 DPI 感知，避免 Windows 把窗口位图拉伸导致整体发虚。

    未声明时，在 150% 缩放的显示器上 Windows 会把整个窗口放大 1.5 倍（位图插值），
    文字边缘因此发糊。声明后进程直接按物理像素渲染，文字恢复清晰。
    必须在创建任何窗口之前调用。

    返回缩放比例：96 DPI（100%）为 1.0，144 DPI（150%）为 1.5。
    """
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE：Win8.1+
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            # 老系统的退路：system DPI aware
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass
    _declare_win32_types()
    hdc = ctypes.windll.user32.GetDC(None)
    try:
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
    finally:
        ctypes.windll.user32.ReleaseDC(None, hdc)
    return max(1.0, dpi / 96.0)


def _scale_from_env(raw: str | None) -> float | None:
    """解析 JIANJI_SCALE 覆盖值；未设置或非法时返回 None。

    测试用它在 100% 缩放下运行，保证断言不随运行机器的显示器缩放而变。
    """
    if not raw:
        return None
    try:
        return max(1.0, float(raw))
    except ValueError:
        return None


def _resolve_scale() -> float:
    """确定界面缩放比例。

    即使有环境变量覆盖，也仍然要声明 DPI 感知——否则窗口会被系统拉伸变糊。
    """
    if sys.platform != "win32":
        return 1.0
    detected = enable_dpi_awareness()
    override = _scale_from_env(os.environ.get("JIANJI_SCALE"))
    return override if override is not None else detected


# 必须在建窗口之前执行，所以放在模块导入时
SCALE: float = _resolve_scale()


def px(value: float) -> int:
    """逻辑像素 → 物理像素。

    界面里所有硬编码尺寸都经它换算：100% 缩放时原样返回，150% 时放大 1.5 倍。
    注意 Tk 的 `tk scaling` 只会自动缩放「以磅为单位」的字体；
    像素尺寸（含编辑器的负数字号）必须自己换算。
    取整用「四舍五入」而不是 round()，避免 .5 被银行家舍入到偶数。
    """
    return int(value * SCALE + 0.5)


# 写作区字体候选：(显示名, 该字体的若干别名)。
# 中文 Windows 的 font.families() 会返回本地化名称（如「楷体」而不是 KaiTi），
# 英文系统则返回拉丁名，所以每个候选都列出两种写法，命中任意一个即视为已安装。
FONT_CANDIDATES: list[tuple[str, tuple[str, ...]]] = [
    ("微软雅黑", ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑")),
    ("等线", ("DengXian", "等线")),
    ("黑体", ("SimHei", "黑体")),
    ("宋体", ("SimSun", "宋体")),
    ("楷体", ("KaiTi", "楷体")),
    ("仿宋", ("FangSong", "仿宋")),
    ("华文中宋", ("STZhongsong", "华文中宋")),
    ("华文楷体", ("STKaiti", "华文楷体")),
    ("幼圆", ("YouYuan", "幼圆")),
    ("隶书", ("LiSu", "隶书")),
    ("霞鹜文楷", ("LXGW WenKai", "霞鹜文楷", "LXGW WenKai Screen")),
    ("思源宋体", ("Source Han Serif SC", "思源宋体", "Noto Serif CJK SC")),
    ("更纱黑体", ("Sarasa Gothic SC", "更纱黑体")),
    ("Consolas", ("Consolas",)),
]

DEFAULT_FONT_ALIASES: tuple[str, ...] = ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑")

FONT_SAMPLE = "今天的天气很好，我写下这段话。"
FONT_SAMPLE_LATIN = "Aa Bb Cc 0123 永字八法"

# 浅色主题配色
NAV_BG = "#F1F0ED"          # 左侧浅灰导航
NAV_HOVER = "#E6E4DF"
CARD_AREA_BG = "#F8F7F5"    # 中间卡片区
CARD_BG = "#FFFFFF"         # 卡片
CARD_SHADOW = "#E6E3DD"     # 柔和阴影
CARD_SELECTED = "#3B6FE0"   # 选中蓝色
CARD_SELECTED_SUB = "#D7E2FA"
EDITOR_BG = "#FFFFFF"       # 右侧纯白编辑器
TEXT = "#23211E"
MUTED = "#8E8A83"
BORDER = "#E7E4DE"
ACCENT = "#3B6FE0"

NAV_W = px(178)
CARDS_W = px(306)
GUTTER_W = px(46)

# ---- 「内容行宽」的新规则（用户第 12 轮）------------------------------------
# 基准是**显示器宽度**（用户明确选的），不是窗口宽度。设屏幕宽 S、编辑区宽 C、
# 内容行宽比例 r，则正文块的目标宽度
#
#     T = min(r·S,  C − 2×EDITOR_SIDE_RATIO×S)
#
# 两种情形是同一个式子的两端：
#   · 编辑区够宽（C ≥ (r + EDITOR_SHARE_HEADROOM)·S）→ T = r·S，正文按设定比例显示，
#     两侧平分剩下的空白（用户：「文本区按设定的最高比例显示，两侧平均分配空白」）。
#   · 编辑区不够宽 → T = C − 10%·S，两侧各留 5% 等比例缩小。
# 两边在 C = (r + 10%)·S 处正好接上，没有跳变。
#
# 副作用要知道：编辑区窄的时候（三栏布局下 C 通常只有屏幕的 40% 上下），
# 第二档生效、`line_width` 设置**不起作用**——正文块就是「编辑区宽 − 10%·屏幕」。
# 想让设定值真的生效，窗口得宽到 C ≥ (r+10%)·S。
EDITOR_SIDE_RATIO = 0.05        # 编辑区两侧各留的比例（相对屏幕宽）
EDITOR_SHARE_HEADROOM = 0.10    # 「编辑区占屏幕比例」超出 行宽+这个 才算够宽
EDITOR_MIN_SHARE = 0.40         # 软件最小宽度：让编辑区至少占屏幕的这个比例

# 顶部栏（左侧编辑状态 + 右侧「专注模式」）高度。常显，不参与自动收起。
TOP_BAR_HEIGHT = 46

# 编辑区底部的呼吸空间：滚到底时最后一行下面留出这么多（**视口高度的比例**）。
# 用户要求 25%。写长文时最后一行贴着窗口下沿很难受，光标停在末尾时连「下一行」
# 都看不见。用最后一行上的 `spacing3` 标签实现——它是**显示层**的空白，不进正文、
# 不进存盘、也不影响导出（导出读的是纯文本，自己另排一套版）。
# **留白必须挂在「末尾那个空行」上**，不能挂在有正文的那一行上：Tk 画选中高亮
# 时把整个显示行盒子（含 `spacing3`）一起涂色，挂在正文行上就会出现
# 「选中最后一行 → 一大块蓝盖住留白」。所以正文末尾要保证有一个空行，
# 见 `_ensure_trailing_newline`。
EDITOR_BOTTOM_PAD_RATIO = 0.25

# 标题徽标：各级标题字号与正文一致后，靠左侧的 H1/H2/H3 标记区分层级
HEADING_LABELS = {"h1": "H1", "h2": "H2", "h3": "H3",
                  "h4": "H4", "h5": "H5", "h6": "H6"}
HEADING_LABEL_W = px(30)        # 徽标绘制区宽度
HEADING_LABEL_GAP = px(8)       # 徽标右边缘与标题正文之间的间距
HEADING_LABEL_COLOR = "#A9A49B"  # 比行号更浅，避免抢正文的注意力
HEADING_LABEL_SIZE = 9          # 逻辑像素字号

# 卡片列表最多读入几行正文。**取大一些**：正文里的回车换行在预览里会变成一个空格
# （见 _card_excerpt），几行源文本往往只折成一两个显示行，读少了卡片会显得很空。
# 真正的「显示几行」由 CARD_EXCERPT_ROWS 按实测高度裁剪，这里只是给足素材。
CARD_EXCERPT_LINES = 40
# 卡片最多渲染几个「显示行」——长行会自动折行，按显示行算才能避免最后一行只剩一个标点。
# 用户要求「调低预览框显示高度，显示 4 行内容即可」：预览栏是拿来扫一眼挑文稿的，
# 6 行太占地方，一屏看不到几篇。
CARD_EXCERPT_ROWS = 4

# 预览栏的字体固定，**不跟随编辑区的字体设置**：编辑区调字号是为了自己写着舒服，
# 预览栏要的是「一眼扫过去能看清哪篇是哪篇」，两者诉求不同（用户要求）。
CARD_FONT_FAMILY = "Microsoft YaHei"
CARD_FONT_SIZE = 16             # 逻辑像素，走 px() 换算
# 相邻卡片之间的间隔。原来是 px(12)，用户要求减半。
CARD_GAP = px(6)

# 文稿列表的排序方式（键要和 storage.SORT_KEYS 一致）。时间类一律「新的在前」，
# 文件名里带日期时按名称排也正好是新的在前。
SORT_LABELS: tuple[tuple[str, str], ...] = (
    ("name", "按名称"),
    ("created", "按创建时间"),
    ("modified", "按修改时间"),
)
SORT_LABEL_BY_KEY = dict(SORT_LABELS)

# ---------- 语法配色 ----------
# 记号本身（隐藏源码时露出的那部分）统一用一种更浅的灰，其余语法元素各有颜色，
# 这样一眼能分清「这是标题」「这是链接」「这是代码」，而不是满屏一种灰。
SYNTAX_COLOR = "#B3AEA6"        # 光标行露出的 Markdown 记号
CODE_BG = "#F1F0EC"             # 行内代码与围栏代码块底色
CODE_TEXT = "#B5396B"           # 行内代码文字（偏洋红，和链接区分）
CODEBLOCK_TEXT = "#3F4A56"      # 代码块文字（冷灰，块内不逐段上色）
HIGHLIGHT_BG = "#FFF3C4"        # ==高亮==
HIGHLIGHT_TEXT = "#8A6412"      # 高亮文字，压在浅黄底上仍要清楚
LINK_COLOR = "#2F6BD8"          # 链接文字与下划线
IMAGE_COLOR = "#7A6BB5"         # 图片说明（紫，和链接区分）
STRIKE_COLOR = "#A8A29A"        # 删除线文字比正文淡，划过之后仍能看清

# 各级标题的颜色：从深到浅，配合加粗与留白，比单纯加粗更容易分辨层级
HEADING_COLORS = {
    1: "#1F3864",
    2: "#2C5282",
    3: "#31688E",
    4: "#3D7A87",
    5: "#4A8A7B",
    6: "#5C8A5C",
}

# 粗体与斜体：都留在暖色里，但色相和明度都要拉开。
# ① 不能借用标题那支蓝绿——粗体原来直接用了 `HEADING_COLORS[3]`，正文里一段加粗
#    看着就是个小标题（用户报的）。
# ② 也不能只在暖色里换个近邻——原来是砖红 #A2452E 与赭褐 #8C6239，色相只差 20°、
#    明度也接近，同一段文字里几乎分不出哪个是粗、哪个是斜（用户报的）。
# 现在深红更沉、琥珀金更亮：色相差约 36°、明度也拉开了，粗体「重」、斜体「亮」。
# 注意别往冷色跑——标题、链接、序号、图片说明已经把蓝/紫占满了。
BOLD_COLOR = "#B3261E"          # 粗体：深红，重
ITALIC_COLOR = "#A87514"        # 斜体：琥珀金，亮

# 列表：最多支持几层缩进、每层缩进多少逻辑像素、标记与正文之间的间距
LIST_MAX_LEVEL = 4
LIST_INDENT = 20
LIST_MARKER_GAP = 10
# 源码里一层缩进写几个空格。**必须和 `_indent_level` 的换算式一致**（那边是
# `空格数 // 2`），否则 Tab 缩进一层、解析出来的层级却是另一层。
# 注意这和上面 `LIST_INDENT`（每层 20 逻辑**像素**的视觉缩进）是两码事。
LIST_INDENT_STEP = 2
LIST_MARKER_R = 3               # 项目符号半径（逻辑像素）
# 每层项目符号换一种颜色，层级一眼可辨（0 层实心、1 层空心、2 层方块）
BULLET_COLORS = ("#3B6FE0", "#2E9E6B", "#D9822B", "#8E5BD0", "#3F9EB8")
ORDERED_NUMBER_COLOR = "#3B6FE0"
TASK_BOX_COLOR = "#B9B4AB"
TASK_CHECK_COLOR = "#2E9E6B"    # 勾选后的对勾用绿色，比蓝色更像「完成」
QUOTE_BAR_COLOR = "#7BA7D9"
QUOTE_BAR_W = 3
QUOTE_TEXT = "#54657A"          # 引用正文偏冷灰蓝，和普通正文区分
HR_COLOR = "#D5D0C8"
HR_THICKNESS = 1

# 表格：表头底色、斑马纹、边框
TABLE_HEAD_BG = "#EEF3FB"       # 表头浅蓝底
TABLE_HEAD_TEXT = "#1F3864"     # 表头文字用 H1 那支深蓝
TABLE_STRIPE_BG = "#FAFAF8"     # 偶数行浅底（斑马纹）
TABLE_BORDER = "#D9DEE8"
TABLE_PIPE_COLOR = "#C6CEDD"    # 行内竖线（比外框略浅，不跟正文抢注意力）
TABLE_BORDER_W = 1

# 控件表面色（设置窗口里的按钮、可点的行）。单独列出来是因为深色主题下它们
# 不能沿用卡片白底——深色主题里「比卡片再亮一点」才是正常的按钮观感。
SURFACE = "#EFEDE8"             # 按钮底色
SURFACE_ALT = "#F7F6F4"         # 可点击的行（字体行）、输入框、预览块
SURFACE_HOVER = "#F0EEE9"       # 上面这些被鼠标扫过时
ACCENT_ACTIVE = "#2F5CC0"       # 强调色被按下时
ON_ACCENT = "#FFFFFF"           # 压在强调色上的文字（主按钮）
SELECTION_BG = "#DCE6FC"        # 文件夹树选中行
EDITOR_SELECTION_BG = "#CFDDFB"  # 编辑区选中文字
CARD_SELECTED_TEXT = "#FFFFFF"  # 选中的文稿卡片上的文字

# ---------- 主题 ----------
# 上面那些常量就是**浅色主题**，也是唯一的真值来源；这里只写深色的覆盖值。
# 换主题 = 把这些名字重新灌进模块全局（`apply_theme`），所有代码都直接引用
# `NAV_BG` / `TEXT` 这类名字，不必改成字典查找。
THEME_KEYS: tuple[str, ...] = (
    "NAV_BG", "NAV_HOVER", "CARD_AREA_BG", "CARD_BG", "CARD_SHADOW",
    "CARD_SELECTED", "CARD_SELECTED_SUB", "EDITOR_BG", "TEXT", "MUTED",
    "BORDER", "ACCENT", "SURFACE", "SURFACE_ALT", "SURFACE_HOVER",
    "ACCENT_ACTIVE", "ON_ACCENT", "SELECTION_BG", "EDITOR_SELECTION_BG",
    "CARD_SELECTED_TEXT",
    "SYNTAX_COLOR", "CODE_BG", "CODE_TEXT", "CODEBLOCK_TEXT",
    "HIGHLIGHT_BG", "HIGHLIGHT_TEXT", "LINK_COLOR", "IMAGE_COLOR",
    "STRIKE_COLOR", "HEADING_COLORS", "HEADING_LABEL_COLOR",
    "BOLD_COLOR", "ITALIC_COLOR", "BULLET_COLORS", "ORDERED_NUMBER_COLOR",
    "TASK_BOX_COLOR", "TASK_CHECK_COLOR", "QUOTE_BAR_COLOR", "QUOTE_TEXT",
    "HR_COLOR", "TABLE_HEAD_BG", "TABLE_HEAD_TEXT", "TABLE_STRIPE_BG",
    "TABLE_BORDER", "TABLE_PIPE_COLOR",
)

DEFAULT_THEME = "light"
THEME_LABELS: tuple[tuple[str, str], ...] = (("light", "浅色"), ("dark", "深色"))
THEME_KEYS_SET = tuple(key for key, _label in THEME_LABELS)

#: 深色主题的覆盖值。挑色的原则和浅色一致：层级靠明度、语法各有色相，
#: 但整体压暗——底色比卡片暗、卡片比正文亮，避免大块纯黑（纯黑配白字太刺眼）。
_DARK_COLORS: dict[str, object] = {
    "NAV_BG": "#1E1F22",
    "NAV_HOVER": "#2A2C31",
    "CARD_AREA_BG": "#17181A",
    "CARD_BG": "#232427",
    "CARD_SHADOW": "#0F1012",
    "CARD_SELECTED": "#3B6FE0",
    "CARD_SELECTED_SUB": "#2B3A5C",
    "EDITOR_BG": "#1B1C1F",
    "TEXT": "#E8E6E3",
    "MUTED": "#98948C",
    "BORDER": "#33353A",
    "ACCENT": "#6C9BF5",
    "SURFACE": "#2C2E33",
    "SURFACE_ALT": "#26282C",
    "SURFACE_HOVER": "#34373D",
    "ACCENT_ACTIVE": "#5B8CF0",
    # 深色下 ACCENT 是亮蓝，白字压上去几乎糊成一片 → 反过来用近黑
    "ON_ACCENT": "#10131A",
    "SELECTION_BG": "#2E3D5E",
    "EDITOR_SELECTION_BG": "#33486E",
    "CARD_SELECTED_TEXT": "#FFFFFF",
    "SYNTAX_COLOR": "#6B6862",
    "CODE_BG": "#26282C",
    "CODE_TEXT": "#F094BC",
    "CODEBLOCK_TEXT": "#C4CBD6",
    "HIGHLIGHT_BG": "#4A3F1B",
    "HIGHLIGHT_TEXT": "#F0D68A",
    "LINK_COLOR": "#7FB0FF",
    "IMAGE_COLOR": "#B9A8F0",
    "STRIKE_COLOR": "#7C7871",
    # 标题：浅色那套是「深蓝→青→绿」，压暗背景之后要整体提亮才看得清
    "HEADING_COLORS": {
        1: "#9DC0F0",
        2: "#8FB4E4",
        3: "#83B6D8",
        4: "#84C4CE",
        5: "#8ACBBE",
        6: "#9BCB9B",
    },
    "HEADING_LABEL_COLOR": "#6E6A64",
    "BOLD_COLOR": "#FF8073",
    "ITALIC_COLOR": "#E0B44C",
    "BULLET_COLORS": ("#6C9BF5", "#5FCF9B", "#F0A85C", "#B79BF0", "#6CC6DC"),
    "ORDERED_NUMBER_COLOR": "#6C9BF5",
    "TASK_BOX_COLOR": "#6E6A64",
    "TASK_CHECK_COLOR": "#5FCF9B",
    "QUOTE_BAR_COLOR": "#4E7BB5",
    "QUOTE_TEXT": "#A8B6C8",
    "HR_COLOR": "#3A3C41",
    "TABLE_HEAD_BG": "#26303F",
    "TABLE_HEAD_TEXT": "#BBD0F0",
    "TABLE_STRIPE_BG": "#1F2124",
    "TABLE_BORDER": "#3A3F49",
    "TABLE_PIPE_COLOR": "#4A5261",
}

#: `THEMES["light"]` 在这里**快照**一次（而不是每次现取全局变量）：
#: 换了深色主题之后再取 `THEMES["light"]`，拿到的仍然是那套浅色。
#: 导出长图就靠它——用户要求导出图不跟主题走，永远浅色。
THEMES: dict[str, dict[str, object]] = {
    "light": {key: globals()[key] for key in THEME_KEYS},
    "dark": dict(_DARK_COLORS),
}


def apply_theme(name: str) -> str:
    """把某个主题的颜色灌进模块全局，返回真正生效的主题名。

    所有绘制代码都直接引用 `NAV_BG` / `TEXT` 这类**模块级名字**，所以换主题
    只要重新绑定这些名字，再重建一遍界面即可——不必把上百处引用改成字典查找。
    名字不认识就退回浅色，绝不抛异常（state.json 是用户手边可能被改坏的文件）。
    """
    if name not in THEMES:
        name = DEFAULT_THEME
    globals().update(THEMES[name])
    return name

# ---------- Markdown 语法 ----------

# 鼠标滚轮：Tk 自带的 Text 绑定是「每格 %D/3 像素」，这里照同一个公式算完再乘 6
# （相对 Tk 默认 6 倍，相对上一版的 2 倍就是 3 倍）。
# 用倍数而不是「一格固定滚几行」，是因为高精度滚轮一个事件可能只有 40 而不是 120，
# 按公式走才能保证不管什么滚轮、什么 DPI，都正好是同一个倍数。
EDITOR_WHEEL_FACTOR = 6

# 两列列表（文稿卡片 / 文件夹树）走的是「单位」而不是像素：
# 一单位 = 视口的十分之一。跟着编辑区一起调快，三处手感才一致。
LIST_WHEEL_UNITS = 3

# 导出长图的尺寸（EXPORT_WIDTH / EXPORT_MARGIN / EXPORT_BODY_SIZE /
# EXPORT_MAX_HEIGHT）定义在 image_export 里，这里直接引用，避免两处各写一份：
# 宽度按手机屏宽定死，字号按「手机上一行正好 20 个汉字」定，长度由内容决定，
# 只有超长的文档才分张。

HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*)$")
HR_RE = re.compile(r"^[ \t]{0,3}(?:-[ \t]*-[ \t]*-[ \t\-]*"
                   r"|\*[ \t]*\*[ \t]*\*[ \t\*]*"
                   r"|_[ \t]*_[ \t]*_[ \t_]*)[ \t]*$")
FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})[ \t]*(\S*)[ \t]*$")
QUOTE_RE = re.compile(r"^[ \t]{0,3}((?:>[ \t]?)+)(.*)$")
TASK_RE = re.compile(r"^([ \t]*)([-*+])[ \t]+\[([ xX])\][ \t]+")
BULLET_RE = re.compile(r"^([ \t]*)([-*+])[ \t]+")
ORDERED_RE = re.compile(r"^([ \t]*)(\d{1,9})([.)])[ \t]+")
INLINE_CODE_RE = re.compile(r"`([^`\n]+?)`")
LINK_RE = re.compile(r"(!?)\[([^\]\n]*)\]\(([^)\n]*)\)")
EMPHASIS_PATTERNS = (
    ("bold", re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")),
    ("bold", re.compile(r"(?<![A-Za-z0-9_])__(?=\S)(.+?)(?<=\S)__(?![A-Za-z0-9_])")),
    ("italic", re.compile(r"(?<!\*)\*(?!\*)(?=\S)([^*\n]+?)(?<=\S)\*(?!\*)")),
    ("italic", re.compile(r"(?<![A-Za-z0-9_])_(?!_)(?=\S)([^_\n]+?)(?<=\S)_(?![A-Za-z0-9_])")),
    ("strike", re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")),
    ("highlight", re.compile(r"==(?=\S)(.+?)(?<=\S)==")),
)

# 表格：GFM 竖线表格。对齐分隔行决定整列的对齐方式（:--- / :---: / ---:）。
# 单元格里的竖线可以用 \| 转义，先按转义切分再还原。
TABLE_SEP_CELL_RE = re.compile(r"^:?-{1,}:?$")

# 所有由 Markdown 解析产生的标签，重新解析前要整体清掉
EDITOR_TAGS = (
    "h1", "h2", "h3", "h4", "h5", "h6",
    "bold", "italic", "strike", "highlight", "code", "codeblock",
    "link", "image", "quote",
    "li0", "li1", "li2", "li3", "li4",
    "table_head", "table_cell", "table_stripe", "table_pipe",
    "syntax", "syntax_current", "syntax_marker",
)

# 增量重解析的比对窗口：只读光标上下各这么多行，用来定位这次到底改了哪几行。
# 窗口内对不齐（比如一次粘进来几十行）就退回整篇重解析——那种情况本来就少见，
# 而且粘进来的内容本来就要整段重排。
INCREMENTAL_WINDOW = 48
# 判断改动有没有动到表格时，往两边多看这么多行。表格是「连续行」语法，
# 光看改动那一行判断不出表格有没有变长变短。
TABLE_LOOKAROUND = 64


def list_indent(level: int) -> int:
    """第 level 层列表的左缩进（逻辑像素）。"""
    return (min(level, LIST_MAX_LEVEL) + 1) * LIST_INDENT


def is_real_display_box(box, line_height: int) -> bool:
    """Tk 给的 `dlineinfo` 盒子是不是**真的量到了这一显示行**。

    行首记号被 elide 时（`1. `、`> `、`# ` 平时都藏着），宽度会算成 0——量到的
    是那个 elided chunk，它没有字，宽度自然是 0。这时要分两种情况，**靠高度分**：

    * **空行、只有记号的行**（整行都没有可见的字，而且不折行）：Tk 给的是**显示行**
      本身的盒子，y / 高 / 基线都是对的（空行实测 `(189, 93, 0, 51, 36)`，正好接着
      上一行），照用即可。这种行的高度就是一个显示行的高度 `line_height`。
    * **折行的显示行**：Tk 给的是行首那个 elided chunk 的盒子，y 比真上沿还**高**
      （高出的量正好是行距那一截），高只剩行距、基线是行距的一半。行距一定小于
      一个显示行的高度，所以 `box[3] < line_height`。

    以前用「高度小于 6 像素」这种**绝对阈值**判，在默认设置下能兜住（行距只有 2），
    把行高调到 34（行距 16）就整个漏掉——用户报的「序号折行后显示不全」就是这么
    复发的。改看宽度 + 相对高度之后，与字号行高都无关了。
    见 tests/test_markdown_syntax.py 的 TallLineHeightWrappedLineGeometryTests。

    空行必须走「照用」这条，不能也送去重新量：一来它的盒子本来就是对的，二来
    每多量一次就是一次跨语言调用，而 `_visible_lines` 是滚动时每帧都要走的热路径
    （守它的是 tests/test_performance.py 的两条）。
    """
    if box is None:
        return False
    if box[2] > 0:
        return True
    return box[3] >= line_height


def fence_step(line: str, fence: tuple[str, int] | None):
    """推进围栏代码块状态，返回 (新状态, 这一行是不是围栏分隔线)。

    CommonMark 规定闭合围栏必须与开启围栏**同字符且不短于**它，所以
    用四个反引号开的块里可以正常写三个反引号作为内容。只按「遇到围栏就取反」
    处理的话，文档里想示范一段围栏代码就会被自己截断。
    """
    match = FENCE_RE.match(line)
    if match is None:
        return fence, False
    marker = match.group(1)
    char, length = marker[0], len(marker)
    if fence is None:
        return (char, length), True
    if char == fence[0] and length >= fence[1]:
        return None, True
    return fence, False      # 更短的同类围栏是代码内容，不是分隔线


def scan_fences(lines: list[str], fence: tuple[str, int] | None):
    """逐行推进围栏状态机。

    返回 `(进入每行之前的围栏状态, 每行是否按代码块渲染, 每行是否围栏分隔线, 走完后的状态)`。

    整篇重解析与增量重解析原来各抄一遍这个循环。它是全篇最容易写错的一段——
    `in_code` 判的是「**进入**本行时已经在围栏里」**或**「本行自己就是围栏分隔线」，
    所以开栅栏那行和闭栅栏那行都按代码块渲染；不是「本行处理完之后的状态」。
    合成一个函数之后两条路径共用同一份语义，改一处就够。

    `fence` 是起始状态：整篇重解析传 `None`，增量重解析从缓存里那一行之前的状态接着走。
    """
    entering: list[tuple[str, int] | None] = []
    in_code: list[bool] = []
    delimiters: list[bool] = []
    for line in lines:
        entering.append(fence)
        new_fence, is_delimiter = fence_step(line, fence)
        in_code.append(fence is not None or is_delimiter)
        delimiters.append(is_delimiter)
        fence = new_fence
    return entering, in_code, delimiters, fence


def _indent_level(indent: str) -> int:
    """缩进折算成层级：两个空格（或一个 Tab）算一层。"""
    spaces = 0
    for char in indent:
        spaces += 4 if char == "\t" else 1
    return min(LIST_MAX_LEVEL, spaces // 2)


# ---------- 表格 ----------

def _looks_like_table_row(line: str) -> bool:
    """这一行像不像表格的一行：去掉两侧竖线后至少还有一个分隔竖线。"""
    stripped = line.strip()
    if not stripped or "|" not in stripped:
        return False
    return _split_row(stripped) is not None


def _split_row(line: str) -> list[str] | None:
    """把 `| a | b |` 切成 ["a", "b"]；不像表格行时返回 None。

    两侧都有竖线的形式（`| 1 |`）哪怕只有一个格子也算表格行——单列表格也要能用。
    先按转义竖线 `\\|` 占位，切分后再还原，这样单元格里可以正常写竖线。
    """
    stripped = line.strip()
    if not stripped or "|" not in stripped:
        return None
    framed = stripped.startswith("|") and stripped.endswith("|") and len(stripped) > 1
    if framed:
        stripped = stripped[1:-1]
    if "|" not in stripped and not framed:
        return None
    placeholder = "\x00"
    parts = stripped.replace("\\|", placeholder).split("|")
    return [part.replace(placeholder, "|").strip() for part in parts]


def _cell_spans(line: str) -> list[tuple[int, int]]:
    """表格行里每一格文字在行内的 [起, 止) 位置（不含竖线与两侧空白）。

    用来给单元格内部单独跑一遍行内语法：格子里的粗体、链接、行内代码
    要照常生效，而不是被表格样式盖掉。
    """
    spans: list[tuple[int, int]] = []
    depth_start = None
    for position, char in enumerate(line):
        if char == "|":
            if depth_start is not None:
                spans.append(_trim_span(line, depth_start, position))
            depth_start = position + 1
    if depth_start is not None:
        spans.append(_trim_span(line, depth_start, len(line)))
    return [span for span in spans if span[1] > span[0]]


def _trim_span(line: str, start: int, end: int) -> tuple[int, int]:
    """把一段区间两侧的空白修掉。"""
    while start < end and line[start] in " \t":
        start += 1
    while end > start and line[end - 1] in " \t":
        end -= 1
    return start, end


def _table_separator(line: str) -> list[str] | None:
    """分隔行 `| --- | :--: |` → ["", "center", "right"] 之类的对齐列表。

    每格必须只由 `-` 和可选的 `:` 组成，否则这一行不是分隔行。
    """
    cells = _split_row(line)
    if not cells or not all(TABLE_SEP_CELL_RE.match(cell) for cell in cells):
        return None
    alignments: list[str] = []
    for cell in cells:
        left, right = cell.startswith(":"), cell.endswith(":")
        if left and right:
            alignments.append("center")
        elif right:
            alignments.append("right")
        elif left:
            alignments.append("left")
        else:
            alignments.append("none")
    return alignments


class TableInfo:
    """一段连续的表格：表头、对齐方式、数据行，以及每行在文档里的行号。

    `rows` 里第 0 项是表头。`alignments` 与列一一对应；`line_numbers` 与
    `rows` 一一对应，用来把解析结果映射回编辑器里要上色的那些行。
    """

    __slots__ = ("rows", "alignments", "line_numbers", "start", "end")

    def __init__(self, rows: list[list[str]], alignments: list[str],
                 line_numbers: list[int], start: int, end: int) -> None:
        self.rows = rows
        self.alignments = alignments
        self.line_numbers = line_numbers
        self.start = start
        self.end = end

    @property
    def columns(self) -> int:
        return max((len(row) for row in self.rows), default=0)

    def cell(self, row: int, column: int) -> str:
        cells = self.rows[row]
        return cells[column] if column < len(cells) else ""


def _pad_row(cells: list[str], columns: int) -> list[str]:
    """把一行的格子补到 columns 个，缺的补空串（短行不能把表格撑歪）。"""
    return cells + [""] * (columns - len(cells)) if len(cells) < columns else cells[:columns]


def collect_tables(lines: list[str], in_code: list[bool] | None = None) -> list[TableInfo]:
    """扫描整篇文档，找出所有表格。

    判定条件（与 GFM 一致）：某一行含有竖线、且**下一行**是合法的分隔行。
    这样普通正文里偶尔出现的竖线不会被误当成表格。

    `in_code` 标记哪些行位于围栏代码块内——代码块里的竖线不能算表格。
    """
    tables: list[TableInfo] = []
    index = 0
    total = len(lines)
    while index < total:
        if in_code is not None and in_code[index]:
            index += 1
            continue
        header = _split_row(lines[index])
        if header is None or index + 1 >= total:
            index += 1
            continue
        if in_code is not None and in_code[index + 1]:
            index += 1
            continue
        alignments = _table_separator(lines[index + 1])
        if alignments is None:
            index += 1
            continue

        rows = [header]
        numbers = [index + 1]      # 行号从 1 开始
        cursor = index + 2
        while cursor < total:
            if in_code is not None and in_code[cursor]:
                break
            cells = _split_row(lines[cursor])
            if cells is None:
                break
            rows.append(cells)
            numbers.append(cursor + 1)
            cursor += 1

        columns = max(len(header), len(alignments))
        tables.append(TableInfo(
            [_pad_row(row, columns) for row in rows],
            _pad_row(alignments, columns),
            numbers,
            index + 1,
            cursor,
        ))
        index = cursor
    return tables


def table_row_map(tables: list[TableInfo]) -> dict[int, tuple[TableInfo, int, int]]:
    """把表格列表转成「行号 → (表格, 表内第几行, 是不是分隔行)」。

    分隔行没有自己的显示内容（它只是语法），所以要单独标出来让调用方整体隐藏。
    拆出来单独一个函数，是因为增量重解析只重扫了改动附近的一小段，
    拿到的是局部的表格列表，同样要转成这个映射。
    """
    mapping: dict[int, tuple[TableInfo, int, int]] = {}
    for table in tables:
        mapping[table.line_numbers[0]] = (table, 0, 0)
        separator_line = table.start + 1
        mapping[separator_line] = (table, -1, 1)
        for offset, number in enumerate(table.line_numbers[1:], start=1):
            mapping[number] = (table, offset, 0)
    return mapping


def build_table_rows(lines: list[str], in_code: list[bool] | None = None) -> dict[int, tuple[TableInfo, int, int]]:
    """行号 → (表格, 这是表内第几行, 是不是分隔行)。"""
    return table_row_map(collect_tables(lines, in_code))


class LineInfo:
    """一行的块级语法解析结果。"""

    __slots__ = ("kind", "level", "marker", "checked", "number", "body_empty")

    def __init__(self, kind: str = "text", level: int = 0,
                 marker: tuple[int, int] | None = None,
                 checked: bool = False, number: str = "",
                 body_empty: bool = False) -> None:
        self.kind = kind
        self.level = level
        # 需要隐藏（elide）的 Markdown 记号区间 [start, end)
        self.marker = marker
        self.checked = checked
        self.number = number
        # 记号后面还有没有内容（`2. ` 后面是空的 → True）。
        # 有序列表要用它：空项那一行如果照常把序号藏起来、改由画布绘制，
        # 光标会正好落在被藏起来的位置上，而画布是不透明的——光标就被序号盖住了。
        self.body_empty = body_empty


def split_document_lines(text: str) -> list[str]:
    """把整篇文本切成行。

    **只按 `\\n` 切，再把行尾的 `\\r` 剥掉**——这正是 Tk 的 Text 控件自己的行模型：
    它以 `\\n` 分行，`\\r` 只是留在行尾的普通字符。

    为什么不能用 `re.split(r"\\r\\n|\\r|\\n")`：文本里一旦出现**单独**的 `\\r`
    （不跟着 `\\n`），正则就会多切出一行，切出来的行数比控件实际的行数多，
    于是行号整体错位，样式、行号、徽标全都会套到错误的行上。实测过：
    同一份文本，正则切出 33 行，而 `index("end-1c")` 说是 32 行，按 `\\n` 切也是 32 行。

    也不能用 `str.splitlines()`：它还会在 `\\x0b`、U+2028 等处断开。
    """
    return [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]


def parse_block(line: str) -> LineInfo:
    """解析一行的块级语法。

    判定顺序很要紧：分隔线要排在无序列表前面，任务列表要排在无序列表前面
    （否则 `- [ ]` 会先被当成普通项目符号，`***` 也可能被误判）。

    注意：Tk 的 Text 控件内部用 `\\r` 作行分隔符，`get()` 取回来的整行会带一个
    结尾的 `\\r`，必须先剥掉，否则 `---`、`1.` 这类靠行尾锚定的规则全都匹配不上。

    表格是**跨行**语法，单看一行判断不出来（要下一行是分隔行才算），
    所以这里只负责把「表格行」标出来，真正的表格结构由 collect_tables() 组装。
    """
    line = line.rstrip("\r\n")
    heading = HEADING_RE.match(line)
    if heading:
        return LineInfo("heading", level=len(heading.group(1)),
                        marker=(0, heading.end(1) + 1))
    if HR_RE.match(line):
        return LineInfo("hr", marker=(0, len(line)))
    quote = QUOTE_RE.match(line)
    if quote:
        return LineInfo("quote", marker=(0, quote.end(1)))
    task = TASK_RE.match(line)
    if task:
        return LineInfo("task", level=_indent_level(task.group(1)),
                        marker=(0, task.end()), checked=task.group(3).lower() == "x",
                        body_empty=not line[task.end():].strip())
    bullet = BULLET_RE.match(line)
    if bullet:
        return LineInfo("bullet", level=_indent_level(bullet.group(1)),
                        marker=(0, bullet.end()),
                        body_empty=not line[bullet.end():].strip())
    ordered = ORDERED_RE.match(line)
    if ordered:
        return LineInfo("ordered", level=_indent_level(ordered.group(1)),
                        marker=(0, ordered.end()),
                        number=f"{ordered.group(2)}{ordered.group(3)}",
                        body_empty=not line[ordered.end():].strip())
    if _looks_like_table_row(line):
        return LineInfo("table")
    return LineInfo("text")


# ---------- 长图导出：把 Markdown 整理成「带样式的行」 ----------

def export_palette() -> Palette:
    """长图配色。

    **固定用浅色主题**（用户要求：深色主题只改界面，导出图保持浅色）。长图是拿来
    分享和打印的，浅底更通用。所以这里读的是 `THEMES["light"]` 那份快照，而不是
    当前的模块全局——否则切成深色之后导出图也会跟着变暗。

    快照在导入时就做好了，切主题只覆盖全局名字，不会动到它。
    """
    light = THEMES["light"]
    return Palette(
        background=light["EDITOR_BG"],
        text=light["TEXT"],
        heading=tuple(light["HEADING_COLORS"][level] for level in range(1, 7)),
        bold=light["BOLD_COLOR"],
        italic=light["ITALIC_COLOR"],
        strike=light["STRIKE_COLOR"],
        highlight_bg=light["HIGHLIGHT_BG"],
        highlight_text=light["HIGHLIGHT_TEXT"],
        code_bg=light["CODE_BG"],
        code_text=light["CODE_TEXT"],
        codeblock_text=light["CODEBLOCK_TEXT"],
        link=light["LINK_COLOR"],
        image=light["IMAGE_COLOR"],
        quote_bar=light["QUOTE_BAR_COLOR"],
        quote_text=light["QUOTE_TEXT"],
        bullet=light["BULLET_COLORS"],
        ordered=light["ORDERED_NUMBER_COLOR"],
        task_box=light["TASK_BOX_COLOR"],
        task_check=light["TASK_CHECK_COLOR"],
        hr=light["HR_COLOR"],
        badge=light["HEADING_LABEL_COLOR"],
        table_head_bg=light["TABLE_HEAD_BG"],
        table_head_text=light["TABLE_HEAD_TEXT"],
        table_stripe_bg=light["TABLE_STRIPE_BG"],
        table_border=light["TABLE_BORDER"],
    )


def _inline_spans(line: str) -> tuple[list[tuple[int, int, str]], list[tuple[int, int]]]:
    """行内语法 → (带样式的区间, 要丢掉的记号区间)。

    与 `JianJiApp._tag_inline` 同一套规则（先匹配到的区间会被 claim 掉），
    只是一个产出标签、一个产出片段——两边必须保持一致，否则导出的图
    会和屏幕上看到的不一样。

    记号区间对应编辑器里的 `syntax` 标签：屏幕上被 elide 藏起来，
    图里则直接不画，所以调用方必须真的把它们从文字里剔掉。
    """
    claimed: list[tuple[int, int]] = []
    spans: list[tuple[int, int, str]] = []
    marks: list[tuple[int, int]] = []

    def claim(start: int, end: int) -> None:
        if end > start:
            claimed.append((start, end))

    def blocked(start: int, end: int) -> bool:
        return any(start < other_end and end > other_start
                   for other_start, other_end in claimed)

    def apply(tag: str, inner: tuple[int, int], outer: tuple[int, int]) -> None:
        if inner[1] > inner[0]:
            spans.append((inner[0], inner[1], tag))
        # 记号 = 整段匹配去掉内容：**x** 的两个星号、`x` 的反引号
        for start, end in ((outer[0], inner[0]), (inner[1], outer[1])):
            if end > start:
                marks.append((start, end))
        claim(*outer)

    if "`" in line:
        for match in INLINE_CODE_RE.finditer(line):
            apply("code", (match.start(1), match.end(1)), match.span())
    if "[" in line:
        for match in LINK_RE.finditer(line):
            start, end = match.span()
            if blocked(start, end):
                continue
            label = (match.start(2), match.end(2))
            if label[1] > label[0]:
                spans.append((label[0], label[1],
                              "image" if match.group(1) == "!" else "link"))
            # 只丢 [ 与 ](url)，链接文字本身留着继续套强调
            for mark in ((start, label[0]), (label[1], end)):
                if mark[1] > mark[0]:
                    marks.append(mark)
            claim(start, label[0])
            claim(label[1], end)

    available = {
        "bold": "**" in line or "__" in line,
        "italic": "*" in line or "_" in line,
        "strike": "~~" in line,
        "highlight": "==" in line,
    }
    for tag, pattern in EMPHASIS_PATTERNS:
        if not available[tag]:
            continue
        # **用「搜索 + 手动推进」而不是 `finditer`。** 两处原因，都踩过：
        #
        # 1. 判「这对强调能不能用」时**只看那两对记号**，不看中间的内容。原来的写法是
        #    拿整个 `start..end` 去比：只要区间跟别人有重叠就整段放弃，于是「加粗里套
        #    行内代码」（`**未压缩的 `.wav`**`）会**整段失效**——行内代码先 claim 了
        #    `.wav` 那一小段，加粗的区间跟它重叠，加粗就被丢掉，四个星号原样画在图上。
        #    改成只看记号之后，加粗照常生效，行内代码那一小段仍由 `code` 盖住
        #    （`EXPORT_STYLE_ORDER` 里 `code` 排在最后 = 赢）。
        #
        # 2. `finditer` 的匹配**不重叠**，这会把后面的真记号一起吃掉。反例：
        #    ``**念的是正文**：`**`、后面还有 **真的加粗** 结尾。``
        #    第二段匹配从反引号里的那个 `**` 开头、把「真的加粗」那对星号当成自己的
        #    收尾——开头落在代码区间里，整段被丢掉，于是**真的那对加粗从没被匹配过**，
        #    四个星号全露出来。用户稿子里「用反引号举例说明 `**` 怎么写」的地方就会这样。
        #    改成被挡下时从 `start + 1` 重新搜，就会退回到真正的那个开头。
        #
        # 反过来 `` `a*b*c` `` 照样不会被斜体化：那两个 `*` 落在代码区间**内部**，
        # 记号判定同样把它们挡住。`**a*b*c**` 里那对 `*` 也落在加粗区间内部，行为不变。
        # 空区间不参与判定（`blocked` 对空区间会给出假阳性）。
        search_from = 0
        while True:
            match = pattern.search(line, search_from)
            if match is None:
                break
            start, end = match.span()
            inner = (match.start(1), match.end(1))
            if any(blocked(left, right) for left, right in
                   ((start, inner[0]), (inner[1], end)) if right > left):
                search_from = start + 1
                continue
            apply(tag, inner, (start, end))
            search_from = end
    return spans, marks


# 行内样式的叠加顺序，与 _configure_editor_tags 里创建标签的顺序一致：
# Tk 里「后创建的标签」赢，这里就「后应用的样式」赢。字体属性是整份覆盖的
# ——Tk 的 font 选项也是整份生效，`**a*b*c**` 这种嵌套按同一规则落到斜体。
EXPORT_STYLE_ORDER = ("bold", "italic", "strike", "highlight", "link", "image", "code")


def _style_of(tag: str, options: Options) -> dict:
    if tag == "bold":
        return {"family": options.family, "size": options.body_size,
                "bold": True, "italic": False, "color": BOLD_COLOR}
    if tag == "italic":
        return {"family": options.family, "size": options.body_size,
                "bold": False, "italic": True, "color": ITALIC_COLOR}
    if tag == "strike":
        return {"strike": True, "color": STRIKE_COLOR}
    if tag == "highlight":
        return {"background": HIGHLIGHT_BG, "color": HIGHLIGHT_TEXT}
    if tag == "link":
        return {"color": LINK_COLOR, "underline": True}
    if tag == "image":
        return {"family": options.family, "size": options.body_size,
                "bold": False, "italic": True, "color": IMAGE_COLOR}
    return {"family": options.mono_family, "size": options.mono_size,
            "bold": False, "italic": False, "color": CODE_TEXT,
            "background": CODE_BG}


def _export_pieces(line: str, base: dict, options: Options) -> list[Piece]:
    """把一行文字切成若干同样式片段。Markdown 记号本身不画（屏幕上也是藏着的）。"""
    if not line:
        return []
    spans, marks = _inline_spans(line)
    if not spans and not marks:
        return [Piece(text=line, **base)]

    dropped = [False] * len(line)
    for start, end in marks:
        for position in range(start, min(end, len(line))):
            dropped[position] = True
    tokens: list[list[str]] = [[] for _ in line]
    for start, end, tag in spans:
        for position in range(start, min(end, len(line))):
            tokens[position].append(tag)

    pieces: list[Piece] = []
    run_start: int | None = None
    for index in range(len(line) + 1):
        at_end = index == len(line)
        if run_start is not None and (at_end or dropped[index]
                                      or tokens[index] != tokens[run_start]):
            style = dict(base)
            for tag in EXPORT_STYLE_ORDER:
                if tag in tokens[run_start]:
                    style.update(_style_of(tag, options))
            pieces.append(Piece(text=line[run_start:index], **style))
            run_start = None
        if not at_end and not dropped[index] and run_start is None:
            run_start = index
    return pieces


def _export_table_row(table: TableInfo, table_id: int, options: Options) -> Row:
    """整张表收进一个 Row：列宽必须整张表一起算，否则各行会各算各的、列对不齐。"""
    cells: list[list[list[Piece]]] = []
    for index, raw in enumerate(table.rows):
        head = index == 0
        base = ({"family": options.family, "size": options.body_size,
                 "bold": True, "color": TABLE_HEAD_TEXT} if head else
                {"family": options.family, "size": options.body_size, "color": TEXT})
        cells.append([_export_pieces(cell, base, options) for cell in raw])
    return Row(kind="table", cells=cells, alignments=list(table.alignments),
               table_id=table_id)


def build_export_rows(text: str, options: Options) -> list[Row]:
    """整篇 Markdown → 长图渲染用的行列表。

    块级判定直接复用编辑器的解析（`parse_block` / `fence_step` /
    `build_table_rows`），所以「屏幕上什么样」和「导出什么样」不会各写一套。
    """
    lines = split_document_lines(text)
    while lines and not lines[-1].strip():      # 收尾的空行没必要画进图里
        lines.pop()
    if not lines:
        return []

    in_code: list[bool] = []
    delimiters: set[int] = set()
    fence: tuple[str, int] | None = None
    for number, line in enumerate(lines, start=1):
        new_fence, is_delimiter = fence_step(line, fence)
        in_code.append(fence is not None or is_delimiter)
        fence = new_fence
        if is_delimiter:
            delimiters.add(number)
    table_rows = build_table_rows(lines, in_code)

    body = {"family": options.family, "size": options.body_size}
    code = {"family": options.mono_family, "size": options.mono_size,
            "color": CODEBLOCK_TEXT, "background": CODE_BG}
    quote = dict(body, color=QUOTE_TEXT)

    rows: list[Row] = []
    emitted: set[int] = set()
    table_id = 0
    # 上一行产生的、还能吞并「缩进续行」的行（Markdown 的惰性续行）。
    # 列表项/段落写成两行、第二行缩进两格时，两行本来是**同一段**，
    # 折行要连着排——见 `_append_continuation`。
    absorber: Row | None = None
    for number, line in enumerate(lines, start=1):
        if in_code[number - 1]:
            # 围栏那两行 ``` 在屏幕上是藏起来的，图里也不画
            if number not in delimiters:
                # 代码块里**不做行内解析**：`*x*`、`~~x~~` 都是要照着写的代码内容，
                # 编辑器对这些行只套 codeblock 标签，图里也必须原样输出。
                rows.append(Row(kind="code", pieces=[Piece(text=line, **code)]))
            absorber = None
            continue

        entry = table_rows.get(number)
        if entry is not None:
            table, _row, is_separator = entry
            absorber = None
            if is_separator or id(table) in emitted:
                continue
            emitted.add(id(table))
            rows.append(_export_table_row(table, table_id, options))
            table_id += 1
            continue

        info = parse_block(line)
        previous = lines[number - 2] if number >= 2 else ""
        if (info.kind == "text" and line[:1].isspace() and absorber is not None
                and not _ends_with_hard_break(previous)):
            # 缩进续行：并进上一段，行首那两格只是 Markdown 的续行缩进，
            # 不是正文内容（以前会被当成正文画出来，图里凭空多出一小块缩进）。
            _append_continuation(
                absorber, line.strip(),
                quote if absorber.kind == "quote" else body, options)
            continue
        if info.kind == "heading":
            rows.append(Row(
                kind="heading", level=info.level,
                badge=HEADING_LABELS[f"h{info.level}"],
                pieces=_export_pieces(line[info.marker[1]:], dict(
                    body, bold=True, color=HEADING_COLORS[info.level]), options),
            ))
            absorber = None
        elif info.kind == "hr":
            rows.append(Row(kind="hr"))
            absorber = None
        elif info.kind == "quote":
            absorber = Row(kind="quote",
                           pieces=_export_pieces(line[info.marker[1]:], quote, options))
            rows.append(absorber)
        elif info.kind in ("bullet", "task", "ordered"):
            absorber = Row(
                kind=info.kind, level=info.level, checked=info.checked,
                number=info.number,
                pieces=_export_pieces(line[info.marker[1]:], body, options),
            )
            rows.append(absorber)
        elif not line.strip():
            rows.append(Row(kind="blank"))
            absorber = None
        else:
            # 行首缩进只是 Markdown 的排版空白，不是正文——但只在「这一行是
            # 上一行的续行」时才去掉（上一行非空）。孤立的缩进行原样留着，
            # 免得把用户特意缩进对齐的文字吃掉。
            # 硬换行的续行（上一行以两个空格结尾）走的也是这条路：
            # 那两格是缩进，不该在图里画成一小块留白。
            text_line = line.lstrip() if (line[:1].isspace() and previous.strip()) else line
            absorber = Row(kind="text", pieces=_export_pieces(text_line, body, options))
            rows.append(absorber)
    return rows


def _ends_with_hard_break(line: str) -> bool:
    """上一行是不是以「硬换行」结尾（两个以上空格，或一个反斜杠）。

    Markdown 用行尾两个空格表示「这里就是要换行」，那下一行**不是**续行，
    不能并进上一段——否则用户特意分开的两行会被粘成一段。
    """
    return line.endswith("\\") or line.endswith("  ")


def _append_continuation(row: Row, text: str, base: dict, options: Options) -> None:
    """把一行「缩进续行」并进上一段（Markdown 的惰性续行）。

    列表项或段落写成长短两行、第二行缩进两格时，两行本来就是**同一段**，
    折行必须连着排。以前每一行各成一个 `Row`，于是这一项被从中间截断：
    第一行末尾常常正好是个逗号，读起来就成了「逗号后面的文字换到了新的一行」
    ——用户报的「导出长图异常换行」就是这个。

    续行行首的缩进是 Markdown 的排版空白，不是正文，必须去掉。

    中文之间直接相接，不插空格；只有两侧都不是中日韩字符时才补一个空格
    （英文段落折行后需要那个空格）。
    """
    pieces = _export_pieces(text, base, options)
    if not pieces:
        return
    left = row.pieces[-1].text[-1:] if row.pieces and row.pieces[-1].text else ""
    right = pieces[0].text[:1]
    if left and right and not (_wide_char(left) or _wide_char(right)):
        pieces[0].text = " " + pieces[0].text
    row.pieces.extend(pieces)


def _wide_char(char: str) -> bool:
    """中日韩字符（含全角标点，`ord > 0x2E80`）：两个这样的字之间不该插空格。"""
    return ord(char) > 0x2E80


def _round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kwargs):
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def _card_excerpt(path: Path, max_lines: int = CARD_EXCERPT_LINES) -> str:
    """取卡片要显示的正文摘录：去掉 Markdown 标记，最多 max_lines 行。

    **行与行之间用空格接起来**，不是换行：一篇日记常常是一行一句，按行断的话
    卡片里一半宽度都是右边那片空白，而且 4 行就写满了。接成一段之后文字会自己
    折行铺满，同样高度能多显示一倍多的内容（用户要求：「正文中的回车换行在预览区
    显示为一个空格」「尽可能多的显示文字」）。

    第一行即使是 `# 一级标题` 也照收——用户明确要求标题也要出现在预览里
    （以前当成文档标题跳过了，可很多文稿的 H1 本身就是正文的第一句）。

    正文为空时退回文件名，保证卡片不会是一片空白。

    这里复用编辑器那套 parse_block，保证「卡片上看到的」和「编辑区渲染的」
    对同一份语法理解一致，不会出现一边认、一边不认的情况。
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return path.stem

    lines: list[str] = []
    fence: tuple[str, int] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        new_fence, is_delimiter = fence_step(line, fence)
        in_code = fence is not None or is_delimiter
        fence = new_fence
        if in_code:
            # 代码块（含围栏）对卡片摘录没有价值，整块跳过
            continue
        if not line:
            continue
        info = parse_block(line)
        if info.kind == "hr":
            continue                      # 分隔线没有文字内容
        if info.marker is not None:
            line = line[info.marker[1]:]  # 去掉标题/引用/列表标记
        line = LINK_RE.sub(lambda match: match.group(2), line)  # 链接、图片留文字
        line = re.sub(r"[*`_~=]", "", line)  # 强调、行内代码、删除线、高亮标记
        line = line.strip()
        if line:
            lines.append(line)
        if len(lines) >= max_lines:
            break

    if not lines:
        return path.stem
    return " ".join(lines)


#: Windows 文件名里不允许出现的字符 → 换成全角同形字。
#: 直接删掉会把 `第1章/第2节` 变成 `第1章第2节`，两截粘在一起读不出边界。
_FILENAME_FALLBACK = {
    "\\": "＼", "/": "／", ":": "：", "*": "＊",
    "?": "？", '"': "＂", "<": "＜", ">": "＞", "|": "｜",
}

#: Windows 保留设备名，不能拿来做文件名主干（`CON.md` 一样是保留的）。
_RESERVED_FILENAME_STEMS = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{index}" for index in range(1, 10)]
    + [f"LPT{index}" for index in range(1, 10)]
)

#: 文件名主干最多留多少个字符。标题可以写很长，但文件名太长在文稿列表里根本读不完，
#: 也容易撞上 Windows 单段 255 字符的上限。
FILENAME_MAX_CHARS = 80


def first_h1_title(text: str) -> str | None:
    """正文**第一行**是一级标题时返回标题文字，否则返回 None。

    只看第一行，不看后面：用户要的是「文件名 = 文档标题」，而标题就是开头那一行。
    开头先空一行再写标题也算「第一行不是标题」——那行标题在用户心里已经不是标题了。

    标题文字为空（只写了 `# `）时同样返回 None，调用方据此放弃改名。

    用 `parse_block` 而不是自己写正则，保证「哪里算标题」和编辑区渲染、卡片摘录
    是同一套判断，不会出现一边认标题、一边不认的情况。
    """
    lines = split_document_lines(text)
    if not lines:
        return None
    info = parse_block(lines[0])
    if info.kind != "heading" or info.level != 1 or info.marker is None:
        return None
    title = lines[0][info.marker[1]:].strip()
    return title or None


def title_to_filename(title: str) -> str | None:
    """把一级标题变成合法的文件名主干；清完什么都不剩就返回 None。

    Windows 禁 `\\ / : * ? " < > |` 和 0x00–0x1F 控制字符，且主干末尾不能是句点或
    空格。禁字符换全角同形字（见 `_FILENAME_FALLBACK`），控制字符直接删；连续空白
    压成一个空格；末尾的句点与空格削掉；超过 `FILENAME_MAX_CHARS` 截断；撞上保留
    设备名就补一个下划线。

    **清完为空必须返回 None，不能返回空串**：调用方据此放弃改名、让文件保住原名。
    标题被删空时把文件叫成 `.md`，是个谁也打不开的名字。
    """
    cleaned = "".join(
        _FILENAME_FALLBACK.get(char, "" if ord(char) < 32 and not char.isspace() else char)
        for char in title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(". ")
    # 截断之后还要再削一次：正好切在句点上就留下个 Windows 不接受的结尾
    cleaned = cleaned[:FILENAME_MAX_CHARS].rstrip(". ")
    if not cleaned:
        return None
    # 保留名的判定看**第一个点之前**那一段：`NUL.md` 在 Windows 上一样是保留的。
    # 下划线要插进那一段里（`NUL_.md`），加在末尾（`NUL.md_`）等于没改。
    head, dot, tail = cleaned.partition(".")
    if head.strip().upper() in _RESERVED_FILENAME_STEMS:
        cleaned = f"{head}_{dot}{tail}"
    return cleaned


class JianJiApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.folder: Path | None = None
        self.current_path: Path | None = None
        self.disk_signature: FileSignature | None = None
        # 上一次改名失败的 (规范化路径, 目标主干)。同一个文件往同一个目标改失败过就
        # 不再重试，免得每次自动保存都弹一遍「改名失败」（见 `_follow_title`）。
        self._rename_failed_for: tuple[str, str] | None = None
        self.dirty = False
        self.save_job: str | None = None
        self.settings_job: str | None = None
        self.watch_job: str | None = None
        # 音频导出在后台线程里跑（合成很慢），主线程只轮询进度
        self._speech_job: dict | None = None
        self._speech_poll: str | None = None
        self.file_paths: list[Path] = []
        self.scope_folder: Path | None = None
        self.trash_mode = False
        self.preview_mode = False
        self.trash_entries: list = []
        self.settings = get_settings()
        # 主题必须在建任何控件**之前**定下来：所有控件都是在建的那一刻读颜色的
        # （`bg=CARD_BG`、`create_text(fill=TEXT)`…），建完再改常量对它们没用。
        self.theme = apply_theme(self.settings["theme"])
        # 「排序」菜单里的单选状态；按钮上的字也跟着它走（见 set_sort_key）
        self.sort_var = tk.StringVar(master=root, value=self.settings["sort_key"])
        self.focus_mode = False
        self.settings_window: tk.Toplevel | None = None
        self.font_window: tk.Toplevel | None = None
        self._current_pad = -1
        # 屏幕宽（物理像素）。None = 读真实的显示器；测试/探针可以钉一个值，
        # 让「内容行宽」的断言不随跑测试那台机器而变（见 _screen_width）。
        self._screen_width_override: int | None = None
        self._min_window_width = -1         # 上次设的窗口下限（见 _apply_min_window_width）
        self._bottom_pad = -1               # 编辑区底部的呼吸空间（见 _apply_bottom_pad）
        self._bottom_pad_line = -1          # 留白标签当前挂在哪一行（换行了才重挂）
        self._cards_width = -1
        self._cards_content_height = 0      # 卡片列内容总高，滚到当前卡片时要用
        # 每张卡片的可点纵向范围 (上沿, 下沿)，画布坐标；重建时一起算好。
        # 点击命中按它判，不再靠 find_overlapping 去撞图元（见 _card_index_at）。
        self._card_bounds: list[tuple[int, int]] = []
        # 卡片的两层缓存，都只为了让 `refresh_files` 别每次重算：
        #   `_excerpt_cache`：文稿没改就复用上次的摘录（键含 mtime/size）
        #   `_trim_cache`：同一段摘录 + 同样宽度/高度预算 → 裁剪结果一样
        # 加上 `_cards_signature_cache`，列表没变时连重建都省掉（见 refresh_files）。
        self._excerpt_cache: dict = {}
        self._trim_cache: dict = {}
        self._cards_signature_cache: tuple | None = None
        self._cards_selected: int | None = None   # 当前画成选中的是第几张
        self._gutter_pending = False
        self._geometry_pending = False
        self._padding_busy = False
        self._margin_signature: tuple | None = None
        self._decor_pool: list[tk.Canvas] = []
        self._table_pool: list[tk.Canvas] = []
        self._font_families_cache: dict[str, str] | None = None
        # 整篇解析结果的缓存：文本一变就整体重算，光标移动与滚动直接复用。
        # 长文档下重解析要几十毫秒，缓存掉之后滚动就只剩画图的开销。
        self._doc_blocks: list[LineInfo] = []
        self._doc_in_code: list[bool] = []
        self._doc_tables: list[TableInfo] = []
        self._heading_lines: dict[int, str] = {}
        # 每行的记号区间 [(隐藏时标签, 光标行标签, 起, 止), ...]
        # 每项：`(隐藏用标签, 露源码用标签, start, end, 露源码时要配的缩进)`
        # 最后一格为 None 表示这个记号不影响排版宽度（表格竖线之类）；
        # 有值时是 `(lmargin1, lmargin2)`，已经减掉了记号的宽度。
        self._line_marks: dict[int, list[tuple[str, str, int, int, tuple | None]]] = {}
        # 量正文文字宽度用的字体对象（给行首记号的缩进补偿算宽度），
        # 字体或字号一变就作废，见 `_apply_typography`。
        self._body_font: "font.Font | None" = None
        self._cursor_line = 1
        # 增量重解析要用的「上一版」快照：每行的原文、进入这一行之前的围栏状态、
        # 以及这一行是不是围栏分隔线。有了它们，打字时只要比对光标附近的一小段
        # 就能定位改动，不必把整篇读出来重算。
        self._doc_lines: list[str] = []
        self._doc_fence: list[tuple[str, int] | None] = []
        self._doc_delimiter: list[bool] = []
        self._doc_table_rows: dict[int, tuple[TableInfo, int, int]] = {}

        self._build_ui()
        self._restore_state()
        self.watch_job = self.root.after(1500, self._watch_external_changes)

    # ---------- 窗口与布局 ----------

    def _build_ui(self) -> None:
        """建窗口 → 排版 → 字体 → 快捷键。

        **顺序不能换**：`_build_layout` 要往 `_build_window` 建出来的控件里放东西，
        `_apply_typography` 得先拿到那些控件才能配标签，`_bind_shortcuts` 最后绑。

        `__init__`（首次启动）与 `_rebuild_ui`（换主题整块重建）都走这一串，
        原来各抄一遍——将来加一步构建就得记得改两处。**换主题那条路在调它之前
        必须先 `root.unbind()`**，那是重建独有的步骤，不在这里（见 `_rebuild_ui`）。
        """
        self._build_window()
        self._build_layout()
        self._apply_typography()
        self._bind_shortcuts()

    def _build_window(self) -> None:
        self.root.title("简记")
        # 最小宽度按「编辑区至少占屏幕 EDITOR_MIN_SHARE」定（用户第 12 轮）：
        # 窗口下限 = 导航栏 + 文稿列表 + 屏幕的 40%。初始宽度也不小于它，
        # 否则窗口一建出来就被 minsize 顶一下、看起来是启动时跳了一下。
        minimum = NAV_W + CARDS_W + int(self._screen_width() * EDITOR_MIN_SHARE)
        self._min_window_width = minimum
        self.root.minsize(minimum, px(560))
        self.root.geometry(f"{max(px(1180), minimum)}x{px(760)}")
        self.root.configure(bg=CARD_AREA_BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        # 任何销毁路径（含测试直接 destroy）都要停掉外部变更轮询，
        # 否则定时器会在解释器销毁后触发，抛出 invalid command name。
        self.root.bind("<Destroy>", self._on_root_destroy, add="+")

    def _build_layout(self) -> None:
        self.root.grid_rowconfigure(0, weight=1)

        # 第 1 栏：浅灰导航
        self.nav = tk.Frame(self.root, bg=NAV_BG, width=NAV_W)
        self.nav.grid(row=0, column=0, sticky="nsew")
        self.nav.grid_propagate(False)
        self.nav.grid_columnconfigure(0, weight=1)
        self.nav.grid_rowconfigure(5, weight=1)

        tk.Label(
            self.nav, text="简记", bg=NAV_BG, fg=TEXT,
            font=(FAMILY, 17, "bold"), anchor="w", padx=px(20),
        ).grid(row=0, column=0, sticky="ew", pady=(px(22), px(18)))

        self.nav_buttons: dict[str, tk.Button] = {}
        nav_items = [
            ("new", "新建文稿", self.new_document),
            ("all", "全部文稿", self.show_all),
            ("settings", "设置", self.open_settings),
        ]
        for row, (key, label, command) in enumerate(nav_items, start=1):
            button = tk.Button(
                self.nav, text=label, command=command, bg=NAV_BG, fg=TEXT,
                activebackground=NAV_HOVER, activeforeground=TEXT, relief="flat",
                borderwidth=0, anchor="w", padx=px(20), pady=px(9), cursor="hand2",
                font=(FAMILY, 10),
            )
            button.grid(row=row, column=0, sticky="ew")
            self.nav_buttons[key] = button

        # 文件夹树
        tree_holder = tk.Frame(self.nav, bg=NAV_BG)
        tree_holder.grid(row=5, column=0, sticky="nsew",
                         padx=(px(10), px(6)), pady=(px(14), px(6)))
        tree_holder.grid_rowconfigure(1, weight=1)
        tree_holder.grid_columnconfigure(0, weight=1)
        folder_caption = tk.Label(
            tree_holder, text="文件夹", bg=NAV_BG, fg=MUTED, font=(FAMILY, 8),
            anchor="w", padx=px(10),
        )
        folder_caption.grid(row=0, column=0, sticky="ew", pady=(0, px(4)))
        # 这行灰色小标题也能右键（用户要求）。右键树里的某一项是「对那个文件夹做事」，
        # 右键标题是「对这一栏做事」——新建文件夹 / 打开日记文件夹 / 刷新，也就是
        # 「全部文稿」那一套菜单。**不切换当前文件夹**：右键标题只是想看菜单，
        # 不该把中间的列表整个换掉。
        folder_caption.bind("<Button-3>", self._on_folder_caption_right_click)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "JJ.Treeview", background=NAV_BG, fieldbackground=NAV_BG, foreground=TEXT,
            borderwidth=0, relief="flat", rowheight=px(26), font=(FAMILY, 10),
        )
        style.map(
            "JJ.Treeview",
            background=[("selected", SELECTION_BG)],
            foreground=[("selected", TEXT)],
        )
        style.layout("JJ.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

        self.folder_tree = ttk.Treeview(tree_holder, style="JJ.Treeview", show="tree", selectmode="browse")
        self.folder_tree.grid(row=1, column=0, sticky="nsew")
        self.folder_tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.folder_tree.bind("<Button-3>", self._on_tree_right_click)
        self.folder_tree.bind("<MouseWheel>", self._on_tree_wheel)
        self._tree_nodes: dict[Path | None, str] = {}
        self._tree_updating = False

        self.folder_label = tk.Label(
            self.nav, text="", bg=NAV_BG, fg=MUTED, font=(FAMILY, 8),
            anchor="w", justify="left", wraplength=NAV_W - px(32), padx=px(20),
        )
        self.folder_label.grid(row=6, column=0, sticky="ew", pady=(0, px(18)))

        # 第 2 栏：圆角卡片列表
        self.cards_panel = tk.Frame(self.root, bg=CARD_AREA_BG, width=CARDS_W)
        self.cards_panel.grid(row=0, column=1, sticky="nsew")
        self.cards_panel.grid_propagate(False)
        self.cards_panel.grid_rowconfigure(1, weight=1)
        self.cards_panel.grid_columnconfigure(0, weight=1)

        self.cards_title = tk.Label(
            self.cards_panel, text="全部文稿", bg=CARD_AREA_BG, fg=MUTED,
            font=(FAMILY, 10, "bold"), anchor="w", padx=px(18),
        )
        self.cards_title.grid(row=0, column=0, sticky="ew", pady=(px(22), px(10)))
        # 排序入口**不放在标题栏**：那会在标题这一行占掉一整列，而 Tk 的 grid 是
        # 「一列宽 = 该列所有行里最宽的那个」，于是下面卡片区的可用宽度被硬生生
        # 挤掉一截（实测面板 459px 里卡片只剩 267px，166px 白给了那个标签）。
        # 现在改成文稿列表的**右键菜单**里一个「排序：…」级联项，见 `_doc_menu`
        # 与 `_on_cards_right_click`——列表本身拿回整幅宽度。

        cards_holder = tk.Frame(self.cards_panel, bg=CARD_AREA_BG)
        # `columnspan=2`：标题栏以后要是再放别的东西到第 1 列，卡片区也不会被挤窄
        cards_holder.grid(row=1, column=0, columnspan=2, sticky="nsew")
        cards_holder.grid_rowconfigure(0, weight=1)
        cards_holder.grid_columnconfigure(0, weight=1)

        self.cards_canvas = tk.Canvas(
            cards_holder, bg=CARD_AREA_BG, highlightthickness=0, borderwidth=0,
        )
        self.cards_canvas.grid(row=0, column=0, sticky="nsew")
        # **不放滚动条**（用户要求）。滚动条同样是从卡片区里切掉一条宽度，而这一列
        # 只有卡片，滚轮已经能滚（`_on_cards_wheel`），拖拽条本身没什么用。
        # 键盘/程序化滚动仍走 `_on_cards_scrollbar`，只是不再有控件去调它。
        self.cards_canvas.bind("<MouseWheel>", self._on_cards_wheel)
        self.cards_canvas.bind("<Configure>", self._on_cards_configure)
        self.cards_canvas.bind("<Button-1>", self._on_cards_left_click)
        self.cards_canvas.bind("<Button-3>", self._on_cards_right_click)

        # 第 3 栏：纯白编辑器 + 右侧行号
        self.editor_panel = tk.Frame(self.root, bg=EDITOR_BG)
        self.editor_panel.grid(row=0, column=2, sticky="nsew")
        self.root.grid_columnconfigure(2, weight=1)
        self.editor_panel.grid_rowconfigure(1, weight=1)
        self.editor_panel.grid_columnconfigure(0, weight=1)

        # 顶部栏：左侧编辑状态、右侧「专注模式」。常显，固定高度免得随内容跳动。
        self.topbar = tk.Frame(self.editor_panel, bg=EDITOR_BG, height=px(TOP_BAR_HEIGHT))
        self.topbar.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.topbar.grid_propagate(False)
        self.topbar.grid_columnconfigure(0, weight=1)

        self.status_label = tk.Label(
            self.topbar, text="", bg=EDITOR_BG, fg=MUTED, font=(FAMILY, 9), anchor="w",
            padx=px(18),
        )
        self.status_label.grid(row=0, column=0, sticky="w", pady=px(13))

        # 「导出长图」不再占顶栏位置，挪到文稿右键菜单里（见 _doc_menu）。
        self.focus_button = tk.Button(
            self.topbar, text="专注模式", command=self.toggle_focus, bg=EDITOR_BG, fg=MUTED,
            activebackground=EDITOR_BG, activeforeground=ACCENT, relief="flat",
            borderwidth=0, padx=px(14), pady=px(6), cursor="hand2", font=(FAMILY, 9),
        )
        self.focus_button.grid(row=0, column=2, sticky="e", padx=(0, px(14)))

        editor_holder = tk.Frame(self.editor_panel, bg=EDITOR_BG)
        editor_holder.grid(row=1, column=0, columnspan=3, sticky="nsew")
        editor_holder.grid_rowconfigure(0, weight=1)
        editor_holder.grid_columnconfigure(0, weight=1)

        # 上下留白交给行距（spacing1/spacing3）管，正文自己不再加一圈空白——
        # 顶部栏下面原来还压着 px(28) 的空白，正文离顶栏太远（用户报的「上方留白
        # 区太大」）。Tk 的 `-pady` 只有对称一个值，减它上下一起减，正好也让
        # 滚到底时最后一行不至于悬在半空。
        # `wrap="char"` 不是随手选的，**不能改回 `word`**：
        # Tk 的 word 折行把「以空格分隔的一串字符」当成一个**不可拆的词**。中文
        # 整行没有空格时这个词就是整行，放不下只好按字符断，反而填得满；可只要行里
        # 出现**一个空格**（自己敲的，或中英混排带出来的），空格后面那一长串中文就
        # 变成了一个词——放不下就**整串挪到下一显示行**，本行右边空掉一大块。
        # 实测（正文可用宽 571px）：「短 后面这一整串中文…」第一显示行只填 **4.7%**；
        # 「> 引用开头 English 后面这一整串中文…」只填 **41.9%**，而且整段多折一行。
        # 这就是用户报的「引用有中英混合时会换行」「文字间插入空格会换行」，
        # 也让正文块看起来比设置的比例窄得多（用户报的「显示比例不对」）。
        # 改成 char 后同样两句分别填到 96.0% / 100.0%，显示行还少一行。
        # 代价：英文单词跨行时会被从中间断开——中文优先的日记里这个代价可以接受，
        # 而且导出长图本来也是按「宽字符」断行的，两边这才对得上。
        self.editor = tk.Text(
            editor_holder, bg=EDITOR_BG, fg=TEXT, insertbackground=TEXT,
            selectbackground=EDITOR_SELECTION_BG, selectforeground=TEXT, relief="flat",
            borderwidth=0, highlightthickness=0, wrap="char", undo=True, maxundo=-1,
            autoseparators=True, padx=px(24), pady=0, state="disabled",
        )
        self.editor.grid(row=0, column=0, sticky="nsew")

        self.gutter = tk.Canvas(
            editor_holder, bg=EDITOR_BG, width=GUTTER_W, highlightthickness=0, borderwidth=0,
        )
        self.gutter.grid(row=0, column=1, sticky="ns")

        # 标题徽标画布：不占网格，用 place 贴在正文左侧的留白里（见 _place_heading_labels）
        self.heading_labels = tk.Canvas(
            editor_holder, bg=EDITOR_BG, highlightthickness=0, borderwidth=0,
        )
        self.heading_labels.bind("<Button-1>", self._on_heading_label_click)

        editor_scroll = tk.Scrollbar(editor_holder, orient="vertical", command=self.editor.yview)
        editor_scroll.grid(row=0, column=2, sticky="ns")
        self.editor_scroll = editor_scroll
        self.editor.configure(yscrollcommand=self._on_editor_scrolled)

        self.editor.bind("<<Modified>>", self._on_editor_modified)
        self.editor.bind("<KeyRelease>", self._on_cursor_moved, add="+")
        self.editor.bind("<ButtonRelease-1>", self._on_cursor_moved, add="+")
        self.editor.bind("<Configure>", self._on_editor_configure)
        self.editor.bind("<MouseWheel>", self._on_editor_wheel)

    # ---------- 字体 ----------

    def _installed_families(self) -> dict[str, str]:
        """系统已安装字体：小写名 → 系统实际报告的名字。只探测一次并缓存。"""
        if self._font_families_cache is None:
            try:
                names = font.families(self.root)
            except tk.TclError:
                names = ()
            self._font_families_cache = {name.lower(): name for name in names}
        return self._font_families_cache

    def _resolve_installed(self, aliases: tuple[str, ...]) -> str | None:
        """在别名里找出系统真正认识的那个名字，用于跨语言环境匹配。"""
        table = self._installed_families()
        for alias in aliases:
            actual = table.get(alias.lower())
            if actual:
                return actual
        return None

    def _is_font_installed(self, family: str) -> bool:
        return bool(family) and family.lower() in self._installed_families()

    def _default_writing_font(self) -> str:
        """默认写作字体；在不同语言环境下取其实际可用的名字。"""
        return self._resolve_installed(DEFAULT_FONT_ALIASES) or DEFAULT_FONT_FAMILY

    def _writing_font(self) -> str:
        """写作区（正文、标题、行号、卡片）实际使用的字体。

        选择保存在设置里的原始值；若该字体在当前机器上不存在（例如换了电脑），
        渲染时回退到默认字体，但不清空用户的选择。
        """
        chosen = str(self.settings.get("font_family") or "").strip()
        if chosen and self._is_font_installed(chosen):
            return chosen
        return self._default_writing_font()

    def _font_row_label(self, family: str) -> str:
        """字体在界面上的显示名：常见中文字体用中文名，其余用族名。"""
        lowered = family.lower()
        for label, aliases in FONT_CANDIDATES:
            if any(alias.lower() == lowered for alias in aliases):
                return label
        return family

    def _writing_font_label(self) -> str:
        """写作区字体的显示名；没有对应中文名时直接用族名。"""
        return self._font_row_label(self._writing_font())

    def _available_font_choices(self) -> list[tuple[str, str]]:
        """候选字体里当前系统确实安装了的那些，值为系统实际字体名。"""
        choices: list[tuple[str, str]] = []
        for label, aliases in FONT_CANDIDATES:
            actual = self._resolve_installed(aliases)
            if actual:
                choices.append((label, actual))
        return choices

    def _system_font_families(self) -> list[str]:
        """全部系统字体：推荐字体排前面，其余按名称排序，整体去重。"""
        try:
            names = list(font.families(self.root))
        except tk.TclError:
            names = []
        ordered: list[str] = []
        seen: set[str] = set()
        for _label, aliases in FONT_CANDIDATES:
            actual = self._resolve_installed(aliases)
            if actual and actual.lower() not in seen:
                seen.add(actual.lower())
                ordered.append(actual)
        for name in sorted(names, key=lambda value: value.lower()):
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(name)
        return ordered

    def _font_linespace(self, size: int, family: str | None = None) -> int:
        """按「逻辑字号」取字体自然行高（内部换算成物理像素）。

        `family` 不给就用写作区字体；预览栏字号固定成自己的那一套，要显式传进来。
        """
        probe = font.Font(family=family or self._writing_font(), size=-px(size))
        return int(probe.metrics("linespace"))

    def _line_metrics(self) -> tuple[int, int, int]:
        """按“每个显示行高度一致”计算行距。

        目标：任意显示行（含段落内自动换行的行）之间距离都为 line_height。
        设 extra = line_height - 字体自然行高，则
          段内换行间距 spacing2 = extra
          段首/段尾补白 spacing1 + spacing3 = extra
        这样 n 个显示行的总高 = n * line_height。
        """
        size = self.settings["font_size"]
        line_height = self.settings["line_height"]
        linespace = self._font_linespace(size)
        extra = max(0, px(line_height) - linespace)
        spacing1 = extra // 2
        spacing3 = extra - spacing1
        return spacing1, extra, spacing3

    def _apply_typography(self) -> None:
        self.editor.configure(font=(self._writing_font(), -px(self.settings["font_size"])))
        self._body_font = None              # 字体或字号变了，量宽度的对象要重建
        spacing1, spacing2, spacing3 = self._line_metrics()
        self.editor.configure(spacing1=spacing1, spacing2=spacing2, spacing3=spacing3)
        self._configure_editor_tags()
        self._apply_ime_font()

    def _ime_logfont(self) -> "LOGFONTW":
        """组字该用的字体：和正文完全一致（同字族、同字号）。

        单独拎出来是为了能测——`_apply_ime_font` 要真去调 Windows 才有效果，
        但「算出来的字体对不对」是纯计算，不该跟着一起没法验证。
        """
        logfont = LOGFONTW()
        # lfHeight 为负表示「字符高度」而不是「外部行距」，单位是像素
        logfont.lfHeight = -px(self.settings["font_size"])
        logfont.lfWeight = 400
        logfont.lfCharSet = 134          # GB2312_CHARSET
        logfont.lfQuality = 5            # CLEARTYPE_QUALITY
        logfont.lfFaceName = self._writing_font()
        return logfont

    def _apply_ime_font(self) -> None:
        """让输入法的组字（拼音字母预览）用上和正文一样的字体与字号。

        这一段归 Windows 的输入法管：默认字号是输入法自己那一套，和正文对不上，
        而 Tk 不会把控件字体同步过去，只能自己调 imm32 告诉它。
        属于锦上添花——失败就当没发生，绝不能影响打字。
        """
        try:
            _declare_imm32_types()
            hwnd = int(self.editor.winfo_id())
            himc = ctypes.windll.imm32.ImmGetContext(hwnd)
            if not himc:
                return
            try:
                logfont = self._ime_logfont()
                ctypes.windll.imm32.ImmSetCompositionFontW(himc, ctypes.byref(logfont))
            finally:
                ctypes.windll.imm32.ImmReleaseContext(hwnd, himc)
        except (AttributeError, OSError, tk.TclError):
            pass

    def _configure_editor_tags(self) -> None:
        size = px(self.settings["font_size"])
        writing = self._writing_font()
        mono = -max(px(10), size - px(2))
        _spacing1, spacing2, _spacing3 = self._line_metrics()
        # 标题留白随行高一起放大，保持与正文比例一致
        head_gap = max(px(4), spacing2)
        # 各级标题与正文同字号，靠加粗、上下留白、左侧徽标和**颜色深浅**区分层级
        for level in range(1, 7):
            self.editor.tag_configure(
                f"h{level}", font=(writing, -size, "bold"),
                foreground=HEADING_COLORS[level],
                spacing1=head_gap, spacing3=head_gap // 2,
            )
        self.editor.tag_configure("bold", font=(writing, -size, "bold"),
                                  foreground=BOLD_COLOR)
        self.editor.tag_configure("italic", font=(writing, -size, "italic"),
                                  foreground=ITALIC_COLOR)
        self.editor.tag_configure("strike", overstrike=True, foreground=STRIKE_COLOR)
        self.editor.tag_configure("highlight", background=HIGHLIGHT_BG,
                                  foreground=HIGHLIGHT_TEXT)
        self.editor.tag_configure("code", font=(MONO_FAMILY, mono),
                                  background=CODE_BG, foreground=CODE_TEXT)
        self.editor.tag_configure("codeblock", font=(MONO_FAMILY, mono),
                                  background=CODE_BG, foreground=CODEBLOCK_TEXT)
        self.editor.tag_configure("link", foreground=LINK_COLOR, underline=True)
        self.editor.tag_configure("image", foreground=IMAGE_COLOR,
                                  font=(writing, -size, "italic"))
        quote_indent = px(LIST_INDENT)
        self.editor.tag_configure("quote", foreground=QUOTE_TEXT,
                                  lmargin1=quote_indent, lmargin2=quote_indent)
        # 列表缩进按层级配置成独立标签：标记被隐藏后，靠 lmargin 让正文
        # 和自动换行的续行对齐到同一个位置（悬挂缩进）
        for level in range(LIST_MAX_LEVEL + 1):
            indent = px(list_indent(level))
            self.editor.tag_configure(f"li{level}", lmargin1=indent, lmargin2=indent)
        # 表格：表头加粗着色并铺浅蓝底，数据行用斑马纹，竖线染成边框色当列分隔线
        self.editor.tag_configure("table_head", font=(writing, -size, "bold"),
                                  foreground=TABLE_HEAD_TEXT, background=TABLE_HEAD_BG)
        self.editor.tag_configure("table_cell", foreground=TEXT)
        self.editor.tag_configure("table_stripe", background=TABLE_STRIPE_BG)
        self.editor.tag_configure("table_pipe", foreground=TABLE_PIPE_COLOR)
        self.editor.tag_configure("syntax", elide=True)
        self.editor.tag_configure("syntax_current", foreground=SYNTAX_COLOR, elide=False)
        # 行首记号（`- `、`* `、`- [ ] `）露出源码时用的标签：`syntax_current`
        # 的「带缩进补偿」版本。
        #
        # 记号平时是 elide 的、宽度为 0，正文从 `lmargin` 开始；光标进到这一行时
        # 记号要露出来，如果不补偿，正文就被顶右「记号宽」那么多像素、可用宽度也
        # 少那么多——**刚好卡在折行边界的那一行会凭空多折一行**（用户报的
        # 「选中无序列表所在行也会自动换行」）。补偿办法是把 `lmargin1` 减去记号
        # 宽度：正文位置与可用宽度都回到隐藏时的值，折行位置一模一样。
        # 只用一个标签（同时只有一行是光标行），用之前重新配置即可。
        # 必须建在 `li{n}` 之后——Tk 里后建的标签优先级更高，否则 `li{n}` 的
        # lmargin 会压过这里配的补偿值。
        #
        # `wrap="char"` 也是必须的，而且比缩进补偿更关键：`word` 折行的规则是
        # 「一个词在当前显示行的**剩余**宽度里放不下，就整个挪到下一显示行」。
        # 记号隐藏时正文就是这一显示行的第一个词，放不下只能在原地按字符断开；
        # **记号一露出来，这一行就有内容了，正文那个「词」于是被整个挪到下一行**
        # ——显示行凭空多一行（这才是用户报的「选中无序列表所在行也会自动换行」的
        # 真正原因，缩进补偿只解决「正文位置」，解决不了这个）。
        #
        # 控件现在已经是 `wrap="char"`（见 `self.editor` 的创建处），所以这一条
        # 按说冗余了；**仍然显式留着**：一是「露出记号不许改变折行」这条不变量值得
        # 写在标签上，二是万一将来有人把控件改回 `word`，这里能挡住那次回退。
        # 测试 `test_the_revealed_marker_compensates_its_own_width` 等三处都盯着它。
        # 还有一个已知残留：记号露出来之后这一行会矮 16px（elide 首块的额外行距），
        # 下方文字会跟着上移一下。原因、试过的办法和取舍见 `_marker_options`。
        self.editor.tag_configure("syntax_marker", foreground=SYNTAX_COLOR,
                                  elide=False, wrap="char")


    def _bind_shortcuts(self) -> None:
        self.root.bind("<Control-n>", lambda _event: self.new_document())
        self.root.bind("<Control-s>", lambda _event: self.save_now())
        self.root.bind("<Control-o>", lambda _event: self.choose_folder())
        self.root.bind("<Control-Shift-F>", lambda _event: self.toggle_focus())
        self.root.bind("<Escape>", lambda _event: self._exit_focus())
        self.editor.bind("<Control-b>", self._wrap_bold)
        self.editor.bind("<Control-i>", self._wrap_italic)
        # 有序列表的序号续写。绑定在**控件级**（不是 bind_all），
        # 返回 "break" 就吃掉这次回车，不让 Text 的类绑定再插一个换行。
        self.editor.bind("<Return>", self._on_editor_return)
        # Tab / Shift+Tab 调列表层级。同样是控件级 + 返回 "break"：
        # Tab 不能落回 Tk 默认的「插一个制表符」（一个 \t 会被算成两层），
        # Shift+Tab 也不能落回默认的「跳到上一个控件」。
        self.editor.bind("<Tab>", self._on_editor_tab)
        self.editor.bind("<Shift-Tab>", self._on_editor_shift_tab)
        self.editor.bind("<ISO_Left_Tab>", self._on_editor_shift_tab)
        # 输入法的组字字体是挂在窗口上的，重新拿到焦点时再设一次最稳妥
        self.editor.bind("<FocusIn>", lambda _event: self._apply_ime_font(), add="+")

    # ---------- 专注模式 ----------

    def toggle_focus(self) -> None:
        self.focus_mode = not self.focus_mode
        if self.focus_mode:
            self.nav.grid_remove()
            self.cards_panel.grid_remove()
            self.focus_button.configure(text="退出专注", fg=ACCENT)
        else:
            self.nav.grid()
            self.cards_panel.grid()
            self.focus_button.configure(text="专注模式", fg=MUTED)
        self._request_geometry()

    def _exit_focus(self) -> None:
        if self.focus_mode:
            self.toggle_focus()

    # ---------- 导出长图 ----------

    def _export_options(self) -> Options:
        """把界面上的排版尺寸换算成长图的像素尺寸。

        比例取自「正文目标字号 ÷ 编辑器字号」，所以行距、缩进、标记大小
        都和屏幕上同比例——图看起来就是这份文档，只是变宽变清楚了。
        """
        font_size = self.settings["font_size"]
        ratio = EXPORT_BODY_SIZE / font_size                   # 逻辑像素 → 图片像素
        physical = EXPORT_BODY_SIZE / max(1, px(font_size))    # 物理像素 → 图片像素
        spacing2 = self._line_metrics()[1]
        return Options(
            width=EXPORT_WIDTH,
            margin=EXPORT_MARGIN,
            body_size=EXPORT_BODY_SIZE,
            family=self._writing_font(),
            mono_family=MONO_FAMILY,
            mono_size=max(8, round(max(10, font_size - 2) * ratio)),
            line_height=max(EXPORT_BODY_SIZE + 6,
                            round(self.settings["line_height"] * ratio)),
            max_height=EXPORT_MAX_HEIGHT,
            list_indent=round(LIST_INDENT * ratio),
            list_marker_gap=round(LIST_MARKER_GAP * ratio),
            marker_radius=max(2, round(LIST_MARKER_R * ratio)),
            task_box=max(8, round(12 * ratio)),
            quote_bar_width=max(1, round(QUOTE_BAR_W * ratio)),
            quote_bar_gap=max(4, round(14 * ratio)),
            hr_thickness=max(1, round(HR_THICKNESS * ratio)),
            badge_size=max(7, round(HEADING_LABEL_SIZE * ratio)),
            badge_gap=round(HEADING_LABEL_GAP / max(0.01, SCALE) * ratio),
            head_gap=max(round(4 * ratio), round(spacing2 * physical)),
            table_padding=max(6, round(14 * ratio)),
        )

    def render_document_image(self, text: str) -> list[bytes]:
        """把一段 Markdown 渲染成一张或多张 PNG（超过高度上限才分张）。"""
        options = self._export_options()
        return render_long_image(build_export_rows(text, options), options,
                                 export_palette())

    def _export_default_path(self, path: Path | None = None) -> Path:
        target = self.current_path if path is None else path
        if target is not None:
            return target.with_suffix(".png")
        folder = Path(self.folder) if self.folder else Path.home()
        return folder / "简记长图.png"

    def _export_source(self, path: Path | None) -> tuple[str, Path | None]:
        """要导出的正文，以及它对应的文档路径。

        右键菜单可以对**没打开**的文档导出，所以分两种：正好是当前打开的那篇时
        用编辑区的文本（把还没自动保存的改动也算上），否则从磁盘读。
        读不出来就报错并返回空串，让调用方走「没有内容可以导出」那条路。
        """
        if path is None or path == self.current_path:
            return self.editor.get("1.0", "end-1c"), self.current_path
        try:
            text, _signature = read_markdown(path)
        except (OSError, UnicodeError) as error:
            messagebox.showerror("导出失败", f"无法读取文档：\n{error}", parent=self.root)
            return "", None
        return text, path

    def speakable_text(self, text: str) -> str:
        """把一篇 Markdown 变成「念得出来」的纯文本（记号全部去掉）。

        **复用长图那套解析**（`build_export_rows`）：它已经把 `# `、`- `、`1. `、
        `> `、`**`、`` ` `` 这些记号剥掉了，`Piece.text` 就是纯粹的文字。
        再写一套「去掉 Markdown」的逻辑，迟早会和导出图对不上——
        屏幕上、长图里、耳朵里三套文本不一致，是这一轮最该避免的事。
        """
        options = self._export_options()
        lines: list[str] = []
        for row in build_export_rows(text, options):
            if row.kind == "hr":
                continue                    # 分隔线念出来只有噪音
            if row.kind == "blank":
                lines.append("")            # 空行 = 停顿
                continue
            if row.kind == "table":
                # 表格按行念，格子之间用顿号，不然会连成一串听不出边界
                for cells in row.cells:
                    joined = "、".join(
                        "".join(piece.text for piece in cell).strip()
                        for cell in cells)
                    joined = joined.strip("、")
                    if joined:
                        lines.append(joined)
                continue
            line = "".join(piece.text for piece in row.pieces).strip()
            if line:
                lines.append(line)
        return "\n".join(lines)

    def _export_default_audio_path(self, path: Path | None = None) -> Path:
        target = self.current_path if path is None else path
        if target is not None:
            return target.with_suffix(".wav")
        folder = Path(self.folder) if self.folder else Path.home()
        return folder / "简记朗读.wav"

    def export_audio(self, path: Path | None = None) -> None:
        """把一篇文档念成音频（WAV）。

        **合成很慢**（中文语速约每秒 3.5 个字），一篇两千字的日记要十来分钟，
        所以放到后台线程里跑、主线程只轮询进度 —— 直接同步调用会把界面冻住，
        用户会以为软件卡死了。状态栏显示已用时间，合成完自动打开文件夹。
        """
        if self._speech_job is not None:
            self.status_label.configure(text="上一段音频还在合成，请稍候…")
            return
        if not speech.is_available():
            messagebox.showerror(
                "无法导出音频",
                "这台机器上没有可用的语音引擎。\n\n"
                "「简记」用的是 Windows 自带的语音合成，"
                "可以在「设置 → 时间和语言 → 语音」里检查是否装了语音包。",
                parent=self.root)
            return

        text, source = self._export_source(path)
        spoken = self.speakable_text(text)
        if not spoken.strip():
            self.status_label.configure(text="没有内容可以朗读")
            return

        default = self._export_default_audio_path(source)
        chosen = filedialog.asksaveasfilename(
            title="导出音频", parent=self.root, defaultextension=".wav",
            initialdir=str(default.parent), initialfile=default.name,
            filetypes=[("WAV 音频", "*.wav")],
        )
        if not chosen:
            return

        target = Path(chosen)
        voice = speech.default_voice_name()
        self._speech_job = {
            "target": target,
            "text": spoken,
            "voice": voice,
            "error": None,
            "size": 0,
            "done": False,
            "started": time.monotonic(),
        }
        self.status_label.configure(
            text=f"正在合成语音…（{voice or '系统默认音色'}）")
        self.root.update_idletasks()

        job = self._speech_job

        def work() -> None:
            """后台线程：只碰 job 字典和文件，**绝不碰任何 Tk 对象**。"""
            try:
                job["size"] = speech.synthesize(spoken, target)
            except Exception as error:                  # noqa: BLE001
                job["error"] = error
            finally:
                job["done"] = True

        threading.Thread(target=work, daemon=True, name="jianji-speech").start()
        self._speech_poll = self.root.after(300, self._poll_speech_job)

    def _poll_speech_job(self) -> None:
        """主线程轮询后台合成进度。只有这里能碰 Tk。"""
        job = self._speech_job
        self._speech_poll = None
        if job is None:
            return
        if not job["done"]:
            elapsed = time.monotonic() - job["started"]
            self.status_label.configure(
                text=f"正在合成语音…已用 {elapsed:.0f} 秒（长文可能要几分钟）")
            self._speech_poll = self.root.after(300, self._poll_speech_job)
            return

        self._speech_job = None
        error = job["error"]
        if error is not None:
            self.status_label.configure(text="导出音频失败")
            messagebox.showerror("导出音频失败",
                                 f"合成语音时出错：\n{error}", parent=self.root)
            return
        target = job["target"]
        seconds = speech.wav_seconds(target)
        length = f"（约 {seconds:.0f} 秒）" if seconds else ""
        self.status_label.configure(text=f"已导出音频：{target.name}{length}")
        self._reveal(target)

    def export_long_image(self, path: Path | None = None) -> None:
        """把一篇文档整篇导出成一张适合在手机上读的长图。

        `path` 省略时导出当前打开的那篇；文稿右键菜单会把具体文档传进来。
        """
        text, source = self._export_source(path)
        if not text.strip():
            self.status_label.configure(text="没有内容可以导出")
            return

        default = self._export_default_path(source)
        chosen = filedialog.asksaveasfilename(
            title="导出长图", parent=self.root, defaultextension=".png",
            initialdir=str(default.parent), initialfile=default.name,
            filetypes=[("PNG 图片", "*.png")],
        )
        if not chosen:
            return

        self.status_label.configure(text="正在生成长图…")
        self.root.update_idletasks()
        try:
            images = self.render_document_image(text)
        except Exception as error:                      # noqa: BLE001
            # 导出失败不能把应用带崩：报出来，编辑区原样留着
            messagebox.showerror("导出失败", f"生成图片时出错：\n{error}", parent=self.root)
            self.status_label.configure(text="导出失败")
            return

        target = Path(chosen)
        written: list[Path] = []
        try:
            for index, data in enumerate(images):
                path = (target if index == 0 else
                        target.with_name(f"{target.stem}-{index + 1}{target.suffix}"))
                path.write_bytes(data)
                written.append(path)
        except OSError as error:
            messagebox.showerror("导出失败", f"写入文件时出错：\n{error}", parent=self.root)
            self.status_label.configure(text="导出失败")
            return

        if len(written) == 1:
            self.status_label.configure(text=f"已导出长图：{written[0].name}")
        else:
            self.status_label.configure(
                text=f"已导出 {len(written)} 张长图：{written[0].name} 等")
        # 导出完顺手把所在文件夹打开（选中刚写出的那张），省得自己去找
        self._reveal(written[0])

    # ---------- 行号与内容宽度 ----------

    def _on_editor_configure(self, _event=None) -> None:
        # 宽度一变，Tk 会在**下一个空闲阶段**把整篇正文重新折行（长文档要几十毫秒）。
        # 左右留白必须赶在那之前改好：Tk 的重折行排在空闲阶段的前头，我们再用
        # after_idle 去改就晚了——会先按旧留白折一遍，再按新留白折一遍，白干一次。
        # 所以留白同步改（只是整数运算），重画仍然走空闲阶段合并。
        self._sync_editor_padding()
        # 底部呼吸空间按**视口高度**算，所以高度一变就得重算。它只在高度真的变了
        # 的时候才动标签，平时只多一次 winfo_height()。
        self._apply_bottom_pad()
        self._request_geometry()

    def _apply_bottom_pad(self, force: bool = False) -> None:
        """给最后一行加一段下留白，滚到底时正文不至于贴着窗口下沿。

        用户要求「长文滚动到底部时，编辑区仍能显示 25% 区域的空白」——写长文时
        最后一行紧贴窗口底边，光标停在末尾时连「下一行」都看不见，很难受。

        实现走**标签的 `spacing3`**，只挂在最后一行上，而不是 Text 的 `-pady`：
        `-pady` 上下对称，会把正文顶部一起顶下去（那个毛病上一轮刚修掉）。
        `spacing3` 只往下撑，而且算进滚动区总高，所以滚到底正好空出这一段。

        **只在必要时重挂**：`tag_remove` + `tag_add` 会让 Tk 认为最后一行的高度变了，
        于是重排一次——打字时每敲一个字都来一次，既费时间又会把滚动位置顶歪
        （实测 `yview_moveto(1.0)` 之后 `yview()[1]` 只到 0.952）。所以判据是
        「留白值变了」或「标签现在没盖住最后一行」（判据本身见
        `_bottom_pad_covers_last_line`）；`open_file` 换整篇正文时标签会被清空，
        那里必须 `force=True`。

        **留白要挂在「末尾那个空行」上才躲得开选中高亮**（理由见
        `_ensure_trailing_newline`）。正文进编辑器时已经保证末尾有换行，所以正常
        情况下挂的就是空行。残留一种情形：用户把末尾那个换行删掉（Backspace 并到
        上一行、整段删掉末尾、粘贴替换），标签会退回正文行上，选中高亮重新盖住留白
        ——这里**故意不去补那个换行**。补了就变成「末尾空行删不掉」，Backspace 在
        那里静默失效，比这个只在选中时才出现的观感问题更烦人；重新打开文稿就会
        自动恢复。
        """
        height = self.editor.winfo_height()
        if height <= 1:
            return
        pad = int(height * EDITOR_BOTTOM_PAD_RATIO)
        if pad != self._bottom_pad:
            self._bottom_pad = pad
            self.editor.tag_configure("bottom_pad", spacing3=pad)
        if not force and self._bottom_pad_covers_last_line():
            return
        self._bottom_pad_line = int(self.editor.index("end-1c").split(".")[0])
        self.editor.tag_remove("bottom_pad", "1.0", "end")
        start, stop = self._bottom_pad_span()
        self.editor.tag_add("bottom_pad", start, stop)

    def _bottom_pad_span(self) -> tuple[str, str]:
        """底部留白标签**该**盖住的区间。

        取哪一段要分两种情形——Tk 的 Text 自己还额外带一个「隐形换行」，所以
        `end-1c` 落在哪一行取决于文稿是不是以换行结尾（实测，
        见 `tools/_probe_pad_tag_range.py`）：
          ① 末尾那行**有内容**（文稿不以换行结尾）→ 标签包这一行的内容；
          ② 末尾那行是**空行**（文稿以换行结尾）→ 退回去包那个空行自己。
        两种都要，缺一种留白就静默失效（只剩 8px，肉眼几乎看不出来）。
        也**不能**图省事写成 `end-1c linestart` → `end`：那个范围跨了**两**行，
        `spacing3` 被算两遍、把 Tk 认的总高度撑虚，可见留白虽然对，但滚到真正的底时
        `yview()[1]` 只有 0.96，编辑区滚动条的滑块到不了底。
        """
        start, stop = "end-1c linestart", "end-1c lineend"
        if self.editor.compare(start, "==", stop):
            start, stop = "end-1c", "end"
        return start, stop

    def _bottom_pad_covers_last_line(self) -> bool:
        """留白标签现在是不是真的盖住了最后一行（`spacing3` 落得到显示行上）。

        **判据不能只看「最后一行行号变没变」**：`spacing3` 只对「标签盖住的最后那个
        显示行」生效，标签一旦滑到别处、或缩成只剩一个换行符，留白就静默消失，
        而那时行号往往一点没变，于是永远修不回来。

        最容易踩的就是这一种：末行是空行时标签是 `end-1c` → `end`（**只有一个换行
        符**），光标停在这一行敲下第一个字——插入点正好是标签的起点，新字按 Tk 的
        规矩继承的是**前一个字符**的标签（没打上），标签起点于是被推到新字后面，
        整段标签只剩那个换行符。`spacing3` 落不到任何显示行上，25% 留白当场没了
        （用户报的「编辑区的空白，在点击回车后闪回」）。删掉末行最后一个字也一样。

        判据就一条：**标签区间里要含得住「最后一行的起点」**（实测：末行空行时
        `61.0..62.0` 生效、滑成 `61.1..62.0` 就不生效）。换行符不算——Tk 不把它
        当作显示行上的字。

        这条判据有个好性质：**修好之后就不会再重挂**。重挂出来的区间是
        `行首 .. end`，之后在行尾打字，插入点落在区间**内部**，新字会继承标签、
        区间原地不动，判据一直成立——于是「打字不重排」那条性能契约也保住了，
        每个「新的空末行」只重挂一次。
        """
        ranges = self.editor.tag_ranges("bottom_pad")
        if len(ranges) != 2:
            return False
        start = self._index_pair(ranges[0])
        stop = self._index_pair(ranges[1])
        line_start = self._index_pair("end-1c linestart")
        return start <= line_start < stop

    def _index_pair(self, index) -> tuple[int, int]:
        """把 Tk 的 `行.列` 下标拆成可比较的元组。

        不能直接比字符串：`"61.10" < "61.2"` 是成立的，而它们实际是相反的。
        """
        line, column = self.editor.index(index).split(".")
        return int(line), int(column)

    def _screen_width(self) -> int:
        """显示器宽度（物理像素）。「内容行宽」的比例基准就是它。

        `_screen_width_override` 是给测试和探针用的：钉住一个屏幕宽，断言就
        不随跑测试那台机器的显示器而变（同 `JIANJI_SCALE` 的思路）。
        拿不到真实值时退回窗口宽，保证比例不会算成 0。
        """
        if self._screen_width_override:
            return self._screen_width_override
        try:
            width = self.root.winfo_screenwidth()
        except tk.TclError:
            width = 0
        return width if width > 1 else max(1, self.root.winfo_width())

    def _editor_target_width(self, column_width: int) -> int:
        """正文块应有的宽度（见 EDITOR_SIDE_RATIO 上面那段推导）。

        `column_width` 是**编辑区**的宽（含行号槽与滚动条），不是正文控件的宽：
        「编辑区占屏幕比例」说的是编辑区，两边各留 5% 也是相对编辑区说的。
        """
        screen = self._screen_width()
        ratio = self.settings["line_width"] / 100.0
        floor = column_width - int(2 * EDITOR_SIDE_RATIO * screen)
        return max(0, min(int(ratio * screen), floor))

    def _sync_editor_padding(self) -> bool:
        """按当前宽度算好左右留白并立刻应用。

        只做整数运算和一次 configure，不碰画布——它是给 `<Configure>`
        处理器用的，必须便宜，否则重排风暴时会被自己拖慢。

        返回 False 表示这会儿不该重画：控件还没排好版（宽度无效），
        或者已经在一次 configure 的嵌套回调里。
        """
        if self._padding_busy:          # configure 可能又触发一次 <Configure>
            return False
        width = self.editor.winfo_width()
        if width <= 1:
            return False
        column = self.editor.master.winfo_width()
        if column <= 1:
            return False
        # 正文块在**正文控件**里居中。控件比编辑区窄一圈（左边行号槽、右边滚动条），
        # 所以这里减的是控件宽而不是编辑区宽；差的那几十像素只让正文块相对编辑区
        # 整体偏一点点，肉眼看不出，但宽度是准的。
        pad = max(0, (width - self._editor_target_width(column)) // 2)
        if pad == self._current_pad:
            return True
        self._padding_busy = True
        try:
            self._current_pad = pad
            self.editor.configure(padx=pad)
        finally:
            self._padding_busy = False
        return True

    def _apply_min_window_width(self) -> None:
        """软件最小宽度：让**编辑区**至少占屏幕 `EDITOR_MIN_SHARE`（用户第 12 轮）。

        编辑区宽 = 窗口宽 − 导航栏 − 文稿列表，所以窗口下限要把这两栏加上，
        否则窗口缩到下限时编辑区还不到 40%（那样正文就只剩几个字一行了）。
        算完就和上次比一比，没变就直接返回——`minsize` 会触发 `<Configure>`，
        不设这道门就会来回弹。
        """
        screen = self._screen_width()
        minimum = NAV_W + CARDS_W + int(screen * EDITOR_MIN_SHARE)
        if minimum == self._min_window_width:
            return
        self._min_window_width = minimum
        self.root.minsize(minimum, px(560))

    def _request_geometry(self) -> None:
        """把连续到来的几何变化合并成一次重排。

        切换专注模式时导航栏和卡片栏一起消失/出现，Tk 会连着发好几次
        <Configure>；正文宽度一改又会再发一次。每次都重画一遍边栏纯属浪费，
        合并成一次就够了。
        """
        if self._geometry_pending:
            return
        self._geometry_pending = True
        self.root.after_idle(self._flush_geometry)

    def _flush_geometry(self) -> None:
        self._geometry_pending = False
        self._apply_editor_geometry()

    def _apply_editor_geometry(self) -> None:
        if not self._sync_editor_padding():
            return
        self._apply_min_window_width()
        # 留白、高度、视口起点都没变，重画出来的东西和上次一模一样，跳过。
        if self._margin_state() == self._margin_signature:
            return
        self._redraw_margins()

    def _margin_state(self) -> tuple:
        """边栏当前的绘制依据：留白、正文高度、视口起点。

        与 `_margin_signature` 比对，一致就说明上次画的结果还有效。
        """
        try:
            first = self.editor.index("@0,0")
        except tk.TclError:
            first = ""
        return (self._current_pad, self.editor.winfo_height(), first)

    def _redraw_margins(self) -> None:
        """行号、标题徽标、左侧装饰与表格边框都要跟着滚动/重排更新，统一从这里刷新。

        这里先把「当前可见的显示行」走一遍，再把结果分给四个模块用。
        以前是四个模块各走一遍，每次滚动要问 Tk 几百次「这是第几行」——
        跨语言调用的开销占了整次重画的九成，是滚动卡顿的主因。
        """
        visible = self._visible_lines()
        self._redraw_line_numbers(visible)
        self._place_heading_labels()
        self._redraw_heading_labels(visible)
        self._redraw_decorations(visible)
        self._redraw_table_frames(visible)
        # 记下这次是按什么状态画的，供 _apply_editor_geometry 判断能否跳过
        self._margin_signature = self._margin_state()

    def _display_line_height(self) -> int:
        """一个**健康**的显示行有多高。

        `dlineinfo` 量到的 `box[3]` 正常情况下就等于这个值（行高设置 34 → 51px，
        实测的盒子正是 `(189, 93, 0, 51, 36)`）。但在**视口下沿**，Tk 会把这一
        显示行的盒子按可见部分裁一刀——`box[3]` 会变小（实测 51 → 42、33 → 29），
        而 `box[4]`（基线偏移）**不裁**。装饰画布照着裁过的 `box[3]` 定高，
        序号就会超出画布下沿被切掉两个像素；引用竖条则会比上一段短一截。
        拿这个值当下限，等于把 Tk 裁掉的那一截补回来。

        只在画布定高时用，**不要**拿它去替换 `_display_line_box` 的结果——
        定位靠的是 `box[1]`（上沿），那个 Tk 裁的是下沿，上沿是准的。
        """
        return px(self.settings["line_height"])

    def _display_line_box(self, index: str) -> tuple | None:
        """量某个显示行的几何，绕开 Tk 在「行首 elide + 折行」下给的空盒子。

        Tk 对**自动换行的显示行**是按该行第一个 chunk 算几何的。行首那段一旦被
        elide（`1. `、`- `、`> `、`# ` 这些记号平时都藏着），量到的就是那个宽度
        为 0 的 elided chunk，盒子退化成「宽 0、高 = 行距、基线 = 行距的一半」。
        直接拿它给装饰定位：画布高算成 `max(px(16), 行距)`、基线算成行距的一半，
        序号被顶到画布外面裁掉大半（见 outputs/修复前-序号折行被裁.png），
        引用的竖条也会缩成一小截。
        守它的是 tests/test_markdown_syntax.py 的 WrappedLineGeometryTests
        与 TallLineHeightWrappedLineGeometryTests。

        判断「是不是空盒子」一律走 `is_real_display_box`（先看**宽度**、再看高度够不够
        一个显示行）：空盒子的高度随行高变，默认设置下是 2、行高 34 时是 16，按高度
        设绝对阈值必然在某个行高下漏掉。行首没 elide 的行、以及不折行的行，Tk 给的
        都是好盒子，所以只在发现空盒子时才重新量——每多量一次就是一次跨语言调用，
        这是每帧都要走的热路径。

        重新量用 `index + 1 display chars`——**它会跳过 elide 的字符**，直接落到
        第一个真正显示出来的字上，一次就到位。别改成逐列往后试：缩进四级的有序项
        记号就有 11 列（`        1. `），上限一不够又回到空盒子。

        量到的盒子直接就用，**不要和空盒子合并**。空盒子的 y 不是显示行的上沿：
        实测它比真正的上沿还**高**（高出的量正好是行距那一截，行高 34 时是 16 像素），
        高和基线也只是「行距」和「行距的一半」。早先按「上沿取更靠上的那个」合并，
        于是引用首段的竖条往上多冒 16 像素、比后面几段高一截
        （tests 里 test_wrapped_quote_bar_segments_are_about_the_same_height 盯着它）。
        probe 的盒子本身就是这个显示行：和上下相邻显示行的 y 严丝合缝
        （第 2 行：109 + 51 = 160，正是下一显示行的 y）。
        """
        box = self.editor.dlineinfo(index)
        line_height = px(self.settings["line_height"])
        if box is None or is_real_display_box(box, line_height):
            return box
        try:
            probe = self.editor.index(f"{index} + 1 display chars")
            next_display = self.editor.index(f"{index} + 1 display lines")
        except tk.TclError:
            return box
        if self.editor.compare(probe, ">=", next_display):
            # 第一个显示出来的字已经落到下一个显示行了（这一显示行整行都被 elide），
            # 那它代表不了本显示行，老盒子凑合用
            return box
        found = self.editor.dlineinfo(probe)
        if is_real_display_box(found, line_height):
            return found
        # 量不出来，只剩这个空盒子，两种情况要分开对待：
        #
        # ① 空行、只有记号的行（整行都没有可见的字，见 tools 里的边界实测）：
        #    空盒子的 y 是对的，照旧返回——不然这些行的行号会整片消失。
        # ② **折了行**的显示行：空盒子的 y 比真上沿还高（高出的量正好是行距那一截），
        #    而量不出来本身就说明这一显示行已经滚到视口外面去了（在视口里的话，
        #    probe 那个字一定量得到）。照着空盒子画，会在这个显示行上方凭空冒出
        #    一块序号，位置正是**上一行**那里，比不画还糟。
        if int(next_display.split(".")[0]) == int(index.split(".")[0]):
            return None
        return box

    def _visible_lines(self) -> list[tuple[int, int, tuple]]:
        """当前视口内的显示行：[(逻辑行号, 行内字符偏移, dlineinfo), ...]。

        行内偏移为 0 表示这是该逻辑行的**第一显示行**（自动换行的续行偏移大于 0）。
        行号与徽标只在第一显示行标注，靠这个判断，不用再多问一次 Tk。

        按**逻辑行**遍历，而不是用 `index("... + 1 display lines")` 一条条显示行地挪。
        后者要 Tk 逐条推算显示行，一次重画要跑几十次，是滚动时最贵的一步；
        而 `dlineinfo("42.0")` 是普通行号查询，便宜得多。
        需要续行信息的只有引用和表格（竖条要连成一条、行高要算全），
        对它们再单独展开——它们在文档里是少数。
        """
        result: list[tuple[int, int, tuple]] = []
        if self.current_path is None and not self.preview_mode:
            return result
        try:
            first_line = int(self.editor.index("@0,0").split(".")[0])
            bottom = int(self.editor.index(f"@0,{max(1, self.editor.winfo_height())}")
                         .split(".")[0])
            end_index = self.editor.index("end-1c")
        except tk.TclError:
            return result
        last_line_text, last_column_text = end_index.split(".")
        content_last_line = int(last_line_text)
        # 文件以换行结尾时 Tk 会多留一行空的收尾行。end-1c 正好落在它的行首，
        # 这种「有行无字」的行不该出现，也不该编号——所以记下它有没有内容。
        last_line_has_content = int(last_column_text) > 0

        for number in range(first_line, bottom + 1):
            if number > content_last_line:
                break
            if number == content_last_line and not last_line_has_content:
                break
            info = self._display_line_box(f"{number}.0")
            if info is None:
                # 整行不在视口里（含第一显示行被滚出上沿的情况）
                continue
            result.append((number, 0, info))
            kind = self._block_of(number).kind
            if kind in ("quote", "table"):
                result.extend(self._continuation_lines(number))
        return result

    def _continuation_lines(self, number: int) -> list[tuple[int, int, tuple]]:
        """某一逻辑行自动换行出来的续行（不含第一显示行）。

        **先花两次便宜的 `dlineinfo` 问「这一行到底折没折」，折了才去走
        `+ 1 display lines`**：后者是 Tk 里最贵的下标运算之一（实测
        200–436 µs 一次，而 `dlineinfo` 只要 4 µs、`@0,y` 只要 19 µs）。
        引用和表格在视口里各占好几行，可绝大多数行根本不折——实测一次滚动重画里
        7 次 `+ 1 display lines` 有 6 次是白问，合起来 1.6 ms，占整帧三分之一
        （`_visible_lines` 的注释里已经写明这个运算最贵，主循环躲开了，
        这里当时漏了）。
        """
        out: list[tuple[int, int, tuple]] = []
        first = self._display_line_box(f"{number}.0")
        if first is None:
            return out
        # 末尾量不到（末行折到视口外面去了）时**不能**当成「没折」，退回下面那条
        # 精确但昂贵的路——那种情况下前半截续行是可见的，竖条/表格框还得画。
        tail = self._display_line_box(f"{number}.end")
        if tail is not None and tail[1] == first[1]:
            return out          # 首尾落在同一显示行上 = 这一行没折
        try:
            index = self.editor.index(f"{number}.0 + 1 display lines")
        except tk.TclError:
            return out
        while int(index.split(".")[0]) == number:
            info = self._display_line_box(index)
            if info is None:
                break
            out.append((number, int(index.split(".")[1]), info))
            following = self.editor.index(f"{index} + 1 display lines")
            if following == index:
                break
            index = following
        return out

    def _place_heading_labels(self) -> None:
        """把徽标画布贴在正文左侧留白里，紧挨标题文字。

        正文是水平居中的（靠 Text 的 padx），所以徽标的横坐标必须跟着 pad 走，
        不能固定在窗口左边——否则正文越窄，徽标离标题越远。

        用父容器（editor_holder）坐标放置，不用 place(in_=editor)：对 Text 而言
        -in 的坐标原点是**内容**原点（已经算进 padx/pady），和 winfo_x() 不同源，
        很容易把留白算两遍。

        这里**不** lift 自己：徽标画布是在正文之后建的，本来就在正文上面；
        一旦抬自己，装饰画布就得跟着逐帧抬回来，每次滚动白花上百微秒。
        """
        canvas = self.heading_labels
        if not canvas.winfo_exists():
            return
        pad = self._current_pad
        if pad < HEADING_LABEL_W + HEADING_LABEL_GAP:
            # 留白不够（窄窗口 + 行宽拉到 80%）：宁可不显示，也不要盖住正文
            canvas.place_forget()
            return
        canvas.place(
            x=self.editor.winfo_x() + pad - HEADING_LABEL_W - HEADING_LABEL_GAP,
            y=self.editor.winfo_y(),
            width=HEADING_LABEL_W, height=self.editor.winfo_height(),
        )

    def _on_heading_label_click(self, _event=None) -> None:
        """徽标本身不可编辑，点它就把焦点交还给编辑器，别把点击吞掉。"""
        self.editor.focus_set()
        return "break"

    # ---------- 左侧装饰（项目符号 / 复选框 / 序号 / 引用条 / 分隔线） ----------

    def _decor_canvas(self, slot: int) -> tk.Canvas:
        """装饰画布池：每个装饰只占一小块，复用画布避免每次滚动都新建控件。"""
        while len(self._decor_pool) <= slot:
            canvas = tk.Canvas(self.editor.master, bg=EDITOR_BG,
                               highlightthickness=0, borderwidth=0)
            canvas.bind("<Button-1>", self._on_heading_label_click)
            canvas.decor_geometry = None
            self._decor_pool.append(canvas)
            self._raise_once(canvas)
        return self._decor_pool[slot]

    def _raise_once(self, canvas: tk.Canvas) -> None:
        """新建的画布抬到最上面，之后就靠叠放顺序，不再逐帧 lift。

        Tk 的兄弟窗口按创建顺序叠放（后建的在上），这些画布都是正文控件之后
        建的，本来就在它上面；但标题徽标画布也是后建的，所以新建时抬一次即可。
        `lift` 是**重排窗口**，一次要上百微秒——逐帧给几十块画布各抬一次，
        光这一项就占了整次重画的四成，纯属白花。
        """
        tk.Misc.lift(canvas)

    def _hide_decorations(self, used: int) -> None:
        for canvas in self._decor_pool[used:]:
            canvas.decor_spec = None
            canvas.decor_geometry = None
            if canvas.winfo_manager():
                canvas.place_forget()

    def _decoration_spec(self, block: LineInfo, first: bool, on_cursor_line: bool):
        """这一显示行要不要画装饰、画什么。

        光标所在行会显示 Markdown 源码，此时不再画装饰，否则会和源码叠在一起。
        **例外是有序列表的序号**：它的记号永远被藏起来（见 `_style_line`），
        序号一律由画布画，所以光标进出该行时它不会在「蓝色小字」和「灰色源码」
        之间跳来跳去（用户明确要求过这一点）。
        """
        if on_cursor_line and block.kind != "ordered":
            return None
        if block.kind == "quote":
            # 引用的竖条在每一显示行都画，换行的续行也连成一条
            return ("quote",)
        if not first:
            return None
        if block.kind == "bullet":
            return ("bullet", block.level)
        if block.kind == "task":
            return ("task", block.level, block.checked)
        if block.kind == "ordered":
            return ("ordered", block.level, block.number)
        if block.kind == "hr":
            return ("hr",)
        return None

    def _draw_decoration(self, slot: int, spec, dline, line_number: int) -> None:
        canvas = self._decor_canvas(slot)
        # 目标几何没变就不用重画：光标在同一屏内移动时装饰一动不动，
        # 而 delete/place/create/lift 每个都是跨语言调用，省下来就是流畅度。
        geometry = (spec, line_number, dline[1], dline[3], dline[4],
                    self._current_pad, self.editor.winfo_width())
        if canvas.decor_geometry == geometry and canvas.winfo_manager():
            return
        canvas.decor_spec = spec
        canvas.decor_line = line_number
        canvas.decor_geometry = geometry
        canvas.delete("all")
        pad = self._current_pad
        text_left = self.editor.winfo_x() + pad
        text_width = max(px(40), self.editor.winfo_width() - pad * 2)
        # 与标题徽标同一套基线算法：dline[4] 是相对本行顶部的基线偏移
        baseline = dline[1] + dline[4]
        centre_y = baseline - px(self.settings["font_size"]) * 0.36
        kind = spec[0]

        if kind in ("bullet", "task", "ordered"):
            indent = px(list_indent(spec[1]))
            # 标记的右边缘落在正文左侧 gap 处；画布在标记外再留 pad，
            # 所以画布左边界要往回退 pad，标记右边缘才正好等于 right。
            right = text_left + indent - px(LIST_MARKER_GAP)
            pad = px(3)
            if kind == "bullet":
                radius = px(LIST_MARKER_R)
                marker_w = radius * 2
                size = marker_w + pad * 2
                canvas.place(x=right - marker_w - pad, y=int(centre_y - size / 2),
                             width=size, height=size)
                cy = size / 2
                box = (pad, cy - radius, pad + marker_w, cy + radius)
                # 每层换一种颜色，层级一眼可辨
                color = BULLET_COLORS[spec[1] % len(BULLET_COLORS)]
                if spec[1] == 0:
                    canvas.create_oval(*box, fill=color, outline="")
                elif spec[1] == 1:
                    canvas.create_oval(*box, outline=color, fill=EDITOR_BG,
                                       width=max(1, px(2)))
                else:
                    canvas.create_rectangle(pad, cy - radius, pad + marker_w, cy + radius,
                                            fill=color, outline="")
            elif kind == "task":
                box_size = px(12)
                size = box_size + pad * 2
                canvas.place(x=right - box_size - pad, y=int(centre_y - size / 2),
                             width=size, height=size)
                top = int(size / 2) - box_size // 2
                checked = spec[2]
                canvas.create_rectangle(
                    pad, top, pad + box_size, top + box_size,
                    outline=TASK_CHECK_COLOR if checked else TASK_BOX_COLOR,
                    fill=TASK_CHECK_COLOR if checked else EDITOR_BG, width=1,
                )
                if checked:
                    canvas.create_line(
                        pad + box_size * 0.22, top + box_size * 0.55,
                        pad + box_size * 0.43, top + box_size * 0.76,
                        pad + box_size * 0.80, top + box_size * 0.26,
                        fill="white", width=max(1, px(2)), capstyle="round", joinstyle="round",
                    )
            else:
                # 序号字号跟正文一致（原来是固定 11px 的小字，光标移进移出还会变样）
                number_size = px(self.settings["font_size"])
                # 画布只负责给序号腾地方；文字右对齐，所以画布宽一点只是往左多占，
                # 序号本身不会被裁掉
                width = max(px(34), number_size * 2)
                height = max(px(16), int(dline[3]), self._display_line_height())
                canvas.place(x=right - width, y=dline[1], width=width, height=height)
                canvas.create_text(
                    width, int(centre_y - dline[1]), anchor="e", text=spec[2],
                    fill=ORDERED_NUMBER_COLOR,
                    font=(self._writing_font(), -number_size, "bold"),
                )
        elif kind == "quote":
            bar_x = text_left + px(LIST_INDENT) - px(14)
            height = max(px(8), int(dline[3]), self._display_line_height())
            canvas.place(x=bar_x, y=dline[1], width=px(QUOTE_BAR_W), height=height)
            canvas.create_rectangle(0, 0, px(QUOTE_BAR_W), height,
                                    fill=QUOTE_BAR_COLOR, outline="")
        else:  # hr
            thickness = max(1, px(HR_THICKNESS))
            y = int(dline[1] + dline[3] / 2 - thickness / 2)
            canvas.place(x=text_left, y=y, width=text_width, height=thickness)
            canvas.create_rectangle(0, 0, text_width, thickness, fill=HR_COLOR, outline="")

    def _redraw_decorations(self, visible: list[tuple[int, int, tuple]] | None = None) -> None:
        """按显示行绘制左侧装饰。

        每个装饰用一个只占一小块的小画布，而不是一整块覆盖层——否则会把正文盖住。
        """
        used = 0
        if (self.current_path is None and not self.preview_mode) or self._current_pad < 0:
            self._hide_decorations(0)
            return
        if visible is None:
            visible = self._visible_lines()
        cursor_line = int(self.editor.index("insert").split(".")[0])
        for number, offset, dline in visible:
            spec = self._decoration_spec(self._block_of(number), offset == 0,
                                         number == cursor_line)
            if spec is not None:
                self._draw_decoration(used, spec, dline, number)
                used += 1
        self._hide_decorations(used)

    def _block_of(self, line_number: int) -> LineInfo:
        """取某逻辑行的块级解析结果，直接用整篇解析时算好的缓存。

        以前每次重画都要 `get()` 一次行文本再 parse 一遍，可视区里几十行
        就是几十次跨语言调用；现在解析结果跟着文本一起缓存，滚动时零成本。
        """
        index = line_number - 1
        if 0 <= index < len(self._doc_blocks):
            return self._doc_blocks[index]
        return LineInfo("text")

    def _on_editor_wheel(self, event) -> str:
        """鼠标滚轮滚动编辑区：滚动量是系统默认的六倍。

        Tk 自带的 Text 绑定算的是「%D/3 像素」，这里沿用同一个公式再乘
        `EDITOR_WHEEL_FACTOR`——不同滚轮（高精度滚轮一个事件可能只有 40）、
        不同 DPI 下都正好是同一个倍数，而不是变成「一格固定跳几行」。

        必须返回 "break"：Text 的类绑定也会响应 <MouseWheel>，
        不拦住这一次事件就会被滚两遍，实际变成三倍。
        """
        delta = int(getattr(event, "delta", 0) or 0)
        if not delta:
            return "break"
        pixels = delta / 3.0
        self.editor.yview_scroll(int(round(-pixels * EDITOR_WHEEL_FACTOR)), "pixels")
        return "break"

    def _on_editor_scrolled(self, first: str, last: str) -> None:
        self.editor_scroll.set(first, last)
        if not self._gutter_pending:
            self._gutter_pending = True
            self.root.after_idle(self._flush_gutter)

    def _flush_gutter(self) -> None:
        self._gutter_pending = False
        self._redraw_margins()

    def _redraw_line_numbers(self, visible: list[tuple[int, int, tuple]] | None = None) -> None:
        """行号只在逻辑行的第一显示行标注；自动换行的续行不重复编号。"""
        if visible is None:
            visible = self._visible_lines()
        self.gutter.delete("all")
        last_shown: int | None = None
        for number, offset, info in visible:
            if offset == 0 and number != last_shown:
                self.gutter.create_text(
                    GUTTER_W - px(8), info[1], anchor="ne", text=number,
                    fill=MUTED, font=(self._writing_font(), -px(11)),
                )
                last_shown = number

    def _heading_label_at(self, line: int) -> str | None:
        """返回该记录行对应的徽标文字（H1..H6）；不是标题返回 None。

        标题行在解析时已经记下来了，这里查表即可——以前每次都问一次
        `tag_names()`，可视区里几十行就是几十次跨语言调用。
        """
        return self._heading_lines.get(line)

    def _redraw_heading_labels(self, visible: list[tuple[int, int, tuple]] | None = None) -> None:
        """在标题所在显示行左侧画出 H1/H2/H3 徽标。

        各级标题字号与正文一致后，层级只能靠加粗和留白区分，徽标把这一点说明白。
        与行号一样，只在记录行的第一显示行标注，自动换行的续行不重复。
        """
        canvas = self.heading_labels
        if not canvas.winfo_exists():
            return
        canvas.delete("all")
        if not self._heading_lines:
            return
        if visible is None:
            visible = self._visible_lines()
        seen: set[int] = set()
        for number, offset, info in visible:
            # 只在记录行的第一显示行标注；标题首行被滚出视野时不画在续行上
            if offset != 0 or number in seen:
                continue
            seen.add(number)
            label = self._heading_lines.get(number)
            if label:
                canvas.create_text(
                    HEADING_LABEL_W, self._heading_label_y(info), anchor="e",
                    text=label, fill=HEADING_LABEL_COLOR,
                    font=(FAMILY, -px(HEADING_LABEL_SIZE)),
                )

    def _heading_label_y(self, info) -> float:
        """把徽标竖直对齐到标题文字的中部。

        dlineinfo 的 info[4] 是**相对本行顶部**的基线偏移，不是绝对坐标，
        所以要先加 info[1]；再用字号的一半左右往上找视觉中心（中文字面大致
        从 baseline - 0.88em 到 baseline + 0.12em）。
        """
        return info[1] + info[4] - px(self.settings["font_size"]) * 0.36

    # ---------- 卡片列表 ----------

    def _on_cards_configure(self, event) -> None:
        if event.width != self._cards_width:
            self._cards_width = event.width
            self.root.after_idle(self._rebuild_cards)

    def _on_cards_wheel(self, event) -> None:
        self.cards_canvas.yview_scroll(
            -LIST_WHEEL_UNITS if event.delta > 0 else LIST_WHEEL_UNITS, "units")
        self._clamp_cards_scroll()

    def _on_cards_scrollbar(self, *args) -> None:
        """按 Tk 滚动条的命令协议滚一屏/一行，再夹一次原点。

        界面上**已经没有滚动条控件**了（用户要求隐藏），这个方法留着是因为：
        ① 它仍是程序化滚动的正规入口（`yview` 的那套 `scroll`/`moveto` 参数）；
        ② 「滚动条不能把列表推下去」这条回归测试直接调它，见
        tests/test_cards.py::test_scrollbar_command_cannot_push_the_list_down。
        """
        self.cards_canvas.yview(*args)
        self._clamp_cards_scroll()

    def _clamp_cards_scroll(self) -> None:
        """把画布原点夹回内容顶部。

        Tk 的 Canvas 只在「滚动区比视口高」时才夹原点。文档比视口少的时候
        滚动区更矮，这时往上滚会把原点推成负数：整列被推到下方、顶部空出
        一条（每格约视口的十分之一），而 yview() 仍然报 (0.0, 1.0)，
        滚动条上看不出任何异常，只能靠滚回去才恢复。这里手动夹住。
        """
        if self.cards_canvas.canvasy(0) < 0:
            self.cards_canvas.yview_moveto(0.0)

    def _set_cards_scrollregion(self, width: int, content_bottom: int) -> None:
        """设置滚动区，并顺手清掉残留的负原点（重建时夹一次）。"""
        self._cards_content_height = content_bottom + px(10)
        self.cards_canvas.configure(scrollregion=(0, 0, width, self._cards_content_height))
        self._clamp_cards_scroll()

    def _on_tree_wheel(self, event) -> None:
        self.folder_tree.yview_scroll(
            -LIST_WHEEL_UNITS if event.delta > 0 else LIST_WHEEL_UNITS, "units")

    def _card_index_at(self, x: int, y: int) -> int | None:
        """命中判定：返回 (x, y) 处的卡片序号，坐标是**控件**坐标。

        以前是把 `event.x/event.y` 直接喂给 `canvas.find_overlapping`。可那是**控件**
        坐标，而 `find_overlapping` 要的是**画布**坐标——列表没滚动时两者恰好一样，
        一滚动就差一个原点：点第 12 张会选中第 0 张，点靠上的卡片干脆什么都撞不到。
        所以这里先把 y 换算到画布坐标（守它的是 tests/test_cards.py 的
        CardClickAndScrollTests，量它的是 tools/_check_card_click.py）。

        也不用再去撞图元了：卡片本来就是自己画的，重建时把每张的可点范围记在
        `_card_bounds` 里，直接查表更准也更快（一次点击省掉一次跨语言调用）。
        范围往上下各撑开半个间隙，于是卡片之间那条 12px 的空隙、以及左右留白
        都归最近的卡片——用户看着明明点在卡片上，就不该没反应。

        横向不参与判定：这一列里除了卡片就是留白，点哪儿都是想选那一张。
        """
        if not self._card_bounds:
            return None
        canvas_y = self.cards_canvas.canvasy(y)
        for order, (top, bottom) in enumerate(self._card_bounds):
            if top <= canvas_y <= bottom:
                return order
        return None

    def _card_at(self, x: int, y: int) -> Path | None:
        """返回坐标处的文档路径；回收站条目返回 None。"""
        order = self._card_index_at(x, y)
        if order is None or self.trash_mode:
            return None
        if 0 <= order < len(self.file_paths):
            return self.file_paths[order]
        return None

    def _on_cards_left_click(self, event) -> None:
        order = self._card_index_at(event.x, event.y)
        if order is None:
            return
        if self.trash_mode:
            if 0 <= order < len(self.trash_entries):
                self.preview_trash_entry(self.trash_entries[order])
            return
        if 0 <= order < len(self.file_paths):
            self.open_file(self.file_paths[order])

    def _on_cards_right_click(self, event) -> None:
        order = self._card_index_at(event.x, event.y)
        if self.trash_mode:
            if order is None or not (0 <= order < len(self.trash_entries)):
                return
            menu = self._trash_menu(self.trash_entries[order])
        elif order is None:
            # 点的是卡片之间的空隙、或列表下方的空白。这里没有「针对某一篇」的操作，
            # 但排序是**整列**的属性，放这儿正合适——而且文稿少的时候卡片之外全是
            # 空白，不给菜单等于没有入口。
            menu = self._sort_only_menu()
        elif 0 <= order < len(self.file_paths):
            menu = self._doc_menu(self.file_paths[order])
        else:
            return
        self._card_menu = menu
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _move_targets(self, path: Path) -> list[tuple[str, Path]]:
        """可以移到的文件夹：[(显示名, 路径)]。

        跳过文档当前所在的文件夹（移过去等于没动），根目录显示成「全部文稿」。

        只列**一层**（`list_subfolders` 的默认），和文件夹树保持一致：树上只有一级，
        菜单里却冒出 `读书/随笔` 这种路径，用户会以为文档挪到自己看不见的地方去了。
        """
        if self.folder is None:
            return []
        choices: list[tuple[str, Path]] = []
        if path.parent != self.folder:
            choices.append(("全部文稿（根目录）", self.folder))
        for sub in list_subfolders(self.folder):
            if sub == path.parent:
                continue
            choices.append((sub.relative_to(self.folder).as_posix(), sub))
        return choices

    def _folder_label(self, path: Path) -> str:
        """文档所在文件夹的显示名：根目录显示「全部文稿」，其余显示相对路径。"""
        if self.folder is None:
            return path.parent.name
        if path.parent == self.folder:
            return "全部文稿"
        try:
            return path.parent.relative_to(self.folder).as_posix()
        except ValueError:
            return path.parent.name

    def _doc_menu(self, path: Path) -> tk.Menu:
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="打开", command=lambda: self.open_file(path))
        menu.add_command(label="在资源管理器中显示", command=lambda: self._reveal(path))
        # 导出长图原先在顶栏，现在集中到这里：右键哪一篇就导出哪一篇
        menu.add_command(label="导出长图", command=lambda: self.export_long_image(path))
        menu.add_command(label="导出音频", command=lambda: self.export_audio(path))

        targets = self._move_targets(path)
        move_menu = tk.Menu(menu, tearoff=0)
        if targets:
            for label, folder in targets:
                move_menu.add_command(
                    label=label,
                    command=lambda target=folder: self.move_document_to(path, target),
                )
        else:
            move_menu.add_command(label="没有其他文件夹", state="disabled")
        move_menu.add_separator()
        move_menu.add_command(
            label="新建文件夹并移动…",
            command=lambda: self.new_folder_and_move(path),
        )
        menu.add_cascade(label="移动到", menu=move_menu)

        menu.add_separator()
        menu.add_command(label="删除文档（移到回收站）", command=lambda: self.delete_document(path))
        menu.add_separator()
        self._add_sort_cascade(menu)
        return menu

    def _add_sort_cascade(self, parent: tk.Menu) -> None:
        """把「排序：XXX」作为级联项挂到 `parent` 菜单末尾。

        标签里直接写着当前用的是哪种，不用点开就知道（原来标题栏那个按钮的作用
        就是这个，现在挪到菜单里，不再占标题栏的地方）。
        """
        parent.add_cascade(label=self._sort_label_text(), menu=self._sort_menu())

    def _sort_only_menu(self) -> tk.Menu:
        """空白处的右键菜单：只有排序，没有针对某一篇的操作。"""
        menu = tk.Menu(self.root, tearoff=0)
        self._add_sort_cascade(menu)
        return menu

    def _card_gap(self) -> int:
        """相邻卡片之间的间隔。

        原来是 `px(12)`，用户要求减半——预览区塞得下更多文稿。
        单独抽成方法是为了让测试能直接对着它断言，而不是把 `px(6)` 抄一遍。
        """
        return CARD_GAP

    def _card_text_height(self, canvas, text: str, font_spec, text_width: int) -> int:
        """试排一段文字并返回它占用的像素高度（量完即删，不留痕迹）。"""
        item = canvas.create_text(0, 0, anchor="nw", text=text, font=font_spec,
                                  width=text_width)
        box = canvas.bbox(item)
        canvas.delete(item)
        return (box[3] - box[1]) if box else 0

    def _trim_card_text(self, canvas, text: str, font_spec, text_width: int,
                        allowed_height: int) -> str:
        """把卡片正文裁剪到不超过 allowed_height（按显示行算，长行折行也计入）。

        这是重建卡片时最贵的一步（实测 60 篇 × 4300 行时占 `refresh_files` 的 85%）。
        每一次探测都是 `create_text` + `bbox` + `delete`，而 Tk 要为**整段文字**折一次
        行，代价随长度线性涨。所以：

        1. **探测长度从短到长成倍增长**，再在最后一小段里二分。答案通常只有一两百字，
           一上来就拿上千字的全文去试排，那一步比后面所有探测加起来还贵。
        2. 结果缓存起来。同一段摘录、同样的宽度和高度预算，答案一定一样；点另一张
           卡片时整批都能直接命中（键里带上字体 spec，字体一改就失效）。

        注意省略号本身也算一个字，必须连同它一起量高度，否则会多出一行。
        """
        if allowed_height <= 0:
            return text
        key = (text, font_spec, text_width, allowed_height)
        cached = self._trim_cache.get(key)
        if cached is not None:
            return cached
        result = self._trim_card_text_uncached(canvas, text, font_spec, text_width,
                                               allowed_height)
        if len(self._trim_cache) > 1024:
            self._trim_cache.clear()
        self._trim_cache[key] = result
        return result

    def _trim_card_text_uncached(self, canvas, text: str, font_spec, text_width: int,
                                 allowed_height: int) -> str:
        def fits(candidate: str) -> bool:
            return self._card_text_height(canvas, candidate, font_spec, text_width) <= allowed_height

        if fits(text):
            return text

        # 先成倍往上探，找到「刚好放不下」的那一档；探测的字符串都不长，很便宜
        limit = len(text)
        step = 32
        low = 0
        while low < limit:
            probe = min(limit, low + step)
            if not fits(text[:probe] + "…"):
                break
            low = probe
            step *= 2
        high = min(limit, low + step)

        # 再在 [low, high] 里二分，找最长可容纳前缀
        while low < high:
            mid = (low + high + 1) // 2
            if fits(text[:mid] + "…"):
                low = mid
            else:
                high = mid - 1

        # 去掉结尾的空白与标点，避免出现「。…」这种别扭的收尾
        trimmed = text[:low].rstrip().rstrip("，。、；：,.;:!?！？…—")
        return (trimmed + "…") if trimmed else "…"

    def _rebuild_cards(self) -> None:
        canvas = self.cards_canvas
        canvas.delete("all")
        # 可点范围跟着一起重建，免得留下上一批卡片的旧坐标
        self._card_bounds = []
        width = self._cards_width if self._cards_width > 1 else CARDS_W
        card_x1, card_x2 = px(14), max(px(120), width - px(20))
        pad_x, pad_top, pad_bottom = px(14), px(14), px(14)
        text_width = card_x2 - card_x1 - pad_x * 2
        writing = CARD_FONT_FAMILY
        # 预览栏字号**固定**，不跟编辑区走（用户要求）：编辑区调字号是为了自己写着舒服，
        # 预览栏要的是一眼扫过去能看清哪篇是哪篇。
        size = px(CARD_FONT_SIZE)
        radius, gap = px(12), self._card_gap()
        # 命中范围往上下各撑开半个间隙，卡片之间那条空隙就归最近的卡片
        # （取上整，间隙是奇数时也不会在正中间留下一条谁也点不到的一像素缝）
        half_gap = (gap + 1) // 2
        y = px(6)

        if self.trash_mode:
            for order, entry in enumerate(self.trash_entries):
                original = Path(entry.original_relative)
                title = original.stem or entry.trash_name
                summary = f"原位置：{entry.original_relative} · 删除于 {entry.deleted_at}"
                click_tag = f"card{order}"
                bg_tag = f"cardbg{order}"
                shadow_tag = f"cardshadow{order}"
                canvas.create_text(
                    card_x1 + pad_x, y + pad_top, anchor="nw", text=title, fill=TEXT,
                    font=(writing, -size, "bold"), width=text_width, tags=(click_tag,),
                )
                summary_item = canvas.create_text(
                    card_x1 + pad_x, y + pad_top + size + px(8), anchor="nw", text=summary,
                    fill=MUTED, font=(writing, -max(px(10), size - px(3))),
                    width=text_width, tags=(click_tag,),
                )
                box = canvas.bbox(summary_item) or (0, y + pad_top, 0, y + pad_top + size)
                card_height = max(px(78), box[3] - y + pad_bottom)
                _round_rect(canvas, card_x1 + px(2), y + px(3), card_x2 + px(2), y + px(3),
                            radius, fill=CARD_SHADOW, outline="", stipple="gray50",
                            tags=(shadow_tag,))
                _round_rect(canvas, card_x1, y, card_x2, y + card_height, radius,
                            fill=CARD_BG, outline=BORDER, tags=(bg_tag,))
                canvas.tag_lower(bg_tag)
                self._card_bounds.append((max(0, y - half_gap),
                                          y + card_height + half_gap))
                y += card_height + gap
            self._set_cards_scrollregion(width, y)
            self._cards_signature_cache = self._cards_signature()
            self._cards_selected = None      # 回收站那一列没有「选中」这回事
            return

        row_px = max(1, self._font_linespace(CARD_FONT_SIZE, CARD_FONT_FAMILY))
        allowed_height = row_px * CARD_EXCERPT_ROWS

        for order, path in enumerate(self.file_paths):
            # 第二列只显示正文，不显示文档标题
            excerpt = self._card_excerpt_cached(path)
            selected = path == self.current_path
            click_tag = f"card{order}"
            bg_tag = f"cardbg{order}"
            # 阴影单独一个标签：`_retint_cards` 换选中色时会整批 `itemconfigure` 背景标签，
            # 阴影要是混在同一个标签里就会被一起染成卡片底色，阴影就没了。
            shadow_tag = f"cardshadow{order}"
            body_fill = CARD_SELECTED if selected else CARD_BG
            text_fill = CARD_SELECTED_TEXT if selected else TEXT
            font_spec = (writing, -size)
            # 按实测显示高度裁剪，长行折行也不会撑破卡片
            excerpt = self._trim_card_text(canvas, excerpt, font_spec, text_width,
                                           allowed_height)

            # 先放文字（用于量高），再放背景并压到文字下方
            text_item = canvas.create_text(
                card_x1 + pad_x, y + pad_top, anchor="nw", text=excerpt,
                fill=text_fill, font=font_spec, width=text_width, tags=(click_tag,),
            )
            box = canvas.bbox(text_item) or (0, y + pad_top, 0, y + pad_top + size)
            card_height = max(px(52), box[3] - y + pad_bottom)

            _round_rect(canvas, card_x1 + px(2), y + px(3), card_x2 + px(2), y + px(3),
                        radius, fill=CARD_SHADOW, outline="", stipple="gray50",
                        tags=(shadow_tag,))
            _round_rect(canvas, card_x1, y, card_x2, y + card_height, radius,
                        fill=body_fill, outline="" if selected else BORDER, tags=(bg_tag,))
            canvas.tag_lower(bg_tag)
            self._card_bounds.append((max(0, y - half_gap),
                                      y + card_height + half_gap))
            y += card_height + gap
        self._set_cards_scrollregion(width, y)
        # 记下「这批卡片是按什么建的」：`refresh_files` 下次就能判断出
        # 「列表没变，只是换了当前打开的那一篇」，从而只换选中色不重建。
        self._cards_signature_cache = self._cards_signature()
        self._cards_selected = (self.file_paths.index(self.current_path)
                                if self.current_path in self.file_paths else None)

    def _cards_signature(self) -> tuple:
        """重建卡片所依赖的全部输入。

        点一张卡片走的是 `open_file` → `refresh_files`，而重建要把每一篇文稿都读
        一遍盘、再逐张试排文字（实测 60 篇 × 4300 行时 815 ms，用户感受到的就是
        「点一下卡半天」）。列表没变时只换选中色，每张卡片两次跨语言调用就够。

        签名里**带上每篇的 mtime/size**：刚编辑过的那一篇摘要要跟着更新，
        不能拿旧摘要糊弄；换了排序、改了面板宽度、换了主题也都得重建。
        """
        if self.trash_mode:
            return ("trash", self._cards_width, CARD_EXCERPT_ROWS, self.theme,
                    tuple((entry.trash_name, entry.deleted_at)
                          for entry in self.trash_entries))
        stamps = []
        for path in self.file_paths:
            try:
                stat = path.stat()
                stamps.append((stat.st_mtime_ns, stat.st_size))
            except OSError:
                stamps.append(None)
        return ("docs", self._cards_width, CARD_EXCERPT_ROWS, self.theme,
                self.settings["sort_key"], tuple(self.file_paths), tuple(stamps))

    def _retint_cards(self) -> None:
        """只改选中态：把**变了的这两张**卡片的底色和文字色换过来。

        `itemconfigure(标签, ...)` 一次就改掉该标签下的所有图元，但一次调用就是一次
        跨语言往返——60 张卡片挨个刷一遍要 40 ms（实测）。所以只动「上一张选中的」
        和「这一张选中的」两张，四次调用就够。
        """
        canvas = self.cards_canvas
        index = (self.file_paths.index(self.current_path)
                 if self.current_path in self.file_paths else None)
        previous = self._cards_selected
        if previous == index:
            return
        self._cards_selected = index
        for order in (previous, index):
            if order is None:
                continue
            selected = order == index
            canvas.itemconfigure(
                f"cardbg{order}",
                fill=CARD_SELECTED if selected else CARD_BG,
                outline="" if selected else BORDER,
            )
            canvas.itemconfigure(
                f"card{order}",
                fill=CARD_SELECTED_TEXT if selected else TEXT,
            )

    def _card_excerpt_cached(self, path: Path) -> str:
        """带缓存的摘录：文件没动过就直接复用上次算好的结果。

        `refresh_files` 要给**每一张**卡片算摘录，而它得读整篇文稿再逐行走
        `parse_block`。点一张卡片也会走到这里，文稿一多就是几十次多余的读盘
        （实测 60 篇 × 4300 行时 75 ms）。键里带 mtime/size，改了文件自然失效。
        """
        try:
            stat = path.stat()
        except OSError:
            return _card_excerpt(path)
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        cached = self._excerpt_cache.get(key)
        if cached is not None:
            return cached
        excerpt = _card_excerpt(path)
        if len(self._excerpt_cache) > 512:
            self._excerpt_cache.clear()
        self._excerpt_cache[key] = excerpt
        return excerpt

    def _scroll_cards_to_current(self) -> None:
        """把当前文稿的卡片滚进视口——已经整张看得见就**一点都不要动**。

        以前写的是 `yview_moveto(index / total - 0.15)`：把「第几张 ÷ 共几张」当成
        滚动比例用了。可 `yview_moveto` 要的是**内容高度**的比例，而卡片高度是按
        摘录行数变的（1～4 行都有），两者根本对不上——点一张卡片，列表会跳到一个
        看着莫名其妙的地方；更糟的是明明已经看得见也照跳。所以改成按卡片的
        **真实几何**来算，并且只在它露不全的时候才滚。
        （守它的是 tests/test_cards.py 的 CardClickAndScrollTests。）
        """
        if not self.file_paths or self.current_path not in self.file_paths:
            return
        canvas = self.cards_canvas
        index = self.file_paths.index(self.current_path)
        box = canvas.bbox(f"cardbg{index}")
        if box is None:                     # 这一张没画出来（不在当前筛选里）
            return
        content = self._cards_content_height
        viewport = canvas.winfo_height()
        if content <= 0 or viewport <= 0:
            return
        top = canvas.canvasy(0)
        card_top, card_bottom = box[1], box[3]
        if card_top >= top and card_bottom <= top + viewport:
            return                          # 整张都在视口里，别动它
        # 露不全就整张挪进来：在上沿之上就顶到上沿，在下面就把下沿对齐视口底
        target = card_top if card_top < top else card_bottom - viewport
        target = min(max(0.0, target), max(0.0, content - viewport))
        canvas.yview_moveto(target / content)
        self._clamp_cards_scroll()

    # ---------- 文件夹与状态 ----------

    def _restore_state(self) -> None:
        folder = get_default_folder()
        if folder is None:
            folder = default_journal_folder()
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                messagebox.showerror("无法创建默认日记文件夹", f"简记无法创建日记文件夹：\n{folder}\n\n{error}")
                return
            set_default_folder(folder)
        self.set_folder(folder)

    def choose_folder(self) -> None:
        chosen = filedialog.askdirectory(title="选择日记文件夹")
        if chosen:
            self.set_folder(Path(chosen))

    def set_current_as_default(self) -> None:
        if self.folder is None:
            messagebox.showinfo("先选择文件夹", "请先选择一个日记文件夹，再设为默认。")
            return
        set_default_folder(self.folder)
        messagebox.showinfo("已设为默认", f"以后打开简记会直接进入：\n{self.folder}")

    def set_folder(self, folder: Path) -> None:
        if not folder.is_dir():
            messagebox.showerror("无法打开", "选择的文件夹不存在。")
            return
        if not self._finish_pending_edit():
            return
        self.folder = folder
        self.scope_folder = None
        set_default_folder(folder)
        self.folder_label.configure(text=str(folder))
        self._rebuild_tree()
        self.refresh_files()
        if not self.file_paths:
            # 空文件夹给一篇「新建文档」当落脚点（原来建的是当天的日记，
            # 「今天」功能已按用户要求取消）。
            self.new_document()

    def show_all(self) -> None:
        self.scope_folder = None
        self.trash_mode = False
        self._sync_tree_selection()
        self.refresh_files()
        self._scroll_cards_to_current()

    def refresh_files(self, select: Path | None = None) -> None:
        if self.folder is None:
            self.file_paths = []
            self.trash_entries = []
        elif self.trash_mode:
            self.file_paths = []
            self.trash_entries = list_trash(self.folder)
        else:
            self.trash_entries = []
            scope = self.scope_folder if self.scope_folder and self.scope_folder.is_dir() else None
            self.file_paths = list_markdown(scope or self.folder, recursive=scope is None,
                                            sort_key=self.settings["sort_key"])
        if self.trash_mode:
            title = f"回收站（{len(self.trash_entries)}）"
        elif self.scope_folder:
            title = self.scope_folder.name
        else:
            title = "全部文稿"
        self.cards_title.configure(text=title)
        target = select or self.current_path
        # 列表本身没变（只是换了「当前打开的那一篇」）→ 只换选中色，不整批重建。
        # 重建要读盘 + 逐张试排文字，是「点一下卡半天」的根源（见 _cards_signature）。
        # 回收站那一列没有「选中」这回事，走原路。
        if not self.trash_mode and self._cards_signature() == self._cards_signature_cache:
            self._retint_cards()
        else:
            self._rebuild_cards()
        if target is not None:
            self._scroll_cards_to_current()

    # ---------- 文稿排序 ----------

    def _sort_label_text(self) -> str:
        """「排序：按修改时间」——当前用的是哪种，写在菜单项的标签里。"""
        return f"排序：{SORT_LABEL_BY_KEY.get(self.settings['sort_key'], '按名称')}"

    def _sort_menu(self) -> tk.Menu:
        """排序菜单。用单选按钮，当前用的那种自动打勾。

        单选按钮的 variable 绑在 `sort_var` 上，所以菜单一弹出来就显示当前状态；
        同时 `set_sort_key` 也会去改 `sort_var`，两边不会脱节。
        """
        menu = tk.Menu(self.root, tearoff=0)
        for key, label in SORT_LABELS:
            menu.add_radiobutton(label=label, value=key, variable=self.sort_var,
                                 command=lambda chosen=key: self.set_sort_key(chosen))
        return menu

    def set_sort_key(self, key: str) -> None:
        """换一种排序方式并立刻重排卡片；当前打开的那篇仍然留在视野里。"""
        if key not in SORT_LABEL_BY_KEY:
            return
        self.sort_var.set(key)
        if key == self.settings["sort_key"]:
            return
        self.settings["sort_key"] = key
        set_settings({"sort_key": key})
        # 回收站列表有自己的顺序（按删除时间），这里只重排文稿列表
        if not self.trash_mode:
            self.refresh_files()

    # ---------- 文件夹树 ----------

    def _rebuild_tree(self) -> None:
        tree = self.folder_tree
        self._tree_updating = True
        try:
            tree.delete(*tree.get_children())
            self._tree_nodes = {}
            if self.folder is None:
                return
            tree.insert("", "end", iid="__all__", text="全部文稿", open=True)
            self._tree_nodes[None] = "__all__"
            self._tree_nodes[self.folder] = "__all__"
            # 文件夹**只有一级**，而且与「全部文稿」**并列**（用户要求）：
            # 都挂在根上，不是挂在「全部文稿」下面。之前是递归列举 + 按 parent
            # 找父节点，于是文件夹会层层嵌套，「全部文稿」看着像个容器。
            for sub in list_subfolders(self.folder):
                iid = str(sub)
                tree.insert("", "end", iid=iid, text=sub.name, open=True)
                self._tree_nodes[sub] = iid
            trash_count = len(list_trash(self.folder))
            trash_label = f"回收站（{trash_count}）" if trash_count else "回收站"
            tree.insert("", "end", iid="__trash__", text=trash_label, open=True)
        finally:
            self._tree_updating = False
        self._sync_tree_selection()

    def _sync_tree_selection(self) -> None:
        if not self._tree_nodes:
            return
        if self.trash_mode:
            iid = "__trash__"
        else:
            key = self.scope_folder if self.scope_folder in self._tree_nodes else None
            iid = self._tree_nodes.get(key, "__all__")
        self._tree_updating = True
        try:
            self.folder_tree.selection_set(iid)
            self.folder_tree.see(iid)
        finally:
            self._tree_updating = False

    def _apply_tree_selection(self, iid: str) -> bool:
        if iid == "__trash__":
            self.trash_mode = True
            self.scope_folder = None
        elif iid == "__all__":
            self.trash_mode = False
            self.scope_folder = None
        else:
            candidate = Path(iid)
            if not candidate.is_dir():
                return False
            self.trash_mode = False
            self.scope_folder = candidate
        return True

    def _current_tree_iid(self) -> str:
        if self.trash_mode:
            return "__trash__"
        if self.scope_folder is not None:
            return str(self.scope_folder)
        return "__all__"

    def _on_tree_select(self, _event=None) -> None:
        if self._tree_updating:
            return
        selection = self.folder_tree.selection()
        if not selection:
            return
        iid = selection[0]
        if iid == self._current_tree_iid():
            return  # 幂等保护：避免选中事件自循环
        if self._apply_tree_selection(iid):
            self.refresh_files()

    def _on_tree_right_click(self, event) -> None:
        iid = self.folder_tree.identify_row(event.y)
        if not iid:
            return
        self._tree_updating = True
        try:
            self.folder_tree.selection_set(iid)
        finally:
            self._tree_updating = False
        if self._apply_tree_selection(iid):
            self.refresh_files()

        menu = self._tree_menu(iid)
        self._tree_menu_ref = menu
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _on_folder_caption_right_click(self, event) -> None:
        """右键「文件夹」那行灰色小标题：弹这一栏的菜单。

        和右键树里的某一项不同——这里**不选中、也不切换**当前文件夹，
        只是把「对这一栏能做的事」列出来（新建文件夹 / 在资源管理器中打开 / 刷新），
        所以直接复用「全部文稿」节点那一套菜单。
        """
        menu = self._tree_menu("__all__")
        self._tree_menu_ref = menu
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _tree_menu(self, iid: str) -> tk.Menu:
        menu = tk.Menu(self.root, tearoff=0)
        if iid == "__trash__":
            menu.add_command(label="在资源管理器中打开", command=lambda: self._reveal(self._trash_folder()))
            menu.add_command(label="清空回收站（移到系统回收站）", command=self.empty_trash)
            menu.add_separator()
            menu.add_command(label="刷新", command=self._refresh_tree_and_cards)
            return menu

        # 文件夹只有一级，所以**新建文件夹只在「全部文稿」上给**：文件夹上不再有
        # 「新建子文件夹」，从入口上就杜绝嵌套（用户要求「不可在文件夹下新建文件夹」）。
        if iid == "__all__":
            menu.add_command(label="新建文件夹…", command=self.new_folder)
        target_folder = Path(iid) if iid != "__all__" else self.folder
        if target_folder is not None:
            menu.add_command(label="在资源管理器中打开",
                             command=lambda: self._reveal(target_folder))
        menu.add_separator()
        if iid != "__all__":
            menu.add_command(
                label="删除文件夹（移到回收站）",
                command=lambda: self.delete_folder(Path(iid)),
            )
        menu.add_command(label="刷新", command=self._refresh_tree_and_cards)
        return menu

    def _trash_folder(self) -> Path | None:
        if self.folder is None:
            return None
        return trash_entry_path(self.folder, "").parent

    # ---------- 回收站 ----------

    def empty_trash(self) -> None:
        if self.folder is None:
            return
        entries = list_trash(self.folder)
        if not entries:
            messagebox.showinfo("回收站", "回收站已经是空的。")
            return
        confirmed = messagebox.askyesno(
            "清空回收站",
            f"确定清空回收站中的 {len(entries)} 个文档吗？\n\n"
            "它们会被移到系统回收站，仍可从系统回收站恢复。",
            icon="warning",
        )
        if not confirmed:
            return
        failed = 0
        for entry in entries:
            try:
                purge_trash_entry(self.folder, entry.trash_name)
            except (OSError, FileNotFoundError):
                failed += 1
        self.refresh_files()
        self._rebuild_tree()
        if failed:
            messagebox.showwarning("部分未清空", f"有 {failed} 个文档未能清空，请稍后重试。")

    def restore_trash_entry(self, entry) -> None:
        if self.folder is None:
            return
        try:
            restored = restore_from_trash(self.folder, entry.trash_name)
        except (OSError, FileNotFoundError) as error:
            messagebox.showerror("还原失败", f"无法还原该文档：\n{error}")
            return
        self._clear_editor()
        self._rebuild_tree()
        self.refresh_files()
        self.status_label.configure(text=f"已还原：{restored.name}")

    def purge_single_entry(self, entry) -> None:
        if self.folder is None:
            return
        try:
            purge_trash_entry(self.folder, entry.trash_name)
        except (OSError, FileNotFoundError) as error:
            messagebox.showerror("删除失败", f"无法彻底删除该文档：\n{error}")
            return
        self._clear_editor()
        self._rebuild_tree()
        self.refresh_files()

    def preview_trash_entry(self, entry) -> None:
        if self.folder is None:
            return
        path = trash_entry_path(self.folder, entry.trash_name)
        try:
            text, _signature = read_markdown(path)
        except (OSError, UnicodeError) as error:
            messagebox.showerror("打开失败", f"无法读取该文档：\n{error}")
            return
        self.current_path = None
        self.disk_signature = None
        self.dirty = False
        self.preview_mode = True
        self._load_editor_text(text)
        self.editor.configure(state="disabled")
        self.status_label.configure(text=f"回收站（只读）· 原位置：{entry.original_relative}")
        self._apply_markdown_styles()

    def _trash_menu(self, entry) -> tk.Menu:
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="查看内容", command=lambda: self.preview_trash_entry(entry))
        menu.add_command(label="还原到原位置", command=lambda: self.restore_trash_entry(entry))
        menu.add_separator()
        menu.add_command(label="彻底删除（移到系统回收站）", command=lambda: self.purge_single_entry(entry))
        return menu

    def _refresh_tree_and_cards(self) -> None:
        self._rebuild_tree()
        self.refresh_files()

    def new_folder(self) -> None:
        """新建一个文件夹——**只建在日记文件夹根下**，与「全部文稿」并列。

        界面上的文件夹只有一级（用户要求），所以这里不接受父目录参数：
        无论从哪里点进来，新文件夹都落在 `self.folder` 下。
        """
        if self.folder is None:
            return
        name = self._ask_folder_name("新建文件夹", "文件夹名称：")
        if name is None:
            return
        try:
            created = create_folder(self.folder, name)
        except FileExistsError:
            messagebox.showinfo("已存在", "同名文件夹已存在。")
            return
        except OSError as error:
            messagebox.showerror("创建失败", f"无法创建文件夹：\n{error}")
            return
        self.scope_folder = created
        self._rebuild_tree()
        self.refresh_files()

    def delete_folder(self, folder: Path) -> None:
        if self.folder is not None and folder == self.folder:
            messagebox.showinfo("不能删除", "这是当前的日记文件夹，请先在设置中更换文件夹。")
            return
        if not folder.is_dir():
            return
        total = count_files(folder)
        confirmed = messagebox.askyesno(
            "删除文件夹",
            f"确定把文件夹「{folder.name}」移到回收站吗？\n\n"
            f"其中包含 {total} 个文件。\n"
            "删除后可从回收站恢复。",
            icon="warning",
        )
        if not confirmed:
            return
        if self.current_path is not None and folder in self.current_path.parents:
            if not self._finish_pending_edit():
                return
        try:
            delete_to_recycle_bin(folder)
        except (OSError, FileNotFoundError) as error:
            messagebox.showerror("删除失败", f"无法删除该文件夹：\n{error}")
            return
        if self.current_path is not None and folder in self.current_path.parents:
            self._clear_editor()
        self.scope_folder = None
        self._rebuild_tree()
        self.refresh_files()

    def _reveal(self, path: Path | None) -> None:
        if path is None:
            return
        try:
            reveal_in_explorer(path)
        except (OSError, FileNotFoundError) as error:
            messagebox.showerror("无法打开", str(error))

    # ---------- 文档右键菜单 ----------

    def _ask_folder_name(self, title: str, prompt: str) -> str | None:
        """问一个合法的文件夹名。取消、空名或含非法字符都返回 None。"""
        name = simpledialog.askstring(title, prompt, parent=self.root)
        if name is None:
            return None
        name = name.strip()
        if not name:
            return None
        if any(char in name for char in '\\/:*?"<>|'):
            messagebox.showerror("名称无效", '文件夹名称不能包含 \\ / : * ? " < > |')
            return None
        return name

    def move_document_to(self, path: Path, target: Path) -> None:
        """把文档移到指定文件夹。重名自动加序号，绝不覆盖别人的文件。"""
        if self.folder is None or not path.exists():
            self.refresh_files()
            return
        was_current = path == self.current_path
        if was_current and self.dirty and not self.save_now():
            return
        if was_current:
            # 刚才那次存盘可能按标题把文件改了名（见 `_follow_title`），
            # 手里的 path 会指向一个已经不存在的名字
            path = self.current_path
        try:
            moved = move_to_folder(path, target, self.folder)
        except (OSError, FileNotFoundError) as error:
            messagebox.showerror("移动失败", f"无法移动该文档：\n{error}")
            return
        if moved == path:
            self.status_label.configure(text="该文档已经在这个文件夹里了")
            return
        if was_current:
            # 编辑器继续编辑同一个文件，只是换了位置；磁盘签名跟着文件走，仍然有效
            self.current_path = moved
        self.refresh_files()
        self.status_label.configure(text=f"已移动到：{self._folder_label(moved)}")

    def new_folder_and_move(self, path: Path) -> None:
        """新建一个文件夹并把文档移进去。文件夹与「全部文稿」并列，只有一级。"""
        if self.folder is None:
            return
        name = self._ask_folder_name("新建文件夹并移动", "新文件夹名称：")
        if name is None:
            return
        try:
            created = create_folder(self.folder, name)
        except FileExistsError:
            messagebox.showinfo("已存在", "同名文件夹已存在，可直接移动到它。")
            return
        except OSError as error:
            messagebox.showerror("创建失败", f"无法创建文件夹：\n{error}")
            return
        self._rebuild_tree()
        self.move_document_to(path, created)

    def delete_document(self, path: Path) -> None:
        """删除文档：不弹确认，直接移入应用内回收站（可还原）。"""
        if not path.exists():
            self.refresh_files()
            return
        if self.folder is None:
            return
        was_current = path == self.current_path
        if was_current and self.dirty and not self.save_now():
            return
        if was_current:
            # 存盘可能已按标题改名，重新取一次路径，否则会拿着旧名字去回收
            path = self.current_path
        try:
            entry = move_to_trash(path, self.folder)
        except (OSError, FileNotFoundError) as error:
            messagebox.showerror("删除失败", f"无法删除该文档：\n{error}")
            return
        if was_current:
            self._clear_editor()
        self._rebuild_tree()
        self.refresh_files()
        self.status_label.configure(text=f"已移到回收站：{entry.original_relative}")

    def _ensure_trailing_newline(self, text: str) -> str:
        """正文进编辑器之前，末尾若没有换行符就补一个，返回补好的正文。

        **为什么非要末尾那一行是空行**：底部留白是给最后一行挂的 `spacing3`，
        而 Tk 画选中高亮（和标签背景）时把**整个显示行盒子**（含 `spacing3`）
        一起涂色。只有让末尾多出一个空行、留白挂在那个空行上，选中上面那行正文
        才画不到留白。实测（用户设置，视口 1071px、留白 267px）：

          末尾无换行 → 留白标签落在正文行上 → 阴影高 310px = 留白 267 + 行自身高
          末尾有换行 → 留白标签落在空行上   → 阴影高  51px = 只剩行自身高（干净）

        **关键是两者相差 ≈ 留白值**（310 − 51 = 259 ≈ 267）：留白有多大，阴影就多出多大。
        绝对像素会随文稿和设置浮动（`tools/_probe_pad_tag_range.py` 量过几种结尾），
        别把 310/51 当成常量。

        用户看到的正是第一种：「选中最后一行的文字后，阴影会覆盖下面留白」。
        实测用户文稿里 21 份有 5 份末尾没有换行（含一份 4.8KB 的日记）。

        **标签怎么摆都躲不开**（逐种摆法都实测过）：空区间 `end..end` 会被 Tk
        直接丢掉（`tag_ranges` 为空、留白整段失效）；`end-1c..end` 只盖住换行符，
        留白同样失效；`end-1c linestart..end-1c lineend` 与 `..end` 都盖住留白。
        标签的 `background` 也一样盖住留白，所以「自己画选中高亮」这条路也堵死。

        **补在字符串上而不是插进控件里**：插一下会再发一次 `<<Modified>>`，
        等于打开一份文稿多跑一遍重解析；拼进 `insert` 的入参就只有一次事件。
        空文稿不补（没有正文行，留白本来就在空行上）。

        副作用：这类文稿存盘后末尾会多一个换行符（`save_now` 写的就是控件里的正文）。
        用户已确认接受——几乎所有编辑器都这么干。
        """
        if text and not text.endswith("\n"):
            return text + "\n"
        return text

    def _load_editor_text(self, text: str, cursor: str | None = None) -> None:
        """把整篇正文换进编辑器，并把撤销栈与脏标志一并重置。

        **这一串的顺序不能改**：`edit_reset()` 必须在 `insert()` 之后，否则撤销栈里
        还留着上一次的正文；`edit_modified(False)` 必须最后，它清的是 `insert()`
        触发出来的那个脏标志。

        三处调用（打开文档、打开回收站条目、主题重建）原来各抄一遍，现在改这里
        就是改全部。只读场景（回收站）由调用方在这之后自己 `configure(state="disabled")`
        ——本函数一律把编辑器留在可写状态，因为调用方紧接着还要改正文之外的东西。

        末尾补的那个换行必须补在 `edit_reset()` **之前**：这样它不进撤销栈
        （否则 Ctrl+Z 会先把留白用的空行撤掉），也不算「用户改动」，
        打开文稿时不会立刻变脏、也就不会平白触发一次存盘。
        """
        self.editor.configure(state="normal")
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", self._ensure_trailing_newline(text))
        if cursor is not None:
            self.editor.mark_set("insert", cursor)
        self.editor.edit_reset()
        self.editor.edit_modified(False)

    def _clear_editor(self) -> None:
        self.current_path = None
        self.disk_signature = None
        self._rename_failed_for = None
        self.preview_mode = False
        self.dirty = False
        if self.save_job:
            self.root.after_cancel(self.save_job)
            self.save_job = None
        self.editor.configure(state="normal")
        self.editor.delete("1.0", "end")
        self.editor.configure(state="disabled")
        self.status_label.configure(text="")
        self.gutter.delete("all")

    # ---------- 文档 ----------

    def _ensure_folder(self) -> bool:
        """没有日记文件夹时先落到默认文件夹，返回「现在可用吗」。

        原来这里是「打开今天的日记」的一部分。**「今天」这个入口已经按用户要求去掉了**
        （左栏不再有这一项，Ctrl+D 也一并取消），但「第一次打开、文件夹还空着」时
        总得有个落脚点，所以把这段逻辑留下来单独成方法。
        """
        if self.folder is not None:
            return True
        default = get_default_folder() or default_journal_folder()
        try:
            default.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            messagebox.showerror("创建失败", f"无法创建默认日记文件夹：\n{error}")
            return False
        self.set_folder(default)
        return self.folder is not None

    def new_document(self) -> None:
        if not self._ensure_folder():
            return
        if self.trash_mode:
            self.trash_mode = False
            self.scope_folder = None
            self._sync_tree_selection()
        target_folder = self.scope_folder if self.scope_folder and self.scope_folder.is_dir() else self.folder
        self._create_and_open(new_note_path(target_folder), default_title="# 新建文档\n\n")

    def _create_and_open(self, path: Path, default_title: str) -> None:
        if not path.exists():
            try:
                atomic_write_markdown(path, default_title, None)
            except OSError as error:
                messagebox.showerror("创建失败", f"无法创建文档：\n{error}")
                return
        self.refresh_files(path)
        self.open_file(path)

    def open_file(self, path: Path) -> None:
        if path == self.current_path:
            return
        if not self._finish_pending_edit():
            self.refresh_files(self.current_path)
            return
        try:
            text, current = read_markdown(path)
        except (OSError, UnicodeError) as error:
            messagebox.showerror("打开失败", f"无法读取 Markdown 文件：\n{error}")
            return
        self.current_path = path
        self.disk_signature = current
        self.preview_mode = False
        self._load_editor_text(text)
        self.dirty = False
        self.status_label.configure(text="")
        self._apply_markdown_styles()
        # 底部留白标签要跟着**新正文**重挂（`delete` 已经把旧标签清空了）。
        # 不能只靠 `<<Modified>>`：上面刚 `edit_modified(False)` 清掉了标志，
        # 那个事件回来时第一道判据就不成立。这里也必须 force。
        self._apply_bottom_pad(force=True)
        self.editor.focus_set()
        self.refresh_files(path)

    def _on_editor_modified(self, _event=None) -> None:
        if not self.editor.edit_modified():
            return
        self.editor.edit_modified(False)
        # 末尾换行会把底部留白标签撑到两行上，每次改动都重挂一次
        self._apply_bottom_pad()
        if self.current_path is None:
            return
        self.dirty = True
        # 编辑中就一个小省略号，保存完清空——安静，不跟正文抢注意力
        self.status_label.configure(text="…")
        # 打字走增量：只重算改动的那几行。改不动（围栏开合变了、表格变了、
        # 一次粘进来太多行）才退回整篇重解析。
        if not self._apply_markdown_styles_incremental():
            self._apply_markdown_styles()
        if self.save_job:
            self.root.after_cancel(self.save_job)
        self.save_job = self.root.after(700, self.save_now)

    def save_now(self) -> bool:
        self.save_job = None
        if not self.dirty or self.current_path is None:
            return True
        text = self.editor.get("1.0", "end-1c")
        try:
            self.disk_signature = atomic_write_markdown(self.current_path, text, self.disk_signature)
        except ExternalChangeError:
            return self._resolve_external_conflict(text)
        except OSError as error:
            self.status_label.configure(text="保存失败")
            messagebox.showerror("保存失败", f"内容仍保留在编辑区，请勿关闭软件。\n\n{error}")
            return False
        self.dirty = False
        self.status_label.configure(text="")
        # 正文落盘之后再改名：万一改名这一步出错，内容也已经安全写进去了
        self.current_path = self._follow_title(self.current_path, text)
        self.refresh_files(self.current_path)
        return True

    def _follow_title(self, path: Path, text: str) -> Path:
        """存盘后让文件名跟随一级标题，返回改名后的路径（没改就原样返回）。

        **判据只有一条：第一行是一级标题，且它清出来的文件名主干与现在的不同就改。**
        不看「标题相对载入时有没有变过」——那条更严的规则漏掉了一种真实情况：

            新建文稿的文件名是 `新建-2026-09-28-160555.md`，首行是占位标题
            `# 新建文档`。用户改成 `# All in One 软件思考`，存盘后名字跟上了。
            可要是**改名这个功能比文稿来得晚**（本功能 2026-09-28 才上线，用户文件夹里
            那批 `新建-*` 就是在那之前建的），这些文稿的名字与标题一直是错位的。
            用户再打开它们、把标题重新敲一遍——按「相对载入变没变」判，标题没变，
            文件名就不动，用户看到的就是「我改了标题，文件名没跟着改」。

        所以规则就一句话：**第一行是一级标题、且清出来的名字与现在的不一样，就改。**
        代价是打开一篇名字与标题本来就不一致的老文稿、动一下正文，名字也会被扶正——
        这正是要的效果（文件名＝一级标题），而且只在**存盘**时发生：光翻看不改，
        一个字都不会落盘，也就不会动任何文件名。

        重名走 `unique_path` 加 `-2`、`-3`，**绝不覆盖**别人的文件。

        改完 `current_path` 指向新名字下的同一个文件，`disk_signature` 仍然有效——
        改名不动修改时间也不动内容，`signature()` 的三个字段（mtime、size、digest）
        一个都没变，所以外部改动监测不会误报。

        改名失败只提示一句、不动任何状态：正文已经存进去了，没什么可丢的，
        不值得弹个错误框吓人。同一个文件往同一个目标失败过就不再试（`_rename_failed_for`），
        否则每次自动保存都会再提示一遍。
        """
        title = first_h1_title(text)
        if title is None:
            return path
        stem = title_to_filename(title)
        if stem is None or stem == path.stem:
            return path
        key = (os.path.normcase(str(path)), stem)
        if self._rename_failed_for == key:
            return path
        target = path.with_name(f"{stem}{path.suffix}")
        if target.exists():
            # 只改大小写时 target 就是 path 自己（Windows 不区分大小写），这时加序号
            # 会变成「待办.md → 待办-2.md」这种莫名其妙的改名
            if os.path.normcase(str(target)) != os.path.normcase(str(path)):
                target = unique_path(path.parent, target.name)
        try:
            replace_with_retry(path, target)
        except OSError:
            self._rename_failed_for = key
            self.status_label.configure(text="改名失败，文件名保持不变")
            return path
        self._rename_failed_for = None
        self.status_label.configure(text=f"已改名为：{target.name}")
        return target

    def _resolve_external_conflict(self, text: str) -> bool:
        answer = messagebox.askyesnocancel(
            "文件在外部发生变化",
            "当前文件已被其他程序或云盘修改。\n\n"
            "选择“是”重新加载外部版本；\n"
            "选择“否”把当前内容保存为冲突副本；\n"
            "选择“取消”继续编辑，暂不保存。",
        )
        if answer is True:
            path = self.current_path
            self.current_path = None
            if path is not None:
                self.open_file(path)
            return True
        if answer is False and self.current_path is not None:
            copy_path = conflict_copy_path(self.current_path)
            try:
                atomic_write_markdown(copy_path, text, None)
            except OSError as error:
                messagebox.showerror("另存失败", f"当前内容仍保留在编辑区。\n\n{error}")
                return False
            self.dirty = False
            self.status_label.configure(text=f"已另存：{copy_path.name}")
            self.current_path = None
            self.refresh_files(copy_path)
            self.open_file(copy_path)
            return True
        self.status_label.configure(text="有冲突，尚未保存")
        return False

    def _finish_pending_edit(self) -> bool:
        if not self.dirty:
            return True
        return self.save_now()

    def _watch_external_changes(self) -> None:
        self.watch_job = None
        if not self.root.winfo_exists():
            return
        if self.current_path is not None and not self.dirty:
            current = signature(self.current_path)
            if current != self.disk_signature:
                answer = messagebox.askyesno(
                    "文件已更新",
                    "当前文件已被其他程序或云盘修改。是否重新加载最新内容？",
                )
                if answer:
                    path = self.current_path
                    self.current_path = None
                    self.open_file(path)
                else:
                    self.disk_signature = current
        self.watch_job = self.root.after(1500, self._watch_external_changes)

    # ---------- 轻量所见即所得 ----------

    def _apply_markdown_styles(self) -> None:
        """把 Markdown 源码即时渲染成排版效果（输入即预览，没有源码/预览切换）。

        所有语法记号都套上 syntax / syntax_current 标签：非光标行被 elide 隐藏，
        光标所在行则显示出来，方便修改。

        分两遍走：第一遍只算「每行是不是在围栏代码块里」「哪些行属于表格」——
        这两件事都是跨行的，必须先把整篇扫一遍；第二遍才逐行套样式。
        否则表格会依赖于尚未确定的行号，代码块里的竖线也会被当成表格。

        这是**整篇**重解析，只在「改动可能影响全篇」时才走（打开文件、改动涉及围栏
        或表格结构、一次粘贴太多行……）。打字走的是 `_apply_markdown_styles_incremental()`，
        只重算改动的那几行——长文档下这才是打字不卡的关键。
        """
        if self.current_path is None and not self.preview_mode:
            return
        cursor_line = int(self.editor.index("insert").split(".")[0])
        for tag in EDITOR_TAGS:
            self.editor.tag_remove(tag, "1.0", "end")
        # 缓存先清空：这次解析出来的就是最新的一份
        self._line_marks = {}
        self._heading_lines = {}
        self._doc_blocks = []
        text = self.editor.get("1.0", "end-1c")
        lines = split_document_lines(text)

        # ---- 第一遍：算围栏状态与表格位置 ----
        fences, in_code, delimiters, _fence = scan_fences(lines, None)
        table_rows = build_table_rows(lines, in_code)
        self._doc_in_code = in_code
        self._doc_tables = collect_tables(lines, in_code)

        # ---- 第二遍：套样式 ----
        for line_number, line in enumerate(lines, start=1):
            self._doc_blocks.append(self._style_line(
                line_number, line, cursor_line,
                in_code[line_number - 1], delimiters[line_number - 1],
                table_rows.get(line_number)))

        self._doc_lines = lines
        self._doc_fence = fences
        self._doc_delimiter = delimiters
        self._doc_table_rows = table_rows
        self._cursor_line = cursor_line
        self._redraw_margins()

    def _style_line(self, line_number: int, line: str, cursor_line: int,
                    in_code: bool, is_delimiter: bool,
                    table_row: tuple["TableInfo", int, int] | None) -> LineInfo:
        """给一行套上它该有的标签，返回块级解析结果。

        整篇重解析和增量重解析共用这一份逻辑——两条路径如果各写一遍，
        迟早会在某个语法上不一致（屏幕上和导出图不一致的 bug 就是这么来的）。
        """
        start = f"{line_number}.0"
        end = f"{line_number}.end"

        if in_code:
            # 围栏行也属于代码块，但把 ``` 和语言名藏起来
            self.editor.tag_add("codeblock", start, end)
            if is_delimiter:
                self._tag_syntax(line_number, 0, len(line), cursor_line)
            return LineInfo("text")

        if table_row is not None:
            self._style_table_row(line_number, line, table_row, cursor_line)
            return LineInfo("table")

        info = parse_block(line)
        if info.kind == "heading":
            self.editor.tag_add(f"h{info.level}", start, end)
            self._heading_lines[line_number] = HEADING_LABELS[f"h{info.level}"]
        elif info.kind == "quote":
            self.editor.tag_add("quote", start, end)
        elif info.kind in ("bullet", "task", "ordered"):
            self.editor.tag_add(f"li{info.level}", start, end)
        if info.marker is not None:
            if info.kind == "ordered":
                # 有序列表的序号一律由左侧画布绘制（蓝色、与正文同号），所以记号
                # **永远**藏起来，光标行也不例外。这里故意不走 _tag_syntax：不记进
                # `_line_marks`，光标进出该行就不会去互换它，序号外观才不会跳。
                #
                # 例外中的例外：**空的有序项 + 光标就在这一行**时，把行尾那个空格
                # 留在 elide 之外。整行字符全被藏起来，Tk 就认定这行没有可见内容，
                # 会把插入光标退回**行首**——也就是序号画布底下，被整个盖住
                # （用户报的「回车续号之后光标丢了」）。留一个空格可见，Tk 就按
                # lmargin 把光标放到正文位置旁边，而画布的右边缘在 gap 处，
                # 永远够不着它（光标比画布右边缘还靠右 gap + 一个空格宽）。
                # 空格本身没有墨迹，看上去什么也没多。
                marker_end = info.marker[1]
                if (info.body_empty and line_number == cursor_line
                        and line[marker_end - 1:marker_end] in (" ", "\t")):
                    marker_end -= 1
                self.editor.tag_add("syntax", f"{line_number}.{info.marker[0]}",
                                    f"{line_number}.{marker_end}")
            elif info.kind in ("bullet", "task", "quote"):
                # 行首记号平时藏起来：无序列表（`- ` / `* ` / `+ `）与任务项
                # （`- [ ] `）由左侧画布画成圆点 / 复选框，引用（`> `）由画布画
                # 左侧竖条；光标进到这一行时露源码。
                # **露源码必须补偿它占的宽度**，否则正文被顶右、可用宽度变小，
                # 刚好卡在折行边界的那一行会凭空多折一行（见 `syntax_marker`）。
                # 三者共用同一条缩进基准（`px(LIST_INDENT)` / `li{n}`），补偿量算法
                # 完全一样；光标行上画布装饰本来就不画，记号正好补上那个位置。
                indent = px(LIST_INDENT if info.kind == "quote"
                            else list_indent(info.level))
                gutter = self._body_text_width(line[:info.marker[1]])
                self._tag_marker(line_number, info.marker[0], info.marker[1],
                                 cursor_line, (indent - gutter, indent))
            else:
                # 标题的 `# ` 也在行首，和列表记号一样必须补偿宽度：露出来会把正文
                # 顶右、可用宽度变小，卡在折行边界上的标题会凭空多折一行。
                # 标题没有缩进（`h{n}` 只配了字体、没配 lmargin），所以补偿就是
                # 把这个记号往左挂到留白里去，正文的位置不动。
                gutter = self._body_text_width(line[:info.marker[1]])
                self._tag_marker(line_number, info.marker[0], info.marker[1],
                                 cursor_line, (-gutter, 0))

        self._tag_inline(line_number, line, cursor_line)
        # 光标这一行会露行内记号（`**`、`` ` ``）——但只在露出来**不会让这一行
        # 多折一行**时才露。行内记号平时不占宽度，露出来就是往这一行里凭空塞进
        # 若干字符，本来正好卡在折行边界上的行会因此多折一行、整行往下挪，
        # 看着就是「光标选中该行即换行」（用户报的）。判据见 `_inline_reveal_fits`。
        if line_number == cursor_line and not self._inline_reveal_fits(line_number, line):
            self._hide_inline_marks(line_number)
        return info

    def _clear_line_tags(self, first: int, last: int) -> None:
        """抹掉 [first, last] 这些行在编辑器里的全部样式标签。

        按「一个标签一次整段 range」清，而不是逐行清：26 个标签就是 26 次 Tcl 调用，
        与区间长度无关；逐行清会变成 26 × 行数。
        """
        start = f"{first}.0"
        end = f"{last}.end"
        for tag in EDITOR_TAGS:
            self.editor.tag_remove(tag, start, end)

    def _drop_line_marks(self, first: int, last: int) -> None:
        """丢掉 [first, last] 这些行的记号与徽标记录（行号是**改动前**的）。"""
        for line in range(first, last + 1):
            self._line_marks.pop(line, None)
            self._heading_lines.pop(line, None)

    # ---------- 增量重解析（打字走这条） ----------

    def _editor_line_count(self) -> int:
        """编辑器里现有多少行。一次 Tcl 调用就够，别读全文再数。"""
        return int(self.editor.index("end-1c").split(".")[0])

    def _read_lines(self, first: int, last: int) -> list[str]:
        """一次 Tcl 调用读回 [first, last] 这几行的原文，行数保证与区间等长。

        切行必须和整篇解析用同一个 `split_document_lines`：Tk 以 `\\n` 分行，
        行尾那个 `\\r` 是行内容的一部分（`get("3.0", "3.end")` 会把它带回来），
        按 `\\r` 当换行切就会多切出一行，行号随之错位。另外**空行的 end 就是它的
        起点**，请求区间的最后一行若是空行，Tk 一个字符都不返回，得补回来。
        """
        lines = split_document_lines(self.editor.get(f"{first}.0", f"{last}.end"))
        while len(lines) < last - first + 1:
            lines.append("")
        return lines

    def _changed_region(self, total: int, cursor_line: int):
        """比对光标附近的一小段，找出这次到底改了哪几行。

        返回 1-based 的闭区间 (first, last)；返回 None 表示窗口内对不齐
        （比如一次粘进来一大段），请退回整篇重解析。

        对齐方式：改动点之前的行「同下标相同」，改动点之后的行「错开
        delta = 新行数 − 旧行数 相同」。两边各取最长相同段，夹出来的就是改动区。
        插入、删除、跨行删除都能一次算准，不需要知道 Tk 内部是怎么改的。
        """
        cached = self._doc_lines
        if not cached:
            return None
        first = max(1, cursor_line - INCREMENTAL_WINDOW)
        last = min(total, cursor_line + INCREMENTAL_WINDOW)
        fresh = self._read_lines(first, last)
        count = last - first + 1
        if len(fresh) != count:
            return None                      # 读回来的行数对不上，不冒险
        delta = total - len(cached)

        prefix = 0
        while (prefix < count and first + prefix - 1 < len(cached)
               and fresh[prefix] == cached[first + prefix - 1]):
            prefix += 1
        suffix = 0
        while suffix < count - prefix:
            index = count - suffix - 1
            old_index = first + index - 1 - delta
            if not 0 <= old_index < len(cached) or fresh[index] != cached[old_index]:
                break
            suffix += 1
        if prefix + suffix >= count:
            # 窗口内完全对得上：改动要么不存在，要么落在窗口之外。交给整篇重解析。
            return None
        return (first + prefix, first + count - suffix - 1)

    def _region_context(self, first: int, last: int, delta: int,
                        fresh: list[str], total: int):
        """算改动区自己的围栏/表格信息，并确认这次改动没有波及区外。

        返回 (in_code, delimiters, 行号→表格项, 窗口表格或 None)；
        返回 None 表示跨行上下文变了（围栏开合变了、表格长了一圈……），
        必须整篇重解析。
        """
        fences = self._doc_fence
        if not 0 <= first - 1 < len(fences):
            return None
        fence = fences[first - 1]
        entering, in_code, delimiters, fence = scan_fences(fresh, fence)
        # 改动区之后的围栏状态必须和改动前一致，否则后面每一行的「是否代码块」都变了
        if last < total:
            old_index = last - delta
            if not 0 <= old_index < len(fences) or fences[old_index] != fence:
                return None

        # 表格：改动行里没有竖线、附近也没有表格时直接跳过——正文打字走这条快路。
        # 表格的判定要「本行是行 + 下一行是分隔行」，只看改动那一行判断不出来，
        # 所以只要沾到边，就多读一段重扫。
        old_last = last - delta
        nearby = [table for table in self._doc_tables
                  if table.start <= old_last + 1 and table.end >= first - 1]
        if not nearby and not any("|" in line for line in fresh):
            return entering, in_code, delimiters, {}, None

        window = self._window_tables(first, last, delta, in_code, total)
        if window is None:
            return None
        tables, window_first, window_last = window
        rows = table_row_map(tables)
        return entering, in_code, delimiters, rows, (tables, window_first, window_last)

    def _window_tables(self, first: int, last: int, delta: int,
                       in_code: list[bool], total: int):
        """在改动区周围多读一段重扫表格，确认表格范围没变。

        返回 (表格列表, 窗口起, 窗口止)。窗口的边界会避开缓存里已有的表格，
        免得把一张表从中间切开、扫出一个「半截表」。
        """
        window_first = max(1, first - TABLE_LOOKAROUND)
        window_last = min(total, last + TABLE_LOOKAROUND)
        for table in self._doc_tables:
            # 缓存里的行号是改动前的，改动区之后的要按 delta 换算过来
            shift = delta if table.start > last - delta else 0
            start, end = table.start + shift, table.end + shift
            if start < window_first <= end:
                window_first = max(1, start)
            if start <= window_last < end:
                window_last = min(total, end)
        if window_last < window_first:
            return None

        lines = self._read_lines(window_first, window_last)
        if len(lines) != window_last - window_first + 1:
            return None
        flags: list[bool] = []
        for number in range(window_first, window_last + 1):
            if first <= number <= last:
                flags.append(in_code[number - first])
            else:
                index = number - 1 - (delta if number > last else 0)
                flags.append(self._doc_in_code[index]
                             if 0 <= index < len(self._doc_in_code) else False)

        offset = window_first - 1
        tables = collect_tables(lines, flags)
        for table in tables:
            table.start += offset
            table.end += offset
            table.line_numbers = [number + offset for number in table.line_numbers]

        # 改动区落在哪张表里：新旧两边必须一致，而且范围不能变。
        # 范围没变就说明「第几行是表头、第几行是分隔行、第几行是第几行数据」
        # 全都没变，可以直接沿用缓存的对应关系。
        old_last = last - delta
        new_table = next((t for t in tables if t.start <= first and last <= t.end), None)
        old_table = next((t for t in self._doc_tables
                          if t.start <= first and old_last <= t.end), None)
        if (new_table is None) != (old_table is None):
            return None
        if new_table is not None and (new_table.start != old_table.start
                                      or new_table.end != old_table.end + delta):
            return None
        return tables, window_first, window_last

    def _splice_cache(self, first: int, last: int, fresh: list[str],
                      blocks: list[LineInfo], entering: list, in_code: list[bool],
                      delimiters: list[bool], delta: int, context) -> None:
        """把改动区的新结果拼回缓存，并处理行号位移。

        关键点：Tk 会自己把插入点之后的标签跟着文本一起挪，所以**改动区之外
        的行不用重新上色**，只需要把按行号索引的这几份缓存对齐过去。
        """
        old_last = last - delta
        span = slice(first - 1, old_last)
        self._doc_lines[span] = fresh
        self._doc_blocks[span] = blocks
        self._doc_fence[span] = entering
        self._doc_in_code[span] = in_code
        self._doc_delimiter[span] = delimiters

        # 表格：改动区附近的整段换成重扫出来的，远处的只按 delta 挪行号
        if context is None:
            self._shift_tables(old_last, delta)
        else:
            tables, window_first, window_last = context
            head = [table for table in self._doc_tables if table.end < window_first]
            tail = [table for table in self._doc_tables if table.start > window_last]
            for table in tail:
                table.start += delta
                table.end += delta
                table.line_numbers = [number + delta for number in table.line_numbers]
            self._doc_tables = head + tables + tail
        self._doc_table_rows = table_row_map(self._doc_tables)

    def _shift_line_maps(self, first: int, old_last: int, delta: int) -> None:
        """把按行号索引的记号/徽标记录整体位移到新行号。

        行号都是**改动前**的：`< first` 不动，`> old_last` 加 delta。
        改动区自己那一段已经由 `_drop_line_marks` 清掉、稍后重新填，
        所以这里必须**在重新上色之前**调用——否则刚填进去的新记录会被
        当成「改动区之后的旧记录」再位移一次。
        """
        marks = {line: value for line, value in self._line_marks.items() if line < first}
        marks.update({line + delta: value for line, value in self._line_marks.items()
                      if line > old_last})
        self._line_marks = marks
        headings = {line: value for line, value in self._heading_lines.items()
                    if line < first}
        headings.update({line + delta: value for line, value in self._heading_lines.items()
                         if line > old_last})
        self._heading_lines = headings

    def _shift_tables(self, old_last: int, delta: int) -> None:
        for table in self._doc_tables:
            if table.start > old_last:
                table.start += delta
                table.end += delta
                table.line_numbers = [number + delta for number in table.line_numbers]

    def _apply_markdown_styles_incremental(self) -> bool:
        """打字时的快路径：只重算改动的那几行。

        改不动的（围栏状态变了、表格范围变了、窗口对不齐）返回 False，
        由调用方退回整篇重解析——宁可慢一次，也不能把样式算错。
        """
        if self.current_path is None and not self.preview_mode:
            return False
        if not self._doc_lines:
            return False
        total = self._editor_line_count()
        cursor_line = int(self.editor.index("insert").split(".")[0])
        region = self._changed_region(total, cursor_line)
        if region is None:
            return False
        first, last = region
        delta = total - len(self._doc_lines)

        # 光标所在的行必须一起重刷。Tk 只会把标签跟着文本一起挪，**标签名不会跟着改**，
        # 所以「原来是光标行、现在不是」和「现在是光标行」这两种行都会留着错的名字
        # （`syntax_current` 和 `syntax` 弄反），必须按新光标行重新上色。
        # 旧光标行在新文档里的行号要按 delta 换算。
        previous = self._cursor_line
        if previous > last - delta:
            previous += delta
        first = max(1, min(first, cursor_line, previous))
        last = min(total, max(last, cursor_line, previous))
        if last - first + 1 > 4 * INCREMENTAL_WINDOW:
            return False                      # 改动和光标离得太远，不值当，整篇来

        old_last = last - delta
        if not 0 <= old_last <= len(self._doc_lines):
            return False                      # 行号对不上，别硬拼缓存
        fresh = self._read_lines(first, last)
        if len(fresh) != last - first + 1:
            return False

        context = self._region_context(first, last, delta, fresh, total)
        if context is None:
            return False
        entering, in_code, delimiters, table_rows, tables = context

        # 顺序很要紧：先按**旧行号**清掉改动区的记录、再把改动区之后的记录整体位移，
        # 最后才抹标签、重新上色。反过来做的话，刚给改动区新加的记录会被
        # 「位移改动区之后的记录」这一步再挪一次。
        self._drop_line_marks(first, old_last)
        if delta:
            self._shift_line_maps(first, old_last, delta)
        self._clear_line_tags(first, last)
        blocks = [self._style_line(number, line, cursor_line,
                                   in_code[number - first],
                                   delimiters[number - first],
                                   table_rows.get(number))
                  for number, line in enumerate(fresh, start=first)]

        self._splice_cache(first, last, fresh, blocks, entering, in_code,
                           delimiters, delta, tables)
        self._cursor_line = cursor_line
        self._redraw_margins()
        return True

    def _refresh_cursor_line(self) -> None:
        """光标换行时的轻量刷新。

        文本没变，真正受影响的只有「离开的那一行」和「新到的那一行」：
        它们要在「显示源码」和「隐藏记号」之间互换。整篇重解析一遍纯属浪费，
        长文档下每敲一次键要几十毫秒，正是打字卡顿的来源。
        """
        if self.current_path is None and not self.preview_mode:
            return
        if not self._doc_blocks:            # 还没解析过（刚打开文件），走完整流程
            self._apply_markdown_styles()
            return
        cursor_line = int(self.editor.index("insert").split(".")[0])
        previous = self._cursor_line
        if previous == cursor_line:
            return
        self._swap_cursor_marks(previous, cursor_line)
        self._cursor_line = cursor_line
        # 记号显示与否会改变这一行的宽度，可能让它在换行边界上多占一个显示行，
        # 后面的行号跟着整体位移——那就整块重画（仍然只走一遍可见行）。
        # 纯正文行之间移动则完全不动排版，只刷新依赖光标行的装饰。
        if self._line_elides(previous) or self._line_elides(cursor_line):
            self._redraw_margins()
            return
        visible = self._visible_lines()
        self._redraw_decorations(visible)
        self._redraw_table_frames(visible)

    def _swap_cursor_marks(self, previous: int, current: int) -> None:
        """把两行的记号在「显示源码」与「隐藏」之间互换。"""
        for line, leaving in ((previous, True), (current, False)):
            reveal = True
            if not leaving:
                text = self.editor.get(f"{line}.0", f"{line}.end").rstrip("\r\n")
                reveal = self._inline_reveal_fits(line, text)
            for off_tag, on_tag, start, end, indent in self._line_marks.get(line, ()):
                remove_tag, add_tag = (on_tag, off_tag) if leaving else (off_tag, on_tag)
                if remove_tag == add_tag:
                    continue
                if not reveal and off_tag == "syntax" and indent is None:
                    # 行内记号露出来会让这一行多折一行（见 `_inline_reveal_fits`）。
                    # 那就别露——整行往下挪比看不到 `**` 更烦。
                    continue
                try:
                    if not leaving and indent is not None:
                        # 露源码之前先把缩进补偿、按字符折行、行高补偿一起配好。
                        # 这个标签同时只有一行在用，而「离开的那一行」已经在这一轮的
                        # 前半段摘掉了，重配不会波及它。
                        self.editor.tag_configure(add_tag,
                                                  **self._marker_options(indent))
                    self.editor.tag_remove(remove_tag, f"{line}.{start}", f"{line}.{end}")
                    self.editor.tag_add(add_tag, f"{line}.{start}", f"{line}.{end}")
                except tk.TclError:
                    # 行号可能已经因为删除而不存在了，跳过即可
                    continue

    def _inline_reveal_fits(self, line_number: int, line: str) -> bool:
        """光标所在行的**行内**记号能不能露出来（露了会不会改变这一行的折行）。

        行内记号（`**`、`` ` ``、链接的 `[` `](url)`）平时 `elide` 藏着、一点宽度
        都不占；光标进到这一行才显示出来。露出来等于往这一行里凭空塞进若干字符的
        宽度，本来正好卡在折行边界上的行会因此多折一行——整行往下挪，用户看到的就是
        「光标选中该行即换行」。

        判据只有一条：**这一行连记号一起量出来，还塞得进一个显示行吗。**

        * 塞得进：露不露都是这一个显示行，正文位置、折点、下方所有行都不会动，随便露；
        * 塞不进：这一行本来就是折行的，记号一露出来可用宽度就变，折点必然要重排
          （多半多出一行），那就**不露**——整行往下挪比看不到 `**` 更烦。

        为什么不做得更精细（按「最后一个显示行还剩多少空」判断）：折行是 Tk 的
        `wrap="word"` 说了算，一个词放不下就**整个挪到下一显示行**，余量够不等于不会
        重排；`count -displaylines` 在行首有 elide 时给的数也和真实几何对不上
        （实测 3 个显示行只报 2）。「本来就是单行」这条判据虽然保守，但它是**精确**的：
        单行的行，露出来一定还是单行。

        宽度按正文那一套字体量，连行内代码也算成正文宽度（等宽字更窄）——量出来偏大，
        偏保守。这一行没有行内记号时直接返回 True，一次测量都不做：
        `_swap_cursor_marks` 每次光标移行都会问一次，纯正文行不能为它付钱。
        """
        marks = [entry for entry in self._line_marks.get(line_number, ())
                 if entry[0] == "syntax" and entry[4] is None]
        if not marks:
            return True
        width = self.editor.winfo_width()
        if width <= 1:
            return True                      # 还没映射出来，量不了，就当能露
        info = parse_block(line)
        if info.kind in ("bullet", "task", "ordered"):
            indent = px(list_indent(info.level))
        elif info.kind == "quote":
            indent = px(LIST_INDENT)
        else:
            indent = 0
        available = width - self._current_pad * 2 - indent
        if available <= 0:
            return True
        return self._body_text_width(line) <= available

    def _hide_inline_marks(self, line_number: int) -> None:
        """把这一行**已经露出来**的行内记号重新藏回去（`_style_line` 用）。"""
        for off_tag, on_tag, start, end, indent in self._line_marks.get(line_number, ()):
            if off_tag != "syntax" or indent is not None:
                continue
            try:
                self.editor.tag_remove(on_tag, f"{line_number}.{start}",
                                       f"{line_number}.{end}")
                self.editor.tag_add(off_tag, f"{line_number}.{start}",
                                    f"{line_number}.{end}")
            except tk.TclError:
                continue

    def _line_elides(self, line: int) -> bool:
        """这一行有没有会被 elide 的记号（有的话，光标进出会改变排版宽度）。"""
        return any(off_tag == "syntax" or on_tag == "syntax"
                   for off_tag, on_tag, _start, _end, _indent
                   in self._line_marks.get(line, ()))

    # ---------- 表格渲染 ----------

    def _style_table_row(self, line_number: int, line: str,
                         entry: tuple["TableInfo", int, int], cursor_line: int) -> None:
        """给表格的一行上色并隐藏竖线。

        竖线只是语法，全部藏掉；格子之间靠画布层画的边框来分隔，
        这样才有「表格」的样子，而不是一排带竖线的文字。
        """
        _table, row, is_separator = entry
        start = f"{line_number}.0"
        end = f"{line_number}.end"

        if is_separator:
            # 分隔行整行都是语法，平时完全藏掉
            self._tag_syntax(line_number, 0, len(line), cursor_line)
            return

        tag = "table_head" if row == 0 else "table_cell"
        self.editor.tag_add(tag, start, end)
        if row > 0 and row % 2 == 0:
            self.editor.tag_add("table_stripe", start, end)

        # 竖线不能 elide：它是唯一撑开列宽的东西，藏掉之后各格文字会挤成一团。
        # 改成把它染成边框色，看上去就是一条列分隔线，同时保持各列对齐。
        placeholders = "\x00"
        scan = line.replace("\\|", placeholders)
        for position, char in enumerate(scan):
            if char == "|":
                self._tag_table_pipe(line_number, position, cursor_line)

        # 单元格里的强调、链接、行内代码照常生效：逐格调用行内解析
        for start_in_cell, end_in_cell in _cell_spans(scan):
            if end_in_cell > start_in_cell:
                self._tag_inline(line_number, line, cursor_line, start_in_cell)

    def _tag_table_pipe(self, line_number: int, position: int, cursor_line: int) -> None:
        """把表格行里的竖线染成边框色，当作列分隔线用。

        注意这里**不能**用 syntax 标签 elide 掉——竖线是唯一撑开列宽的东西，
        藏了之后各格文字会挤成一团，表格就散了。
        """
        tag = "syntax_current" if line_number == cursor_line else "table_pipe"
        self.editor.tag_add(tag, f"{line_number}.{position}", f"{line_number}.{position + 1}")
        # table_pipe 不 elide，所以光标进出这一行不会改变排版宽度
        self._line_marks.setdefault(line_number, []).append(
            ("table_pipe", "syntax_current", position, position + 1, None))

    def _redraw_table_frames(self, visible: list[tuple[int, int, tuple]] | None = None) -> None:
        """在表格区域画列分隔线与外框。

        边框用画布画而不是靠字符，缩放和换字体都不会糊；只画可见的表格。
        """
        used = 0
        if (self.current_path is None and not self.preview_mode) or self._current_pad < 0:
            self._hide_table_frames(0)
            return
        if visible is None:
            visible = self._visible_lines()
        if not visible:
            self._hide_table_frames(0)
            return

        # 表格位置跟着文本一起缓存，滚动时不再重读全文重扫一遍围栏
        tables = {table.start: table for table in self._doc_tables}
        cursor_line = int(self.editor.index("insert").split(".")[0])

        # 收集可见表格每一行的显示区域
        boxes: dict[int, list] = {}
        for number, _offset, info in visible:
            boxes.setdefault(number, []).append(info)

        for table in tables.values():
            if table.start not in boxes:
                continue                      # 表格不在可见范围内
            if table.start == cursor_line:
                continue                      # 光标在表头行时显示源码，不画框
            used = self._draw_table_frame(used, table, boxes)
        self._hide_table_frames(used)

    def _draw_table_frame(self, used: int, table: "TableInfo", boxes: dict[int, list]) -> int:
        """画一张可见表格的边框与分隔线，返回用掉的画布数。

        Tk 的 Canvas 是不透明的，一整块盖在文字上会直接把格子内容遮掉。
        所以边框**只由几条细线组成**，每条线占一个只够放下这条线的小画布，
        线之间的空隙留给正文——这样既画得出表格，又不挡字。
        """
        # 表格当前可见的行（表头 + 数据行，分隔行也要算进去占位）
        row_tops = self._table_row_bounds(table, boxes)
        if len(row_tops) < 2:
            return used
        top = row_tops[0][1]
        bottom = row_tops[-1][2]

        pad = self._current_pad
        text_left = self.editor.winfo_x() + pad
        text_width = max(px(40), self.editor.winfo_width() - pad * 2)

        thickness = max(1, px(TABLE_BORDER_W))
        height = max(1, bottom - top)
        key = ("table", table.start)

        # 外框：上下两条横线 + 左右两条竖线。
        # 列分隔线不画——表格行里的竖线已经染成边框色充当列分隔线了，
        # 再画一条会与它错位（列宽由字体决定，画布量不准）。
        used = self._table_line(used, key, text_left, top, text_width, thickness)
        used = self._table_line(used, key, text_left, bottom - thickness,
                                text_width, thickness)
        used = self._table_line(used, key, text_left, top, thickness, height)
        used = self._table_line(used, key, text_left + text_width - thickness, top,
                                thickness, height)
        # 行分隔线：画在上一行下沿与下一行上沿之间的空隙里。
        # 空隙放不下一条线时（行距为 0），退到「下一行上沿 − 线宽」，
        # 宁可贴着字的边缘，也不要整条线消失。
        for index in range(1, len(row_tops)):
            previous_bottom = row_tops[index - 1][2]
            current_top = row_tops[index][1]
            gap = current_top - previous_bottom
            if gap >= thickness:
                y = previous_bottom + (gap - thickness) // 2
            else:
                y = current_top - thickness
            color = TABLE_HEAD_TEXT if index == 1 else TABLE_BORDER
            line_thickness = max(thickness, px(2)) if index == 1 else thickness
            used = self._table_line(used, key, text_left, y, text_width,
                                    line_thickness, color=color)
        return used

    def _table_row_bounds(self, table: "TableInfo",
                          boxes: dict[int, list]) -> list[tuple[int, int, int]]:
        """表格可见行的 (行号, 上沿, 下沿)，按文档顺序排列。

        用 dline 的整行上下沿（含行距）而不是字面的 bbox：
        分隔线要画在行与行之间，拿行沿算才不会压到字。
        """
        order = [table.line_numbers[0]]
        order.append(table.start + 1)                       # 分隔行
        order.extend(table.line_numbers[1:])
        bounds: list[tuple[int, int, int]] = []
        for number in order:
            info = boxes.get(number)
            if not info:
                continue
            top = info[0][1]
            bottom = info[-1][1] + info[-1][3]
            bounds.append((number, top, bottom))
        return bounds

    def _table_line(self, used: int, key, x: int, y: int,
                    width: int, height: int, color: str = TABLE_BORDER) -> int:
        """画一条表格线。每条线一块小画布，线外的地方不占，所以不挡字。"""
        canvas = self._table_canvas(used)
        geometry = (key, int(x), int(y), max(1, int(width)), max(1, int(height)), color)
        # 位置没变就不用重画，理由同装饰画布
        if canvas.line_geometry == geometry and canvas.winfo_manager():
            return used + 1
        canvas.delete("all")
        canvas.table_key = key
        canvas.line_geometry = geometry
        canvas.configure(bg=EDITOR_BG)
        canvas.place(x=geometry[1], y=geometry[2], width=geometry[3], height=geometry[4])
        canvas.create_rectangle(0, 0, geometry[3], geometry[4], fill=color, outline="")
        return used + 1

    def _table_canvas(self, slot: int) -> tk.Canvas:
        while len(self._table_pool) <= slot:
            canvas = tk.Canvas(self.editor.master, bg=EDITOR_BG,
                               highlightthickness=0, borderwidth=0)
            canvas.bind("<Button-1>", self._on_heading_label_click)
            canvas.table_key = None
            canvas.line_geometry = None
            self._table_pool.append(canvas)
            self._raise_once(canvas)
        return self._table_pool[slot]

    def _hide_table_frames(self, used: int) -> None:
        for canvas in self._table_pool[used:]:
            canvas.table_key = None
            canvas.line_geometry = None
            if canvas.winfo_manager():
                canvas.place_forget()

    def _tag_inline(self, line_number: int, line: str, cursor_line: int,
                    offset: int = 0) -> None:
        """行内语法：代码、链接/图片、粗体、斜体、删除线、高亮。

        先匹配到的区间会被 claim 掉，后面的规则不再往里套——否则
        `` `**x**` `` 里的星号会被当成粗体记号。

        `offset` 用来只处理行内从某一列开始的片段（表格单元格）：正则仍然
        跑整行，但只对落在 offset 之后的区间生效，这样单元格里的标记位置
        不用另做坐标换算。
        """
        claimed: list[tuple[int, int]] = []
        base = offset

        def claim(start: int, end: int) -> None:
            if end > start:
                claimed.append((start, end))

        def blocked(start: int, end: int) -> bool:
            return any(start < other_end and end > other_start
                       for other_start, other_end in claimed)

        def in_scope(start: int, end: int) -> bool:
            return start >= base and end <= len(line)

        def apply(tag: str, inner: tuple[int, int], outer: tuple[int, int]) -> None:
            if not in_scope(*outer):
                return
            self.editor.tag_add(tag, f"{line_number}.{inner[0]}", f"{line_number}.{inner[1]}")
            self._tag_syntax(line_number, outer[0], inner[0], cursor_line)
            self._tag_syntax(line_number, inner[1], outer[1], cursor_line)
            claim(*outer)

        def spans(pattern) -> list:
            return [match for match in pattern.finditer(line) if match.start() >= base]

        # 先做一次极便宜的「这行有没有可能命中」判断再跑正则。
        # 正文里绝大多数行一个记号都没有，跳过之后整篇的匹配次数能少一个量级。
        tail = line[base:]
        has_code = "`" in tail
        has_link = "[" in tail
        has_bold = "**" in tail or "__" in tail
        has_italic = "*" in tail or "_" in tail
        has_strike = "~~" in tail
        has_highlight = "==" in tail

        # 行内代码优先：反引号里的记号一律不当语法
        if has_code:
            for match in spans(INLINE_CODE_RE):
                start, end = match.span()
                apply("code", (match.start(1), match.end(1)), (start, end))

        if has_link:
            for match in spans(LINK_RE):
                start, end = match.span()
                if blocked(start, end) or not in_scope(start, end):
                    continue
                label = (match.start(2), match.end(2))
                tag = "image" if match.group(1) == "!" else "link"
                self.editor.tag_add(tag, f"{line_number}.{label[0]}",
                                    f"{line_number}.{label[1]}")
                # 只藏 [ 与 ](url)，链接文字本身仍可继续套用强调等样式
                self._tag_syntax(line_number, start, label[0], cursor_line)
                self._tag_syntax(line_number, label[1], end, cursor_line)
                claim(start, label[0])
                claim(label[1], end)

        for tag, pattern in EMPHASIS_PATTERNS:
            if tag == "bold" and not has_bold:
                continue
            if tag == "italic" and not has_italic:
                continue
            if tag == "strike" and not has_strike:
                continue
            if tag == "highlight" and not has_highlight:
                continue
            # 只看记号、不看内容，且被挡下时从 `start + 1` 重新搜——和
            # `_inline_spans` 一字不差地同步（理由见那里两段注释：既不能因为
            # 「加粗里套了行内代码」整段放弃，也不能让反引号里的 `**` 把后面
            # 真正的那对记号当成收尾吃掉）。两边不同步的话，同一行文字在屏幕上
            # 一个样、导出的长图上另一个样。
            search_from = base
            while True:
                match = pattern.search(line, search_from)
                if match is None:
                    break
                start, end = match.span()
                inner = (match.start(1), match.end(1))
                if any(blocked(left, right) for left, right in
                       ((start, inner[0]), (inner[1], end)) if right > left):
                    search_from = start + 1
                    continue
                apply(tag, inner, (start, end))
                search_from = end

    def _tag_syntax(self, line: int, start: int, end: int, cursor_line: int) -> None:
        tag = "syntax_current" if line == cursor_line else "syntax"
        self.editor.tag_add(tag, f"{line}.{start}", f"{line}.{end}")
        # 记下这一行的记号区间：光标移进移出时只要把这两行的标签换一下，
        # 不必为了「显示源码 / 隐藏记号」把整篇重新解析一遍。
        self._line_marks.setdefault(line, []).append(
            ("syntax", "syntax_current", start, end, None))

    def _body_font_object(self) -> "font.Font":
        """正文那一套字体。字体对象建一次就缓存住——每量一次都新建一个
        `font.Font` 要跨语言调用好几轮，而光标每换一行都要量。"""
        if self._body_font is None:
            self._body_font = font.Font(
                family=self._writing_font(), size=-px(self.settings["font_size"]))
        return self._body_font

    def _body_text_width(self, text: str) -> int:
        """按正文那一套字体量一段文字的宽度（像素），给行首记号算缩进补偿用。"""
        return int(self._body_font_object().measure(text))

    def _marker_options(self, indent: tuple[int, int]) -> dict:
        """行首记号「露源码」时该配的标签选项。

        * `lmargin1 = 行缩进 − 记号宽度`：正文位置与可用宽度回到隐藏时的值；
        * `lmargin2 = 行缩进`：续行照旧从缩进处开始；
        * `wrap="char"`：见 `syntax_marker` 的创建处，不按字符折行会凭空多一行。

        **别再加 `spacing1` 想补行高**（试过，没用）：记号一露出来，这一行确实会
        矮 `px(行高) − 字体行距`（用户设置下 16px），因为 elide 的首块被 Tk 当成了
        一个「零高的显示行」、还照算一份 `spacing2`。但 `spacing1` 这个**标签**选项
        实测在 Tk 里根本不生效（只盖记号也好、盖整行也好，行高一点都不变），
        `spacing2` 无论取 0 还是负值都只能消掉一半。要根治得让 elide 的首块不占
        那份额外行距，代价是列表行整体收紧 16px——改动面比这个 bug 大，先留着。
        """
        return {"lmargin1": indent[0], "lmargin2": indent[1], "wrap": "char"}

    def _tag_marker(self, line: int, start: int, end: int, cursor_line: int,
                    indent: tuple[int, int]) -> None:
        """给行首记号套上「隐藏 ↔ 露源码」这一对标签。

        `indent` 是记号**露出来**时该用的 `(lmargin1, lmargin2)`：第一个值已经
        减掉了记号自己的宽度，所以正文位置与可用宽度跟隐藏时完全一致，
        折行位置不会因为光标进出而改变（其余选项见 `_marker_options`）。
        """
        if line == cursor_line:
            self.editor.tag_configure("syntax_marker", **self._marker_options(indent))
            self.editor.tag_add("syntax_marker", f"{line}.{start}", f"{line}.{end}")
        else:
            self.editor.tag_add("syntax", f"{line}.{start}", f"{line}.{end}")
        self._line_marks.setdefault(line, []).append(
            ("syntax", "syntax_marker", start, end, indent))

    def _on_cursor_moved(self, _event=None) -> None:
        self.root.after_idle(self._refresh_cursor_line)

    def _apply_styles_without_dirty_flag(self) -> None:
        was_modified = self.editor.edit_modified()
        self._apply_markdown_styles()
        self.editor.edit_modified(was_modified)

    def _wrap_bold(self, _event=None):
        return self._wrap_selection("**")

    def _wrap_italic(self, _event=None):
        return self._wrap_selection("*")

    def _on_editor_return(self, _event=None) -> str | None:
        """有序列表里在行末回车，自动带出下一个序号。

        **只接管一种情况**：光标停在行尾、且这一行是有序列表项。其余（行中间回车、
        有选区、普通正文）一律返回 None，交给 Tk 的默认换行——绝不改变普通正文的手感。

        缩进和分隔符都照抄当前行（`1.` 续成 `2.`，`1)` 续成 `2)`），所以四级缩进
        也能对齐；序号本身 +1。

        空列表项（只有记号没有内容）再回车 = 结束这个列表：把记号清掉留一个空行，
        不然会一直「1. 2. 3.」地无限续下去。
        """
        if self.editor.tag_ranges("sel"):
            return None                       # 有选区：交给默认（先删选区再换行）
        if self.editor.index("insert") != self.editor.index("insert lineend"):
            return None                       # 只在行末回车时接管
        line = self.editor.get("insert linestart", "insert lineend")
        info = parse_block(line)
        if info.kind != "ordered" or not info.number or info.marker is None:
            return None

        indent = line[:len(line) - len(line.lstrip())]
        body = line[info.marker[1]:].strip()
        if not body:
            self.editor.delete("insert linestart", "insert lineend")
            self.editor.see("insert")
            return "break"

        digits, delimiter = info.number[:-1], info.number[-1]
        self.editor.insert("insert", f"\n{indent}{int(digits) + 1}{delimiter} ")
        # 程序化 insert 不像键盘输入那样自动把光标带进视口：在页面底部回车时
        # 新行会落在屏幕外，看起来就像「没换行」。see 只在光标看不见时才滚。
        self.editor.see("insert")
        return "break"

    def _selected_line_range(self) -> tuple[int, int]:
        """这次缩进要处理的逻辑行范围（1 基，闭区间）。

        有选区就按选区取。**选区右端正好落在行首时不算那一行**——「从行中间选到
        下一行行首」是最常见的拖选方式，把下一行也缩进去会让人以为多缩了一行。
        """
        if not self.editor.tag_ranges("sel"):
            number = int(self.editor.index("insert").split(".")[0])
            return number, number
        first = int(self.editor.index("sel.first linestart").split(".")[0])
        last = self.editor.index("sel.last")
        if last.endswith(".0"):
            last = self.editor.index(f"{last} - 1 chars")
        last_line = int(self.editor.index(f"{last} linestart").split(".")[0])
        return first, max(first, last_line)

    def _list_lines_in(self, first: int, last: int) -> list[int]:
        """范围内**确实是列表项**的行号（有序 / 无序 / 任务）。"""
        return [number for number in range(first, last + 1)
                if self._block_of(number).kind in ("bullet", "task", "ordered")]

    def _shift_list_level(self, delta: int) -> bool:
        """把范围内的列表项整体升/降 delta 层，返回是否真的动了。

        层级换算走 `_indent_level`（`空格数 // 2`），所以写回去的缩进一律是空格：
        原来用 Tab 缩进的行会被顺手归一成空格，层级才稳定。
        """
        first, last = self._selected_line_range()
        lines = self._list_lines_in(first, last)
        if not lines:
            return False
        moved = False
        for number in lines:
            line = self.editor.get(f"{number}.0", f"{number}.end")
            indent = line[:len(line) - len(line.lstrip())]
            level = _indent_level(indent)
            target = max(0, min(LIST_MAX_LEVEL, level + delta))
            if target == level:
                continue
            self.editor.delete(f"{number}.0", f"{number}.{len(indent)}")
            self.editor.insert(f"{number}.0", " " * (target * LIST_INDENT_STEP))
            moved = True
        return moved

    def _on_editor_tab(self, _event=None) -> str:
        """Tab：列表项缩进一层；普通行插两个空格。

        绑定在控件级、一律返回 `"break"`：Tk 给 `Text` 的默认 Tab 行为是**插一个
        制表符**，而 `_indent_level` 把一个 `\\t` 算成四格 = 两层 —— 不挡住的话，
        用户敲一次 Tab 再把这行改成列表项，会直接跳到第二层。

        判据是「范围内有没有列表行」，**不是「有没有真的动」**：已经到第 4 层的
        列表项再按 Tab 必须什么都不做，不能掉进下面「普通行插两个空格」那条路——
        那样每按一次就多两个空格，缩进会一路涨上去（测试
        `test_tab_stops_at_the_top_level` 盯着这个）。
        """
        first, last = self._selected_line_range()
        if self._list_lines_in(first, last):
            self._shift_list_level(+1)
            # 程序化改文本不像键盘输入那样自动把光标带进视口，行在屏幕外时
            # 看起来就像「没反应」。see 只在光标看不见时才滚。
            self.editor.see("insert")
            return "break"
        if self.editor.tag_ranges("sel"):
            return "break"          # 有选区但不是列表：不动内容，免得正文被空格替掉
        self.editor.insert("insert", " " * LIST_INDENT_STEP)
        self.editor.see("insert")
        return "break"

    def _on_editor_shift_tab(self, _event=None) -> str:
        """Shift+Tab：列表项反缩进一层。

        最低就到第 0 层——**不会顺手把 `1. ` 记号删掉**。删记号等于把列表项变回
        普通正文，是一次内容改写；层级调错了还能再调回来，记号没了得重打。
        """
        if self._shift_list_level(-1):
            self.editor.see("insert")
        return "break"

    def _wrap_selection(self, marker: str):
        try:
            start = self.editor.index("sel.first")
            end = self.editor.index("sel.last")
        except tk.TclError:
            self.editor.insert("insert", marker + marker)
            self.editor.mark_set("insert", f"insert-{len(marker)}c")
            return "break"
        self.editor.insert(end, marker)
        self.editor.insert(start, marker)
        return "break"

    # ---------- 主题 ----------

    def set_theme(self, name: str) -> None:
        """切换界面主题：重灌颜色常量 + 重建整个界面，并记住选择。

        为什么是「重建」而不是「逐个改配置」：全项目上百处都是在**建控件那一刻**
        读的颜色（`bg=CARD_BG`、`canvas.create_text(fill=TEXT)`…），逐个回溯去
        `configure` 既写不完也容易漏。而这些控件重建一遍只要几十毫秒，状态
        （文件夹、当前文稿、光标、滚动位置）都在 `self` 上，重建完按原样恢复。

        **导出长图不跟着变**：它固定读浅色快照，见 `export_palette()`。
        """
        name = apply_theme(name)
        if name == self.theme:
            return
        self.theme = name
        self.settings["theme"] = name
        set_settings({"theme": name})
        self._rebuild_ui()
        # 设置窗口是按旧配色建的，重建之后要把它按新配色重新开出来——
        # 主题开关就在里面，关掉会让用户以为自己点错了。
        self.open_settings()

    def _rebuild_ui(self) -> None:
        """按当前主题把界面整个重建一遍，并恢复到原来的状态。"""
        # 先把正文、光标和滚动位置取出来：编辑器控件马上要被销毁
        has_document = self.current_path is not None
        text = self.editor.get("1.0", "end-1c") if has_document else ""
        cursor = self.editor.index("insert") if has_document else "1.0"
        scroll = self.editor.yview()[0] if has_document else 0.0

        # 子窗口也是按旧配色建的，一并销毁（设置窗口由调用方重新开）
        for attribute in ("font_window", "settings_window"):
            window = getattr(self, attribute, None)
            if window is not None and window.winfo_exists():
                window.destroy()
            setattr(self, attribute, None)

        for child in self.root.winfo_children():
            child.destroy()

        # **根窗口上的绑定会一直留着**，重建前必须清掉，否则每换一次主题
        # 就多绑一层，按一次 Ctrl+S 会存好几次。子控件是新建的，不用管。
        for sequence in ("<Control-n>", "<Control-s>", "<Control-o>",
                         "<Control-Shift-F>", "<Escape>", "<Destroy>"):
            self.root.unbind(sequence)

        # 这些缓存都记的是旧控件的几何 / 旧画布的图元，一并作废
        self._decor_pool = []
        self._table_pool = []
        self._card_bounds = []
        self._cards_width = -1
        self._cards_content_height = 0
        self._current_pad = -1
        self._bottom_pad = -1
        self._bottom_pad_line = -1          # 编辑区控件重建了，留白标签要重新挂
        # 换主题会整块重建界面，卡片和裁剪缓存一起作废（新控件、新配色）
        self._excerpt_cache = {}
        self._trim_cache = {}
        self._cards_signature_cache = None
        self._cards_selected = None
        self._margin_signature = None
        self._gutter_pending = False
        self._geometry_pending = False

        self._build_ui()

        # 恢复状态
        self.folder_label.configure(
            text=str(self.folder) if self.folder else "（未选择文件夹）")
        if self.focus_mode:
            # 专注模式是「把两栏 grid_remove 掉」，重建后得重新收一遍
            self.nav.grid_remove()
            self.cards_panel.grid_remove()
            self.focus_button.configure(text="退出专注", fg=ACCENT)
        self._rebuild_tree()
        self.refresh_files(self.current_path)

        if has_document:
            self._load_editor_text(text, cursor)
            self.dirty = False
            self._apply_markdown_styles()
            self.editor.yview_moveto(scroll)
            self.editor.focus_set()

    # ---------- 设置窗口 ----------

    def _place_dialog(self, window: "tk.Toplevel", width: int, height: int) -> None:
        """把对话框摆到主窗口水平居中、纵向 1/3 处，并夹在屏幕内。

        `self.root.update_idletasks()` 不能省：不先跑一遍，`winfo_width()` 还是旧值，
        算出来的位置会偏。纵向不取正中而取 1/3——对话框矮的时候靠上一点更顺手，
        也不会被任务栏咬掉。设置窗口与字体选择器原来各抄一遍。
        """
        self.root.update_idletasks()
        parent_x = self.root.winfo_rootx()
        parent_y = self.root.winfo_rooty()
        parent_w = max(self.root.winfo_width(), width + px(40))
        parent_h = max(self.root.winfo_height(), height + px(40))
        x = parent_x + (parent_w - width) // 2
        y = parent_y + (parent_h - height) // 3
        window.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")

    def open_settings(self) -> None:
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.lift()
            self.settings_window.focus_set()
            return
        window = tk.Toplevel(self.root)
        self.settings_window = window
        window.title("设置")
        window.configure(bg=CARD_BG)
        window.geometry(f"{px(420)}x{px(560)}")
        window.resizable(False, False)
        window.transient(self.root)

        tk.Label(window, text="设置", bg=CARD_BG, fg=TEXT, font=(FAMILY, 14, "bold"),
                 anchor="w", padx=px(24)).pack(fill="x", pady=(px(22), px(6)))

        folder_frame = tk.Frame(window, bg=CARD_BG)
        folder_frame.pack(fill="x", padx=px(24), pady=(px(4), px(10)))
        tk.Label(folder_frame, text="日记文件夹", bg=CARD_BG, fg=MUTED,
                 font=(FAMILY, 9), anchor="w").pack(fill="x")
        self.settings_folder_label = tk.Label(
            folder_frame, text=str(self.folder or "（未选择）"), bg=CARD_BG, fg=TEXT,
            font=(FAMILY, 9), anchor="w", justify="left", wraplength=px(360),
        )
        self.settings_folder_label.pack(fill="x", pady=(px(2), px(6)))
        folder_buttons = tk.Frame(folder_frame, bg=CARD_BG)
        folder_buttons.pack(fill="x")
        tk.Button(folder_buttons, text="选择文件夹", command=self.choose_folder, bg=SURFACE,
                  fg=TEXT, relief="flat", borderwidth=0, padx=px(12), pady=px(6),
                  cursor="hand2", font=(FAMILY, 9)).pack(side="left")
        tk.Button(folder_buttons, text="设为默认", command=self.set_current_as_default, bg=SURFACE,
                  fg=TEXT, relief="flat", borderwidth=0, padx=px(12), pady=px(6),
                  cursor="hand2", font=(FAMILY, 9)).pack(side="left", padx=(px(8), 0))

        tk.Frame(window, bg=BORDER, height=1).pack(fill="x", padx=px(24), pady=px(8))

        # 主题：两枚互斥的按钮。点一下立刻换肤（整个界面重建），设置窗口跟着重开
        theme_block = tk.Frame(window, bg=CARD_BG)
        theme_block.pack(fill="x", padx=px(24), pady=(px(2), 0))
        tk.Label(theme_block, text="主题", bg=CARD_BG, fg=TEXT, font=(FAMILY, 9, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(theme_block, text="只改界面配色，导出的长图始终是浅色",
                 bg=CARD_BG, fg=MUTED, font=(FAMILY, 8), anchor="w").pack(fill="x")
        theme_row = tk.Frame(theme_block, bg=CARD_BG)
        theme_row.pack(fill="x", pady=(px(6), 0))
        for theme_key, theme_label in THEME_LABELS:
            active = theme_key == self.theme
            tk.Button(
                theme_row, text=theme_label, relief="flat", borderwidth=0,
                bg=ACCENT if active else SURFACE_ALT,
                fg=ON_ACCENT if active else TEXT,
                activebackground=ACCENT_ACTIVE if active else SURFACE_HOVER,
                activeforeground=ON_ACCENT if active else TEXT,
                padx=px(18), pady=px(7), cursor="hand2", font=(FAMILY, 9),
                command=lambda key=theme_key: self.set_theme(key),
            ).pack(side="left", padx=(0, px(8)))

        tk.Frame(window, bg=BORDER, height=1).pack(fill="x", padx=px(24), pady=px(8))

        # 字体：显示当前字体名（用它自己渲染，本身就是预览），点击打开字体选择
        font_block = tk.Frame(window, bg=CARD_BG)
        font_block.pack(fill="x", padx=px(24), pady=(px(2), 0))
        tk.Label(font_block, text="字体", bg=CARD_BG, fg=TEXT, font=(FAMILY, 9, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(font_block, text="写作区正文、标题、行号使用的字体（预览栏固定为 16 号微软雅黑）",
                 bg=CARD_BG, fg=MUTED, font=(FAMILY, 8), anchor="w").pack(fill="x")
        self.font_row = tk.Frame(font_block, bg=SURFACE_ALT, cursor="hand2",
                                 highlightthickness=1, highlightbackground=BORDER,
                                 highlightcolor=BORDER)
        self.font_row.pack(fill="x", pady=(px(6), 0))
        self.font_row_value = tk.Label(
            self.font_row, text="", bg=SURFACE_ALT, fg=ACCENT, anchor="w",
            font=(FAMILY, 10, "bold"), padx=px(12), pady=px(9),
        )
        self.font_row_value.pack(side="left")
        self.font_row_hint = tk.Label(
            self.font_row, text="更换 ›", bg=SURFACE_ALT, fg=MUTED, anchor="e",
            font=(FAMILY, 8), padx=px(12),
        )
        self.font_row_hint.pack(side="right")
        for widget in (self.font_row, self.font_row_value, self.font_row_hint):
            widget.bind("<Button-1>", lambda _event: self.open_font_picker())
            widget.bind("<Enter>", lambda _event: self._hover_font_row(True))
            widget.bind("<Leave>", lambda _event: self._hover_font_row(False))
        self._sync_font_row()

        tk.Frame(window, bg=BORDER, height=1).pack(fill="x", padx=px(24), pady=px(8))

        self._setting_scales: dict[str, tk.Scale] = {}
        specs = [
            ("line_width", "内容行宽（%）", "调整正文左右留白，越小越窄"),
            ("font_size", "字号（px）", "正文字号大小"),
            ("line_height", "行高（px）", "行与行之间的间距"),
        ]
        for key, label, hint in specs:
            low, high = SETTING_RANGES[key]
            block = tk.Frame(window, bg=CARD_BG)
            block.pack(fill="x", padx=px(24), pady=(px(6), 0))
            tk.Label(block, text=label, bg=CARD_BG, fg=TEXT, font=(FAMILY, 9, "bold"),
                     anchor="w").pack(fill="x")
            tk.Label(block, text=hint, bg=CARD_BG, fg=MUTED, font=(FAMILY, 8),
                     anchor="w").pack(fill="x")
            scale = tk.Scale(
                block, from_=low, to=high, orient="horizontal", resolution=1,
                bg=CARD_BG, fg=TEXT, troughcolor=SURFACE, highlightthickness=0,
                borderwidth=0, sliderrelief="flat", length=px(340), showvalue=True,
                command=lambda value, k=key: self._on_setting_changed(k, value),
            )
            scale.set(self.settings[key])
            scale.pack(fill="x")
            self._setting_scales[key] = scale

        tk.Button(window, text="完成", command=self._close_settings, bg=ACCENT, fg=ON_ACCENT,
                  activebackground=ACCENT_ACTIVE, activeforeground=ON_ACCENT, relief="flat",
                  borderwidth=0, padx=px(16), pady=px(9), cursor="hand2",
                  font=(FAMILY, 9)).pack(pady=px(18))
        window.protocol("WM_DELETE_WINDOW", self._close_settings)
        self._fit_settings_window(window)

    def _fit_settings_window(self, window: tk.Toplevel) -> None:
        """按内容自适应高度，并居中于主窗口，避免控件被裁掉。"""
        window.update_idletasks()
        width = px(420)
        height = max(px(520), window.winfo_reqheight())
        try:
            screen_h = window.winfo_screenheight()
        except tk.TclError:
            screen_h = px(900)
        height = min(height, max(px(420), screen_h - px(120)))
        self._place_dialog(window, width, height)

    def _on_setting_changed(self, key: str, value: str) -> None:
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            return
        low, high = SETTING_RANGES[key]
        self.settings[key] = max(low, min(high, number))
        if key in ("font_size", "line_height"):
            self._apply_typography()
        self._apply_editor_geometry()
        self._schedule_settings_persist()

    def _schedule_settings_persist(self) -> None:
        if self.settings_job:
            self.root.after_cancel(self.settings_job)
        self.settings_job = self.root.after(400, self._persist_settings)

    def _persist_settings(self) -> None:
        self.settings_job = None
        set_settings(self.settings)

    # ---------- 字体选择 ----------

    def _hover_font_row(self, active: bool) -> None:
        if not hasattr(self, "font_row") or not self.font_row.winfo_exists():
            return
        bg = SURFACE_HOVER if active else SURFACE_ALT
        for widget in (self.font_row, self.font_row_value, self.font_row_hint):
            widget.configure(bg=bg)

    def _sync_font_row(self) -> None:
        """把当前字体名写进设置里的那一行（用它自己渲染，即所见即所得）。"""
        if not hasattr(self, "font_row_value") or not self.font_row_value.winfo_exists():
            return
        writing = self._writing_font()
        self.font_row_value.configure(
            text=self._writing_font_label(), font=(writing, 10, "bold"),
        )

    def _on_font_changed(self, family: str) -> None:
        self.settings["font_family"] = family
        self._apply_typography()
        self._rebuild_cards()
        self._redraw_margins()
        self._sync_font_row()
        self._schedule_settings_persist()

    def open_font_picker(self) -> None:
        if self.font_window is not None and self.font_window.winfo_exists():
            self.font_window.lift()
            self.font_window.focus_set()
            return
        window = tk.Toplevel(self.root)
        self.font_window = window
        window.title("选择字体")
        window.configure(bg=CARD_BG)
        window.transient(self.root)

        tk.Label(window, text="选择字体", bg=CARD_BG, fg=TEXT, font=(FAMILY, 14, "bold"),
                 anchor="w", padx=px(22)).pack(fill="x", pady=(px(18), px(8)))

        search_var = tk.StringVar()
        search = tk.Entry(window, textvariable=search_var, bg=SURFACE_ALT, fg=TEXT,
                          relief="flat", insertbackground=TEXT, font=(FAMILY, 10),
                          highlightthickness=1, highlightbackground=BORDER,
                          highlightcolor=ACCENT)
        search.pack(fill="x", padx=px(22), ipady=px(6))
        tk.Label(window, text="点击任意字体即可立即应用到写作区", bg=CARD_BG, fg=MUTED,
                 font=(FAMILY, 8), anchor="w", padx=px(22)).pack(fill="x", pady=(px(6), px(4)))

        # 预览区：用当前选中字体渲染一行中英文样例
        preview = tk.Frame(window, bg=SURFACE_ALT, highlightthickness=1,
                           highlightbackground=BORDER)
        preview.pack(fill="x", padx=px(22), pady=(px(2), px(8)))
        preview_label = tk.Label(preview, text="", bg=SURFACE_ALT, fg=TEXT,
                                 anchor="w", justify="left", wraplength=px(360),
                                 padx=px(14), pady=px(12))
        preview_label.pack(fill="x")

        list_holder = tk.Frame(window, bg=CARD_BG)
        list_holder.pack(fill="both", expand=True, padx=px(22), pady=(0, px(4)))
        canvas = tk.Canvas(list_holder, bg=CARD_BG, highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True)

        families = self._system_font_families()
        # (显示名, 实际族名)：常见中文字体显示中文名，点击时用实际族名
        rows = [(self._font_row_label(family), family) for family in families]
        recommended = {family.lower() for _label, family in self._available_font_choices()}
        row_height = px(30)
        row_inset = px(4)

        def current_family() -> str:
            return str(self.settings.get("font_family") or DEFAULT_FONT_FAMILY)

        def visible_rows() -> list[tuple[str, str]]:
            keyword = search_var.get().strip().lower()
            if not keyword:
                return rows
            return [
                row for row in rows
                if keyword in row[0].lower() or keyword in row[1].lower()
            ]

        def refresh_preview() -> None:
            writing = self._writing_font()
            preview_label.configure(
                text=f"{FONT_SAMPLE}\n{FONT_SAMPLE_LATIN}",
                font=(writing, -max(px(11), px(self.settings["font_size"]))),
            )

        def render(*_args) -> None:
            canvas.delete("all")
            chosen = current_family().lower()
            canvas_width = max(canvas.winfo_width(), px(200))
            y = row_inset
            shown = 0
            for display, family in visible_rows():
                center = y + row_height // 2 - px(2)
                if family.lower() == chosen:
                    canvas.create_rectangle(0, y - px(1), canvas_width, y + row_height - px(3),
                                            fill=SELECTION_BG, outline="")
                    canvas.create_text(px(8), center, anchor="w", text="✓",
                                       font=(FAMILY, 10, "bold"), fill=ACCENT)
                canvas.create_text(
                    px(26), center, anchor="w", text=display,
                    font=(family, 11),
                    fill=ACCENT if family.lower() == chosen else TEXT,
                )
                if family.lower() in recommended:
                    canvas.create_text(
                        canvas_width - px(58), center, anchor="e",
                        text="推荐", font=(FAMILY, 8), fill=MUTED,
                    )
                canvas.create_text(
                    canvas_width - px(10), center, anchor="e", text="Aa 永",
                    font=(family, 9), fill=MUTED,
                )
                y += row_height
                shown += 1
            canvas.configure(scrollregion=(0, 0, canvas_width, max(y, 1)))
            canvas.yview_moveto(0.0)
            if shown == 0:
                canvas.create_text(px(10), px(16), anchor="w", text="没有匹配的字体",
                                   font=(FAMILY, 10), fill=MUTED)

        def on_click(event) -> None:
            # 行号按“过滤后可见顺序”还原成真实字体名；减掉列表顶部偏移精确命中
            offset = canvas.canvasy(event.y) - row_inset
            if offset < 0:
                return
            index = int(offset // row_height)
            current_rows = visible_rows()
            if 0 <= index < len(current_rows):
                self._on_font_changed(current_rows[index][1])
                refresh_preview()
                render()

        def on_wheel(event) -> None:
            canvas.yview_scroll(int(-event.delta / 120) or -1, "units")

        canvas.bind("<Button-1>", on_click)
        canvas.bind("<MouseWheel>", on_wheel)
        canvas.bind("<Configure>", lambda _event: render())
        search_var.trace_add("write", lambda *_args: render())
        window.bind("<Escape>", lambda _event: self._close_font_picker())

        tk.Button(window, text="完成", command=self._close_font_picker, bg=ACCENT, fg=ON_ACCENT,
                  activebackground=ACCENT_ACTIVE, activeforeground=ON_ACCENT, relief="flat",
                  borderwidth=0, padx=px(16), pady=px(9), cursor="hand2",
                  font=(FAMILY, 9)).pack(pady=px(14))
        window.protocol("WM_DELETE_WINDOW", self._close_font_picker)

        window.update_idletasks()
        width, height = px(460), px(520)
        self._place_dialog(window, width, height)

        refresh_preview()
        render()
        search.focus_set()

    def _close_font_picker(self) -> None:
        if self.font_window is not None:
            self.font_window.destroy()
            self.font_window = None
        self._sync_font_row()

    def _close_settings(self) -> None:
        if self.settings_job:
            self.root.after_cancel(self.settings_job)
            self._persist_settings()
        self._close_font_picker()
        if self.settings_window is not None:
            self.settings_window.destroy()
            self.settings_window = None

    # ---------- 关闭 ----------

    def _cancel_after_jobs(self) -> None:
        """取消所有待触发的 after 定时器。

        窗口销毁时若还有排期，回调会在解释器销毁后触发，抛出
        `invalid command name "..._persist_settings"` 之类的噪音。
        """
        for name in ("watch_job", "settings_job", "save_job", "_speech_poll"):
            job = getattr(self, name, None)
            if not job:
                continue
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
            setattr(self, name, None)

    def _on_root_destroy(self, event: "tk.Event") -> None:
        # 任何销毁路径（含测试里直接 destroy）都要清掉定时器
        if event.widget is self.root:
            self._cancel_after_jobs()

    def _on_close(self) -> None:
        # 顺序要紧：先把设置落盘、再保存正文，最后才销毁
        if self.settings_job:
            try:
                self.root.after_cancel(self.settings_job)
            except tk.TclError:
                pass
            self.settings_job = None
            self._persist_settings()
        if self._finish_pending_edit():
            self.root.destroy()


def main() -> None:
    root = tk.Tk()
    JianJiApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
