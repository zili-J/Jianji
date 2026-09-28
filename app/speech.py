"""把文字合成语音、写成 WAV 文件。**只用标准库**（ctypes 直接调 COM）。

## 为什么是这个方案

导出音频要用 Windows 自带的语音引擎（SAPI）。可选的调用方式有三种，前两种都不行：

1. **`Add-Type -AssemblyName System.Speech`**（PowerShell + .NET）—— 依赖 PowerShell
   的可用性和执行策略，还要临时编译 .NET 代码；沙箱里直接被安全策略拦掉，**没法核验**。
2. **`New-Object -ComObject SAPI.SpVoice`**（PowerShell + COM）—— 同样依赖 PowerShell，
   还会额外起进程、闪控制台窗口。同样核验不了。
3. **ctypes 直接调 COM**（本模块）—— 不依赖任何外部程序、不起进程、不闪窗口，
   而且能用纯 Python 端到端核验（实测能写出 438KB 的 WAV）。

顺带的好处：这个应用本来就是「无第三方依赖、纯标准库」，本模块延续这一点。

## 为什么走 IDispatch 晚绑定

`ISpVoice` 的 vtable 是 `IUnknown(3) + ISpNotifySource(7) + ISpEventSource(3) + ISpVoice(N)`，
`SetOutput` 落在第 13 个槽位、`Speak` 在第 29 个 —— 这种偏移量写错一个就是一个
access violation，换台机器 / 换个 Windows 版本还可能对不上。
`IDispatch` 的 vtable 只有 `IUnknown(3) + GetTypeInfoCount/GetTypeInfo/GetIDsOfNames/Invoke`，
稳定且有文档，代价只是每次调用多一次名字解析。

## 踩过的四个坑（都记在这里，免得下次重踩）

- **`SysAllocString` 必须显式声明 `restype = c_void_p`**。ctypes 默认把返回值当 32 位
  `int`，BSTR 指针的高 32 位会被砍掉，接着就是
  `access violation reading 0x00000000463C3C98`。
- **`CoCreateInstance` 的 `argtypes` 必须在 `_GUID` 定义之后设**。用
  `POINTER("_GUID")` 这种字符串前向引用**不报错**，但传进去的指针是坏的，
  结果是 `_create()` 里一个 access violation。
- **`DISPPARAMS.rgvarg` 是逆序的**：`rgvarg[0]` 是**最后一个**实参。给反了不崩溃，
  只报 `DISP_E_TYPEMISMATCH (0x80020005)`，很容易误以为是参数类型写错了。
  本模块的 `_invoke()` 收的是**源码顺序**，翻转在它内部做。
- **`GetVoices` 是方法不是属性**（dispId 17）。按 `DISPATCH_PROPERTYGET` 调会报
  `DISP_E_MEMBERNOTFOUND (0x80020003)`；而且必须**收返回值**，否则拿不到音色集合。

## 音色

不提供音色选择：`SAPI.SpVoice` 用系统默认音色，本机是
`Microsoft Huihui Desktop - Chinese (Simplified)`（见 `default_voice_name()`），
中文文稿直接就是中文朗读。换音色要往 `Voice` putref 传一个 `ISpeechObjectToken`，
而那个 token 的 `GetDescription` 在 `IDispatch` 下取不到（`DISP_E_TYPEMISMATCH`），
投入产出不划算 —— 真要做，得改走 vtable 直调。
"""

from __future__ import annotations

import ctypes
import os
import wave
import winreg
from ctypes import POINTER, byref, c_void_p
from pathlib import Path

__all__ = ["SpeechError", "default_voice_name", "is_available", "synthesize",
           "wav_seconds"]


class SpeechError(RuntimeError):
    """合成语音失败。调用方只需要弹个框，不必关心 HRESULT。"""


# ---------------------------------------------------------------- COM 基础设施

_ole32 = ctypes.WinDLL("ole32")
_oleaut32 = ctypes.WinDLL("oleaut32")

# **必须显式声明**：见模块开头第 1 条坑
_oleaut32.SysAllocString.restype = c_void_p
_oleaut32.SysAllocString.argtypes = [ctypes.c_wchar_p]
_oleaut32.SysFreeString.restype = None
_oleaut32.SysFreeString.argtypes = [c_void_p]
_ole32.CoInitializeEx.restype = ctypes.c_long
_ole32.CoInitializeEx.argtypes = [c_void_p, ctypes.c_ulong]
_ole32.CoUninitialize.restype = None
# `CoCreateInstance` 的 argtypes 在 `_GUID` 定义之后再设（见第 2 条坑）

CLSCTX_ALL = 0x17
COINIT_APARTMENTTHREADED = 0x2
_RPC_E_CHANGED_MODE = 0x80010106 - 0x100000000     # 线程已在 MTA，不算失败

IID_NULL = "{00000000-0000-0000-0000-000000000000}"
IID_IDISPATCH = "{00020400-0000-0000-C000-000000000046}"

# 从 HKCR\SAPI.SpVoice\CLSID 读出来核对过；注册表读不到时退回这两个常量
_FALLBACK_CLSID = {
    "SAPI.SpVoice": "{96749377-3391-11D2-9EE3-00C04F797396}",
    "SAPI.SpFileStream": "{947812B3-2AE1-4644-BA86-9E90DED7EC91}",
}

DISPATCH_METHOD = 1
DISPATCH_PROPERTYGET = 2
DISPATCH_PROPERTYPUT = 4
DISPATCH_PROPERTYPUTREF = 8
DISPID_PROPERTYPUT = -3

VT_I4, VT_BSTR, VT_DISPATCH, VT_BOOL = 3, 8, 9, 11

SSFM_CREATE_FOR_WRITE = 3


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, text: str | None = None):
        super().__init__()
        if text:
            _ole32.CLSIDFromString(ctypes.c_wchar_p(text), byref(self))


class _VARIANT(ctypes.Structure):
    """x64 上 VARIANT 是 24 字节：8 字节头 + 16 字节 union。

    union 里只用到指针和 32 位整数，用两个 `c_void_p` 占满 16 字节即可。
    """
    _fields_ = [("vt", ctypes.c_ushort),
                ("wReserved1", ctypes.c_ushort),
                ("wReserved2", ctypes.c_ushort),
                ("wReserved3", ctypes.c_ushort),
                ("value", c_void_p),
                ("extra", c_void_p)]


class _DISPPARAMS(ctypes.Structure):
    _fields_ = [("rgvarg", POINTER(_VARIANT)),
                ("rgdispidNamedArgs", POINTER(ctypes.c_long)),
                ("cArgs", ctypes.c_uint),
                ("cNamedArgs", ctypes.c_uint)]


class _EXCEPINFO(ctypes.Structure):
    _fields_ = [("wCode", ctypes.c_ushort), ("wReserved", ctypes.c_ushort),
                ("bstrSource", c_void_p), ("bstrDescription", c_void_p),
                ("bstrHelpFile", c_void_p), ("dwHelpContext", ctypes.c_ulong),
                ("pvReserved", c_void_p), ("pfnDeferredFillIn", c_void_p),
                ("scode", ctypes.c_long)]


_ole32.CLSIDFromString.restype = ctypes.c_long
_ole32.CLSIDFromString.argtypes = [ctypes.c_wchar_p, POINTER(_GUID)]
# `_GUID` 有了，现在才能设 `CoCreateInstance` 的签名
_ole32.CoCreateInstance.restype = ctypes.c_long
_ole32.CoCreateInstance.argtypes = [POINTER(_GUID), c_void_p, ctypes.c_ulong,
                                    POINTER(_GUID), POINTER(c_void_p)]


def _variant_i4(value: int) -> _VARIANT:
    out = _VARIANT()
    out.vt = VT_I4
    out.value = c_void_p(value)
    return out


def _variant_bool(value: bool) -> _VARIANT:
    out = _VARIANT()
    out.vt = VT_BOOL
    out.value = c_void_p(-1 if value else 0)
    return out


def _variant_bstr(text: str) -> tuple[_VARIANT, int]:
    """返回 `(VARIANT, BSTR 句柄)`。句柄要留着，用完由调用方 `SysFreeString`。"""
    handle = _oleaut32.SysAllocString(text)
    out = _VARIANT()
    out.vt = VT_BSTR
    out.value = c_void_p(handle)
    return out, handle


def _variant_dispatch(pointer) -> _VARIANT:
    out = _VARIANT()
    out.vt = VT_DISPATCH
    # `pointer` 既可能是 `c_void_p` 也可能是裸 int，两种都要能接
    out.value = pointer if isinstance(pointer, c_void_p) else c_void_p(pointer)
    return out


def _slot(pointer, index: int, *argtypes, restype=ctypes.c_long):
    """取出 COM 对象 vtable 的第 index 个函数，返回可调用的 ctypes 包装。"""
    table = ctypes.cast(pointer, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return proto(table[index])


def _release(pointer) -> None:
    """`IUnknown::Release`（vtable 第 2 项）。"""
    if pointer:
        _slot(pointer, 2, restype=ctypes.c_ulong)(pointer)


def _clsid(prog_id: str) -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id + r"\CLSID") as key:
            return winreg.QueryValue(key, None)
    except OSError:
        return _FALLBACK_CLSID[prog_id]


def _create(prog_id: str):
    """`CoCreateInstance`，直接要 `IDispatch`。"""
    out = c_void_p()
    hr = _ole32.CoCreateInstance(byref(_GUID(_clsid(prog_id))), None, CLSCTX_ALL,
                                 byref(_GUID(IID_IDISPATCH)), byref(out))
    if hr != 0:
        raise SpeechError(f"无法创建 {prog_id}（0x{hr & 0xFFFFFFFF:08X}）"
                          f"，这台机器可能没有可用的语音引擎。")
    return out


def _member_id(disp, name: str) -> int:
    """`IDispatch::GetIDsOfNames`（vtable 第 5 项）。"""
    names = (ctypes.c_wchar_p * 1)(ctypes.c_wchar_p(name))
    result = ctypes.c_long()
    call = _slot(disp, 5, POINTER(_GUID), POINTER(ctypes.c_wchar_p), ctypes.c_uint,
                 ctypes.c_ulong, POINTER(ctypes.c_long))
    hr = call(disp, byref(_GUID(IID_NULL)),
              ctypes.cast(names, POINTER(ctypes.c_wchar_p)), 1, 0, byref(result))
    if hr != 0:
        raise SpeechError(f"语音引擎没有 {name!r} 这个成员"
                          f"（0x{hr & 0xFFFFFFFF:08X}）。")
    return result.value


def _invoke(disp, name: str, args: list[_VARIANT], *, kind: str = "method") -> _VARIANT:
    """`IDispatch::Invoke`（vtable 第 6 项）。返回结果 VARIANT（没返回值时 `vt` 为空）。

    `args` 按**源码顺序**给；翻转成 COM 要的逆序在本函数内部做（见第 3 条坑）。
    `kind` 取 `method` / `get` / `put` / `putref`。

    **返回值一律收**：`GetVoices` 是方法、返回一个 `ISpeechObjectTokens`，
    只按 `kind="get"` 收结果的话这里会拿到空值（见第 4 条坑）。
    """
    member = _member_id(disp, name)
    flags = {"method": DISPATCH_METHOD, "get": DISPATCH_PROPERTYGET,
             "put": DISPATCH_PROPERTYPUT, "putref": DISPATCH_PROPERTYPUTREF}[kind]

    if args:
        array = (_VARIANT * len(args))(*reversed(args))
        named = None
        named_count = 0
        if kind in ("put", "putref"):
            named = (ctypes.c_long * 1)(DISPID_PROPERTYPUT)
            named_count = 1
        params = _DISPPARAMS(array, named, len(args), named_count)
    else:
        array = None
        params = _DISPPARAMS(None, None, 0, 0)

    result = _VARIANT()
    info = _EXCEPINFO()
    error = ctypes.c_uint()
    call = _slot(disp, 6, ctypes.c_long, POINTER(_GUID), ctypes.c_ulong,
                 ctypes.c_ushort, POINTER(_DISPPARAMS), POINTER(_VARIANT),
                 POINTER(_EXCEPINFO), POINTER(ctypes.c_uint))
    hr = call(disp, member, byref(_GUID(IID_NULL)), 0, flags, byref(params),
              byref(result), byref(info), byref(error))
    # `array` 要活到调用结束之后：`params.rgvarg` 指着它
    del array
    if hr != 0:
        detail = ""
        if info.bstrDescription:
            detail = "：" + (ctypes.cast(info.bstrDescription, ctypes.c_wchar_p).value or "")
        raise SpeechError(f"调用 {name} 失败（0x{hr & 0xFFFFFFFF:08X}）{detail}")
    return result


class _Apartment:
    """`CoInitializeEx` / `CoUninitialize` 的成对封装。

    线程已经是 MTA 时 `CoInitializeEx` 返回 `RPC_E_CHANGED_MODE`，那不算失败
    （COM 已经可用），但**这时不能去 `CoUninitialize`** —— 初始化的不是我们。
    """

    def __init__(self) -> None:
        self._owned = False

    def __enter__(self) -> "_Apartment":
        hr = _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
        code = hr & 0xFFFFFFFF
        if code == 0:                       # S_OK
            self._owned = True
        elif code == 1:                     # S_FALSE：本线程已经初始化过
            self._owned = False
        elif hr == _RPC_E_CHANGED_MODE:
            self._owned = False
        else:
            raise SpeechError(f"COM 初始化失败（0x{code:08X}）。")
        return self

    def __exit__(self, *_exc) -> None:
        if self._owned:
            _ole32.CoUninitialize()
        return None


# ------------------------------------------------------------------ 对外接口

_VOICE_KEY = r"SOFTWARE\Microsoft\Speech\Voices"


def default_voice_name() -> str | None:
    """系统默认音色的描述串，例如 `Microsoft Huihui Desktop - Chinese (Simplified)`。

    只读注册表、不碰 COM —— 用来在状态栏告诉用户「用哪个声音念的」。
    """
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _VOICE_KEY) as key:
            token_id = winreg.QueryValueEx(key, "DefaultTokenId")[0]
    except OSError:
        return None
    prefix = "HKEY_LOCAL_MACHINE\\"
    if not token_id.startswith(prefix):
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            token_id[len(prefix):]) as token:
            return winreg.QueryValueEx(token, None)[0]
    except OSError:
        return None


def is_available() -> bool:
    """这台机器有没有可用的语音引擎。任何异常都当成「没有」。"""
    if os.name != "nt":                     # pragma: no cover - 只在 Windows 上跑
        return False
    try:
        with _Apartment():
            _release(_create("SAPI.SpVoice"))
            return True
    except Exception:                       # noqa: BLE001
        return False


def synthesize(text: str, path: Path, *, rate: int = 0, volume: int = 100,
               timeout_ms: int | None = None) -> int:
    """把 `text` 合成到 `path`（WAV），返回写出的字节数。

    `rate` 是语速（-10..10，0 为正常），`volume` 是音量（0..100）。
    音色用系统默认（本机是中文音色，见模块开头的「音色」一节）。

    **会阻塞**，直到整篇念完。合成比排版慢得多（语速按每秒 5 个汉字估），
    长文可能要好几分钟，调用方必须放到后台线程里跑，别卡住界面。
    """
    text = text.strip()
    if not text:
        raise SpeechError("没有可以朗读的文字。")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if timeout_ms is None:
        timeout_ms = max(60_000, int(len(text) / 5 * 1000 * 4))

    with _Apartment():
        stream = voice = None
        handles: list[int] = []
        try:
            stream = _create("SAPI.SpFileStream")
            name_variant, name_handle = _variant_bstr(str(target))
            handles.append(name_handle)
            _invoke(stream, "Open", [name_variant,
                                     _variant_i4(SSFM_CREATE_FOR_WRITE),
                                     _variant_bool(False)])

            voice = _create("SAPI.SpVoice")
            _invoke(voice, "AudioOutputStream", [_variant_dispatch(stream)], kind="putref")
            for name, value in (("Rate", rate), ("Volume", volume)):
                _invoke(voice, name, [_variant_i4(value)], kind="put")

            text_variant, text_handle = _variant_bstr(text)
            handles.append(text_handle)
            _invoke(voice, "Speak", [text_variant, _variant_i4(0)])
            _invoke(voice, "WaitUntilDone", [_variant_i4(timeout_ms)])
            _invoke(stream, "Close", [])
        finally:
            for handle in handles:
                _oleaut32.SysFreeString(c_void_p(handle))
            _release(voice)
            _release(stream)

    if not target.exists():
        raise SpeechError("语音引擎没有写出文件，可能没有可用的音色。")
    return target.stat().st_size


def wav_seconds(path: Path) -> float | None:
    """WAV 文件的时长（秒）。读不出来返回 None。

    **不要拿「字节数 ÷ 44100」估算**：那假设了 22.05kHz 16 位单声道，
    而输出格式由音色决定，换个音色就对不上了。读头部才靠得住。
    """
    try:
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            if not rate:
                return None
            return handle.getnframes() / rate
    except (OSError, wave.Error, EOFError):
        return None


if __name__ == "__main__":                   # pragma: no cover - 手工核验用
    import tempfile

    print("可用:", is_available())
    print("默认音色:", default_voice_name())
    out = Path(tempfile.gettempdir()) / "jianji_speech_demo.wav"
    size = synthesize("简记，把文字变成声音。这是第二句话，确认多句也连着念。", out)
    print(f"写出 {out}  {size} 字节")
