from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path


HRESULT = ctypes.c_long
LPVOID = ctypes.c_void_p


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def guid(value: str) -> GUID:
    import uuid

    raw = uuid.UUID(value).bytes_le
    result = GUID()
    ctypes.memmove(ctypes.byref(result), raw, 16)
    return result


def method(instance: LPVOID, index: int, restype, *argtypes):
    vtable = ctypes.cast(instance, ctypes.POINTER(ctypes.POINTER(LPVOID))).contents
    address = vtable[index]
    return ctypes.WINFUNCTYPE(restype, LPVOID, *argtypes)(address)


def check(result: int, action: str) -> None:
    if result < 0:
        raise OSError(f"{action} failed with HRESULT 0x{result & 0xFFFFFFFF:08X}")


def desktop_path() -> Path:
    shell32 = ctypes.windll.shell32
    buffer = ctypes.create_unicode_buffer(32768)
    check(shell32.SHGetFolderPathW(None, 0x0000, None, 0, buffer), "Find Desktop")
    return Path(buffer.value)


def create_shortcut(shortcut_path: Path, target: Path, arguments: str, working_directory: Path) -> None:
    ole32 = ctypes.windll.ole32
    check(ole32.CoInitialize(None), "Initialize COM")
    shell_link = LPVOID()
    clsid = guid("00021401-0000-0000-C000-000000000046")
    iid_shell_link = guid("000214F9-0000-0000-C000-000000000046")
    iid_persist_file = guid("0000010B-0000-0000-C000-000000000046")
    try:
        check(
            ole32.CoCreateInstance(
                ctypes.byref(clsid), None, 1, ctypes.byref(iid_shell_link), ctypes.byref(shell_link)
            ),
            "Create ShellLink",
        )
        check(method(shell_link, 20, HRESULT, wintypes.LPCWSTR)(shell_link, str(target)), "Set target")
        check(method(shell_link, 11, HRESULT, wintypes.LPCWSTR)(shell_link, arguments), "Set arguments")
        check(
            method(shell_link, 9, HRESULT, wintypes.LPCWSTR)(shell_link, str(working_directory)),
            "Set working directory",
        )
        check(method(shell_link, 7, HRESULT, wintypes.LPCWSTR)(shell_link, "启动简记 Markdown 日记"), "Set description")
        check(
            method(shell_link, 17, HRESULT, wintypes.LPCWSTR, ctypes.c_int)(shell_link, str(target), 0),
            "Set icon",
        )

        persist_file = LPVOID()
        check(
            method(shell_link, 0, HRESULT, ctypes.POINTER(GUID), ctypes.POINTER(LPVOID))(
                shell_link, ctypes.byref(iid_persist_file), ctypes.byref(persist_file)
            ),
            "Get IPersistFile",
        )
        try:
            check(
                method(persist_file, 6, HRESULT, wintypes.LPCWSTR, wintypes.BOOL)(
                    persist_file, str(shortcut_path), True
                ),
                "Save shortcut",
            )
        finally:
            method(persist_file, 2, wintypes.ULONG)(persist_file)
    finally:
        if shell_link:
            method(shell_link, 2, wintypes.ULONG)(shell_link)
        ole32.CoUninitialize()


def main() -> None:
    project = Path(__file__).resolve().parent.parent
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    shortcut = desktop_path() / "简记（当前版本）.lnk"
    create_shortcut(shortcut, pythonw, f'"{project / "app" / "main.py"}"', project)
    print(shortcut)


if __name__ == "__main__":
    if os.name != "nt":
        raise SystemExit("Windows only")
    main()
