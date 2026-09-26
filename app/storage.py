from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


class ExternalChangeError(RuntimeError):
    """Raised when a file changed on disk after it was loaded."""


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
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()

    saved = signature(path)
    if saved is None:
        raise OSError(f"Save failed: {path}")
    return saved


def default_journal_folder() -> Path:
    docs = Path.home() / "Documents"
    if docs.is_dir():
        return docs / "简记日记"
    return Path.home() / "简记日记"


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
    try:
        info = item.stat()
    except OSError:
        return 0.0
    # Windows 上 st_ctime 就是**创建时间**（POSIX 上它是元数据变更时间，
    # 但这是 Windows 桌面应用，取创建时间正是想要的）。
    return info.st_ctime if sort_key == "created" else info.st_mtime


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

    **默认只看一层**：界面上文件夹与「全部文稿」并列，只有一级，不再往下嵌套
    （用户要求「当前文件夹层级提供一级」「不可在文件夹下新建文件夹」）。
    要递归列举（老测试、统计用途）显式传 `recursive=True`。
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
    os.replace(source, target)
    return target


def count_files(folder: Path) -> int:
    if not folder.is_dir():
        return 0
    return sum(1 for item in folder.rglob("*") if item.is_file())


def delete_to_recycle_bin(path: Path) -> None:
    """把文件或文件夹移到回收站；不做永久删除，失败即报错。"""
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"目标不存在：{target}")
    if os.name != "nt":
        raise OSError("当前系统不支持移到回收站，已取消删除。")

    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x40
    FOF_NOCONFIRMATION = 0x10
    FOF_SILENT = 0x4
    FOF_NOERRORUI = 0x400

    operation = SHFILEOPSTRUCTW()
    operation.hwnd = None
    operation.wFunc = FO_DELETE
    operation.pFrom = str(target) + "\x00\x00"
    operation.pTo = None
    operation.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if code != 0:
        raise OSError(f"移到回收站失败（错误码 {code}）")
    if operation.fAnyOperationsAborted:
        raise OSError("删除操作被中止。")


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

    os.replace(source, target)
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
    os.replace(source, target)
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
    target = Path(path)
    if os.name != "nt":
        raise OSError("当前系统不支持打开资源管理器。")
    if not target.exists():
        raise FileNotFoundError(f"目标不存在：{target}")
    if target.is_dir():
        os.startfile(str(target))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["explorer", f"/select,{target}"])


def default_journal_folder(home: Path | None = None) -> Path:
    user_home = home or Path.home()
    return user_home / "Documents" / "简记日记"


def default_state_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "JianJi" / "state.json"


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
    os.replace(temp, target)


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
