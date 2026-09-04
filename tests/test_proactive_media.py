# -*- coding: utf-8 -*-
"""A2 主动发图策略：关系、状态、概率与视觉锚点。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core.proactive_media import decide_proactive_image, proactive_image_prompt


def decide(**overrides):
    args = {
        "stage": "亲密", "emotion": 80, "energy": 70, "gap_hours": 12,
        "enabled": True, "image_service_enabled": True,
        "chance_percent": 20, "min_mood": 70, "roll": 10,
    }
    args.update(overrides)
    return decide_proactive_image(**args)


def main() -> None:
    assert not decide(stage="熟悉").create
    assert not decide(emotion=60).create
    assert not decide(energy=20).create
    assert not decide(enabled=False).create
    assert not decide(image_service_enabled=False).create
    print("[OK] 关系、心情、精力与服务开关共同限制主动发图")

    assert decide(roll=19).create
    assert not decide(roll=20).create
    assert decide(stage="恋人", gap_hours=30, roll=39).create
    assert not decide(stage="恋人", gap_hours=30, roll=40).create
    print("[OK] 基础概率边界明确，久别恋人概率翻倍")

    assert decide(emotion=90).mode == "selfie"
    assert decide(emotion=75).mode == "doodle"
    assert decide(stage="恋人", gap_hours=30, emotion=75).mode == "selfie"
    print("[OK] 雀跃/久别用自拍，其余好心情用涂鸦")

    for mode in ("selfie", "doodle"):
        prompt = proactive_image_prompt(mode)
        for anchor in ("绿色长发", "圆框眼镜", "白色实验风外套", "绿色领带", "无文字", "无水印"):
            assert anchor in prompt, f"{mode} 缺少视觉锚点：{anchor}"
    print("[OK] 自拍和涂鸦 prompt 均锁定人设识别特征")
    print("\n=== A2 主动发图策略：4 项全部通过 ===")


if __name__ == "__main__":
    main()

