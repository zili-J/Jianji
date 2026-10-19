"""全文搜索：只回答「哪篇文档、第几行、第几列」，不碰 Tk。

单独成模块的理由和 `platform_support` 一样：这里的判据（行号怎么数、列号是字符
偏移还是像素、摘录怎么截）全是纯函数，可以不起窗口就测；`main.py` 已经有五千多行，
再把这些塞进去，界面和搜索规则会搅在一起，改一边就得担心另一边。

**匹配规则只有一条：字面量子串，不区分大小写。** 没有正则、没有分词。
日记里搜的是「昨天那件事」，不是写查询语言；把用户敲的 `(` 当正则语法去解析，
只会得到一个「正则写错了」的报错。`re.escape` 之后剩下的就是「找这几个字」。

行号 **1 起**、列号 **0 起**——前者和编辑器里看到的行号一致，后者是 Tk 的
`line.column` 索引格式，两个数字拼起来 `f"{line}.{column}"` 就能直接喂给 Text。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: 一次搜索最多返回多少条命中。再多列表也翻不动，而且用户真正要的是前几条。
MAX_RESULTS = 300

#: 单篇文档最多贡献多少条。防止一个高频词把整屏结果都占成同一篇。
MAX_HITS_PER_FILE = 20

#: 摘录在命中点前后各留多少字。前面少留一点（命中点靠左更好扫），后面多留一点。
SNIPPET_BEFORE = 24
SNIPPET_AFTER = 60

#: 摘录的硬上限（字符）。结果面板一行放得下这么多，再长就换行了。
SNIPPET_MAX = 64

#: 只认第一行的一级标题作为文档名。和 `main.first_h1_title` 同一个意思，
#: 但不能 import main（main 反过来要 import 本模块）。
_H1_RE = re.compile(r"^#\s+(.+?)\s*$")


@dataclass(frozen=True)
class Hit:
    """一条命中。

    `path` 为 None 表示这是在内存里的文本上搜的（当前文稿），没有对应文件。
    """

    path: Path | None
    line: int          # 1 起
    column: int        # 0 起，行内字符偏移
    length: int        # 命中字符数
    snippet: str       # 结果列表里显示的那一行（已截好、带省略号）
    title: str         # 文档名（H1，退回文件名主干）

    @property
    def index(self) -> str:
        """Tk Text 的索引串，例如 `12.5`。"""
        return f"{self.line}.{self.column}"


def normalize(query: str) -> str:
    """把用户敲的查询收拾成「可以拿去比」的样子。

    只做首尾去空白。**不做大小写折叠**——折叠要另存一份和原文等长的串才能保住
    列号，而 `re.IGNORECASE` 已经能做到同样的效果，还不会让列号错位。
    """
    return (query or "").strip()


def _compile(query: str) -> re.Pattern[str] | None:
    query = normalize(query)
    if not query:
        return None
    return re.compile(re.escape(query), re.IGNORECASE)


def snippet_for(line: str, column: int, length: int) -> str:
    """从整行里截出命中点附近的一小段，两端该加省略号就加。

    先按**原始行**算窗口（列号是相对原始行的，先 strip 会让它错位），再收拾
    制表符和首尾空白。命中点保证落在窗口里——这是这个函数唯一的硬要求，
    否则结果列表里会出现「有这条结果，但看不见搜的词」。
    """
    start = max(0, column - SNIPPET_BEFORE)
    end = min(len(line), column + length + SNIPPET_AFTER)
    piece = line[start:end].replace("\t", " ")
    if len(piece) > SNIPPET_MAX:
        # 超长行（一行几千字的那种）只留窗口左半边：命中点在最左，一定保得住
        piece = piece[:SNIPPET_MAX]
        end = start + SNIPPET_MAX
    piece = piece.strip()
    if start > 0:
        piece = "…" + piece
    if end < len(line):
        piece = piece + "…"
    return piece


def title_of(text: str, path: Path | None = None) -> str:
    """文档名：第一个非空行是一级标题就用它，否则用文件名主干。

    比 `first_h1_title` 宽松一格：**跳过开头的空行**。文件名是打开时按 H1 起的，
    但有人会先在正文最前面敲个回车；因为一个空行就让结果列表全变成
    `新建-2026-10-19-2233` 这种名字，不值得。
    """
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _H1_RE.match(line)
        if match:
            return match.group(1).strip()
        break
    return path.stem if path is not None else ""


def find_in_text(text: str, query: str, *, path: Path | None = None,
                 title: str | None = None,
                 limit: int = MAX_HITS_PER_FILE) -> list[Hit]:
    """在一段文本里找出所有命中，按行号顺序返回。"""
    pattern = _compile(query)
    if pattern is None or limit <= 0:
        return []
    name = title_of(text, path) if title is None else title
    hits: list[Hit] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for match in pattern.finditer(line):
            hits.append(Hit(
                path=path,
                line=number,
                column=match.start(),
                length=match.end() - match.start(),
                snippet=snippet_for(line, match.start(), match.end() - match.start()),
                title=name,
            ))
            if len(hits) >= limit:
                return hits
    return hits


def _read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def search_files(paths, query: str, *, limit: int = MAX_RESULTS,
                 per_file: int = MAX_HITS_PER_FILE,
                 read=None) -> tuple[list[Hit], bool]:
    """在一批文件里搜，返回 `(命中列表, 是否被截断)`。

    `paths` 由调用方给（顺序就是结果的顺序），本函数不负责遍历目录——那属于
    `storage` 的事，而且「要不要跳过回收站」这种判断已经在 `list_markdown` 里了。

    `read` 是可选的自定义读法（`path -> str`），界面层用它挂文件缓存、
    顺便让「当前打开但还没存盘」的那篇走编辑区里的正文。

    读不了的文件（权限、非 UTF-8）**跳过而不是抛错**：搜索是随手用的功能，
    一个坏文件不该让整次搜索失败。
    """
    pattern = _compile(query)
    if pattern is None:
        return [], False
    reader = read or _read_file
    hits: list[Hit] = []
    for path in paths:
        remaining = limit - len(hits)
        if remaining <= 0:
            return hits, True
        try:
            text = reader(path)
        except (OSError, UnicodeError):
            continue
        if text is None:
            continue
        # 先算标题再找：同一个文件里每条结果都要用，别在循环里算 N 遍
        title = title_of(text, path)
        file_hits = find_in_text(text, query, path=path, title=title,
                                 limit=min(per_file, remaining))
        hits.extend(file_hits)
        if len(hits) >= limit:
            return hits, True
    return hits, False
