"""核验「按文件名回填创建时间」这件事干得对不对。

只读，不改任何东西。三件事：

1. **正文一个字都没动** —— 跟动手前那份 SHA-256 快照逐个比对；
2. **该改的改了** —— 列出回填备份里每篇文件的创建时间；
3. **重跑是幂等的** —— 再预演一遍，应当没有任何要改的。

    python tools/_check_backfill.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))
sys.path.insert(0, str(PROJECT / "tools"))

import backfill_created as bf  # noqa: E402
import storage  # noqa: E402

SNAPSHOT = PROJECT / "outputs" / "_backfill-before.json"
BACKUPS = (
    PROJECT / "outputs" / "created-backfill-wb01.json",
    PROJECT / "outputs" / "created-backfill-riji.json",
)


def moment(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def check_contents() -> bool:
    """正文有没有被动过。"""
    print("=== 1. 正文完整性 ===")
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    changed: list[str] = []
    missing: list[str] = []
    for raw, digest in snapshot.items():
        path = Path(raw)
        if not path.exists():
            missing.append(raw)
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != digest:
            changed.append(raw)
    print(f"快照 {len(snapshot)} 篇")
    if missing:
        print(f"  ✗ 少了 {len(missing)} 篇：")
        for raw in missing:
            print(f"      {raw}")
    if changed:
        print(f"  ✗ 正文变了 {len(changed)} 篇：")
        for raw in changed:
            print(f"      {raw}")
    if not missing and not changed:
        print("  ✓ 全部逐字节一致，正文一个字都没动")
    return not missing and not changed


def check_times() -> bool:
    """回填过的文件现在是什么时间。"""
    print()
    print("=== 2. 回填结果 ===")
    ok = True
    for backup_path in BACKUPS:
        if not backup_path.exists():
            print(f"  （没有 {backup_path.name}，跳过）")
            continue
        saved = json.loads(backup_path.read_text(encoding="utf-8"))
        print(f"  {backup_path.name}（{len(saved)} 篇）")
        for raw, old in saved.items():
            path = Path(raw)
            if not path.exists():
                print(f"    - {path.name[:44]}  已不存在")
                continue
            now = storage.creation_time(path)
            # 备份里记的是**改之前**的值；要确认现在跟它不同（= 真改了）。
            # 唯一例外：后来整体还原过的那一篇。
            same = now is not None and abs(now - old) < 1.0
            flag = "（与改前相同 = 已还原）" if same else ""
            print(f"    创建 {moment(now)}  修改 {moment(path.stat().st_mtime)}"
                  f"  {path.name[:44]} {flag}")
    return ok


def check_idempotent() -> bool:
    """再预演一遍。"""
    print()
    print("=== 3. 重跑幂等 ===")
    ok = True
    for folder in (Path(r"C:\Users\22910\Documents\wb01"),
                   Path(r"C:\Users\22910\Documents\日记")):
        if not folder.is_dir():
            continue
        rows = bf.plan(folder)
        todo = [r for r in rows if r["skip"] is None]
        mark = "✓" if not todo else "✗"
        print(f"  {mark} {folder}：名字带戳 {len(rows)} 篇，该改 {len(todo)} 篇")
        for row in todo:
            print(f"      {row['path'].name} → {moment(row['stamp'])}")
        ok = ok and not todo
    return ok


def main() -> int:
    results = [check_contents(), check_times(), check_idempotent()]
    print()
    print("总结：", "全部通过" if all(results) else "有需要看的项（见上）")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
