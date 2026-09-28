"""量编辑区「显示行」的填充率，找出折行断得过早的行。

用户报：「引用有中英混合时会换行」「文字间插入空格会换行」（都在**编辑区**里）。

两个要害，缺一个就量不出来：

1. **必须分别量「光标在别处」和「光标在本行」**。光标落在某行时那行会露出源码记号
   （`> `、`**`、`` ` ``），整行变宽，正好可能把一个词挤到下一显示行——用户盯着的
   恰恰就是光标所在那行，所以这才是主场景。光标在别处时量出来的「好看」没有意义。
2. **填充率要扣掉行尾空白**。编辑区控件用 `wrap="word"`，Tk 只在**空格处**断行，
   断点那个空格会被画在行尾；算上它就「满」了，其实右边空了一截。
   所以 ink 宽度要取**最后一个非空格字符**的右边缘。

判据：同一段里**不是最后一行的显示行**都必须填满。纯中文（一个空格都没有）做对照。

阈值取 **90%** 而不是 95%：在空格处断行时，行尾那个空格是**正常**被消耗掉的，
扣掉它之后行末天然会空出「一个空格」的宽度（实测 94.6%~95.6%）。真正要抓的是
「整串被挪走」那种大空档——实测能到 4.7% 和 41.9%，跟一个空格差着量级。

    python tools/quiet_desktop.py <python 全路径> tools/_probe_editor_wrap.py

只读用户 `state.json` 的 `settings`；文档写在临时目录里，不碰用户文件夹。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import storage  # noqa: E402
import tkinter as tk  # noqa: E402

REAL_STATE = Path(os.environ.get("LOCALAPPDATA", "")) / "JianJi" / "state.json"

# (标签, 源文本)——标签只用来打印，源文本才是写进文档的那一行
CASES = [
    ("对照：纯中文无空格",
     "这是一段用来做对照的纯中文文字里面完全没有空格所以折行位置应该填满整行才对。"),
    ("中文里插空格",
     "这是一段 用来做对照的 中文文字 里面 插入了 空格 所以 折行 位置 会 提前 结束。"),
    ("引用 + 中英混合",
     "> 这是一段引用，里面 English words mixed 在一起，看看会不会异常换行。"),
    ("普通段落 + 中英混合",
     "这一段中文里夹杂 English words 和 more words，用来观察中英边界处的断行行为。"),
    ("中文里插一个空格（贴近行尾）",
     "这一段中文本来没有空格只是在很靠后的位置 插了一个空格看看会不会因此断行。"),
    ("空格后跟一整串长中文（最坏情况）",
     "短 后面这一整串中文没有任何空格所以会被当成一个词整串挪到下一行去。"),
    ("引用 + 空格 + 长中文串",
     "> 引用开头 English 后面这一整串中文没有任何空格会被整串挪到下一行去。"),
    ("列表项 + 空格 + 长中文串",
     "- 列表开头 English 后面这一整串中文没有任何空格会被整串挪到下一行去。"),
]


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    saved = storage.default_state_path
    storage.default_state_path = lambda: tmp / "state.json"

    settings = {}
    if REAL_STATE.is_file():
        settings = json.loads(REAL_STATE.read_text(encoding="utf-8")).get("settings", {})
    folder = tmp / "简记日记"
    folder.mkdir()
    doc = folder / "折行核验.md"
    body = ["# 折行核验", ""]
    for _label, source in CASES:
        body += [source, ""]
    doc.write_text("\n".join(body) + "\n", encoding="utf-8")
    (tmp / "state.json").write_text(json.dumps({
        "folder": str(folder), "default_folder": str(folder),
        "last_file": str(doc), "settings": settings,
    }, ensure_ascii=False), encoding="utf-8")

    import main as app_main  # noqa: E402

    root = tk.Tk()
    root.geometry("1180x760")
    app = app_main.JianJiApp(root)
    root.update()
    if os.environ.get("JIANJI_WRAP"):
        app.editor.configure(wrap=os.environ["JIANJI_WRAP"])
        print(f"**控件级 wrap 强制为 {os.environ['JIANJI_WRAP']}**")
    app.open_file(doc)
    root.update()

    editor = app.editor
    total = int(editor.index("end-1c").split(".")[0])
    avail = editor.winfo_width() - 2 * app._current_pad
    print(f"设置 {app.settings}")
    print(f"控件宽 {editor.winfo_width()}  留白 {app._current_pad}  "
          f"→ 正文可用宽 {avail}  (行宽设置 {app.settings['line_width']}%)\n")

    def display_lines(line: int):
        """按 y 分组把显示行拼回来。返回 [(y, 起始列, 结束列, 左x, 墨迹右x)]。"""
        editor.tk.call(editor, "count", "-update", "1.0", "end")
        source = editor.get(f"{line}.0", f"{line}.end")
        groups: list[list[int]] = []
        for i in range(len(source) + 1):
            box = editor.bbox(f"{line}.{i}")
            if box is None:
                continue
            x, y, w, _h = box
            if groups and groups[-1][0] == y:
                groups[-1][2] = i
                groups[-1][3] = min(groups[-1][3], x)
                groups[-1][4] = max(groups[-1][4], x + w)
            else:
                groups.append([y, i, i, x, x + w])
        # 行尾空格会把「填充率」撑满，墨迹右边缘改取最后一个非空格字符
        for group in groups:
            start, last = group[1], group[2]
            for j in range(last, start - 1, -1):
                if source[j:j + 1].strip():
                    box = editor.bbox(f"{line}.{j}")
                    if box is not None:
                        group[4] = box[0] + box[2]
                    break
        return source, groups

    def move_cursor(target: str) -> None:
        editor.mark_set("insert", target)
        editor.see("insert")
        app._refresh_cursor_line()
        root.update()

    early_total = 0
    for index, (label, _source) in enumerate(CASES):
        line = 3 + index * 2
        print("=" * 78)
        print(f"{label}  第 {line} 行")
        for state, target in (("光标在别处", f"{total}.0"), ("光标在本行", f"{line}.end")):
            move_cursor(target)
            text, groups = display_lines(line)
            print(f"  [{state}] 显示行 {len(groups)} 个")
            for k, (_y, start, last, left, right) in enumerate(groups):
                used = right - left
                ratio = used / avail if avail > 0 else 0.0
                is_last = k == len(groups) - 1
                flag = ""
                if not is_last and ratio < 0.90:
                    flag = "  ← 断得太早"
                print(f"      显示行{k} [列 {start},{last + 1}) 墨迹宽 {used:4d} "
                      f"填充 {ratio * 100:5.1f}% [{text[start:last + 1]}]{flag}")
            early = sum(1 for k, (_y, s, _l, lf, rt) in enumerate(groups)
                        if k != len(groups) - 1
                        and avail > 0 and (rt - lf) / avail < 0.90)
            print(f"      → 段中行断得太早：{early} 处")
            early_total += early
    print("=" * 78)
    print(f"合计断得太早：{early_total} 处（两个光标状态都算；对照那一条应为 0）")
    root.destroy()
    storage.default_state_path = saved
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
