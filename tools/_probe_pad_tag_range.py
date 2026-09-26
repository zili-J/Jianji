"""底部留白标签该挂到哪个范围：两种文稿结尾 × 若干候选范围，全都量一遍。

要同时满足三件事：
1. 滚到**真正的底**时，最后一行下面留出 ≈ 25% 视口高的空白；
2. `yview()` 的右端是 1.0（否则编辑区滚动条的滑块到不了底）；
3. **两种结尾都成立**——文稿可能以换行结尾（`...正文。\\n`），也可能不以换行结尾。

踩过的：`end-1c linestart` → `end` 跨了**两**行，`spacing3` 算两遍，把 Tk 认的
总高度撑虚（可见留白对，但滑块到不了底）；`end-1c lineend` → `end` 在
「结尾没有换行」的文稿上退化成**空区间**，留白静默失效。

    python tools/quiet_desktop.py <python> tools/_probe_pad_tag_range.py
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

BODY = "\n".join(f"第 {i} 段正文，用来把文稿撑长一点，好让它超过一屏。"
                 for i in range(1, 80))

DOCS = {
    "以换行结尾": "# 长文\n\n" + BODY + "\n最后一句正文。\n",
    "不以换行结尾": "# 长文\n\n" + BODY + "\n最后一句正文。",
}

RANGES = {
    "【不动标签，就用 open_file 挂的那份】": None,
    "【再 force 挂一次（同 open_file 的代码路径）】": "force",
    "end-1c linestart → end    （跨两行，旧写法）": ("end-1c linestart", "end"),
    "end-1c → end              （只覆盖末尾一个字符）": ("end-1c", "end"),
    "end-1c lineend → end      （正文行尾 → 末尾）": ("end-1c lineend", "end"),
    "end-2c → end              （末尾两个字符）": ("end-2c", "end"),
    "end-1c linestart → end-1c lineend（只包最后一行，不碰末尾空行）":
        ("end-1c linestart", "end-1c lineend"),
}


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
        (folder / f"{name}.md").write_text(text, encoding="utf-8")
    storage.set_default_folder(folder)

    import tkinter as tk  # noqa: E402
    import main as app_main  # noqa: E402

    root = tk.Tk()
    root.geometry("1240x820")
    app = app_main.JianJiApp(root)
    root.update()

    for doc_name in DOCS:
        app.open_file(folder / f"{doc_name}.md")
        root.update_idletasks()
        root.update()
        editor = app.editor
        viewport = editor.winfo_height()
        pad = app._bottom_pad
        print(f"\n=== {doc_name} ===   视口高 {viewport}  留白 {pad}"
              f"（应 ≈ {int(viewport * app_main.EDITOR_BOTTOM_PAD_RATIO)}）")

        for label, spec in RANGES.items():
            if spec is None:
                pass                                  # 什么都不做，用 open_file 挂的
            elif spec == "force":
                app._apply_bottom_pad(force=True)     # 走真实代码路径重挂一次
            else:
                start, end = spec
                editor.tag_remove("bottom_pad", "1.0", "end")
                editor.tag_add("bottom_pad", start, end)
            # 多转几圈：Tk 只为视口附近的行排版，`yview()` 的分母是逐步收敛的
            for _ in range(3):
                root.update_idletasks()
                root.update()
            editor.yview_moveto(1.0)
            for _ in range(3):
                root.update_idletasks()
                root.update()

            ranges = editor.tag_ranges("bottom_pad")
            covered = editor.get(*ranges).replace("\n", "\\n") if ranges else ""
            first, last = editor.yview()
            box = editor.bbox("end-1c")
            if box:
                blank = viewport - (box[1] + box[3])
                ratio = blank / viewport
                blank_txt = f"{blank} px = {ratio:.1%}"
            else:
                blank_txt = "末行量不到"
            ok = (last > 0.999 and box is not None
                  and 0.8 <= (blank / viewport) / app_main.EDITOR_BOTTOM_PAD_RATIO <= 1.2)
            print(f"  {label}")
            print(f"      覆盖 {covered!r}   留白 {blank_txt}"
                  f"   yview 右端 {last:.4f}   {'OK' if ok else '** 不行 **'}")

    root.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
