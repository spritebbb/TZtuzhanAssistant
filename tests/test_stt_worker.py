# -*- coding: utf-8 -*-
"""L09 本地 STT worker 骨架回归：帧协议、限额、取消、无模型、迟到帧。

覆盖六件事：
1. 帧编解码 roundtrip 与坏帧头退出；
2. 正常链路 start→audio→stop→final（假转写器）；
3. 单段超限（>60s 等效字节 / >10MB）即 error(limit_exceeded) 且状态复位；
4. cancel 不产出 final、缓冲清空、后续请求可用；
5. 无模型：模型目录缺失 → model_missing；模型名不在固定清单 → bad_model；
6. 协议边界：start 前的音频帧、重复 start、无活跃请求的 stop、非 PCM 无 ffmpeg。
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_stt_"))

from backend.local_stt import worker as w  # noqa: E402


def _fake_transcriber(audio: bytes, language: str, model_path: str) -> str:
    return f"[{language}] {len(audio)}B 转写结果"


def _session(messages: list[bytes], transcriber=None, with_model: bool = True) -> list[dict]:
    """把输入帧喂给 run_loop，收集全部输出控制消息。

    with_model=True 时在临时模型目录里建好 base/ 子目录（假转写器场景）；
    False 保持空目录（model_missing 场景）。
    """
    models_dir = Path(tempfile.mkdtemp(prefix="tztuzhan_stt_models_"))
    if with_model:
        (models_dir / "base").mkdir()
    inp = io.BytesIO(b"".join(messages))
    out = io.BytesIO()
    code = w.run_loop(inp, out, transcriber=transcriber or _fake_transcriber,
                      models_dir=models_dir)
    events = []
    stream = io.BytesIO(out.getvalue())
    while True:
        frame = w.decode_frame(stream)
        if frame is None:
            break
        kind, payload = frame
        assert kind == w.FRAME_CONTROL, "worker 只应输出控制帧"
        events.append(json.loads(payload.decode("utf-8")))
    return events if code == 0 else events + [{"op": "__exit__", "code": code}]


def _start(rid="r1", **extra) -> bytes:
    msg = {"op": "start", "request_id": rid, "model_ref": "base", "language": "zh"}
    msg.update(extra)
    return w.encode_control_frame(msg)


def test_codec_roundtrip() -> None:
    frame = w.encode_control_frame({"op": "start", "request_id": "x"})
    kind, payload = w.decode_frame(io.BytesIO(frame))
    assert kind == w.FRAME_CONTROL and json.loads(payload)["op"] == "start"
    audio = w.encode_audio_frame(b"\x01\x02\x03")
    kind, payload = w.decode_frame(io.BytesIO(audio))
    assert kind == w.FRAME_AUDIO and payload == b"\x01\x02\x03"
    assert w.decode_frame(io.BytesIO(b"")) is None
    print("[OK] 帧编解码：控制/音频 roundtrip、EOF")


def test_bad_frame_header_exits() -> None:
    events = _session([b"\x99\x00\x00\x00\x00"])
    assert events[0]["op"] == "error" and events[0]["code"] == "protocol"
    assert events[-1]["op"] == "__exit__" and events[-1]["code"] == 1, "坏帧头后应退出"
    print("[OK] 坏帧头：报告 protocol 错误后退出（流不可信不续读）")


def test_happy_path() -> None:
    events = _session([
        _start("r1"),
        w.encode_audio_frame(b"\x00" * 3200),
        w.encode_control_frame({"op": "stop", "request_id": "r1"}),
    ])
    ops = [e["op"] for e in events]
    assert "started" in ops and "final" in ops, f"应有 started 与 final：{ops}"
    final = next(e for e in events if e["op"] == "final")
    assert final["request_id"] == "r1" and "3200B" in final["text"]
    print("[OK] 正常链路：start→audio→stop→final（假转写器）")


def test_second_limit_exceeded() -> None:
    # 60s = 16000Hz × 2B × 60s = 1_920_000B；多 1 字节即超（10MB 上限另测）
    events = _session([
        _start("r2"),
        w.encode_audio_frame(b"\x00" * (60 * 32000 + 1)),
    ])
    err = next(e for e in events if e["op"] == "error")
    assert err["code"] == "limit_exceeded" and err["request_id"] == "r2"
    # 状态已复位：随后 stop 属于协议错误而不是产出 final
    events2 = _session([
        _start("r2"),
        w.encode_audio_frame(b"\x00" * (60 * 32000 + 1)),
        w.encode_control_frame({"op": "stop", "request_id": "r2"}),
    ])
    assert not any(e["op"] == "final" for e in events2), "超限后不得再产出 final"
    print("[OK] 单段时长超限：error(limit_exceeded)，状态复位")


def test_byte_cap_exceeded() -> None:
    events = _session([
        _start("r3"),
        w.encode_audio_frame(b"\x00" * (w.MAX_AUDIO_BYTES + 1)),
    ])
    err = next(e for e in events if e["op"] == "error")
    assert err["code"] == "limit_exceeded"
    print("[OK] 单段 10MB 字节上限：error(limit_exceeded)")


def test_cancel_then_new_request() -> None:
    events = _session([
        _start("r4"),
        w.encode_audio_frame(b"\x00" * 1600),
        w.encode_control_frame({"op": "cancel", "request_id": "r4"}),
        _start("r5"),
        w.encode_audio_frame(b"\x00" * 1600),
        w.encode_control_frame({"op": "stop", "request_id": "r5"}),
    ])
    assert any(e["op"] == "cancelled" and e["request_id"] == "r4" for e in events)
    assert not any(e["op"] == "final" and e["request_id"] == "r4" for e in events)
    final5 = next(e for e in events if e["op"] == "final")
    assert final5["request_id"] == "r5", "取消后新请求应正常完成"
    print("[OK] 取消：不产出 final、缓冲复位、后续请求可用")


def test_model_missing_and_bad_model() -> None:
    # models_dir 是空目录：清单内的模型名有效但目录缺失 → start 即 model_missing
    events = _session([
        _start("r6"),
        w.encode_audio_frame(b"\x00" * 1600),
        w.encode_control_frame({"op": "stop", "request_id": "r6"}),
    ], with_model=False)
    err = next(e for e in events if e["op"] == "error")
    assert err["code"] == "model_missing", f"空模型目录应 model_missing：{err}"

    events = _session([
        _start("r7", model_ref="C:/evil/model.bin"),
    ])
    err = next(e for e in events if e["op"] == "error")
    assert err["code"] == "bad_model", "模型名只接受固定清单，路径串应被拒"
    print("[OK] 无模型：目录缺失→model_missing；任意路径→bad_model")


def test_protocol_edges() -> None:
    # start 之前来音频帧
    events = _session([w.encode_audio_frame(b"\x00" * 32)])
    assert events[0]["op"] == "error" and events[0]["code"] == "protocol"
    # 重复 start
    events = _session([_start("r8"), _start("r9")])
    errs = [e for e in events if e["op"] == "error"]
    assert errs and errs[-1]["code"] == "protocol", "上一段未结束就 start 应报协议错误"
    # 没有活跃请求的 stop
    events = _session([w.encode_control_frame({"op": "stop", "request_id": "zz"})])
    assert events[0]["op"] == "error" and events[0]["code"] == "protocol"
    # 非标注格式：直通转写器（faster-whisper 自解码任意容器），不拒绝
    events = _session([
        _start("r10", format="webm-opus"),
        w.encode_audio_frame(b"\x00" * 32),
        w.encode_control_frame({"op": "stop", "request_id": "r10"}),
    ])
    finals = [e for e in events if e["op"] == "final"]
    assert finals and finals[0]["request_id"] == "r10", f"非标注格式应直通转写: {events}"
    print("[OK] 协议边界：迟到/重复/无主 stop/非 PCM")


def main() -> None:
    test_codec_roundtrip()
    test_bad_frame_header_exits()
    test_happy_path()
    test_second_limit_exceeded()
    test_byte_cap_exceeded()
    test_cancel_then_new_request()
    test_model_missing_and_bad_model()
    test_protocol_edges()
    print("\n=== L09 STT worker 骨架：8 组全部通过 ===")


if __name__ == "__main__":
    main()
