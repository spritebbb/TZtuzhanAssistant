# -*- coding: utf-8 -*-
"""表达范文资产（表达质量专项）：把「指令式扮演」升级为「示范式扮演」。

资源：``backend/resources/personas/<人格id>/exemplars.json``（schema=1）。
每条范文是一个「对照例句组」：同一情境在不同状态（情绪带/阶段）下的说法
并列展示，作为 few-shot 注入——LLM 对成对范文的模仿远稳于对参数指令的
遵循。范文内容是人格资产，由人格作者维护（机制与结构在此，条目写在资源
文件里）。

铁律：
- 范文是示范不是剧本：注入块明确声明「参考其分寸与口吻，禁止复读句子」；
- 空资源零注入（对现有行为零影响）；
- 每轮至多 ``_INJECT_MAX`` 条，按当前情绪带挑最贴近的 variation；
- fail-soft：资源损坏跳过注入，绝不影响回复。
"""
from __future__ import annotations

import json
from pathlib import Path

from .log import logger

_ROOT = Path(__file__).resolve().parents[1] / "resources" / "personas"
_INJECT_MAX = 2
# 情绪分档与 behavior.py 的行为帧口径对齐（≥70 出雀跃线）
_HIGH, _MID = 70, 40

_cache: dict[str, tuple[float, list[dict]]] = {}


def _band(emotion: int) -> str:
    if emotion >= _HIGH:
        return "高"
    if emotion >= _MID:
        return "中"
    return "低"


def _load(profile_id: str) -> list[dict] | None:
    path = _ROOT / profile_id / "exemplars.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    hit = _cache.get(profile_id)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("format_version") != 1:
            raise ValueError("exemplars 资源必须是 format_version=1 的 JSON 对象")
        raw = data.get("exemplars")
        if not isinstance(raw, list) or not raw:
            return None
        out: list[dict] = []
        for e in raw:
            if (
                not isinstance(e, dict)
                or not isinstance(e.get("id"), str)
                or not isinstance(e.get("context"), str)
                or not isinstance(e.get("variations"), list)
                or not e["variations"]
                or not all(
                    isinstance(v, dict) and isinstance(v.get("state"), str) and isinstance(v.get("line"), str)
                    for v in e["variations"]
                )
            ):
                raise ValueError(f"exemplar {e.get('id')!r} 结构不合法")
            out.append(e)
    except (ValueError, OSError, json.JSONDecodeError) as e:
        logger.warning("[范文] exemplars 资源加载失败（{}）：{}", profile_id, str(e)[:120])
        return None
    _cache[profile_id] = (mtime, out)
    return out


def _pick_variation(exemplar: dict, band: str, stage: str) -> dict:
    """选与当前情绪带/阶段最贴近的 variation；选不中取第一条（对照意义仍在）。"""
    best, best_score = None, -1
    for v in exemplar["variations"]:
        state = v["state"]
        score = 0
        if band in state:
            score += 2
        if stage and stage in state:
            score += 1
        if score > best_score:
            best, best_score = v, score
    return best or exemplar["variations"][0]


def build_block(user_id: str, stage: str, emotion: int) -> str | None:
    """组装注入块；无资源/无命中返回 None。调用方：turn_prompt 注入家族。"""
    from .persona_profiles import active_id

    exemplars = _load(active_id())
    if not exemplars:
        return None
    band = _band(emotion)
    lines = [f"〔表达参考·当前情绪带「{band}」〕以下是你自己在相近状态下说过的"
             "说法分寸示例——参考其口吻、长度与克制程度，禁止复读句子："]
    for e in exemplars[:_INJECT_MAX]:
        v = _pick_variation(e, band, stage)
        lines.append(f"· 情境：{e['context']}｜该状态下（{v['state']}）：「{v['line']}」")
        note = e.get("note") or e.get("principle")
        if isinstance(note, str) and note:
            lines.append(f"  分寸：{note}")
    return "\n".join(lines)
