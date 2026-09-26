"""拆解「打字 → 整篇重解析」的耗时构成，找出该改哪一段。

`_apply_markdown_styles()` 是打字热路径上最贵的一步。它里面混着三类工作：
读文本、Python 解析、以及大量 Tcl 调用。光看总耗时没法判断瓶颈在哪，
所以这里做两件事：

1. 给编辑器的 `tk` 换成计数代理，按 Tcl 子命令统计调用次数与总耗时
   （`_tkinter.tkapp.call` 本身只读打不了补丁，但控件的 `tk` 是普通属性）。
2. 给几个可替换的解析函数套上计时器，算各自的累计耗时。

用法：python tools/_bench_reparse.py [段落数]
"""
from __future__ import annotations

import statistics
import sys
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))
sys.path.insert(0, str(PROJECT / "tools"))

import storage  # noqa: E402
import tkinter as tk  # noqa: E402
from _bench_perf import build_document  # noqa: E402


class TclProxy:
    """把对 `tk.call` 的调用记下来再转发。

    只替换某一个控件的 `tk` 属性，所以统计到的就是走这个控件发出的调用。
    """

    def __init__(self, real) -> None:
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "calls", {})
        object.__setattr__(self, "total", 0)
        object.__setattr__(self, "seconds", {})

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_real"), name)

    def call(self, *args):
        real = object.__getattribute__(self, "_real")
        calls = object.__getattribute__(self, "calls")
        seconds = object.__getattribute__(self, "seconds")
        key = "?"
        if args:
            first = args[0]
            if isinstance(first, (tuple, list)) and len(first) > 1:
                key = str(first[1])          # 子命令，如 tag / insert / index
            elif isinstance(first, str):
                key = first
        start = time.perf_counter()
        try:
            return real.call(*args)
        finally:
            elapsed = time.perf_counter() - start
            calls[key] = calls.get(key, 0) + 1
            seconds[key] = seconds.get(key, 0.0) + elapsed
            object.__setattr__(self, "total", object.__getattribute__(self, "total") + 1)

    def reset(self) -> None:
        object.__setattr__(self, "calls", {})
        object.__setattr__(self, "seconds", {})
        object.__setattr__(self, "total", 0)


def timed(module, name: str, bucket: dict):
    """把模块里的某个函数换成计时包装，返回 (module, name) 供之后还原。"""
    original = getattr(module, name)

    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            bucket[name] = bucket.get(name, 0.0) + (time.perf_counter() - start) * 1000

    wrapper.__wrapped__ = original
    setattr(module, name, wrapper)
    return module, name


def main() -> None:
    repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 40

    tmp = Path(tempfile.mkdtemp())
    folder = tmp / "简记日记"
    folder.mkdir()
    text = build_document(repeats)
    doc = folder / "长文档.md"
    doc.write_text(text, encoding="utf-8")
    real_state = storage.default_state_path
    storage.default_state_path = lambda: tmp / "state.json"
    storage.set_default_folder(folder)

    import main as main_module
    from main import JianJiApp

    root = tk.Tk()
    root.geometry("1180x760")
    app = JianJiApp(root)
    app.open_file(doc)
    root.update()

    lines = text.count("\n") + 1
    print(f"文档规模：{lines} 行 / {len(text)} 字符\n")

    # ---- 1. Tcl 调用统计 ----
    proxy = TclProxy(app.editor.tk)
    app.editor.tk = proxy
    samples = []
    for _ in range(5):
        proxy.reset()
        start = time.perf_counter()
        app._apply_markdown_styles()
        samples.append((time.perf_counter() - start) * 1000)
        snapshot = dict(proxy.calls)
        tcl_seconds = dict(proxy.seconds)
    total_tcl = sum(tcl_seconds.values())
    print(f"整篇重解析中位数 {statistics.median(samples):.1f} ms")
    print(f"其中编辑器发出的 Tcl 调用 {proxy.total} 次，合计 {total_tcl * 1000:.1f} ms")
    print(f"  → Tcl 占 {total_tcl * 1000 / statistics.median(samples) * 100:.0f}%，"
          f"Python 侧占 {100 - total_tcl * 1000 / statistics.median(samples) * 100:.0f}%\n")
    print("  调用最多的 Tcl 子命令：")
    for key, count in sorted(snapshot.items(), key=lambda kv: -kv[1])[:8]:
        print(f"    {key:<12} {count:>7} 次   {tcl_seconds[key] * 1000:8.1f} ms")

    # ---- 2. 分段计时 ----
    bucket: dict[str, float] = {}
    originals = []
    for name in ("fence_step", "parse_block", "collect_tables",
                 "build_table_rows"):
        originals.append(timed(main_module, name, bucket))
    inline_time = {"value": 0.0}
    original_inline = JianJiApp._tag_inline

    def timed_inline(self, *args, **kwargs):
        start = time.perf_counter()
        try:
            return original_inline(self, *args, **kwargs)
        finally:
            inline_time["value"] += (time.perf_counter() - start) * 1000

    JianJiApp._tag_inline = timed_inline

    # 读全文 + 切行单独量。切行要用应用真正在用的 `split_document_lines`：
    # 以前这里量的是 `re.split(r"\r\n|\r|\n")`，那个切法本身是错的（单独的 \r
    # 会多切一行），量出来的耗时也和应用实际走的路径不是一回事。
    from main import split_document_lines

    read_samples = []
    split_samples = []
    for _ in range(5):
        start = time.perf_counter()
        full = app.editor.get("1.0", "end-1c")
        read_samples.append((time.perf_counter() - start) * 1000)
        start = time.perf_counter()
        split_document_lines(full)
        split_samples.append((time.perf_counter() - start) * 1000)

    start = time.perf_counter()
    app._apply_markdown_styles()
    whole = (time.perf_counter() - start) * 1000

    print(f"\n  整篇重解析（带计时器）{whole:.1f} ms，分段：")
    print(f"    {'editor.get 读全文':<28} {statistics.median(read_samples):8.2f} ms")
    print(f"    {'split_document_lines 切行':<28} {statistics.median(split_samples):8.2f} ms")
    for name, value in sorted(bucket.items(), key=lambda kv: -kv[1]):
        print(f"    {name:<28} {value:8.2f} ms")
    print(f"    {'_tag_inline 累计':<28} {inline_time['value']:8.2f} ms")
    known = (statistics.median(read_samples) + statistics.median(split_samples)
             + sum(bucket.values()) + inline_time["value"])
    print(f"    {'其余（标签清理/表格行/其它）':<28} {whole - known:8.2f} ms")

    for module, name in originals:
        setattr(module, name, module.__dict__[name].__wrapped__)
    JianJiApp._tag_inline = original_inline

    root.destroy()
    storage.default_state_path = real_state


if __name__ == "__main__":
    main()
