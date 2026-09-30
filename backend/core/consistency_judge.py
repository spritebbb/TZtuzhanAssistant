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


_PROFILE_EVOLUTION_PROMPT = """你是用户画像的演变判定器。下面是「现有画像条目」和本批「新提炼候选」。
多数候选是新增信息（op=add，无需列出）；你只找**演变**：某条候选明确显示用户的某个长期特征
**发生了真实变化**，与某条现有条目矛盾（如「爱喝咖啡」→「戒了咖啡」）。对每处演变输出一个
replace 计划：旧条目归档、新候选顶替。

判定纪律：
- 只在变化有明确依据时判 replace：对话中明确说了改变（"我戒了/我换成了/现在改"）；
  一次性的情绪、临时状态、偶尔行为不算演变；
- 互补信息（新增喜好而非替代）、换个说法表达同一事实，都不是 replace；
- 旧条目标了 [manual]（用户手写）的绝不 replace；
- 没有演变就输出空数组。

现有画像条目：
{existing}

新提炼候选：
{candidates}

只输出 JSON：{{"replaces": [{{"old_id": 整数, "candidate_idx": 整数(候选序号从0), "reason": "不超过50字"}}]}}"""


async def judge_profile_evolution(existing: list[dict], candidates: list[str]) -> list[dict]:
    """岗位二：一批画像候选 vs 现有画像的演变判定（一次调用判全批）。

    existing: [{"id", "content", "source", ...}]（调用方应只传 source=llm 的）；
    candidates: 本批候选文本列表（序号即 candidate_idx）。
    返回 [{"old_id", "candidate_idx", "reason"}]；失败/无演变返回 []（fail-open：
    全部按普通新增走，与旧行为一致）。
    """
    if not existing or not candidates:
        return []
    existing_text = "\n".join(
        f"- [old_id={int(e['id'])}]{'[manual]' if str(e.get('source')) == 'manual' else ''} "
        f"{str(e['content'])[:80]}" for e in existing
    )
    cand_text = "\n".join(f"- [idx={i}] {c[:80]}" for i, c in enumerate(candidates))
    try:
        from .llm import chat

        resp = await chat(
            [
                {"role": "system", "content": _PROFILE_EVOLUTION_PROMPT},
                {"role": "user",
                 "content": f"现有画像条目：\n{existing_text}\n\n新提炼候选：\n{cand_text}"},
            ],
            temperature=0.2,
            max_tokens=300,
            task="judge",
            thinking=False,  # 小预算 JSON：思考段会吃光 max_tokens 致正文空
        )
        start, end = resp.find("{"), resp.rfind("}")
        data = json.loads(resp[start:end + 1]) if 0 <= start < end else {}
        plans = data.get("replaces") if isinstance(data, dict) else None
        if not isinstance(plans, list):
            return []
        valid_old = {int(e["id"]) for e in existing}
        out: list[dict] = []
        for p in plans:
            if not isinstance(p, dict):
                continue
            try:
                old_id, idx = int(p.get("old_id")), int(p.get("candidate_idx"))
            except (TypeError, ValueError):
                continue
            if old_id in valid_old and 0 <= idx < len(candidates):
                out.append({"old_id": old_id, "candidate_idx": idx,
                            "reason": re.sub(r"\s+", " ", str(p.get("reason") or ""))[:120]})
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("[判定器] 画像演变判定失败（fail-open=全普通新增）：{}", str(e)[:120])
        return []


_HER_STATEMENT_JUDGE_PROMPT = """她是同一个虚构人格。下面是「她已有的关于自己的陈述」（按主题分组标签标注）和
本批「新陈述」。找出**矛盾**：新陈述与某条同主题的现有陈述说的事实不能同时成立
（如「怕吵」vs「研究所很安静我喜欢在那补觉」）。

判定纪律：
- 只在真矛盾（同主题、事实不相容）时报；互补信息、不同角度、程度差异不算；
- 换说法表达同一事实不算矛盾；
- 拿不准不报（漏检安全，误报会制造不存在的人格裂缝）。

她已有的陈述：
{existing}

新陈述：
{candidates}

只输出 JSON：{{"conflicts": [{{"idx": 新陈述序号从0, "with_id": 现有陈述id整数, "reason": "不超过40字"}}]}}"""


async def judge_her_statements(existing: list[dict], candidates: list[str]) -> list[dict]:
    """岗位三：新自述 vs 现有同主题自述的矛盾判定（一次批量）。

    existing: [{"id", "topic", "content", ...}]（调用方按主题预过滤）。
    返回 [{"idx", "with_id", "reason"}]；失败/无矛盾返回 []（fail-open）。
    """
    if not existing or not candidates:
        return []
    existing_text = "\n".join(
        f"- [id={int(e['id'])}][主题:{e['topic']}] {str(e['content'])[:60]}" for e in existing
    )
    cand_text = "\n".join(f"- [idx={i}] {c[:60]}" for i, c in enumerate(candidates))
    try:
        from .llm import chat

        resp = await chat(
            [
                {"role": "system", "content": _HER_STATEMENT_JUDGE_PROMPT},
                {"role": "user",
                 "content": f"她已有的陈述：\n{existing_text}\n\n新陈述：\n{cand_text}"},
            ],
            temperature=0.2,
            max_tokens=250,
            task="judge",
            thinking=False,  # 小预算 JSON：思考段会吃光 max_tokens 致正文空
        )
        start, end = resp.find("{"), resp.rfind("}")
        data = json.loads(resp[start:end + 1]) if 0 <= start < end else {}
        plans = data.get("conflicts") if isinstance(data, dict) else None
        if not isinstance(plans, list):
            return []
        valid_ids = {int(e["id"]) for e in existing}
        out: list[dict] = []
        for p in plans:
            if not isinstance(p, dict):
                continue
            try:
                idx, with_id = int(p.get("idx")), int(p.get("with_id"))
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(candidates) and with_id in valid_ids:
                out.append({"idx": idx, "with_id": with_id,
                            "reason": re.sub(r"\s+", " ", str(p.get("reason") or ""))[:100]})
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("[判定器] 自述矛盾判定失败（fail-open=无矛盾入库）：{}", str(e)[:120])
        return []
