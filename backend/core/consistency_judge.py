# -*- coding: utf-8 -*-
"""一致性判定器（岗位一/二/三的公共基建）：LLM-in-the-loop 的增量一致性决策。

机制源自 Mem0 换岗方案（2026-09-30 拍板）：把「每条新知识 vs 现有同主题知识
→ ADD / UPDATE / DELETE / NOOP」的黑盒决策机制移植到菟菚，但按四条铁律重造——

1. 权威表落盘：决策结果进 SQLite（向量永远只是可重建索引）；
2. LLM 只出判定不出文本：action + target_id + ≤50 字 reason，文本本体永远
   来自输入原文或确定性模板——Mem0 实测的英文改写/日期幻觉在此制度上不可能；
3. 决策即事件：每次判定写 append-only 日志（可审计可撤销）；
4. fail-open：判定失败/超时/非法输出一律降级为「add」（= 现状行为，宁可
   并存不可丢失）。

首期消费方：岗位一（知识观点冲突合并，judge_opinion）。岗位二（画像演变）、
岗位三（她的自述）复用同一模式各自带提示词。
"""
from __future__ import annotations

import json
import re

from .log import logger

_ACTIONS = {"add", "supersede", "corroborate", "conflict"}

_OPINION_JUDGE_PROMPT = """你是知识库的一致性判定器。一条「新观点」即将入库，下面是库中与它最相关的现有观点。
判断新观点与现有观点的关系，四选一：

- "add"：与现有观点无关或无实质交集 → 直接新增；
- "supersede"：新观点推翻/修正了某条现有观点（同一主题、结论相反或更新）→ 新增并指明被取代者；
- "corroborate"：新观点与某条现有观点结论一致（互相佐证）→ 不新增，指明被佐证者；
- "conflict"：矛盾但无法判定谁新谁旧 → 正常新增（由调用方另行处理）。

判定标准：只看结论层面的支持/冲突/无关；换个说法表达同一结论算 corroborate；
互补信息（不同主题）算 add。拿不准一律 add。target_id 必须从现有观点里选。

现有观点：
{existing}

新观点：{new_stance}

只输出 JSON：{{"action": "add|supersede|corroborate|conflict", "target_id": 整数或null, "reason": "不超过50字的判定理由"}}"""


def _parse_verdict(resp: str, valid_ids: set[int]) -> dict:
    """解析判定输出；任何不合法（动作白名单外/target 不在候选集/解析失败）→ add。"""
    start, end = resp.find("{"), resp.rfind("}")
    data = json.loads(resp[start:end + 1]) if 0 <= start < end else {}
    action = str(data.get("action") or "").strip().lower()
    if action not in _ACTIONS:
        return {"action": "add", "target_id": None, "reason": f"非法动作 {action!r}，fail-open"}
    target = data.get("target_id")
    try:
        target = int(target) if target is not None else None
    except (TypeError, ValueError):
        target = None
    reason = re.sub(r"\s+", " ", str(data.get("reason") or "")).strip()[:120]
    if action in {"supersede", "corroborate"} and target not in valid_ids:
        return {"action": "add", "target_id": None,
                "reason": f"target {target!r} 不在候选集，fail-open；原判定 {action}：{reason}"}
    return {"action": action, "target_id": target, "reason": reason}


async def judge_opinion(new_stance: str, existing: list[dict]) -> dict:
    """岗位一：新知识观点 vs 现有相关观点的四路判定。

    existing 为 [{"id": ..., "stance": ..., "confidence": ...}]；空列表直接 add
    （无判定成本）。返回 {"action", "target_id", "reason"}；LLM 失败 fail-open。
    """
    if not existing:
        return {"action": "add", "target_id": None, "reason": "无相关现有观点"}
    existing_text = "\n".join(
        f"- [id={int(o['id'])}] {str(o['stance'])[:200]}" for o in existing
    )
    try:
        from .llm import chat

        resp = await chat(
            [
                {"role": "system", "content": _OPINION_JUDGE_PROMPT},
                {"role": "user",
                 "content": f"现有观点：\n{existing_text}\n\n新观点：{new_stance}"},
            ],
            temperature=0.2,
            max_tokens=200,
            task="judge",
            thinking=False,  # 小预算 JSON：思考段会吃光 max_tokens 致正文空
        )
        return _parse_verdict(resp, {int(o["id"]) for o in existing})
    except Exception as e:  # noqa: BLE001
        logger.warning("[判定器] 观点判定失败（fail-open=add）：{}", str(e)[:120])
        return {"action": "add", "target_id": None, "reason": f"判定失败 fail-open：{type(e).__name__}"}
