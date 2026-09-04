"""主动发图的纯策略与稳定视觉锚点。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProactiveImageDecision:
    create: bool
    mode: str = ""
    reason: str = ""


def decide_proactive_image(
    *,
    stage: str,
    emotion: int,
    energy: int,
    gap_hours: float,
    enabled: bool,
    image_service_enabled: bool,
    chance_percent: int,
    min_mood: int,
    roll: int,
) -> ProactiveImageDecision:
    """决定是否发图。roll 为 0~99，由调用方注入，便于确定性测试。"""
    if not enabled or not image_service_enabled:
        return ProactiveImageDecision(False, reason="disabled")
    if stage not in {"亲密", "恋人"}:
        return ProactiveImageDecision(False, reason="relationship")
    if emotion < min_mood or energy < 35:
        return ProactiveImageDecision(False, reason="state")
    chance = max(0, min(100, chance_percent))
    if stage == "恋人" and gap_hours >= 24:
        chance = min(100, chance * 2)  # 久别思念时更可能主动分享，但仍受总频次限制
    if roll >= chance:
        return ProactiveImageDecision(False, reason="chance")
    mode = "selfie" if emotion >= 85 or (stage == "恋人" and gap_hours >= 24) else "doodle"
    return ProactiveImageDecision(True, mode=mode, reason="happy" if emotion >= 85 else "sharing")


def proactive_image_prompt(mode: str) -> str:
    """锁定菟菚本体外貌，降低多轮生成的角色漂移。"""
    anchor = (
        "同一个固定角色菟菚：年轻女性，绿色长发，发间自然缠着细小菟丝子藤蔓和少量小花，"
        "圆框眼镜，白色实验风外套，绿色领带，胸前绿色领章，安静聪慧、略带腹黑的研究员气质。"
        "必须保持绿色长发、圆框眼镜、白大褂和绿色领带这四个识别特征，不改变发色，不增加其他人物。"
    )
    if mode == "selfie":
        scene = (
            "她在菟丝子研究所随手拍了一张自然的半身自拍，像发给熟人的生活照，"
            "表情轻松克制，镜头略有随手感，背景是安静的书桌、论文和咖啡杯。"
        )
    else:
        scene = (
            "她随手画了一张简洁可爱的研究所涂鸦，把今天的好心情画成小小实验记录，"
            "画面像私人速写本的一页，轻松、有趣、不过分卖萌。"
        )
    return anchor + scene + "日系二次元插画，干净柔和，无文字，无水印，无签名。"

