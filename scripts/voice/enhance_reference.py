# -*- coding: utf-8 -*-
"""参考音频去混响增强（2026-09-30 实战固化）。

背景：游戏角色语音普遍带场景后期混响，GPT-SoVITS 连混响一起克隆——
「回音/空谷传响」的根因在参考不在模型。两轮听感迭代得到的流程：
  ① 挑选：api_v2 硬限 3~10s；两个客观指标各测混响的一面——
     - 停顿残余比（10 分位 RMS / 峰值）：测独立混响尾
     - 音节尾衰减（峰值后 200ms RMS / 峰值）：测贴在人声上的早期反射
     （实测有单指标 0.0004 但尾衰减 0.477 的重混响条，两个都要看）
  ② 增强：noisereduce stationary 谱减（prop_decrease 0.85）+ 峰值归一化 95%
  ③ 部署后必须清 data/tts_cache/*.wav——/api/tts 缓存键只含文本+韵律，
     换参考不清缓存会「听起来没变化」

用法：
  python scripts/voice/enhance_reference.py <输入.wav> [输出.wav]
  省略输出时打印两个指标不写文件（体检模式）
"""
from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np


def load(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        rate, ch = w.getframerate(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16).astype(np.float32)
    if ch == 2:
        a = a.reshape(-1, 2).mean(axis=1)
    return a, rate


def metrics(a: np.ndarray, rate: int) -> tuple[float, float]:
    """(停顿残余比, 音节尾衰减)——两者都低才算干净。"""
    win = max(1, int(rate * 0.05))
    rs = [float(np.sqrt(np.mean(a[i:i + win] ** 2))) for i in range(0, len(a) - win, win)]
    if not rs:
        return 1.0, 1.0
    quiet = sorted(rs)[max(0, len(rs) // 10 - 1)]
    pi = int(np.argmax(rs))
    peak = rs[pi] or 1.0
    tail = rs[pi + 4] if pi + 4 < len(rs) else 0.0
    return quiet / peak, tail / peak


def enhance(a: np.ndarray, rate: int) -> np.ndarray:
    import noisereduce as nr

    den = nr.reduce_noise(y=a, sr=rate, stationary=True,
                          n_fft=1024, prop_decrease=0.85)
    peak = np.abs(den).max() or 1.0
    return (den / peak * 32767 * 0.95).astype(np.int16)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    src = Path(sys.argv[1])
    a, rate = load(src)
    q, t = metrics(a, rate)
    print(f"{src.name}: 停顿残余={q:.4f} 尾衰减={t:.3f}")
    if len(sys.argv) < 3:
        return 0
    out = Path(sys.argv[2])
    den = enhance(a, rate)
    q2, t2 = metrics(den.astype(np.float32), rate)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(den.tobytes())
    print(f"增强后: 停顿残余={q2:.4f} 尾衰减={t2:.3f} → {out}")
    print("提醒：部署后清 data/tts_cache/*.wav，否则命中旧缓存")
    return 0


if __name__ == "__main__":
    sys.exit(main())
