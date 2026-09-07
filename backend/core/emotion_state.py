# -*- coding: utf-8 -*-
"""P1-03 离散情绪状态：并发少量命名情绪的确定性消退与修复。

契约（docs/Zcode技术指导.md §5 P1-03 / §14.3 / docs/adr/M9-emotion-state.md）：
- 情绪集合 joy/sadness/anger/hurt/anxiety/calm/tenderness，intensity∈[0,1]，
  同时最多 3 条；calm 是无主态回退，不入列；
- 消退 ``i(now)=i0·2^(−Δh/half_life)``，i<0.05 或过 expires_at 移除；
  advance 是纯函数，读取只做投影不落库；
- 显式道歉/安抚按已验证来源 −0.2（每来源终身一次）；
- 旧 mood 0–100 与立绘标签不动；无离散情绪时消费方完全走旧逻辑；
- 参数集中在 EMOTION_RULES（rule_version），变更须重跑人格 eval。

本模块不做网络调用；LLM 只能产出候选，入账由确定性 apply 完成。
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime

from .userdb import db

KV_EMOTIONS = "state:emotions"
KV_REPAIRS = "state:emotion_repairs"
FORMAT_VERSION = 1

EMOTIONS = ("joy", "sadness", "anger", "hurt", "anxiety", "calm", "tenderness")
NEGATIVE_EMOTIONS = ("anger", "hurt", "anxiety")
# 五轴态度基础向量（14.3 可审初值；P3-01 全矩阵消费）
ATTITUDE_AXES = ("patience", "humor", "guard", "directness", "followup")
ATTITUDE_VECTORS: dict[str, tuple[float, float, float, float, float]] = {
    "calm":      (0.60, 0.30, 0.30, 0.50, 0.30),
    "anger":     (0.25, 0.10, 0.80, 0.85, 0.10),
    "hurt":      (0.40, 0.10, 0.70, 0.35, 0.15),
    "joy":       (0.75, 0.70, 0.20, 0.60, 0.50),
    "sadness":   (0.40, 0.10, 0.45, 0.40, 0.15),
    "anxiety":   (0.45, 0.10, 0.65, 0.55, 0.25),
    "tenderness": (0.80, 0.35, 0.15, 0.50, 0.35),
}

EMOTION_RULES = {
    "rule_version": 1,
    "half_life_hours": {"joy": 6, "sadness": 12, "anger": 4, "hurt": 12,
                        "anxiety": 6, "tenderness": 8},
    "remove_below": 0.05,
    "max_active": 3,
    "repair_delta": 0.2,
    "expires_after_half_lives": 5,
}

TARGETS = ("diffuse", "self", "relation")


@dataclass
class EmotionItem:
    emotion: str
    intensity: float
    cause_type: str = ""
    cause_id: str = ""
    target: str = "diffuse"
    onset_at: str = ""
    updated_at: str = ""
    expires_at: str = ""
    confidence: float = 1.0


def _now_iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now()).isoformat(timespec="seconds")


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


# ---- 纯函数：确定性推进 ----

def advance_emotions(items: list[EmotionItem], now: datetime) -> list[EmotionItem]:
    """按绝对时间衰减并裁剪：过期/低于阈值的移除，超量按强度+稳定 id 截断。"""
    alive: list[EmotionItem] = []
    for item in items:
        if item.emotion not in EMOTIONS or item.emotion == "calm":
            continue
        try:
            elapsed_h = max(0.0, (now - _parse(item.updated_at)).total_seconds() / 3600.0)
        except (ValueError, TypeError):
            elapsed_h = 0.0
        half = EMOTION_RULES["half_life_hours"].get(item.emotion, 6)
        intensity = float(item.intensity) * math.pow(2.0, -elapsed_h / half)
        if intensity < EMOTION_RULES["remove_below"]:
            continue
        if item.expires_at:
            try:
                if now > _parse(item.expires_at):
                    continue
            except (ValueError, TypeError):
                pass
        alive.append(EmotionItem(
            emotion=item.emotion,
            intensity=round(min(1.0, intensity), 4),
            cause_type=item.cause_type,
            cause_id=item.cause_id,
            target=item.target if item.target in TARGETS else "diffuse",
            onset_at=item.onset_at,
            updated_at=_now_iso(now),  # 推进后的时间锚：分步推进与一步等价
            expires_at=item.expires_at,
            confidence=item.confidence,
        ))
    alive.sort(key=lambda i: (-i.intensity, i.emotion))
    return alive[: EMOTION_RULES["max_active"]]


# ---- kv 读写（kv_store 为 (user_id, key) 二元组模型，走 userdb 权威入口）----

def _kv_get_json(user_id: str, key: str) -> dict | None:
    from .userdb import kv_get

    raw = kv_get(user_id, key)
    if raw is None:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _kv_set_json(user_id: str, key: str, value: dict) -> None:
    from .userdb import kv_set

    kv_set(user_id, key, json.dumps(value, ensure_ascii=False))


def load_emotions(user_id: str, *, now: datetime | None = None) -> list[EmotionItem]:
    """读取当前活跃情绪（投影，不落库——读取不反复扣减）。"""
    data = _kv_get_json(user_id, KV_EMOTIONS)
    if not data or data.get("format_version") != FORMAT_VERSION:
        return []
    raw = data.get("items", [])
    items = []
    for row in raw:
        try:
            items.append(EmotionItem(
                emotion=str(row["emotion"]),
                intensity=float(row["intensity"]),
                cause_type=str(row.get("cause_type", "")),
                cause_id=str(row.get("cause_id", "")),
                target=str(row.get("target", "diffuse")),
                onset_at=str(row.get("onset_at", "")),
                updated_at=str(row.get("updated_at", "")),
                expires_at=str(row.get("expires_at", "")),
                confidence=float(row.get("confidence", 1.0)),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return advance_emotions(items, now or datetime.now())


def save_emotions(user_id: str, items: list[EmotionItem], *, now: datetime | None = None) -> None:
    """保存有界快照（已在 now 时刻推进过的列表）。"""
    _kv_set_json(user_id, KV_EMOTIONS, {
        "format_version": FORMAT_VERSION,
        "updated": _now_iso(now),
        "items": [asdict(i) for i in items],
    })


def apply_emotion(
    user_id: str, emotion: str, intensity: float, *,
    cause_type: str = "", cause_id: str = "",
    target: str = "diffuse", confidence: float = 1.0,
    now: datetime | None = None,
) -> list[EmotionItem]:
    """入账一条情绪（确定性入口）：合并同情绪条目后裁剪并落库。

    calm 不入列（无主态回退）；同 emotion 条目取强度较大者并刷新时间锚。
    """
    now = now or datetime.now()
    if emotion not in EMOTIONS or emotion == "calm":
        return load_emotions(user_id, now=now)
    intensity = max(0.0, min(1.0, float(intensity)))
    current = advance_emotions(load_emotions(user_id, now=now), now)
    half = EMOTION_RULES["half_life_hours"][emotion]
    fresh = EmotionItem(
        emotion=emotion,
        intensity=intensity,
        cause_type=cause_type[:40],
        cause_id=str(cause_id)[:80],
        target=target if target in TARGETS else "diffuse",
        onset_at=_now_iso(now),
        updated_at=_now_iso(now),
        expires_at=_now_iso(datetime.fromtimestamp(
            now.timestamp() + half * EMOTION_RULES["expires_after_half_lives"] * 3600
        )),
        confidence=max(0.0, min(1.0, float(confidence))),
    )
    merged: list[EmotionItem] = []
    replaced = False
    for item in current:
        if item.emotion == emotion:
            merged.append(fresh if fresh.intensity >= item.intensity else EmotionItem(
                emotion=item.emotion, intensity=item.intensity,
                cause_type=item.cause_type, cause_id=item.cause_id,
                target=item.target, onset_at=item.onset_at,
                updated_at=_now_iso(now),
                expires_at=item.expires_at, confidence=item.confidence,
            ))
            replaced = True
        else:
            merged.append(item)
    if not replaced:
        merged.append(fresh)
    merged = advance_emotions(merged, now)
    save_emotions(user_id, merged, now=now)
    return merged


def apply_repair(user_id: str, *, cause_type: str, cause_id: str,
                 now: datetime | None = None) -> list[EmotionItem]:
    """显式道歉/安抚：负向情绪各 −0.2，同一来源终身只一次。"""
    now = now or datetime.now()
    repair_key = f"{cause_type[:40]}:{str(cause_id)[:80]}"
    data = _kv_get_json(user_id, KV_REPAIRS) or {"format_version": 1, "repaired": []}
    repaired = list(data.get("repaired", []))
    if repair_key in repaired:
        return load_emotions(user_id, now=now)
    delta = EMOTION_RULES["repair_delta"]
    items = advance_emotions(load_emotions(user_id, now=now), now)
    for item in items:
        if item.emotion in NEGATIVE_EMOTIONS:
            item.intensity = round(max(0.0, item.intensity - delta), 4)
    items = [i for i in items if i.intensity >= EMOTION_RULES["remove_below"]]
    save_emotions(user_id, items, now=now)
    repaired.append(repair_key)
    _kv_set_json(user_id, KV_REPAIRS, {"format_version": 1, "repaired": repaired})
    return items


def active_emotions_map(user_id: str, *, now: datetime | None = None) -> dict[str, float]:
    """{emotion: intensity}，供 P1-01 状态视图等只读消费。"""
    return {i.emotion: i.intensity for i in load_emotions(user_id, now=now)}


# ---- 态度摘要（P3-01 种子；本切片保证确定性可测）----

def attitude_summary(
    items: list[EmotionItem] | dict[str, float], *,
    trust: float = 50.0, intimacy: float = 50.0,
    low_energy_or_late: bool = False,
) -> dict[str, float]:
    """强度加权基础向量 + 信任/亲密/时段修正，全轴 clamp [0,1]。

    trust 每 50 点最多降 guard 0.2；intimacy 同理升柔软（降 guard、升 patience）；
    深夜/低精力只能降 followup，不升亲密（14.3 边界规则）。
    """
    if isinstance(items, dict):
        weights = {k: float(v) for k, v in items.items() if k in ATTITUDE_VECTORS}
    else:
        weights = {i.emotion: i.intensity for i in items if i.emotion in ATTITUDE_VECTORS}
    total = sum(weights.values())
    axes = {axis: 0.0 for axis in ATTITUDE_AXES}
    if total > 0:
        for name, weight in weights.items():
            vector = ATTITUDE_VECTORS[name]
            for idx, axis in enumerate(ATTITUDE_AXES):
                axes[axis] += weight * vector[idx] / total
    # 两维独立、以 50 为中点修正：信任主要影响防备/耐心，亲密主要影响
    # 柔软与追问意愿。低亲密不会被高信任抵消成越级亲昵。
    trust_offset = max(-1.0, min(1.0, (float(trust) - 50.0) / 50.0))
    intimacy_offset = max(-1.0, min(1.0, (float(intimacy) - 50.0) / 50.0))
    axes["guard"] -= trust_offset * 0.15 + intimacy_offset * 0.05
    axes["patience"] += trust_offset * 0.05 + intimacy_offset * 0.10
    axes["followup"] += intimacy_offset * 0.10
    if low_energy_or_late:
        axes["followup"] = max(0.0, axes["followup"] - 0.2)
    return {axis: round(min(1.0, max(0.0, value)), 4) for axis, value in axes.items()}


def attitude_instruction(
    items: list[EmotionItem] | list[dict] | dict[str, float], *,
    trust: float = 50.0, intimacy: float = 50.0,
    low_energy_or_late: bool = False,
) -> str:
    """把完整态度矩阵编译成短行为片段，不泄露轴名、数值或心理理论。"""
    if isinstance(items, dict):
        emotions = {
            str(name): max(0.0, min(1.0, float(intensity)))
            for name, intensity in items.items() if name in ATTITUDE_VECTORS
        }
    else:
        emotions = {}
        for raw in items:
            if isinstance(raw, dict):
                name = str(raw.get("emotion", ""))
                intensity = float(raw.get("intensity", 0) or 0)
            else:
                name, intensity = raw.emotion, raw.intensity
            if name in ATTITUDE_VECTORS:
                emotions[name] = max(emotions.get(name, 0.0), max(0.0, min(1.0, intensity)))
    if not emotions:
        return ""

    axes = attitude_summary(
        emotions, trust=trust, intimacy=intimacy,
        low_energy_or_late=low_energy_or_late,
    )
    negative = any(emotions.get(name, 0.0) >= 0.4 for name in NEGATIVE_EMOTIONS)
    tender = emotions.get("tenderness", 0.0) >= 0.4
    clauses: list[str] = []
    if negative and tender:
        clauses.append("委屈或戒备与关心可以同时流露，不要强行抹平其中一种")
    elif axes["guard"] >= 0.58:
        clauses.append("先保留一点距离，立场说清即可")

    if axes["directness"] >= 0.68:
        clauses.append("表达直接、具体，不绕弯也不攻击对方")
    if axes["patience"] <= 0.42:
        clauses.append("句子短些，允许自然停住，但不要敷衍")
    if axes["humor"] <= 0.20:
        clauses.append("收住调侃")
    elif axes["humor"] >= 0.55 and not negative:
        clauses.append("可以带一点轻微调侃，别连续抖机灵")

    if axes["followup"] <= 0.25:
        clauses.append("不要为了续聊硬追问")
    elif axes["followup"] >= 0.48:
        clauses.append("确有帮助时最多追问一句")

    if float(intimacy) < 50:
        clauses.append("关心保持当前关系分寸，不使用越级昵称或暧昧承诺")
    clauses.append("始终保留菟菚直白、克制又会具体关心人的说话方式")
    return "；".join(dict.fromkeys(clauses)) + "。"


def emotion_hint(items: list[EmotionItem] | list[dict]) -> str:
    """活跃情绪的自然语言摘要（供行为帧；不出现情绪学术语与数值）。"""
    if not items:
        return ""
    parts = []
    for raw in items:
        if isinstance(raw, dict):
            name = str(raw.get("emotion", ""))
            intensity = float(raw.get("intensity", 0) or 0)
        else:
            name, intensity = raw.emotion, raw.intensity
        if name == "anger" and intensity >= 0.4:
            parts.append("你此刻还有点火气")
        elif name == "hurt" and intensity >= 0.4:
            parts.append("你心里有点不得劲")
        elif name == "tenderness" and intensity >= 0.4:
            parts.append("你心里此刻有点软")
        elif name == "joy" and intensity >= 0.5:
            parts.append("你现在心情挺好")
        elif name == "sadness" and intensity >= 0.5:
            parts.append("你有点提不起劲")
        elif name == "anxiety" and intensity >= 0.5:
            parts.append("你有点心神不宁")
    return "；".join(parts)
