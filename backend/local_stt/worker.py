# -*- coding: utf-8 -*-
"""L09 本地 STT worker：stdin/stdout 帧协议（骨架，不随包安装模型）。

帧格式（两个方向一致）：
    [1 字节类型][4 字节小端长度][payload]
    类型 FRAME_CONTROL(b'J') = payload 是 UTF-8 JSON 控制消息
    类型 FRAME_AUDIO(b'A')   = payload 是原始音频字节

控制消息（stdin → worker）：
    {"op": "start", "request_id": "...", "language": "zh", "model_ref": "base", "format": "pcm16"}
    {"op": "stop", "request_id": "..."}     # 结束本段，产出 final
    {"op": "cancel", "request_id": "..."}   # 放弃本段，不产出 final
音频帧只能在 start 与 stop/cancel 之间出现，归属当前活跃请求。

控制消息（worker → stdout）：
    {"op": "started", "request_id": "...", "model_ref": "..."}
    {"op": "partial", "request_id": "...", "text": "..."}
    {"op": "final",   "request_id": "...", "text": "..."}
    {"op": "cancelled", "request_id": "..."}
    {"op": "error",   "request_id": "...", "code": "...", "message": "..."}

边界（L09 任务书）：
- 16kHz 单声道 PCM16；非 PCM 需先转码（骨架：ffmpeg 缺失时拒绝该段，不假装成功）；
- 单段 ≤60 秒 / ≤10MB，超限即 error(limit_exceeded) 并清空缓冲；
- 模型只能从固定清单取名字（renderer 不能传任意路径），模型目录缺失 → model_missing；
- cancel/stop 后缓冲立即清空（RAM 清理），迟到音频按协议错误处理；
- 转写器可注入（测试用假实现；生产尝试 faster-whisper，未安装即 model_missing 级错误）。

入口：python -m backend.local_stt.worker
"""
from __future__ import annotations

import io
import json
import os
import shutil
import struct
import sys
from pathlib import Path
from typing import BinaryIO, Callable

FRAME_CONTROL = b"J"
FRAME_AUDIO = b"A"
_HEADER = struct.Struct("<cI")

SAMPLE_RATE = 16_000          # 16kHz 单声道 PCM16
_BYTES_PER_SECOND = SAMPLE_RATE * 2
MAX_AUDIO_BYTES = 10 * 1024 * 1024   # 单段 ≤10MB
MAX_AUDIO_SECONDS = 60               # 单段 ≤60 秒

# 固定模型清单：renderer 只能传这里的名字，不能传任意文件路径
WORKER_MODELS = ("tiny", "base", "small")

_ERROR_CODES = {
    "bad_model", "model_missing", "limit_exceeded", "protocol",
    "transcode_unavailable", "transcribe_failed", "bad_request",
}


class WorkerError(Exception):
    """worker 业务错误；code 进 error 帧，message 面向用户可读。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code if code in _ERROR_CODES else "bad_request"
        self.message = message


# ---- 帧编解码（独立函数，便于两侧共享与测试）----

def encode_control_frame(obj: dict) -> bytes:
    payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return _HEADER.pack(FRAME_CONTROL, len(payload)) + payload


def encode_audio_frame(data: bytes) -> bytes:
    return _HEADER.pack(FRAME_AUDIO, len(data)) + data


def decode_frame(stream: BinaryIO) -> tuple[bytes, bytes] | None:
    """读一帧；流结束返回 None。帧头损坏抛 WorkerError(protocol)。"""
    header = stream.read(5)
    if not header:
        return None
    if len(header) != 5:
        raise WorkerError("protocol", "帧头不完整")
    kind, length = _HEADER.unpack(header)
    if kind not in (FRAME_CONTROL, FRAME_AUDIO):
        raise WorkerError("protocol", f"未知帧类型: {kind!r}")
    if length > 64 * 1024 * 1024:
        raise WorkerError("protocol", "帧长度超出上限")
    payload = stream.read(length)
    if len(payload) != length:
        raise WorkerError("protocol", "帧体不完整")
    return kind, payload


# ---- 模型解析 ----

def resolve_model_path(model_ref: str, models_dir: Path) -> Path:
    if model_ref not in WORKER_MODELS:
        raise WorkerError("bad_model", f"未知模型：只支持 {'/'.join(WORKER_MODELS)}")
    return models_dir / model_ref


def _ffmpeg_path() -> str | None:
    override = os.environ.get("LOCAL_STT_FFMPEG", "").strip()
    if override:
        return override if shutil.which(override) else None
    return shutil.which("ffmpeg")


def _transcode_to_pcm(audio: bytes) -> bytes:
    """非 PCM 输入先转码（骨架：需要本机 ffmpeg；缺失即拒绝，不假装成功）。

    生产实现应把音频写进受控随机临时目录、ffmpeg 转 16k 单声道 PCM16 后在
    finally 里删除临时文件；骨架阶段仅探测能力并给出确定性错误。
    """
    if not _ffmpeg_path():
        raise WorkerError(
            "transcode_unavailable",
            "当前音频不是 16kHz 单声道 PCM，且本机未配置 ffmpeg 转码",
        )
    raise WorkerError("transcode_unavailable", "转码尚未实装（骨架阶段仅探测能力）")


Transcriber = Callable[[bytes, str, str], str]
"""转写器： (pcm16 音频, language, 模型目录路径) -> 文本。"""


def _real_transcriber(audio: bytes, language: str, model_path: str) -> str:
    if not Path(model_path).is_dir():
        raise WorkerError("model_missing", "本地模型未下载（设置里选择并下载后才可用）")
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    except ImportError as exc:
        raise WorkerError(
            "model_missing", "本机未安装 faster-whisper 运行库，无法本地转写"
        ) from exc
    model = WhisperModel(model_path, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(audio, language=language or None)
    return "".join(seg.text for seg in segments).strip()


# ---- 主循环 ----

def run_loop(
    inp: BinaryIO,
    out: BinaryIO,
    *,
    transcriber: Transcriber = _real_transcriber,
    models_dir: Path | None = None,
) -> int:
    """帧循环。返回退出码（正常 EOF=0）。异常帧回 error 后继续，坏帧头退出。"""
    if models_dir is None:
        models_dir = _default_models_dir()
    active_id: str | None = None
    active_model_path: Path | None = None
    active_language = "zh"
    active_format = "pcm16"
    buf = bytearray()

    def send(obj: dict) -> None:
        out.write(encode_control_frame(obj))
        out.flush()

    def reset() -> None:
        nonlocal active_id, active_model_path, buf
        active_id = None
        active_model_path = None
        buf = bytearray()  # RAM 清理：终态即清，不等待下一段

    def fail(request_id: str | None, exc: Exception) -> None:
        code = getattr(exc, "code", "transcribe_failed")
        send({"op": "error", "request_id": request_id or "", "code": code, "message": str(exc)})
        reset()

    while True:
        try:
            frame = decode_frame(inp)
        except WorkerError as exc:
            # 帧结构损坏后流已不可信，无法继续安全解析——报告后退出
            fail(active_id, exc)
            return 1
        if frame is None:
            return 0
        kind, payload = frame

        if kind == FRAME_AUDIO:
            if active_id is None:
                fail(None, WorkerError("protocol", "start 之前收到音频帧"))
                continue
            buf.extend(payload)
            if len(buf) > MAX_AUDIO_BYTES or len(buf) > MAX_AUDIO_SECONDS * _BYTES_PER_SECOND:
                fail(active_id, WorkerError(
                    "limit_exceeded", f"单段超过 {MAX_AUDIO_SECONDS}s/10MB 上限"))
                continue
            # 骨架无流式解码器：不发 partial；协议位保留
            continue

        try:
            msg = json.loads(payload.decode("utf-8"))
            if not isinstance(msg, dict):
                raise WorkerError("protocol", "控制消息必须是 JSON 对象")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            fail(active_id, WorkerError("protocol", f"控制消息解析失败: {exc}"))
            continue

        op = str(msg.get("op") or "")
        request_id = str(msg.get("request_id") or "")

        if op == "start":
            if active_id is not None:
                fail(active_id, WorkerError("protocol", "上一段尚未 stop/cancel"))
                continue
            model_ref = str(msg.get("model_ref") or "base")
            try:
                active_model_path = resolve_model_path(model_ref, models_dir)
                # fail-fast：模型目录不在当场拒绝，而不是等说完一段才报错
                if not active_model_path.is_dir():
                    raise WorkerError("model_missing", "本地模型未下载（设置里选择并下载后才可用）")
            except WorkerError as exc:
                fail(request_id, exc)
                continue
            active_id = request_id or "default"
            active_language = str(msg.get("language") or "zh")
            active_format = str(msg.get("format") or "pcm16")
            send({"op": "started", "request_id": active_id, "model_ref": model_ref})
            continue

        if op in ("stop", "cancel"):
            if active_id is None or (request_id and request_id != active_id):
                fail(request_id or None, WorkerError("protocol", "没有对应的活跃请求"))
                continue
            rid = active_id
            if op == "cancel":
                send({"op": "cancelled", "request_id": rid})
                reset()
                continue
            audio = bytes(buf)
            fmt, model_path = active_format, str(active_model_path)
            lang = active_language
            try:
                if fmt != "pcm16":
                    audio = _transcode_to_pcm(audio)
                reset()  # 转写前清缓冲：长转写期间不占 RAM 保存原音频
                if not audio:
                    raise WorkerError("bad_request", "本段没有音频数据")
                text = transcriber(audio, lang, model_path)
                send({"op": "final", "request_id": rid, "text": text})
            except WorkerError as exc:
                fail(rid, exc)
            except Exception as exc:  # 转写器意外失败：兜成业务错误，不让 worker 崩
                fail(rid, WorkerError("transcribe_failed", f"本地转写失败: {type(exc).__name__}"))
            continue

        fail(request_id or None, WorkerError("protocol", f"未知操作: {op!r}"))


def _default_models_dir() -> Path:
    env = os.environ.get("LOCAL_STT_MODELS_DIR", "").strip()
    if env:
        return Path(env)
    data_root = os.environ.get("TZTUZHAN_DATA_DIR", "").strip()
    base = Path(data_root) if data_root else Path(__file__).resolve().parents[2] / "data"
    return base / "local_stt_models"


def main() -> int:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stdout.reconfigure(encoding="utf-8")                    # type: ignore[union-attr]
    return run_loop(sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    raise SystemExit(main())
