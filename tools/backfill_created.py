"""按**文件名里的时间戳**回填文稿的创建时间。

## 为什么需要它

保存走的是「临时文件 + `os.replace`」，而 Windows 上那是**用源文件顶掉目标文件**，
目标文件连创建时间也变成保存那一刻。修好之后（见 `storage.set_creation_time`）不再
继续丢，但**已经丢掉的救不回来**——被替换掉的旧文件对象已经不存在了，MFT 里也没有。

唯一还留着原始创建时刻的地方，往往是**文件名**。

## 只认这两种命名，别的一律不碰

1. `新建-2026-09-13-161624.md` —— 简记自己的「新建文稿」命名
   （`storage.new_note_path()` 的 `%Y-%m-%d-%H%M%S`）。这是**创建那一刻**打上去的。
2. `2026-09-09 222022.md` —— `文档\日记` 那批老文稿的约定（日期 + 空格 + 时刻）。

**故意不认「只有日期」的戳**（`今日日记20260914.md`、`20260927广州草莓音乐节.md`）。
那些数字常常是**内容里提到的日期**而不是创建时间，猜错了比不猜更糟。

## 前缀那种戳**不能**用

有些文件名长这样：`20260925-112829 2026-09-09.md`。前缀看着像时间戳，但它是
**备份/导入那一刻**打的，不是创建时间。判据很硬：这个文件自己的时间戳是
`2026-09-10`，前缀却是 `2026-09-25`——创建时间不可能晚于修改时间 15 天。
拿它回填会把创建时间**改得更错**。

## 四道闸（全过才动）

- 戳必须**早于**文件当前的创建时间，且至少早 `MIN_GAIN_SECONDS`——
  不然没有改进，白动一次文件（也保证重跑是幂等的）；
- 戳必须**不晚于**修改时间（创建不可能在最后修改之后）；
- 戳不能在**未来**；
- 只处理 `.md`，只处理给定文件夹底下的文件。

## 只认「界面上看得见的文稿」

清单走 `storage.list_markdown(..., recursive=True)`，**不走裸 `rglob`**。
差别只有一个但很要命：前者会跳过隐藏目录，也就是 `.简记回收站`。
回收站里的文件界面上不列、用户也点不到，去改它们的创建时间纯属越界
（踩过一次：`wb01` 预演报的「1 篇」其实是回收站里的文件）。

`--dry-run`（默认）只列不改；`--apply` 才真的写，并先把旧值存成一份 JSON，
方便整体还原。

    python tools/backfill_created.py <文件夹>             # 只看
    python tools/backfill_created.py <文件夹> --apply      # 真改
    python tools/backfill_created.py <文件夹> --restore <备份.json>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import storage  # noqa: E402

#: 至少早这么多秒才算「有改进」。防止为了几秒的噪声去动文件，也保证重跑幂等。
MIN_GAIN_SECONDS = 60

#: 认的两种命名。**顺序有意义**：先试更具体的。
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("新建-",
     re.compile(r"新建-(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})")),
    ("日期 时刻",
     re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})[ _](\d{2})(\d{2})(\d{2})(?!\d)")),
)


def stamp_from_name(name: str) -> tuple[float, str] | None:
    """从文件名里解出创建时刻；解不出或解出来不合法就返回 None。"""
    for label, pattern in PATTERNS:
        match = pattern.search(name)
        if not match:
            continue
        year, month, day, hour, minute, second = (int(g) for g in match.groups())
        try:
            moment = datetime(year, month, day, hour, minute, second)
        except ValueError:
            return None
        return moment.timestamp(), label
    return None


def plan(folder: Path, *, now: float | None = None) -> list[dict]:
    """算出「哪些文件的创建时间会被改成什么」。不改任何东西。"""
    now = time.time() if now is None else now
    rows: list[dict] = []
    # 用 storage 的清单而不是裸 rglob：它会跳过 `.简记回收站` 这类隐藏目录，
    # 与界面上列出来的文稿完全一致。
    for path in sorted(storage.list_markdown(folder, recursive=True)):
        parsed = stamp_from_name(path.stem)
        if parsed is None:
            continue
        stamp, label = parsed
        try:
            info = path.stat()
        except OSError:
            continue
        current = storage.creation_time(path)
        if current is None:
            continue

        row = {
            "path": path,
            "label": label,
            "stamp": stamp,
            "current": current,
            "modified": info.st_mtime,
            "skip": None,
        }
        if stamp > now:
            row["skip"] = "戳在未来"
        elif stamp > info.st_mtime + 2:
            # 创建不可能晚于最后修改。撞上这条说明那个戳不是创建时间
            # （备份前缀就长这样），宁可不动。
            row["skip"] = "晚于修改时间"
        elif current - stamp < MIN_GAIN_SECONDS:
            row["skip"] = "没有改进"
        rows.append(row)
    return rows


def _moment(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def report(rows: list[dict], folder: Path) -> tuple[list[dict], list[dict]]:
    todo = [row for row in rows if row["skip"] is None]
    skipped = [row for row in rows if row["skip"] is not None]

    print(f"文件夹：{folder}")
    print(f"名字里带时间戳的文稿：{len(rows)} 篇"
          f"（其中 {len(todo)} 篇该改、{len(skipped)} 篇不动）")
    print()
    if todo:
        print("%-40s %-20s %-20s %s" % ("文件", "现在(错)", "改成", "来源"))
        print("-" * 104)
        for row in todo:
            print("%-40s %-20s %-20s %s" % (
                row["path"].name[:40], _moment(row["current"]),
                _moment(row["stamp"]), row["label"]))
    else:
        print("没有需要改的。")
    if skipped:
        print()
        print("不动的（附原因）：")
        for row in skipped:
            print("  %-40s %s" % (row["path"].name[:40], row["skip"]))
    return todo, skipped


def apply(rows: list[dict], backup_path: Path) -> int:
    """真的写。先落一份旧值备份，方便整体还原。"""
    backup = {str(row["path"]): row["current"] for row in rows}
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"旧创建时间已备份到：{backup_path}")

    changed = failed = 0
    for row in rows:
        if storage.set_creation_time(row["path"], row["stamp"]):
            changed += 1
        else:
            failed += 1
            print(f"  失败：{row['path'].name}")
    print(f"已改 {changed} 篇" + (f"，失败 {failed} 篇" if failed else ""))
    return 0 if failed == 0 else 1


def restore(backup_path: Path) -> int:
    """把创建时间还原成备份里的值。"""
    backup = json.loads(backup_path.read_text(encoding="utf-8"))
    changed = missing = 0
    for raw, timestamp in backup.items():
        path = Path(raw)
        if not path.exists():
            missing += 1
            continue
        if storage.set_creation_time(path, timestamp):
            changed += 1
    print(f"已还原 {changed} 篇" + (f"，{missing} 篇已不存在" if missing else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="按文件名里的时间戳回填创建时间")
    ap.add_argument("folder", nargs="?", help="要处理的文件夹")
    ap.add_argument("--apply", action="store_true", help="真的写（默认只看）")
    ap.add_argument("--restore", metavar="备份.json", help="从备份还原")
    ap.add_argument("--backup", metavar="路径", help="备份写到哪儿")
    args = ap.parse_args(argv)

    if args.restore:
        return restore(Path(args.restore))
    if not args.folder:
        ap.error("要么给一个文件夹，要么用 --restore")
    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"不是文件夹：{folder}")
        return 1

    rows = plan(folder)
    todo, _ = report(rows, folder)
    if not args.apply:
        if todo:
            print()
            print("以上只是预演。确认无误后加 --apply 才会真的写。")
        return 0
    if not todo:
        return 0
    print()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = Path(args.backup) if args.backup else \
        PROJECT / "outputs" / f"created-backfill-{stamp}.json"
    return apply(todo, backup)


if __name__ == "__main__":
    raise SystemExit(main())
