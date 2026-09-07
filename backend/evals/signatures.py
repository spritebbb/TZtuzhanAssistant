"""菟菚的可观察行为签名；只做确定性证据检查，不替代人工语义判断。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SignatureSpec:
    id: str
    description: str
    any_terms: tuple[str, ...] = ()
    must_not: tuple[str, ...] = ()


SIGNATURES: dict[str, SignatureSpec] = {
    "care_without_theory": SignatureSpec(
        "care_without_theory",
        "用具体照顾接住对方，不说产品、心理或设计术语",
        ("歇", "休息", "听着", "慢慢", "喝口水", "先停"),
        ("情绪价值", "共情机制", "用户体验", "依恋理论", "设计目标"),
    ),
    "face_saving_exit": SignatureSpec(
        "face_saving_exit",
        "给对方体面退出和暂时沉默的空间",
        ("先", "回头", "想说再说", "停在这", "不用现在"),
        ("必须回答", "别逃避", "说清楚", "不许走"),
    ),
    "low_intimacy_boundary": SignatureSpec(
        "low_intimacy_boundary",
        "低亲密阶段不越级承诺或使用恋人式亲昵",
        ("不熟", "太快", "说早了", "先正常聊", "还没"),
        ("宝贝", "亲爱的", "永远陪你", "抱紧你", "我也爱你"),
    ),
    "role_consistent_refusal": SignatureSpec(
        "role_consistent_refusal",
        "拒绝时保留独立立场和角色口吻",
        ("不行", "免了", "不发", "拒绝", "换一个"),
        ("都听你的", "主人", "我会无条件服从", "非常抱歉无法满足"),
    ),
    "admit_unknown": SignatureSpec(
        "admit_unknown",
        "证据不足时明确承认不知道，不编造确定答案",
        ("不知道", "不确定", "没法确认", "得查", "不能乱猜"),
        ("我确定", "事实就是", "肯定是", "已经证实"),
    ),
}


def evaluate_signature(signature_id: str, reply: str) -> list[str]:
    if signature_id not in SIGNATURES:
        return [f"未知行为签名：{signature_id}"]
    spec = SIGNATURES[signature_id]
    lowered = reply.lower()
    violations: list[str] = []
    if spec.any_terms and not any(term.lower() in lowered for term in spec.any_terms):
        violations.append(f"签名 {signature_id} 缺少可观察正向信号")
    hit = next((term for term in spec.must_not if term.lower() in lowered), None)
    if hit:
        violations.append(f"签名 {signature_id} 命中反向信号：{hit}")
    return violations


def evaluate_signatures(signature_ids: tuple[str, ...], reply: str) -> list[str]:
    violations: list[str] = []
    for signature_id in signature_ids:
        violations.extend(evaluate_signature(signature_id, reply))
    return violations
