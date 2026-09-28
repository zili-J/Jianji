"""探针：**纯标准库**调用微软 Edge「大声朗读」的在线语音合成，验证能否直接出 MP3。

这个探针要回答的是一个具体问题：不装任何第三方包（不 `pip install edge-tts`），
只靠 socket / ssl / hashlib / json，能不能拿到比本机 SAPI5 自然得多的中文语音？

## 协议要点（都是实测确认过的，写在这里免得下次重踩）

- 端点是 `wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1`，
  一个 **WebSocket**：要么用 `websockets` 库，要么自己实现握手 + 分帧（本探针走后者）。
- 必须带 `TrustedClientToken=6A5AA1D4EAFF4E9FB37E23D68491D6F4`（Edge 朗读功能内置的公开令牌）。
- 还必须带 **`Sec-MS-GEC`** 反滥用头，否则一律 403。算法：当前时间戳加上 Windows 纪元偏移
  （11644473600 秒），**向下取整到 300 秒**，再乘 1e7 换成 100 纳秒刻度，拼上令牌做
  SHA256，取大写十六进制。**本机时钟偏差过大同样 403**。
- 发两条消息：先 `Path:speech.config`（声明输出格式），再 `Path:ssml`。
- 收消息时音频藏在 **二进制帧** 里，帧结构是 **2 字节大端头部长度 + 头部文本 + 音频字节**；
  头部以**单个** `\r\n` 结尾，别拿 `\r\n\r\n` 去切（切不出来，正文会恒为空）。
- 服务端偶尔发 ping（opcode 0x9），**必须回 pong（0xA）**，否则会被掐断。
- `recv` 不保证按帧边界返回，**读帧必须带缓冲**，否则一次读到半帧就解崩。
- **SSML 支持是残缺的**：`<break .../>` 一律非法（服务端回 `SSML is invalid`），
  `<speak>` 下必须有 `<voice>`。段间停顿靠 `\n\n`，神经音色自己会停。
- 服务端报错时**不发 `turn.end`、也不关连接**，只把 socket 晾着 —— 必须自己设超时，
  否则表现是「永久卡住」而不是「报错」。
- **`User-Agent` 里的 `Edg/<主版本>` 必须和 `Sec-MS-GEC-Version` 一致**，
  否则握手直接 403（实测 `Edg/130` 全挂、`Edg/143` 才通）。

## 在线音色比 SAPI 强在哪

`<prosody rate>` 在**这里**是真管用的（和 SAPI5 相反，见 `app/speech.py` 的说明）。
而且音色本身是神经网络合成的，不是拼接式的，中文有 14 个可选（含东北/陕西方言）。

用法：
    python tools/_probe_edge_tts.py                 # 只出在线版
    python tools/_probe_edge_tts.py --compare       # 顺带出本机 SAPI 版做 A/B
    python tools/_probe_edge_tts.py 某篇文稿.md --compare
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import sys
import tempfile
import time
import uuid
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))

HOST = "speech.platform.bing.com"
WS_PATH = "/consumer/speech/synthesize/readaloud/edge/v1"
TRUSTED_TOKEN = "6A5AA1D4EAFF4E9FB37E23D68491D6F4"
#: 与 Edge 版本绑定。**`User-Agent` 里的 `Edg/<主版本>` 必须和它一致**，
#: 否则服务端直接 403（实测 `Edg/130` 全挂、`Edg/143` 才通）。被拒时先升这里和 UA。
CHROMIUM_FULL_VERSION = "143.0.3650.75"
CHROMIUM_MAJOR_VERSION = CHROMIUM_FULL_VERSION.split(".", 1)[0]
GEC_VERSION = f"1-{CHROMIUM_FULL_VERSION}"
#: 24 kHz / 48 kbps 单声道 MP3，体积和音质的平衡点。
OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
WIN_EPOCH_OFFSET = 11_644_473_600  # 1601-01-01 → 1970-01-01 的秒数

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


class EdgeTTSError(RuntimeError):
    pass


# ---------------------------------------------------------------- 反滥用令牌

def sec_ms_gec() -> str:
    """算 `Sec-MS-GEC`。少了它服务端一律 403。"""
    ticks = int(time.time()) + WIN_EPOCH_OFFSET
    ticks -= ticks % 300  # 5 分钟对齐，容忍客户端时钟有一点漂移
    ticks *= 10_000_000
    return hashlib.sha256(f"{ticks}{TRUSTED_TOKEN}".encode("ascii")).hexdigest().upper()


def _timestamp() -> str:
    return time.strftime("%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)",
                         time.gmtime())


# ---------------------------------------------------------------- WebSocket

def ws_connect(path: str):
    """手写 WebSocket 握手。返回 `(socket, 握手响应之后多读到的字节)`。"""
    ctx = ssl.create_default_context()
    raw = socket.create_connection((HOST, 443), timeout=20)
    sock = ctx.wrap_socket(raw, server_hostname=HOST)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {HOST}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "Origin: chrome-extension://jdiccldimpdaibmpdkjnbmckianbfold\r\n"
        "Pragma: no-cache\r\n"
        "Cache-Control: no-cache\r\n"
        "Accept-Encoding: gzip, deflate, br, zstd\r\n"
        "Accept-Language: en-US,en;q=0.9\r\n"
        "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{CHROMIUM_MAJOR_VERSION}.0.0.0 Safari/537.36 "
        f"Edg/{CHROMIUM_MAJOR_VERSION}.0.0.0\r\n"
        "\r\n"
    )
    sock.sendall(request.encode("ascii"))
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise EdgeTTSError("握手过程中连接被关闭")
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    status = head.split(b"\r\n")[0].decode("latin-1")
    if "101" not in status:
        sock.close()
        raise EdgeTTSError(f"握手失败：{status}")
    return sock, rest


def ws_send(sock, payload: bytes, opcode: int = 0x1) -> None:
    """发一个**带掩码**的帧（客户端→服务端必须掩码，服务端→客户端必须不掩码）。"""
    mask = os.urandom(4)
    header = bytearray([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", n)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", n)
    header += mask
    sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))


class FrameReader:
    """按帧读。**必须带缓冲**——`recv` 不保证按帧边界返回。"""

    def __init__(self, sock, initial: bytes = b""):
        self.sock = sock
        self.buf = bytearray(initial)

    def _need(self, n: int) -> None:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EdgeTTSError("连接被服务端关闭")
            self.buf += chunk

    def read(self) -> tuple[int, bytes]:
        self._need(2)
        b0, b1 = self.buf[0], self.buf[1]
        del self.buf[:2]
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        n = b1 & 0x7F
        if n == 126:
            self._need(2)
            n = struct.unpack(">H", bytes(self.buf[:2]))[0]
            del self.buf[:2]
        elif n == 127:
            self._need(8)
            n = struct.unpack(">Q", bytes(self.buf[:8]))[0]
            del self.buf[:8]
        mask = b""
        if masked:
            self._need(4)
            mask = bytes(self.buf[:4])
            del self.buf[:4]
        self._need(n)
        payload = bytes(self.buf[:n])
        del self.buf[:n]
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload


# ---------------------------------------------------------------- SSML 与合成

def _escape(text: str) -> str:
    """`&` 必须**第一个**换，否则会把后面换出来的 `&amp;` 再换一遍。"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_edge_ssml(text: str, voice: str, *, rate: str = "-8%") -> str:
    """按空行分段，**段间用 `\\n\\n` 而不是 `<break>`**。

    实测这个端点**完全不支持 `<break>`**：只要 SSML 里出现 `<break .../>`，服务端就回
    `SSML is invalid`，而且**不发 `turn.end`**、把连接一直晾着（表现是读超时，不是报错）。
    `<speak>` 下也**必须有 `<voice>`**，裸 `<speak>正文</speak>` 同样非法。

    好消息是**不需要**手动加停顿：神经音色把空行当段落停顿、把 `。！？` 当句末停顿，
    自带节奏。段内的换行按空格处理（交给它自己断句）。
    """
    paragraphs = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        body = " ".join(line.strip() for line in block.split("\n") if line.strip())
        if body:
            paragraphs.append(_escape(body))
    joined = "\n\n".join(paragraphs)
    return (
        "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='zh-CN'>"
        f"<voice name='{voice}'><prosody rate='{rate}'>{joined}</prosody></voice></speak>"
    )


def synthesize(text: str, out_path: Path, *, voice: str = DEFAULT_VOICE,
               rate: str = "-8%") -> int:
    """合成并写文件，返回音频字节数。"""
    path = (f"{WS_PATH}?TrustedClientToken={TRUSTED_TOKEN}"
            f"&Sec-MS-GEC={sec_ms_gec()}&Sec-MS-GEC-Version={GEC_VERSION}"
            f"&ConnectionId={uuid.uuid4().hex}")
    sock, rest = ws_connect(path)
    # 服务端报错时**不发 turn.end**、也不关连接，只是把 socket 晾着，
    # 所以必须有超时，否则会永久卡住（表现成「没反应」而不是「报错」）。
    sock.settimeout(20)
    reader = FrameReader(sock, rest)
    try:
        config = {"context": {"synthesis": {"audio": {
            "metadataoptions": {"sentenceBoundaryEnabled": "false",
                                "wordBoundaryEnabled": "false"},
            "outputFormat": OUTPUT_FORMAT}}}}
        ws_send(sock, (
            f"X-Timestamp:{_timestamp()}\r\n"
            "Content-Type:application/json; charset=utf-8\r\n"
            "Path:speech.config\r\n\r\n"
            + json.dumps(config, separators=(",", ":"))
        ).encode("utf-8"))
        ws_send(sock, (
            f"X-RequestId:{uuid.uuid4().hex}\r\n"
            "Content-Type:application/ssml+xml\r\n"
            f"X-Timestamp:{_timestamp()}\r\n"
            "Path:ssml\r\n\r\n"
            + build_edge_ssml(text, voice, rate=rate)
        ).encode("utf-8"))

        audio = bytearray()
        while True:
            opcode, payload = reader.read()
            if opcode == 0x8:  # close
                break
            if opcode == 0x9:  # ping → pong
                ws_send(sock, payload, opcode=0xA)
                continue
            if opcode == 0x1:  # 文本帧：状态通知
                if b"turn.end" in payload:
                    break
                if b"Path:" not in payload:  # 没有 Path 头 = 服务端在骂人
                    raise EdgeTTSError(
                        "服务端拒绝了这段 SSML："
                        + payload.decode("utf-8", "replace").strip()[-80:])
            elif opcode == 0x2:  # 二进制帧：音频
                # 结构是 **2 字节大端头部长度 + 头部 + 音频**。头部以**单个** `\r\n`
                # 结尾（不是 `\r\n\r\n`），所以不能用 `partition("\r\n\r\n")` 切——
                # 那样切不出分隔符，正文会恒为空、看起来像「服务端没返回音频」。
                if len(payload) <= 2:
                    continue
                header_len = int.from_bytes(payload[:2], "big")
                head = payload[2:2 + header_len].decode("utf-8", "replace")
                if "Path:audio" in head and "metadata" not in head:
                    audio += payload[2 + header_len:]
    finally:
        try:
            sock.close()
        except OSError:
            pass
    if not audio:
        raise EdgeTTSError("服务端没有返回音频（可能被限流，或音色名无效）")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(audio))
    return len(audio)


def list_chinese_voices() -> list[str]:
    """拉一次在线音色表（这一步只要 urllib，不需要 WebSocket）。"""
    import urllib.request
    url = ("https://speech.platform.bing.com/consumer/speech/synthesize/"
           f"readaloud/voices/list?trustedclienttoken={TRUSTED_TOKEN}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8"))
    return [v["ShortName"] for v in data if v.get("Locale", "").startswith("zh-CN")]


def _spoken_text(app, source: Path | None) -> tuple[str, str]:
    """取要念的文本：用主程序真实的 `speakable_text()`，保证和导出音频一致。"""
    if source and source.is_file():
        text = source.read_text(encoding="utf-8-sig")
        print(f"用文稿：{source.name}")
    else:
        text = SAMPLE
        print("用内置样例（想换成自己的文稿就把它当路径传进来）")
    return text, app.speakable_text(text)


def main() -> int:
    ap = argparse.ArgumentParser(description="Edge 在线语音合成探针")
    ap.add_argument("document", nargs="?", help="可选：要念的 Markdown 文稿")
    ap.add_argument("--voice", default=DEFAULT_VOICE)
    ap.add_argument("--rate", default="-8%")
    ap.add_argument("--compare", action="store_true", help="顺带出本机 SAPI 版做 A/B")
    args = ap.parse_args()

    import tkinter as tk

    import ffmpeg
    import main as app_main
    import speech
    import storage

    tmp = Path(tempfile.mkdtemp())
    saved = storage.default_state_path
    storage.default_state_path = lambda: tmp / "state.json"
    real = Path(os.environ.get("LOCALAPPDATA", "")) / "JianJi" / "state.json"
    settings = json.loads(real.read_text(encoding="utf-8")).get("settings", {}) \
        if real.exists() else {}
    folder = tmp / "简记日记"
    folder.mkdir()
    (tmp / "state.json").write_text(json.dumps({
        "folder": str(folder), "default_folder": str(folder),
        "last_file": "", "settings": settings}, ensure_ascii=False), encoding="utf-8")

    root = tk.Tk()
    root.geometry("1180x760")
    app = app_main.JianJiApp(root)
    root.update()
    source = Path(args.document) if args.document else None
    text, spoken = _spoken_text(app, source)
    root.destroy()
    storage.default_state_path = saved

    words, characters = app_main.document_counts(text)
    print(f"字数 {words} · 字符数 {characters} · 本机音色 {speech.default_voice_name()}")

    OUT.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[str, float | None, int]] = []

    print(f"  在线合成中（音色 {args.voice}，语速 {args.rate}）…", flush=True)
    t0 = time.time()
    edge_mp3 = OUT / "edge.mp3"
    size = synthesize(spoken, edge_mp3, voice=args.voice, rate=args.rate)
    elapsed = time.time() - t0
    seconds = None
    if ffmpeg.is_available():
        seconds = _mp3_seconds(edge_mp3, ffmpeg.find())
    rows.append(("edge 在线", seconds, size))
    print(f"    完成：{size / 1024:.0f} KB，耗时 {elapsed:.1f} s")

    if args.compare and speech.is_available():
        work = Path(tempfile.mkdtemp(prefix="jianji-demo-"))
        for label, payload, rate, xml in (
                ("local-plain 本机原样", spoken, 0, False),
                ("local-ssml 本机加停顿", speech.build_ssml(spoken),
                 speech.NATURAL_RATE, True)):
            wav = work / f"{label}.wav"
            print(f"  正在合成 {label} …", flush=True)
            speech.synthesize(payload, wav, rate=rate, xml=xml)
            mp3 = OUT / f"{label.split()[0]}.mp3"
            if ffmpeg.is_available():
                ffmpeg.to_mp3(wav, mp3)
                rows.append((label, speech.wav_seconds(wav), mp3.stat().st_size))
            else:
                target = OUT / f"{label.split()[0]}.wav"
                target.write_bytes(wav.read_bytes())
                rows.append((label, speech.wav_seconds(wav), target.stat().st_size))

    print("\n版本                     时长        大小")
    for label, seconds, size in rows:
        shown = f"{seconds:6.1f} 秒" if seconds else "     —   "
        print(f"  {label:22s} {shown}  {size / 1024:6.0f} KB")
    print(f"\n样音在：{OUT}")
    print("**「哪个更自然」得自己听**，脚本只负责把几份摆到一起。")
    return 0


def _mp3_seconds(path: Path, binary: str | None) -> float | None:
    """用 ffprobe 读时长；没有就返回 None（不猜）。"""
    if not binary:
        return None
    probe = Path(binary).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    if not probe.exists():
        return None
    import subprocess
    flags = 0x08000000 if os.name == "nt" else 0
    try:
        out = subprocess.run(
            [str(probe), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30, creationflags=flags)
        return float(out.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
