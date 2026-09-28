"""把工程根目录的《简记使用说明.md》同步到用户的固定位置，并重出长图。

固定位置是用户明确要求的：`C:\\Users\\22910\\Documents\\wb01\\说明书\\`。
长图走**应用自己的导出链路**（`JianJiApp._export_options()` + `render_long_image`），
所以它和用户右键「导出长图」得到的图完全一致——包括当前设置换算出来的行距。

    python tools/quiet_desktop.py <python 全路径> tools/_sync_manual.py

只读用户的 `state.json` 拿 `settings`，写文件只用临时 state.json，
绝不碰用户真实状态与文档文件夹（除固定同步位置）。
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import image_export as ie  # noqa: E402
import main as app_main  # noqa: E402
import storage  # noqa: E402
import tkinter as tk  # noqa: E402

SOURCE = PROJECT / "简记使用说明.md"
TARGET_DIR = Path(r"C:\Users\22910\Documents\wb01\说明书")
#: 这个位置最早用的文件名。**现在文件名会跟着正文第一行的一级标题走**——用户只要在软件里
#: 打开这篇说明、动一下标题（哪怕只是把标题重新敲一遍），它就会变成 `简记 · 使用说明.md`。
#: 硬写死一个名字的话，下一次同步会**在旁边多出一份副本**，两份说明各说各话。
LEGACY_STEM = "简记使用说明"
REAL_STATE = Path(os.environ.get("LOCALAPPDATA", "")) / "JianJi" / "state.json"


def png_size(data: bytes) -> tuple[int, int]:
    return struct.unpack(">II", data[16:24])


def resolve_stem() -> str:
    """目标文件名主干：已经存在的那一份优先，其次按正文第一行的 H1 推算。"""
    text = SOURCE.read_text(encoding="utf-8-sig")
    title = app_main.first_h1_title(text)
    computed = app_main.title_to_filename(title) if title else None
    for candidate in (computed, LEGACY_STEM):
        if candidate and (TARGET_DIR / f"{candidate}.md").exists():
            return candidate
    return computed or LEGACY_STEM


def main() -> int:
    if not SOURCE.exists():
        print(f"找不到源文件：{SOURCE}")
        return 1

    # 1) 同步 Markdown（先落盘，长图从同步后的正文渲染，两者必然一致）
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    stem = resolve_stem()
    target_md = TARGET_DIR / f"{stem}.md"
    target_png = TARGET_DIR / f"{stem}.png"
    shutil.copyfile(SOURCE, target_md)
    print(f"已同步 {SOURCE.name} → {target_md}")

    # 2) 用用户真实 settings 构造应用，渲染长图
    tmp = Path(tempfile.mkdtemp())
    saved = storage.default_state_path
    storage.default_state_path = lambda: tmp / "state.json"
    settings = {}
    if REAL_STATE.exists():
        settings = json.loads(REAL_STATE.read_text(encoding="utf-8")).get("settings", {})
    folder = tmp / "简记日记"
    folder.mkdir()
    (tmp / "state.json").write_text(json.dumps({
        "folder": str(folder), "default_folder": str(folder),
        "last_file": "", "settings": settings}, ensure_ascii=False), encoding="utf-8")

    root = tk.Tk()
    root.geometry("1180x760")
    app = app_main.JianJiApp(root)
    root.update()
    options = app._export_options()
    fonts = ie._Fonts()
    try:
        text = target_md.read_text(encoding="utf-8-sig")
        images = ie.render(app_main.build_export_rows(text, options), options,
                           app_main.export_palette(), fonts)
    finally:
        fonts.close()
        root.destroy()
        storage.default_state_path = saved

    # 3) 落盘；清掉上一次留下的多余分张（比如上一版是 3 张、这一版只要 1 张）
    written: list[Path] = []
    for index, data in enumerate(images):
        target = target_png if index == 0 else TARGET_DIR / f"{stem}-{index + 1}.png"
        target.write_bytes(data)
        width, height = png_size(data)
        written.append(target)
        print(f"{target.name}: {width}×{height}  {len(data) / 1024:.0f} KB")

    keep = {path.name for path in written}
    # 3a) 说明改过名时，旧名字那一套 png 会变成孤儿，一并清掉
    for other in ({stem, LEGACY_STEM} - {stem}):
        orphans = sorted(TARGET_DIR.glob(f"{other}.png")) + sorted(TARGET_DIR.glob(f"{other}-*.png"))
        for orphan in orphans:
            if orphan.name not in keep:
                orphan.unlink()
                print(f"已删除过期分张：{orphan.name}")

    # 3b) 同一主干下的多余分张
    stale_index = len(images) + 1
    while True:
        stale = TARGET_DIR / f"{stem}-{stale_index}.png"
        if not stale.exists():
            break
        stale.unlink()
        print(f"已删除过期分张：{stale.name}")
        stale_index += 1

    lines = len(app_main.split_document_lines(text))
    print(f"共 {lines} 行，{len(images)} 张")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
