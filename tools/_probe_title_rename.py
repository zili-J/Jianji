"""「文件名跟随一级标题」端到端核验：真的开窗口、真的改标题、真的存盘、真的看盘上文件名。

**为什么不能只测 `title_to_filename`**：真正会出事的地方是「什么时候才改名」。
用户文稿里现成的反例一抓一把——`2026-09-13.md` 与 `2026-09-14.md` 的 H1 都是
`# 今日日记`、`开发记录.md` 的 H1 是 `# 简记优化记录`。要是「存盘就按 H1 改名」，
第一次保存就会把这一片名字全换掉（前两个还会当场撞车）。所以这里逐条摆出来量。

另外还要量两条被改名牵连的路径：`move_document_to` / `delete_document` 都是
「先 save_now 再拿手里的 path 去干活」，改名会让那个 path 指向一个不存在的名字。

    python tools/quiet_desktop.py <python> tools/_probe_title_rename.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))
sys.path.insert(0, str(PROJECT / "tools"))

import storage  # noqa: E402

# 名字与标题故意对不上，抄的就是用户文稿里真实的样子
DOCS = {
    "2026-09-13.md": "# 今日日记\n\n今天把留白那件事收尾了。\n",
    "2026-09-14.md": "# 今日日记\n\n另一天的日记，标题跟上面一模一样。\n",
    "开发记录.md": "# 简记优化记录\n\n这一篇名字是用户自己起的。\n",
    "新建-2026-09-28-105452.md": "# 人生重要的没有几件事\n\n正文。\n",
    "无标题.md": "第一行不是标题，只是一句正文。\n\n# 后面才有标题\n",
    "空标题.md": "# 有个标题\n\n正文。\n",
}

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if ok else '**NG**'}] {label}" + (f"    {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def real_settings() -> dict:
    path = Path(os.environ.get("LOCALAPPDATA", "")) / "JianJi" / "state.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("settings", {})
    except (OSError, ValueError):
        return {}


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    state_path = tmp / "state.json"
    state_path.write_text(json.dumps({"settings": real_settings()}, ensure_ascii=False),
                          encoding="utf-8")
    storage.default_state_path = lambda: state_path
    folder = tmp / "简记日记"
    folder.mkdir()
    for name, text in DOCS.items():
        (folder / name).write_text(text, encoding="utf-8")
    storage.set_default_folder(folder)

    import tkinter as tk  # noqa: E402
    import main as app_main  # noqa: E402

    # 探针绝不能停在模态框上：隐藏桌面上没人能点「确定」，会一直挂到被杀。
    # 把 messagebox 换成记录器，最后统一列出来。
    dialogs: list[tuple[str, str, str]] = []

    class _DialogRecorder:
        def __getattr__(self, name: str):
            def record(title, message="", **_kwargs):
                dialogs.append((name, str(title), str(message)))
                if name in ("askyesno", "askokcancel"):
                    return True
                if name == "askyesnocancel":
                    return False
                if name == "askstring":
                    return None
                return None
            return record

    app_main.messagebox = _DialogRecorder()          # type: ignore[assignment]

    root = tk.Tk()
    root.geometry("1240x820")
    app = app_main.JianJiApp(root)
    root.update()

    def flush() -> bool:
        """把待存的那一次立刻存掉（顶掉 700ms 的自动保存）。"""
        if app.save_job:
            root.after_cancel(app.save_job)
            app.save_job = None
        return app.save_now()

    def edit_first_line(text: str) -> None:
        app.editor.delete("1.0", "1.end")
        app.editor.insert("1.0", text)
        root.update()
        if not app.dirty:                       # 隐藏桌面上虚拟事件偶尔不投递，兜一手
            app._on_editor_modified()

    def on_disk() -> set[str]:
        return {p.name for p in folder.glob("*.md")}

    # ---------- 1. 只改正文、不动标题：不许改名 ----------
    print("\n=== 1. 只改正文，标题没动 ===")
    for name in ("2026-09-13.md", "2026-09-14.md", "开发记录.md",
                 "新建-2026-09-28-105452.md"):
        before = on_disk()
        app.open_file(folder / name)
        root.update()
        check(f"{name}：载入时的标题 = {app._title_at_load!r}", app._title_at_load is not None)
        app.editor.insert("end-1c", "补一句正文。")
        root.update()
        if not app.dirty:
            app._on_editor_modified()
        ok = flush()
        check(f"{name}：存盘后文件名不变（H1 与名字对不上也照旧）",
              ok and app.current_path.name == name and on_disk() == before,
              f"现在叫 {app.current_path.name!r}")
        check(f"{name}：正文补进去了", "补一句正文。" in app.current_path.read_text(encoding="utf-8"))

    # ---------- 2. 改标题：文件名跟上 ----------
    print("\n=== 2. 改一级标题，文件名跟着改 ===")
    app.open_file(folder / "新建-2026-09-28-105452.md")
    root.update()
    edit_first_line("# 今天想通了")
    ok = flush()
    check("改名成功", app.current_path.name == "今天想通了.md", f"现在叫 {app.current_path.name!r}")
    check("旧名字已经从盘上消失", "新建-2026-09-28-105452.md" not in on_disk())
    check("正文没丢", "正文。" in app.current_path.read_text(encoding="utf-8"),
          repr(app.current_path.read_text(encoding="utf-8")))
    check("状态栏报出改名", "今天想通了" in app.status_label.cget("text"),
          repr(app.status_label.cget("text")))
    check("磁盘签名仍然有效（改名不动 mtime/size/digest）",
          app.disk_signature == storage.signature(app.current_path))
    check("文稿列表选中跟着新名字", app.current_path in app.file_paths)

    print("\n=== 2b. 同一标题再存一次：不该反复改名 ===")
    app.editor.insert("end-1c", "再补一句。")
    root.update()
    if not app.dirty:
        app._on_editor_modified()
    flush()
    check("文件名没变", app.current_path.name == "今天想通了.md", app.current_path.name)
    check("_title_at_load 已更新", app._title_at_load == "今天想通了", repr(app._title_at_load))

    # ---------- 3. 非法字符 ----------
    print("\n=== 3. 标题里有 Windows 禁用的字符 ===")
    edit_first_line("# 第1章/第2节: 真的吗?")
    flush()
    check("禁字符换成全角同形字",
          app.current_path.name == "第1章／第2节： 真的吗？.md", app.current_path.name)
    check("文件确实存在", app.current_path.exists())

    # ---------- 4. 标题被删空：保住原名 ----------
    print("\n=== 4. 标题被删空 ===")
    app.open_file(folder / "空标题.md")
    root.update()
    edit_first_line("# ")
    flush()
    check("不改名，也不报错", app.current_path.name == "空标题.md", app.current_path.name)
    check("文件还在", app.current_path.exists())

    print("\n=== 4b. 第一行不是标题（标题在第 3 行）===")
    app.open_file(folder / "无标题.md")
    root.update()
    check("载入时就没有标题", app._title_at_load is None, repr(app._title_at_load))
    app.editor.insert("end-1c", "加一句。")
    root.update()
    if not app.dirty:
        app._on_editor_modified()
    flush()
    check("不改名", app.current_path.name == "无标题.md", app.current_path.name)

    # ---------- 5. 撞名 ----------
    print("\n=== 5. 改出来的名字跟别人撞了 ===")
    app.open_file(folder / "2026-09-13.md")
    root.update()
    edit_first_line("# 开发记录")             # 已经有 开发记录.md 了
    flush()
    check("自动加序号，绝不覆盖",
          app.current_path.name == "开发记录-2.md", app.current_path.name)
    check("被撞的那份内容没被动过",
          (folder / "开发记录.md").read_text(encoding="utf-8").startswith("# 简记优化记录"),
          repr((folder / "开发记录.md").read_text(encoding="utf-8")[:20]))

    # ---------- 6. 改名之后「移动到」还要能用 ----------
    print("\n=== 6. 改名之后紧接着「移动到其他文件夹」 ===")
    target = storage.create_folder(folder, "归档")
    app.open_file(folder / "2026-09-14.md")
    root.update()
    edit_first_line("# 归档的那一天")
    app.editor.insert("end-1c", "补一句，让它变脏。")
    root.update()
    if not app.dirty:
        app._on_editor_modified()
    app.move_document_to(app.current_path, target)      # 里面会先 save_now（触发改名）
    check("移动成功（没拿着改名前的旧路径去移动）",
          app.current_path.parent == target and app.current_path.name == "归档的那一天.md",
          str(app.current_path))
    check("文件真的在归档里", (target / "归档的那一天.md").is_file())

    # ---------- 7. 改名之后「删除」还要能用 ----------
    print("\n=== 7. 改名之后紧接着删除 ===")
    app.open_file(folder / "无标题.md")
    root.update()
    edit_first_line("# 临时名字")
    app.editor.insert("end-1c", "补一句，让它变脏。")
    root.update()
    if not app.dirty:
        app._on_editor_modified()
    app.delete_document(app.current_path)
    check("删除成功（没拿着改名前的旧路径去回收）",
          not (folder / "临时名字.md").exists() and app.current_path is None,
          str(app.current_path))
    check("确实进了回收站",
          any("临时名字.md" in str(entry.original_relative)
              for entry in storage.list_trash(folder)),
          str([entry.original_relative for entry in storage.list_trash(folder)]))

    # ---------- 8. 切文档时把上一篇的改名也落实 ----------
    print("\n=== 8. 改完标题直接点开另一篇（没等自动保存）===")
    app.open_file(folder / "空标题.md")
    root.update()
    edit_first_line("# 换名字了")
    app.open_file(folder / "开发记录.md")            # 切走，open_file 里会 _finish_pending_edit
    root.update()
    check("上一篇按新标题改了名", (folder / "换名字了.md").is_file(), str(sorted(on_disk())))
    check("切过去的那一篇正常打开", app.current_path.name == "开发记录.md", app.current_path.name)

    print("\n=== 最后盘上的文件名 ===")
    for path in sorted(folder.rglob("*.md")):
        print(f"    {path.relative_to(folder).as_posix()}    H1={app_main.first_h1_title(path.read_text(encoding='utf-8-sig'))!r}")

    if dialogs:
        print("\n=== 弹过的对话框 ===")
        for name, title, message in dialogs:
            print(f"    {name}({title!r}, {message[:60]!r})")
    check("全程没有弹过错误框",
          not [d for d in dialogs if d[0] in ("showerror", "showwarning")],
          str(dialogs))

    root.destroy()
    print("\n" + ("全部通过" if not FAILURES else f"**{len(FAILURES)} 项不过**：" + "、".join(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
