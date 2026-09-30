# -*- coding: utf-8 -*-
"""岗位三：她的自述记忆——人格长期一致性的矛盾检测与演绎素材。

背景：菟菚的记忆几乎全是「关于用户」的；她自己聊天时说过的关于自己的话
（作息/喜好/过去/立场）无处存储——三个月后她可能自相矛盾而毫无察觉，
人格一致性全靠上下文运气。

机制（用户拍板 2026-09-30：矛盾交人格演绎）：
- daily 批处理提取当日 assistant 消息中的**明确第一人称自述**（保守提取，
  上限 3 条/天，原文摘录不改写）；
- 入库前一致性判定：与同主题现有自述矛盾时 **conflict 双条并存**（不自动
  取代——「她记错自己」是有价值的人设素材）；
- 注入：用户消息与某矛盾组主题相关时，向行为层提示「她说过 A 又说过 B」，
  **如何演绎（承认记混/找补/装傻）由人格当下状态决定**；提示一次即 resolved，
  绝不反复盘问；
- 正典防线：提取 prompt 声明「不得提取与人设矛盾的自述」（首期提示级防线，
  正典级自动校验为后续增强——自行决策，见模块测试注释）。

namespace 固定 character_fiction：她的自述是虚构人格的一部分，但聊天交互
产物与 character_life_events（行程事件）分表存放。
"""
from __future__ import annotations

from datetime import date

from .log import logger

_TOPICS = ("作息", "饮食", "研究所", "过去经历", "喜好", "厌恶", "立场观点", "人际关系", "其他")

_EXTRACT_PROMPT = """你是人格一致性档案员。从「她」（助手）当天的消息里，提取**她关于自己的明确陈述**——
她亲口说出的、关于自己的偏好/习惯/过去/立场/生活的事实（如「我讨厌下雨」「我在研究所熬夜赶课题」）。

提取纪律（保守优先）：
- 只收第一人称明确自述；转述用户的话、对用户的共情复述、提问、玩笑拟态不算；
- 不与人设矛盾（她的基本设定以人设卡为准）；
- 原文摘录，不改写不润色（每条 ≤60 字，过长截取关键句）；
- 没有就输出空数组，宁缺勿滥。

topic 只能从这些里选：{topics}

她当天的消息：
{messages}

只输出 JSON：{{"statements": [{{"topic": "...", "content": "...", "msg_idx": 消息序号从0}}]}}（最多 3 条）"""


async def extract_her_statements(user_id: str, day: date, transcript_rows: list[dict]) -> int:
    """daily 第 8 件：提取→判定→落库。返回新增条数；失败静默（不影响批次）。"""
    from .features import flag

    if not flag("her_statements_enabled"):
        return 0
    assistant = [r for r in transcript_rows if r.get("role") == "assistant" and r.get("content")]
    if not assistant:
        return 0
    numbered = "\n".join(f"[{i}] {r['content'][:200]}" for i, r in enumerate(assistant))
    try:
        from .llm import chat

        resp = await chat(
            [
                {"role": "system",
                 "content": _EXTRACT_PROMPT.format(topics="/".join(_TOPICS), messages=numbered)},
                {"role": "user", "content": "提取她今天的自述。"},
            ],
            temperature=0.2,
            max_tokens=300,
            task="batch_other",
            thinking=False,  # 小预算 JSON：思考段会吃光 max_tokens 致正文空
        )
    except Exception:
        logger.warning("[自述] {} 提取失败（跳过当日）", user_id)
        return 0
    start, end = resp.find("{"), resp.rfind("}")
    import json as _json

    try:
        data = _json.loads(resp[start:end + 1]) if 0 <= start < end else {}
    except ValueError:
        return 0
    items = data.get("statements") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return 0

    candidates: list[tuple[str, str, int | None]] = []
    for it in items[:3]:
        if not isinstance(it, dict):
            continue
        topic = str(it.get("topic") or "其他").strip()
        if topic not in _TOPICS:
            topic = "其他"
        content = str(it.get("content") or "").strip()[:60]
        if not content:
            continue
        try:
            msg_id = assistant[int(it.get("msg_idx"))]["id"] if it.get("msg_idx") is not None else None
        except (ValueError, IndexError, KeyError, TypeError):
            msg_id = None
        candidates.append((topic, content, msg_id))
    if not candidates:
        return 0

    # 一致性判定（一次批量）：与同主题 active 陈述比对
    conflicts: dict[int, dict] = {}  # candidate 序号 -> {"with_id", "reason"}
    try:
        from .consistency_judge import judge_her_statements

        existing = active_by_topic(user_id, {t for t, _, _ in candidates})
        if existing:
            verdicts = await judge_her_statements(existing, [c[1] for c in candidates])
            conflicts = {v["idx"]: v for v in verdicts}
    except Exception:  # noqa: BLE001
        conflicts = {}  # fail-open：全部按无矛盾入库（漏检比误判安全）

    from .userdb import db

    added = 0
    next_group = db.next_conflict_group(user_id)
    for idx, (topic, content, msg_id) in enumerate(candidates):
        v = conflicts.get(idx)
        if v is not None:
            group = db.add_her_statement(user_id, topic, content, msg_id,
                                         conflict_group=next_group)
            if group is not None:
                db.set_conflict_group(user_id, v["with_id"], next_group)
                logger.info("[自述] {} 记录矛盾组 {}: {!r} vs #{}", user_id,
                            next_group, content[:30], v["with_id"])
            added += 1
        else:
            if db.add_her_statement(user_id, topic, content, msg_id) is not None:
                added += 1
    return added


def active_by_topic(user_id: str, topics: set[str]) -> list[dict]:
    from .userdb import db

    return [
        s for s in db.list_her_statements(user_id, active_only=True)
        if s["topic"] in topics
    ]


def pending_conflict_hint(user_id: str, text: str) -> str | None:
    """注入查询：用户消息与某未交付矛盾组话题相关（bigram 交集 ≥2，取
    pending_thoughts._RELEVANCE_MIN=1 的同法再收紧一档——1 会命中单个偶共现
    字，矛盾提示比心事注入更敏感、误触发成本更高）→ 返回演绎提示；同时把
    该组标 resolved（一次交付，绝不反复盘问——重复提示同一矛盾=机械感）。"""
    from .userdb import _bigrams, db

    groups: dict[int, list[dict]] = {}
    for s in db.list_her_statements(user_id, active_only=True):
        if s["conflict_group"]:
            groups.setdefault(s["conflict_group"], []).append(s)
    if not groups:
        return None
    terms = _bigrams(text)
    for group, stmts in sorted(groups.items()):
        for s in stmts:
            if len(terms & _bigrams(s["content"])) >= 2:
                db.resolve_conflict_group(user_id, group)
                pair = " vs ".join(f"「{x['content'][:40]}」（{x['created_at'][:10]}）"
                                   for x in sorted(stmts, key=lambda x: x["created_at"]))
                return (
                    f"〔自我一致性提示·仅本次〕关于「{stmts[0]['topic']}」，你之前说过互相矛盾的话："
                    f"{pair}。你自己可能没意识到。如何接（承认记混、找补个理由、还是装傻）"
                    "由你当下的状态和关系分寸决定——不要主动向用户『汇报』这个矛盾。"
                )
    return None
