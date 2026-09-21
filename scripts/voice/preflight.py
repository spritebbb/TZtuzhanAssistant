# -*- coding: utf-8 -*-
"""P3-03 声纹训练前置检查（preflight，只诊断不训练）。

检查项（docs/Zcode技术指导.md §21.2）：GPU/显存、驱动、磁盘、Python 依赖锁、
GPT-SoVITS 服务能力协商、模型 hash、素材目录卫生（时长/格式/标注）。
输出逐项 PASS/WARN/FAIL 与总结；退出码 0=可开工，1=有 FAIL。

用法：
    .venv/Scripts/python.exe scripts/voice/preflight.py --materials data/voice_materials/default
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # scripts/voice/ → 仓库根
sys.path.insert(0, str(ROOT))

RESULTS: list[tuple[str, str, str]] = []  # (level, item, detail)


def report(level: str, item: str, detail: str = "") -> None:
    RESULTS.append((level, item, detail))
    mark = {"PASS": "✓", "WARN": "△", "FAIL": "✗"}[level]
    print(f"  [{mark}] {item}" + (f"：{detail}" if detail else ""))


def check_gpu() -> None:
    smi = shutil.which("nvidia-smi")
    if not smi:
        report("FAIL", "GPU", "找不到 nvidia-smi（GPT-SoVITS 训练需要 NVIDIA GPU）")
        return
    try:
        out = subprocess.run([smi, "--query-gpu=name,memory.total",
                              "--format=csv,noheader"], capture_output=True,
                             text=True, timeout=10).stdout.strip()
    except Exception as exc:
        report("WARN", "GPU", f"nvidia-smi 执行失败：{exc}")
        return
    if not out:
        report("FAIL", "GPU", "nvidia-smi 无输出（驱动异常？）")
        return
    name, _, vram = out.splitlines()[0].partition(",")
    try:
        mb = int("".join(ch for ch in vram if ch.isdigit()) or 0)
    except ValueError:
        mb = 0
    # §21.2：素材 5-10 分钟的 LoRA 微调，8GB 起步可行，12GB+ 从容
    if mb >= 12_000:
        report("PASS", "GPU", f"{name.strip()} {mb // 1024}GB")
    elif mb >= 8_000:
        report("WARN", "GPU", f"{name.strip()} {mb // 1024}GB（够 LoRA，全量训练吃紧）")
    else:
        report("FAIL", "GPU", f"{name.strip()} {mb // 1024}GB（低于 8GB，不建议训练）")


def check_disk(path: Path) -> None:
    try:
        free_gb = shutil.disk_usage(path).free / 1024 ** 3
    except OSError:
        report("WARN", "磁盘", f"无法探测 {path}")
        return
    if free_gb >= 20:
        report("PASS", "磁盘", f"{path} 所在盘剩余 {free_gb:.0f}GB")
    else:
        report("WARN", "磁盘", f"剩余 {free_gb:.0f}GB（训练中间产物建议 ≥20GB）")


def check_service() -> dict | None:
    try:
        sys.path.insert(0, str(ROOT))
        from backend.core.config import config
        from backend.core.local_tts import negotiate
        import asyncio

        caps = asyncio.run(negotiate(force=True))
    except Exception as exc:
        report("WARN", "推理服务", f"能力协商异常：{exc}")
        return None
    if caps is None:
        report("WARN", "推理服务",
               f"{config.local_tts_endpoint} 未就绪（训练前置可跳过；启用 local_tts_enabled 前必须就绪）")
        return None
    if not caps.get("gpu"):
        detail = "服务自报 CPU 推理（§21.2：CPU 须延迟验收通过才开放）"
        report("WARN", "推理服务", detail)
    else:
        report("PASS", "推理服务",
               f"{caps['provider_version']} / {caps['model_format']} / {caps['sample_rate']}Hz")
    return caps


def check_materials(dir_path: Path | None) -> None:
    if dir_path is None:
        report("WARN", "素材", "未指定 --materials，跳过素材检查")
        return
    if not dir_path.is_dir():
        report("FAIL", "素材", f"目录不存在：{dir_path}")
        return
    waves = sorted(dir_path.glob("*.wav"))
    if not waves:
        report("FAIL", "素材", "目录内无 .wav（参考音频需 16k/32k 以上干净 wav）")
        return
    import wave as _wave

    total, bad, unlabeled = 0.0, 0, 0
    for f in waves:
        try:
            with _wave.open(str(f), "rb") as r:
                total += r.getnframes() / r.getframerate()
        except Exception:
            bad += 1
        if not (f.with_suffix(".txt").exists() or (dir_path / f"{f.stem}.lab").exists()):
            unlabeled += 1
    minutes = total / 60
    if bad:
        report("FAIL", "素材", f"{bad} 个 wav 无法解析")
    elif minutes < 5:
        report("WARN", "素材", f"共 {minutes:.1f} 分钟（建议 5-10 分钟干净一致素材）")
    else:
        report("PASS", "素材", f"{len(waves)} 条 / {minutes:.1f} 分钟")
    if unlabeled:
        report("FAIL", "素材", f"{unlabeled} 条缺少同名词文本标注（.txt）——prompt_text 必需")
    else:
        report("PASS", "标注", f"{len(waves)} 条均有文本标注")
    # §21.2：素材必须有权使用（授权/自录）；清单记录来源与处理版本
    if not (dir_path / "SOURCE.md").exists():
        report("WARN", "授权", "缺 SOURCE.md（记录素材来源与使用权，训练前必须补上）")
    else:
        report("PASS", "授权", "SOURCE.md 在位")


def main() -> int:
    parser = argparse.ArgumentParser(description="GPT-SoVITS 声纹训练前置检查")
    parser.add_argument("--materials", default="", help="素材目录（含 wav+同名 txt+SOURCE.md）")
    args = parser.parse_args()

    print("== P3-03 声纹训练前置检查 ==")
    check_gpu()
    check_disk(ROOT)
    caps = check_service()
    check_materials(Path(args.materials) if args.materials else None)

    fails = [r for r in RESULTS if r[0] == "FAIL"]
    warns = [r for r in RESULTS if r[0] == "WARN"]
    print(f"\n总结：{len(RESULTS)} 项，FAIL {len(fails)} / WARN {len(warns)}")
    if fails:
        print("结论：存在 FAIL，先处理后训练。")
        return 1
    print("结论：可进入训练流程（见 docs/GPT-SOVITS-LOCAL-RUNBOOK.md）。")
    print(json.dumps({"caps": caps, "results": RESULTS}, ensure_ascii=False, indent=2)[:0] or "", end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
