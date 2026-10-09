from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import platform_support


class ExternalChangeError(RuntimeError):
    """Raised when a file changed on disk after it was loaded."""


#: `os.replace` 在这几个 Windows 错误码上值得重试。它们都不是「文件坏了」或
#: 「没有权限」，而是**别人正好拿着**：杀毒软件刚扫到、索引器在读、云盘客户端
#: 还没放开。Windows 自己给 `ReplaceFile`/`MoveFileEx` 的建议也是重试。
_TRANSIENT_REPLACE_WINERRORS = frozenset({
    5,    # ERROR_ACCESS_DENIED    —— 拒绝访问
    32,   # ERROR_SHARING_VIOLATION —— 共享冲突
    33,   # ERROR_LOCK_VIOLATION    —— 锁冲突
})
_REPLACE_ATTEMPTS = 6
_REPLACE_DELAY = 0.08          # 秒；每次翻倍式加长，总共最多等约 1.2 秒


def replace_with_retry(source: Path, target: Path) -> None:
    """`os.replace` 的一层薄重试，专治 Windows 上的**瞬时**占用。

    为什么需要它：用户的日记文件夹在 `文档` 下面，而 `文档` 常常被 OneDrive 之类的
    云盘同步；杀毒软件也会在文件刚写完的那一刻扫一遍。那一瞬间 `os.replace` 抛
    `PermissionError: [WinError 5] 拒绝访问`——**和「真的没有写权限」报的是同一个
    异常**，可实际上零点几秒之后就没事了。不重试的话，用户会看到一个「保存失败」
    的对话框，而他什么都没做错。

    这不是纸上推演：一次全量测试里 `save_state()` 就在 `%TEMP%` 下抛了 WinError 5，
    单独重跑同一个模块 10 项全绿——典型的瞬时占用。

    **只重试上面那三个错误码**。别的错误（磁盘满、路径不存在、真的是只读文件）
    立刻原样抛出去，免得把真问题拖成六次无谓的等待。
    """
    last: OSError | None = None
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except OSError as error:
            if getattr(error, "winerror", None) not in _TRANSIENT_REPLACE_WINERRORS:
                raise
            last = error
            if attempt + 1 < _REPLACE_ATTEMPTS:
                time.sleep(_REPLACE_DELAY * (attempt + 1))
    assert last is not None                     # 循环里必然赋过值
    raise last


# --------------------------------------------------------------- 保留创建时间
#
# 保存走的是「写临时文件 + `os.replace`」。而 `os.replace` 是**用源文件顶掉目标
# 文件**，于是目标文件整个被换成了临时文件 —— 连创建时间也变成临时文件的创建时间，
# 也就是「这一次保存的时刻」。
#
# 后果不是理论推演，是实测：用户文件夹里 12 篇文稿，`st_ctime` **全部等于** `st_mtime`。
# 这会让两件事同时失效：
#
#   · 「创建日期」永远显示成修改日期，等于没这个信息；
#   · 「按创建时间排序」与「按修改时间排序」结果完全一样。
#
# 所以每次替换之后，把替换前的创建时间写回去。
#
# 「怎么读写创建时间」是**平台差异**（Windows 走 `SetFileTime`，macOS 走
# `setattrlist`，见 `platform_support`），这里只留两个薄壳 —— 因为 `main.py`
# 和一批测试是按 `storage.creation_time` 这个名字用的，改名会牵一发动全身。
#
# 注意：**这只能救回「从今往后」的创建时间**。已经丢掉的没法从文件系统上找回，
# 因为替换掉的旧文件对象已经不存在了。


def creation_time(path: Path) -> float | None:
    """文件的**创建**时间；拿不到返回 None（Linux 上就是拿不到）。"""
    return platform_support.creation_time(path)


def set_creation_time(path: Path, timestamp: float) -> bool:
    """把创建时间写回文件。**best-effort**：失败返回 False，绝不打断保存。

    创建时间是给人看的信息，为它让保存失败不划算。
    """
    return platform_support.set_creation_time(path, timestamp)


@dataclass(frozen=True)
class FileSignature:
    modified_ns: int
    size: int
    digest: str


def signature(path: Path) -> FileSignature | None:
    if not path.exists():
        return None
    data = path.read_bytes()
    stat = path.stat()
    return FileSignature(stat.st_mtime_ns, stat.st_size, hashlib.sha256(data).hexdigest())


def read_markdown(path: Path) -> tuple[str, FileSignature]:
    data = path.read_bytes()
    text = data.decode("utf-8-sig")
    current = signature(path)
    if current is None:
        raise FileNotFoundError(path)
    return text, current


def atomic_write_markdown(
    path: Path,
    text: str,
    expected: FileSignature | None,
    *,
    allow_overwrite: bool = False,
) -> FileSignature:
    path.parent.mkdir(parents=True, exist_ok=True)
    current = signature(path)
    if not allow_overwrite and current != expected:
        raise ExternalChangeError(f"File changed outside JianJi: {path}")

    payload = text.encode("utf-8")
    # 替换会把创建时间冲掉，先把它记下来（见上面「保留创建时间」一节）。
    previous_created = creation_time(path)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        replace_with_retry(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()

    if previous_created is not None:
        set_creation_time(path, previous_created)

    saved = signature(path)
    if saved is None:
        raise OSError(f"Save failed: {path}")
    return saved


def new_note_path(folder: Path, now: datetime | None = None) -> Path:
    moment = now or datetime.now()
    stamp = moment.strftime("%Y-%m-%d-%H%M%S")
    return folder / f"新建-{stamp}.md"


def conflict_copy_path(path: Path, now: datetime | None = None) -> Path:
    moment = now or datetime.now()
    stamp = moment.strftime("%Y%m%d-%H%M%S")
    return path.with_name(f"{path.stem}.简记冲突-{stamp}{path.suffix}")


def _is_hidden_relative(relative: Path) -> bool:
    return any(part.startswith(".") for part in relative.parts)


def _sort_key_of(item: Path, sort_key: str) -> float:
    """取排序用的时间戳；文件刚被移走/删掉时退到 0，别让列表整体崩掉。"""
    if sort_key == "created":
        # **必须走 creation_time()**，不能直接读 `st_ctime`：Windows 上它是创建时间，
        # 但 macOS/Linux 上它是**元数据变更时间**（改权限、改名都会动它），
        # 拿它当「创建时间」排出来的顺序是错的，而且错得不明显。
        return creation_time(item) or 0.0
    try:
        return item.stat().st_mtime
    except OSError:
        return 0.0


def list_markdown(folder: Path, recursive: bool = False,
                  sort_key: str = "name") -> list[Path]:
    """列出文件夹里的 Markdown，按 sort_key 排序，**新的排在前面**。

    sort_key：`name`（默认，按文件名，文件名里带日期时效果就是新的在前）、
    `created`（按创建时间）、`modified`（按修改时间）。时间倒序——写作软件里
    最常想找的是「刚写的那篇」。
    """
    if not folder.exists():
        return []
    if recursive:
        items = [
            item for item in folder.rglob("*")
            if item.is_file()
            and item.suffix.lower() == ".md"
            and not _is_hidden_relative(item.relative_to(folder))
        ]
    else:
        items = [
            item for item in folder.iterdir()
            if item.is_file() and item.suffix.lower() == ".md"
        ]
    if sort_key in ("created", "modified"):
        return sorted(items, key=lambda item: _sort_key_of(item, sort_key), reverse=True)
    return sorted(items, key=lambda item: item.name.lower(), reverse=True)


def list_subfolders(root: Path, recursive: bool = False) -> list[Path]:
    """列出 root 下的文件夹。

    **默认只看一层**（只要直接子目录）。界面上的文件夹树是**多层**的，走的是
    `recursive=True`；默认这个「一层」留给「只要一层」的调用方，别改默认值。

    两种模式都跳过隐藏目录（`.简记回收站` 之类）。
    """
    if not root.is_dir():
        return []
    if recursive:
        found = [
            item for item in root.rglob("*")
            if item.is_dir() and not _is_hidden_relative(item.relative_to(root))
        ]
    else:
        found = [
            item for item in root.iterdir()
            if item.is_dir() and not _is_hidden_relative(item.relative_to(root))
        ]
    return sorted(found, key=lambda item: str(item).lower())


def create_folder(parent: Path, name: str) -> Path:
    target = parent / name
    target.mkdir(parents=True, exist_ok=False)
    return target


def unique_path(directory: Path, name: str) -> Path:
    """在 directory 下找一个不冲突的文件名：a.md → a-2.md → a-3.md…"""
    target = directory / name
    if not target.exists():
        return target
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 2
    while target.exists():
        target = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    return target


def _ensure_inside(path: Path, root: Path, message: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as error:
        raise OSError(message) from error


def move_to_folder(path: Path, target_dir: Path, root: Path) -> Path:
    """把文档移到 target_dir 下，返回移动后的路径。

    目标里已有同名文件时自动加 -2、-3…，**绝不覆盖**。源文档与目标文件夹都必须
    在 root 内：界面只列 root 下的子文件夹，这里再拦一道，避免误移出去。
    目标就是当前文件夹时原样返回（调用方据此判断「没动」）。
    """
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"目标不存在：{source}")
    target_dir = Path(target_dir)
    _ensure_inside(source, root, "只能移动当前日记文件夹内的文档。")
    _ensure_inside(target_dir, root, "只能移动到当前日记文件夹内的文件夹。")
    if not target_dir.is_dir():
        raise OSError(f"目标文件夹不存在：{target_dir}")
    if source.parent == target_dir:
        return source

    target = unique_path(target_dir, source.name)
    # 同一个文件夹内改名，os.replace 是原子的；失败就让它抛出去，不做半吊子处理
    # （重试只针对「别人正好拿着」那三个瞬时错误，见 replace_with_retry）
    replace_with_retry(source, target)
    return target


def count_files(folder: Path) -> int:
    if not folder.is_dir():
        return 0
    return sum(1 for item in folder.rglob("*") if item.is_file())


def delete_to_recycle_bin(path: Path) -> None:
    """把文件或文件夹移到**系统**回收站；不做永久删除，失败即报错。

    Windows 走 `SHFileOperationW`、macOS 走「移进 `~/.Trash`，必要时交给访达」——
    两边都在 `platform_support.move_to_system_trash` 里。
    """
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"目标不存在：{target}")
    platform_support.move_to_system_trash(target)


TRASH_DIR_NAME = ".简记回收站"
TRASH_INDEX_NAME = "index.json"


@dataclass(frozen=True)
class TrashEntry:
    trash_name: str
    original_relative: str
    deleted_at: str


def trash_dir(root: Path) -> Path:
    return root / TRASH_DIR_NAME


def _load_trash_index(root: Path) -> dict[str, dict[str, str]]:
    try:
        raw = json.loads((trash_dir(root) / TRASH_INDEX_NAME).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_trash_index(root: Path, index: dict[str, dict[str, str]]) -> None:
    folder = trash_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / TRASH_INDEX_NAME).write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def move_to_trash(path: Path, root: Path, now: datetime | None = None) -> TrashEntry:
    """把文档移入应用内回收站，保留原相对路径以便还原。"""
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"目标不存在：{source}")
    try:
        relative = source.relative_to(root)
    except ValueError as error:
        raise OSError("只能回收当前日记文件夹内的文档。") from error

    folder = trash_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    trash_name = f"{stamp} {relative.name}"
    target = folder / trash_name
    counter = 1
    while target.exists():
        trash_name = f"{stamp}-{counter} {relative.name}"
        target = folder / trash_name
        counter += 1

    replace_with_retry(source, target)
    index = _load_trash_index(root)
    index[trash_name] = {"original": relative.as_posix(), "deleted_at": stamp}
    _save_trash_index(root, index)
    return TrashEntry(trash_name, relative.as_posix(), stamp)


def list_trash(root: Path) -> list[TrashEntry]:
    index = _load_trash_index(root)
    entries = [
        TrashEntry(name, str(meta.get("original", "")), str(meta.get("deleted_at", "")))
        for name, meta in index.items()
        if (trash_dir(root) / name).exists()
    ]
    return sorted(entries, key=lambda entry: entry.deleted_at, reverse=True)


def trash_entry_path(root: Path, trash_name: str) -> Path:
    return trash_dir(root) / trash_name


def restore_from_trash(root: Path, trash_name: str) -> Path:
    source = trash_entry_path(root, trash_name)
    if not source.exists():
        raise FileNotFoundError(f"回收站中没有该文档：{trash_name}")
    index = _load_trash_index(root)
    meta = index.get(trash_name, {})
    original = str(meta.get("original") or trash_name)
    target = root / Path(original)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target = target.with_name(f"{target.stem}-还原{target.suffix}")
        counter = 1
        while target.exists():
            target = target.with_name(f"{target.stem}-还原{counter}{target.suffix}")
            counter += 1
    replace_with_retry(source, target)
    index.pop(trash_name, None)
    _save_trash_index(root, index)
    return target


def purge_trash_entry(root: Path, trash_name: str) -> None:
    """彻底删除：交给系统回收站，仍然可恢复。"""
    source = trash_entry_path(root, trash_name)
    if not source.exists():
        raise FileNotFoundError(f"回收站中没有该文档：{trash_name}")
    delete_to_recycle_bin(source)
    index = _load_trash_index(root)
    index.pop(trash_name, None)
    _save_trash_index(root, index)


def reveal_in_explorer(path: Path) -> None:
    """在系统文件管理器里选中它。

    名字里的 `explorer` 是历史包袱（当年只有 Windows），没改名是因为
    `main.py` 和两个测试模块都按这个名字引用它；macOS 上它走的是访达。
    """
    platform_support.reveal_in_file_manager(path)


def default_journal_folder(home: Path | None = None) -> Path:
    user_home = home or Path.home()
    return user_home / "Documents" / "简记日记"


def default_state_path() -> Path:
    """设置文件的位置。Windows 是 `%LOCALAPPDATA%\\JianJi`，
    macOS 是 `~/Library/Application Support/JianJi`（见 `platform_support`）。"""
    return platform_support.default_state_path()


def load_state(path: Path | None = None) -> dict[str, Any]:
    target = path or default_state_path()
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_state(value: dict[str, Any], path: Path | None = None) -> None:
    target = path or default_state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2)
    temp = target.with_suffix(".tmp")
    temp.write_text(payload, encoding="utf-8")
    replace_with_retry(temp, target)


def get_default_folder(path: Path | None = None) -> Path | None:
    raw = load_state(path).get("default_folder")
    if isinstance(raw, str):
        candidate = Path(raw)
        if candidate.is_dir():
            return candidate
    return None


def set_default_folder(folder: Path, path: Path | None = None) -> None:
    state = load_state(path)
    state["default_folder"] = str(folder)
    save_state(state, path)


DEFAULT_FONT_FAMILY = "Microsoft YaHei UI"
FONT_FAMILY_MAX_LEN = 64

# 文稿列表的排序方式。时间类一律「新的在前」——写作软件里最常想找刚写的那篇。
SORT_KEYS = ("name", "created", "modified")

# 界面主题。键名要和 main.THEME_LABELS 一致。
THEME_KEYS = ("light", "dark")

DEFAULT_SETTINGS: dict[str, object] = {
    "line_width": 50,           # 内容行宽，百分比 30-80
    "font_size": 15,            # 字号，像素 12-24
    "line_height": 22,          # 行高，像素 12-40
    "font_family": DEFAULT_FONT_FAMILY,  # 写作区字体
    "sort_key": "name",         # 文稿排序：name / created / modified
    "theme": "light",           # 界面主题：light / dark
}

SETTING_RANGES: dict[str, tuple[int, int]] = {
    "line_width": (30, 80),
    "font_size": (12, 24),
    "line_height": (12, 40),
}


def _clean_font_family(raw: object) -> str | None:
    """规范化字体名；空值或非字符串返回 None 表示“未设置”。"""
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    return cleaned[:FONT_FAMILY_MAX_LEN]


def get_settings(path: Path | None = None) -> dict[str, object]:
    stored = load_state(path).get("settings")
    values = dict(DEFAULT_SETTINGS)
    if isinstance(stored, dict):
        for key, (low, high) in SETTING_RANGES.items():
            raw = stored.get(key)
            if isinstance(raw, (int, float)):
                values[key] = max(low, min(high, int(raw)))
        family = _clean_font_family(stored.get("font_family"))
        if family is not None:
            values["font_family"] = family
        sort_key = stored.get("sort_key")
        if sort_key in SORT_KEYS:
            values["sort_key"] = sort_key
        theme = stored.get("theme")
        if theme in THEME_KEYS:
            values["theme"] = theme
    return values


def set_settings(values: dict[str, object], path: Path | None = None) -> None:
    state = load_state(path)
    current = get_settings(path)
    for key, (low, high) in SETTING_RANGES.items():
        raw = values.get(key)
        if isinstance(raw, (int, float)):
            current[key] = max(low, min(high, int(raw)))
    family = _clean_font_family(values.get("font_family"))
    if family is not None:
        current["font_family"] = family
    sort_key = values.get("sort_key")
    if sort_key in SORT_KEYS:
        current["sort_key"] = sort_key
    theme = values.get("theme")
    if theme in THEME_KEYS:
        current["theme"] = theme
    state["settings"] = current
    save_state(state, path)
