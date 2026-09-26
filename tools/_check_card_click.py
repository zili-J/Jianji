"""核验卡片列的点击命中与「点完不乱跳」，用于人工核验。

产出 `outputs/卡片点击命中.txt`。要看的是：

1. **点得中**：列表在顶部 / 滚到中间 / 滚到底，三个位置上点每张卡片的正中，
   都要命中它自己。过去把控件坐标直接喂给 `find_overlapping`（要画布坐标），
   一滚动就错位——点第 12 张选中第 0 张，靠上的卡片干脆撞不到。
2. **空隙也能点**：卡片之间那条 12px 的空隙、左右留白，都该归最近的卡片。
3. **点完不乱跳**：点一张已经整张可见的卡片，列表一动不动；点一张露不全的，
   正好把它整张挪进来，不多不少。

同类见 `tools/_check_incremental.py`、`tools/_check_wheel_speed.py`。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))
sys.path.insert(0, str(PROJECT / "tools"))

import storage  # noqa: E402
import tkinter as tk  # noqa: E402


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    real = storage.default_state_path
    storage.default_state_path = lambda: tmp / "state.json"

    folder = tmp / "简记日记"
    folder.mkdir()
    # 卡片高度故意有高有低：奇数篇一行正文，偶数篇四行——「按张数算比例」的错法
    # 只有在高度不齐时才会露出来
    for day in range(1, 25):
        body = [f"# 第 {day} 篇", ""]
        if day % 2:
            body.append("只有一行正文。")
        else:
            body.extend(f"正文第 {i} 行，写长一点好让它折行、把卡片撑高。"
                        for i in range(1, 5))
        (folder / f"2026-09-{day:02d}.md").write_text(
            "\n".join(body) + "\n", encoding="utf-8")
    storage.set_default_folder(folder)

    import main as app_main

    root = tk.Tk()
    root.geometry("1180x760")
    app = app_main.JianJiApp(root)
    root.update()
    app.open_file(folder / "2026-09-24.md")
    root.update()

    canvas = app.cards_canvas
    total = len(app.file_paths)
    viewport = canvas.winfo_height()
    lines: list[str] = []
    bad = 0

    def say(text: str) -> None:
        print(text, flush=True)
        lines.append(text)

    say(f"共 {total} 篇，视口高 {viewport}，内容高 {app._cards_content_height}")

    def click(order: int) -> int | None:
        """按卡片正中点击——换算成控件坐标，走真实回调那条路径。"""
        box = canvas.bbox(f"cardbg{order}")
        widget_y = int((box[1] + box[3]) / 2 - canvas.canvasy(0))
        widget_x = int((box[0] + box[2]) / 2 - canvas.canvasx(0))
        return app._card_index_at(widget_x, widget_y)

    say("")
    say("== 一、三个滚动位置上，每张卡片都要点得中自己 ==")
    for fraction in (0.0, 0.5, 1.0):
        canvas.yview_moveto(fraction)
        root.update()
        missed, wrong = [], []
        for order in range(total):
            got = click(order)
            if got is None:
                missed.append(order)
            elif got != order:
                wrong.append(f"{order}->{got}")
        bad += len(missed) + len(wrong)
        say(f"  滚到 {fraction:.1f}（原点 {canvas.canvasy(0):7.0f}）："
            f"点不中 {len(missed)} 张，点错 {len(wrong)} 张"
            + (f"  点不中={missed} 点错={wrong}" if missed or wrong else "  ✓"))

    canvas.yview_moveto(0.0)
    root.update()
    say("")
    say("== 二、卡片之间的空隙、左右留白、圆角 ==")
    for order in (0, 1, 2):
        box = canvas.bbox(f"cardbg{order}")
        centre_x = (box[0] + box[2]) / 2
        for label, x, cy in (
            ("下沿下方 2px", centre_x, box[3] + 2),
            ("左侧留白 x=4", 4, (box[1] + box[3]) / 2),
            ("右下圆角内侧", box[2] - 3, box[3] - 3),
        ):
            got = app._card_index_at(int(x - canvas.canvasx(0)),
                                     int(cy - canvas.canvasy(0)))
            ok = got == order
            bad += 0 if ok else 1
            say(f"  第 {order} 张 {label:14s} -> {got}"
                f"{'' if ok else '   ✗ 应该选它自己'}")

    say("")
    say("== 三、点完之后列表跳多少 ==")
    canvas.yview_moveto(0.5)
    root.update()
    origin = canvas.canvasy(0)
    visible = [o for o in range(total)
               if canvas.bbox(f"cardbg{o}")[1] >= origin
               and canvas.bbox(f"cardbg{o}")[3] <= origin + viewport]
    for order in visible:
        before = canvas.canvasy(0)
        app.open_file(app.file_paths[order])
        root.update()
        moved = canvas.canvasy(0) - before
        ok = moved == 0
        bad += 0 if ok else 1
        say(f"  点第 {order:2d} 张（本来整张可见）：原点 {before:7.0f} -> "
            f"{canvas.canvasy(0):7.0f}  位移 {moved:+7.0f}"
            f"{'  ✓' if ok else '   ✗ 不该动'}")

    for order in (6, 12, 18, 23):
        canvas.yview_moveto(0.0)
        root.update()
        app.open_file(app.file_paths[order])
        root.update()
        box = canvas.bbox(f"cardbg{order}")
        top = canvas.canvasy(0)
        inside = box[1] >= top - 1 and box[3] <= top + viewport + 1
        bad += 0 if inside else 1
        say(f"  从顶部点第 {order:2d} 张：原点 {top:7.0f}，"
            f"卡片 {box[1]:7.0f}~{box[3]:7.0f}  整张可见={inside}"
            f"{'  ✓' if inside else '   ✗ 没挪进来'}")

    say("")
    say(f"总计：{bad} 处不对" if bad else "总计：全部通过 ✓")

    out = PROJECT / "outputs" / "卡片点击命中.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n报告已写入 {out}")

    root.destroy()
    storage.default_state_path = real
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
