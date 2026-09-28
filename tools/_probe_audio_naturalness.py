"""做一对 A/B 样音，用来**用耳朵**判断「加停顿 + 降一档语速」到底有没有用。

输出两个 MP3，同一段文字：

  · `plain.mp3`   —— 旧行为：纯文本送进引擎，语速 0
  · `natural.mp3` —— 新行为：带 `<break>` 的 SSML，语速 `NATURAL_RATE`

再顺手打一张表：字数、时长、字节数。**「更自然」没法机器判定，只能人听**；
这个脚本负责把两份样音摆到同一个文件夹里，别的交给耳朵。

    python tools/quiet_desktop.py <python> tools/_probe_audio_naturalness.py [文稿.md]
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

import ffmpeg  # noqa: E402
import speech  # noqa: E402
import storage  # noqa: E402

#: 没有给文稿时用这一段。两段、多句、带逗号——正好能听出段间与句末的停顿。
SAMPLE = (
    "# 留白那件事\n"
    "\n"
    "今天把留白那件事收尾了。一开始以为是行距的问题，后来才发现是标签的范围。\n"
    "改完之后，滚到底也舒服了。你说，这算不算一个小小的胜利？\n"
    "\n"
    "不过还有一处没想明白：列表项里的缩进续行，到底该并进上一段还是另起一段。\n"
    "先记在这里，明天再说。\n"
)

OUT = PROJECT / "outputs" / "audio-demo"


def main() -> int:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    import tkinter as tk

    import main as app_main

    tmp = Path(tempfile.mkdtemp())
    saved = storage.default_state_path
    storage.default_state_path = lambda: tmp / "state.json"
    real = Path(os.environ.get("LOCALAPPDATA", "")) / "JianJi" / "state.json"
    settings = json.loads(real.read_text(encoding="utf-8")).get("settings", {}) if real.exists() else {}
    folder = tmp / "简记日记"
    folder.mkdir()
    (tmp / "state.json").write_text(json.dumps({
        "folder": str(folder), "default_folder": str(folder),
        "last_file": "", "settings": settings}, ensure_ascii=False), encoding="utf-8")

    root = tk.Tk()
    root.geometry("1180x760")
    app = app_main.JianJiApp(root)
    root.update()

    if source and source.is_file():
        text = source.read_text(encoding="utf-8-sig")
        print(f"用文稿：{source.name}")
    else:
        text = SAMPLE
        print("用内置样例（想换成自己的文稿就把它当参数传进来）")
    spoken = app.speakable_text(text)
    root.destroy()
    storage.default_state_path = saved

    words, characters = app_main.document_counts(text)
    print(f"字数 {words} · 字符数 {characters} · 音色 {speech.default_voice_name()}")
    print(f"ffmpeg: {ffmpeg.find() or '（没找到，只能出 WAV）'}")

    if not speech.is_available():
        print("这台机器没有可用的语音引擎，做不了样音。")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="jianji-demo-"))
    rows: list[tuple[str, float, int]] = []

    for label, payload, rate, xml in (
            ("plain", spoken, 0, False),
            ("natural", speech.build_ssml(spoken), speech.NATURAL_RATE, True)):
        wav = work / f"{label}.wav"
        print(f"  正在合成 {label} …", flush=True)
        speech.synthesize(payload, wav, rate=rate, xml=xml)
        seconds = speech.wav_seconds(wav) or 0.0
        mp3 = OUT / f"{label}.mp3"
        if ffmpeg.is_available():
            ffmpeg.to_mp3(wav, mp3)
            size = mp3.stat().st_size
        else:
            target = OUT / f"{label}.wav"
            target.write_bytes(wav.read_bytes())
            size = target.stat().st_size
        rows.append((label, seconds, size))

    print("\n文件            时长        大小")
    for label, seconds, size in rows:
        print(f"  {label:12s} {seconds:6.1f} 秒  {size / 1024:6.0f} KB")
    if len(rows) == 2:
        delta = rows[1][1] - rows[0][1]
        print(f"\n新的一版比旧的长 {delta:+.1f} 秒 —— 多出来的就是停顿和放慢的那一档。")
    print(f"\n样音在：{OUT}")
    print("**「哪个更自然」得自己听**，脚本只负责把两份摆到一起。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
