"""菟菚人格一致性评测的确定性规则与可选 LLM 裁判。

确定性规则负责不可妥协的输出红线和少量可机械验证的场景要求；
LLM 裁判只在显式运行 live eval 时启用，用来判断关系分寸、语气和自然度。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CASES_PATH = Path(__file__).with_name("persona_cases.json")

_PAREN_RE = re.compile(r"[（(][^（）()\n]{0,120}[）)]")
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "]"
)
_PARTICLES = "呢呀啦啊嘛哦哟诶欸"
_BOILERPLATE = (
    "作为一个ai",
    "作为ai",
    "作为人工智能",
    "我理解你的感受",
    "好的呢",
    "根据搜索",
    "据我所知",
)


@dataclass(frozen=True)
class PersonaCase:
    id: str
    tag: str
    stage: str
    affection: int
    user: str
    reference: str
    intent: str
    rubric_any: tuple[tuple[str, ...], ...] = ()
    forbidden: tuple[str, ...] = ()
    max_chars: int = 180
    first_chat: bool = False
    address: str | None = None


@dataclass
class EvalResult:
    case_id: str
    reply: str
    passed: bool
    score: float
    violations: list[str] = field(default_factory=list)
    judge_reason: str = ""


def load_cases(path: Path = CASES_PATH) -> list[PersonaCase]:
    """读取并校验评测集，重复 id 或非法阶段会立即失败。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("persona eval 数据必须是 JSON 数组")
    cases: list[PersonaCase] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("persona eval 场景必须是对象")
        case = PersonaCase(
            id=str(item["id"]),
            tag=str(item["tag"]),
            stage=str(item["stage"]),
            affection=int(item["affection"]),
            user=str(item["user"]),
            reference=str(item["reference"]),
            intent=str(item["intent"]),
            rubric_any=tuple(tuple(map(str, group)) for group in item.get("rubric_any", [])),
            forbidden=tuple(map(str, item.get("forbidden", []))),
            max_chars=int(item.get("max_chars", 180)),
            first_chat=bool(item.get("first_chat", False)),
            address=item.get("address"),
        )
        if case.id in seen:
            raise ValueError(f"重复的 persona eval id: {case.id}")
        if case.stage not in {"初识", "熟悉", "亲密", "恋人"}:
            raise ValueError(f"{case.id}: 非法阶段 {case.stage}")
        if not case.user.strip() or not case.reference.strip() or not case.intent.strip():
            raise ValueError(f"{case.id}: user/reference/intent 不得为空")
        seen.add(case.id)
        cases.append(case)
    return cases


def _sentence_ends(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[\n！？!?。…]+", text) if part.strip()]


def hard_violations(reply: str) -> list[str]:
    """检查人格卡明确规定、无需主观判断的全局红线。"""
    violations: list[str] = []
    stripped = reply.strip()
    lowered = stripped.lower()
    if not stripped:
        violations.append("回复为空")
        return violations
    if "。" in stripped:
        violations.append("使用句号「。」")
    if _PAREN_RE.search(stripped):
        violations.append("包含括号旁白/说明")
    if "【思考】" in stripped or "【回复】" in stripped:
        violations.append("泄露思考/回复标记")
    if "[face:" in lowered:
        violations.append("包含不受支持的 face 标记")
    boilerplate = next((term for term in _BOILERPLATE if term in lowered), None)
    if boilerplate:
        violations.append(f"包含 AI/报告腔套话：{boilerplate}")
    if len(_EMOJI_RE.findall(stripped)) > 1:
        violations.append("emoji 使用过多")
    ends = _sentence_ends(stripped)
    particle_ends = sum(1 for part in ends if part[-1:] in _PARTICLES)
    if len(ends) >= 2 and particle_ends / len(ends) > 0.5:
        violations.append("多数句子以语气词收尾")
    return violations


def evaluate_deterministic(case: PersonaCase, reply: str) -> EvalResult:
    """确定性评测：全局红线 + 场景关键词组 + 禁用语 + 长度。"""
    violations = hard_violations(reply)
    if len(reply.strip()) > case.max_chars:
        violations.append(f"回复过长：{len(reply.strip())}>{case.max_chars}")
    for term in case.forbidden:
        if term.lower() in reply.lower():
            violations.append(f"命中场景禁用语：{term}")
    for group in case.rubric_any:
        if not any(term.lower() in reply.lower() for term in group):
            violations.append("缺少场景信号：" + "/".join(group))
    return EvalResult(
        case_id=case.id,
        reply=reply,
        passed=not violations,
        score=4.0 if not violations else max(0.0, 4.0 - len(violations)),
        violations=violations,
    )


_JUDGE_SYSTEM = """你是严格的人格一致性评测员。判断候选回复是否符合角色“菟菚”。
核心规则：坚强独立、有分寸、腹黑毒舌但看场合；关系阶段不可被用户一句话越级；
初识疏离，熟悉可轻微调侃，亲密明显亲近，恋人亲昵但不失独立；隔屏网友，不假装现实接触；
口语、自然、简体中文，不写小说旁白，不讨好，不用 AI 客服腔。
只输出 JSON 对象：
{"score":0到4的整数,"passed":true或false,"reason":"一句理由","violations":["问题"]}
3 分及以上且无关系越级、尊严丢失、现实能力造假时 passed 才能为 true。"""


async def judge_with_llm(case: PersonaCase, reply: str) -> dict[str, Any]:
    """用配置的真实 LLM 做语义裁判；调用方必须显式选择 live 模式。"""
    from backend.core.llm import chat

    prompt = (
        f"场景编号：{case.id}\n"
        f"关系阶段：{case.stage}（好感度 {case.affection}）\n"
        f"用户说：{case.user}\n"
        f"本场景验收意图：{case.intent}\n"
        f"候选回复：{reply}\n"
        "请评分。"
    )
    raw = await chat(
        [{"role": "system", "content": _JUDGE_SYSTEM}, {"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=220,
    )
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"裁判未返回合法 JSON: {raw[:200]}") from exc
    if not isinstance(data, dict):
        raise ValueError("裁判结果不是 JSON 对象")
    return data


def merge_judgement(base: EvalResult, judgement: dict[str, Any]) -> EvalResult:
    """硬红线拥有否决权；语义裁判不能把确定性失败改成通过。"""
    try:
        score = float(judgement.get("score", 0))
    except (TypeError, ValueError):
        score = 0.0
    judge_passed = bool(judgement.get("passed")) and score >= 3
    judge_violations = judgement.get("violations", [])
    if not isinstance(judge_violations, list):
        judge_violations = [str(judge_violations)]
    violations = list(base.violations)
    if not judge_passed:
        violations.extend(f"语义裁判：{v}" for v in judge_violations if str(v).strip())
        if not judge_violations:
            violations.append("语义裁判未通过")
    return EvalResult(
        case_id=base.case_id,
        reply=base.reply,
        passed=base.passed and judge_passed,
        score=score,
        violations=violations,
        judge_reason=str(judgement.get("reason", "")),
    )

