# -*- coding: utf-8 -*-
"""本地 STT：独立 worker 进程包（L09 骨架）。

原音频只在本机专用 worker 内处理，不经过现有 HTTP 后端，也不上传任何云端；
主进程（Electron）通过 stdin/stdout 帧协议与之通信，见 worker.py。
"""
from .worker import (
    FRAME_AUDIO,
    FRAME_CONTROL,
    MAX_AUDIO_BYTES,
    MAX_AUDIO_SECONDS,
    SAMPLE_RATE,
    WORKER_MODELS,
    WorkerError,
    decode_frame,
    encode_audio_frame,
    encode_control_frame,
    run_loop,
)

__all__ = [
    "FRAME_AUDIO",
    "FRAME_CONTROL",
    "MAX_AUDIO_BYTES",
    "MAX_AUDIO_SECONDS",
    "SAMPLE_RATE",
    "WORKER_MODELS",
    "WorkerError",
    "decode_frame",
    "encode_audio_frame",
    "encode_control_frame",
    "run_loop",
]
