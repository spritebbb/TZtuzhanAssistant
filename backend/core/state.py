"""拟人状态机核心：菟菚的「内在状态」多维模型。

这是拟人核心层的基石。它把菟菚从「每轮对话失忆重来」变成「一个持续演化的存在」：
- 情绪（emotion）：0-100，当前心境，向基线回归 + 互动扰动 + 自然漂移
- 精力（energy）：0-100，疲劳会让她话少、懒散、不耐烦
- 好感度（affection）：0-100，沿用 users 表，仍是关系主轴
- 关系阶段（stage）：初识/熟悉/亲密/恋人，由好感度推导
- 情绪记忆（emotion_memory）：最近几次情绪冲击的短时残留，影响她「还在闹别扭/还开心着」

设计原则：
1. 纯业务逻辑，不依赖 FastAPI；只依赖 userdb 的 SQLite 读写。
2. 状态可持久化、可跨天连续（情绪记忆存 kv_store，其余在 users 表）。
3. 对外只暴露「读状态」和「应用一次演化」两个干净接口，让 perception/behavior 消费。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
import json
import re
from typing import Any

from .log import logger

# ---- 情绪状态（情绪值 → 名称 + 说话特征）----
EMOTION_LEVELS = (
    (0, "低落", "心情很差，有点烦闷，话少、直接，容易不耐烦"),
    (25, "平淡", "心情一般，不悲不喜，说话平静、有条理"),
    (45, "慵懒", "整个人懒懒的，提不起劲，说话简短随意"),
    (65, "开心", "心情不错，说话轻快，偶尔俏皮、爱开玩笑"),
    (85, "雀跃", "心情非常好，活跃、爱说话，想找人分享开心的事"),
)

# 关系阶段（与 affection.stage_of 保持一致，避免两套标准打架）
STAGE_THRESHOLDS = ((0, "初识"), (25, "熟悉"), (50, "亲密"), (75, "恋人"))


def emotion_label(value: int) -> tuple[str, str]:
    name, desc = EMOTION_LEVELS[0][1], EMOTION_LEVELS[0][2]
    for threshold, n, d in EMOTION_LEVELS:
        if value >= threshold:
            name, desc = n, d
    return name, desc


def stage_of(affection: int) -> str:
    label = STAGE_THRESHOLDS[0][1]
    for threshold, name in STAGE_THRESHOLDS:
        if affection >= threshold:
            label = name
    return label


@dataclass
class AgentState:
    """菟菚某个时刻的完整内在状态（纯数据，无副作用）。"""

    emotion: int = 60          # 情绪 0-100
    energy: int = 80           # 精力 0-100
    affection: int = 0         # 好感度 0-100
    stage: str = "初识"        # 关系阶段
    emotion_memory: list[dict] = field(default_factory=list)  # 最近情绪冲击残留
    emotion_archive: list[dict] = field(default_factory=list)  # 长期情绪档案
    event_memory: list[dict] = field(default_factory=list)  # 事件级长期记忆（带原文，可点名引用）
    resting: bool = False     # 是否正在用户建议的休息时段内
    rest_until: str | None = None
    tension: int = 0          # 关系张力 0-100；冲突后不会随普通心情漂移瞬间消失
    repair_hint: str = ""     # 最近一次修复方式，供行为层自然反馈
    last_update: str | None = None  # 上次更新时间 ISO

    # ---- 派生（不落库，消费方用）----
    @property
    def emotion_name(self) -> str:
        return emotion_label(self.emotion)[0]

    @property
    def emotion_desc(self) -> str:
        return emotion_label(self.emotion)[1]

    @property
    def is_tired(self) -> bool:
        return self.energy < 35

    @property
    def is_bubbly(self) -> bool:
        return self.emotion >= 65

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["emotion_name"] = self.emotion_name
        d["emotion_desc"] = self.emotion_desc
        d["is_tired"] = self.is_tired
        return d


# ---- 情绪冲击的残留记忆（短时）：让她「还在闹别扭 / 还开心着」----
_EMOTION_MEMORY_MAX = 6          # 最多记最近 6 次情绪冲击
_EMOTION_MEMORY_HALF_LIFE_H = 3  # 半衰期 3 小时：越久远的冲击影响越淡

# ---- 可交互精力 / 关系修复（C1）----
_REST_KEY = "state:rest"
_TENSION_KEY = "state:relationship_tension"
_REST_DEFAULT_MINUTES = 90
_REST_RECOVERY_TARGET = 92
_REST_BONUS_FADE_HOURS = 6


def _load_json_state(user_id: str, key: str) -> dict:
    try:
        from .userdb import kv_get

        raw = kv_get(user_id, key)
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_json_state(user_id: str, key: str, value: dict) -> None:
    try:
        from .userdb import kv_set

        kv_set(user_id, key, json.dumps(value, ensure_ascii=False))
    except Exception:
        logger.exception("[state] 交互状态写入失败: {}", key)


def _rest_snapshot(user_id: str, base_energy: int, now: datetime) -> tuple[int, bool, str | None]:
    """把持久化休息进度覆盖到自然精力上；休满后收益在 6 小时内缓慢退场。"""
    data = _load_json_state(user_id, _REST_KEY)
    if not data:
        return base_energy, False, None
    try:
        started = datetime.fromisoformat(str(data["started_at"]))
        ends = datetime.fromisoformat(str(data["ends_at"]))
        start_energy = max(0, min(100, int(data.get("start_energy", base_energy))))
        target = max(start_energy, min(100, int(data.get("target", _REST_RECOVERY_TARGET))))
    except Exception:
        return base_energy, False, None

    duration = max(1.0, (ends - started).total_seconds())
    if now < ends:
        progress = max(0.0, min(1.0, (now - started).total_seconds() / duration))
        recovered = round(start_energy + (target - start_energy) * progress)
        return max(base_energy, recovered), True, ends.isoformat(timespec="seconds")

    hours_after = max(0.0, (now - ends).total_seconds() / 3600)
    if hours_after < _REST_BONUS_FADE_HOURS:
        retained = round(target - (target - base_energy) * (hours_after / _REST_BONUS_FADE_HOURS))
        return max(base_energy, retained), False, ends.isoformat(timespec="seconds")

    try:
        from .userdb import kv_del

        kv_del(user_id, _REST_KEY)
    except Exception:
        pass
    return base_energy, False, None


def begin_rest(user_id: str, *, minutes: int = _REST_DEFAULT_MINUTES, now: datetime | None = None) -> dict:
    """开始一次真实的休息计时；重复劝休息不会把计时无限向后续。"""
    now = now or datetime.now()
    existing = _load_json_state(user_id, _REST_KEY)
    try:
        if existing and now < datetime.fromisoformat(str(existing["ends_at"])):
            return existing
    except Exception:
        pass
    try:
        from .userdb import db

        _, mood_updated = db.get_mood(user_id)
    except Exception:
        mood_updated = None
    start_energy = _derive_base_energy(user_id, mood_updated, now=now)
    data = {
        "started_at": now.isoformat(timespec="seconds"),
        "ends_at": (now + timedelta(minutes=max(15, min(240, minutes)))).isoformat(timespec="seconds"),
        "start_energy": start_energy,
        "target": max(start_energy, _REST_RECOVERY_TARGET),
    }
    _save_json_state(user_id, _REST_KEY, data)
    return data


def is_rest_request(text: str) -> bool:
    """只识别用户让菟菚休息，不把“我去睡了”误当成让她睡。"""
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return False
    first_person = re.search(r"我.{0,4}(?:睡|休息|歇)", compact)
    addresses_her = "你" in compact or "菟菚" in compact
    if first_person and not addresses_her:
        return False
    return bool(re.search(
        r"(?:你|菟菚).{0,6}(?:去|快|先|好好|也该|可以)?(?:睡|休息|歇)|"
        r"(?:去|快|赶紧|先|好好)(?:睡|休息|歇)(?:会儿|一会儿|一下)?吧?|"
        r"(?:睡|休息|歇)(?:会儿|一会儿|一下)吧",
        compact,
    ))


def _load_tension(user_id: str) -> dict:
    data = _load_json_state(user_id, _TENSION_KEY)
    try:
        data["level"] = max(0, min(100, int(data.get("level", 0))))
    except Exception:
        data["level"] = 0
    return data


def _repair_attempt(text: str) -> tuple[str, int]:
    """识别修复方式。承担责任比一句软话更有效，且不依赖 LLM 是否可用。"""
    compact = re.sub(r"\s+", "", text or "")
    apology = bool(re.search(r"对不起|抱歉|我错了|原谅我", compact))
    accountable = bool(re.search(r"是我的错|我不该|我会改|不会再|下次我会", compact))
    listening = bool(re.search(r"哪里让你不舒服|你可以告诉我|我听你说|我会听", compact))
    gentle = bool(re.search(r"别生气|消消气|哄哄你|哄你|抱抱你|给你抱抱|请你吃", compact))
    if apology and accountable:
        return "认真道歉并承担责任", 58
    if apology:
        return "真诚道歉", 36
    if accountable:
        return "承担责任", 28
    if listening:
        return "耐心倾听", 22
    if gentle:
        return "温柔安抚", 14
    return "", 0


def repair_tension(user_id: str, text: str) -> dict:
    """按用户的修复方式降低关系张力；普通闲聊不会自动清零。"""
    data = _load_tension(user_id)
    level = int(data.get("level", 0))
    kind, amount = _repair_attempt(text)
    if amount <= 0:
        if data.get("last_repair"):
            data["last_repair"] = ""
            _save_json_state(user_id, _TENSION_KEY, data)
        return data
    if level <= 0:
        return data
    new_level = max(0, level - amount)
    data.update({
        "level": new_level,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "last_repair": kind,
    })
    _save_json_state(user_id, _TENSION_KEY, data)
    try:
        from .userdb import db

        mood, _ = db.get_mood(user_id)
        db.set_mood(user_id, min(100, mood + max(3, amount // 6)))
    except Exception:
        logger.exception("[state] 修复关系时更新心情失败")
    return data


def handle_state_interaction(user_id: str, text: str) -> dict:
    """同步处理会影响当轮行为帧的显式交互意图。"""
    result = {"rest_started": False, "repair": "", "tension": 0}
    if is_rest_request(text):
        begin_rest(user_id)
        result["rest_started"] = True
    repaired = repair_tension(user_id, text)
    result["repair"] = str(repaired.get("last_repair", ""))
    result["tension"] = int(repaired.get("level", 0) or 0)
    return result


def _memory_key(user_id: str) -> str:
    return f"state:emotion_memory"


def _load_emotion_memory(user_id: str) -> list[dict]:
    """从 kv_store 读情绪记忆（失败返回空，不影响主流程）。"""
    try:
        from .userdb import kv_get
        import json

        raw = kv_get(user_id, _memory_key(user_id))
        if not raw:
            return []
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_emotion_memory(user_id: str, memory: list[dict]) -> None:
    """情绪记忆写回 kv_store（失败静默）。"""
    try:
        from .userdb import kv_set
        import json

        kv_set(user_id, _memory_key(user_id), json.dumps(memory, ensure_ascii=False))
    except Exception:
        logger.exception("[state] 情绪记忆写入失败")


def _decay_emotion_memory(memory: list[dict], now: datetime) -> list[dict]:
    """按半衰期衰减情绪记忆的影响权重，并清理过期项。"""
    kept = []
    for item in memory:
        try:
            ts = datetime.fromisoformat(item.get("ts", ""))
        except Exception:
            continue
        hours = (now - ts).total_seconds() / 3600
        if hours < 0:
            hours = 0
        # 指数衰减：半衰期 3h，超过 12h 权重几乎归零
        weight = item.get("weight", 1.0) * (0.5 ** (hours / _EMOTION_MEMORY_HALF_LIFE_H))
        if weight < 0.05:
            continue  # 影响已可忽略，丢弃
        item = dict(item)
        item["weight"] = round(weight, 3)
        kept.append(item)
    return kept


# ---- 状态读取 ----
def load_state(user_id: str) -> AgentState:
    """读取用户当前状态（含漂移演化，读时即推进，无需外部定时器）。"""
    from .userdb import db

    user = db.ensure_user(user_id)
    affection = int(user["affection"] or 0)
    emotion, updated = db.get_mood(user_id)

    # 精力：从「上次聊天到现在」的时长推疲惫度（越久越没聊 → 越累/越闷）
    energy, resting, rest_until = _derive_energy_details(user_id, updated)

    memory = _decay_emotion_memory(_load_emotion_memory(user_id), datetime.now())
    tension_state = _load_tension(user_id)
    tension = int(tension_state.get("level", 0) or 0)
    # 普通 mood 漂移仍可运行，但有未修复冲突时，外显心情不会自动瞬间恢复。
    if tension >= 70:
        emotion = min(emotion, 24)
    elif tension >= 40:
        emotion = min(emotion, 34)
    elif tension > 0:
        emotion = min(emotion, 44)

    return AgentState(
        emotion=emotion,
        energy=energy,
        affection=affection,
        stage=stage_of(affection),
        emotion_memory=memory,
        emotion_archive=recall_emotion_archive(user_id),
        event_memory=recall_event_memory(user_id),
        resting=resting,
        rest_until=rest_until,
        tension=tension,
        repair_hint=str(tension_state.get("last_repair", "")),
        last_update=updated,
    )


def _derive_energy(user_id: str, mood_updated: str | None) -> int:
    return _derive_energy_details(user_id, mood_updated)[0]


def _derive_energy_details(user_id: str, mood_updated: str | None) -> tuple[int, bool, str | None]:
    now = datetime.now()
    base = _derive_base_energy(user_id, mood_updated, now=now)
    return _rest_snapshot(user_id, base, now)


def _derive_base_energy(user_id: str, mood_updated: str | None, *, now: datetime | None = None) -> int:
    """精力：完整昼夜节律 + 互动衰减 + 天气联动，三者叠加出「当下精力」。

    1. 昼夜节律（circadian）：按一天 24 小时的正弦曲线给出自然精力基线——
       清晨 6 点最低（刚醒困），上午爬升，午后 14-15 点小低谷（饭后犯困），
       傍晚 19-20 点小高峰，深夜回落。这是「人一天精力起伏」的主干。
    2. 互动衰减：距上次聊天越久，精力略降（闷着/倦怠），但影响比节律小。
    3. 天气联动：阴雨/霾/雷等压抑天气让精力基线略降，晴朗天气略升。
    """
    now = now or datetime.now()
    # ---- 1) 昼夜节律：双峰曲线（午前爬升 + 午后低谷 + 傍晚高峰 + 深夜回落）----
    # 用「小时 → 精力」分段折线近似真实节律，避免单正弦曲线太平滑、缺午后低谷。
    # 折线：0点50 / 6点45 / 8点70 / 11点82 / 14点72 / 15点70 / 19点85 / 22点62
    _CIRCADIAN = (
        (0, 50), (6, 45), (8, 70), (11, 82), (14, 72), (15, 70), (19, 85), (22, 62),
    )
    h = now.hour + now.minute / 60.0  # 精确到分，让曲线连续
    base = _interp_circadian(h, _CIRCADIAN)

    # ---- 2) 互动衰减：距上次聊天越久越倦（封顶 -12，影响小于节律）----
    if mood_updated:
        try:
            last = datetime.fromisoformat(mood_updated)
            hours_idle = max(0.0, (now - last).total_seconds() / 3600)
            base -= min(12, hours_idle * 1.5)  # 每小时 -1.5，封顶 -12
        except Exception:
            pass

    # ---- 3) 天气联动：压抑天气降精力、晴朗升精力 ----
    base += _weather_energy_offset(user_id)

    return max(0, min(100, round(base)))


def _interp_circadian(hour: float, points: tuple[tuple[int, int], ...]) -> float:
    """按折线线性插值出某小时的精力值（首尾相连，跨午夜连续）。"""
    if hour < points[0][0]:
        hour += 24  # 凌晨 0 点前，绕到前一天末尾
    if hour >= 24:
        hour -= 24
    for i in range(len(points)):
        h0, v0 = points[i]
        h1, v1 = points[(i + 1) % len(points)]
        if i == len(points) - 1:
            h1 += 24  # 最后一段跨午夜（22点 → 24+0点）
        if h0 <= hour <= h1:
            if h1 == h0:
                return float(v0)
            return v0 + (v1 - v0) * (hour - h0) / (h1 - h0)
    return float(points[0][1])


def _weather_energy_offset(user_id: str) -> int:
    """天气对精力的影响（压抑 -、晴朗 +），失败静默返回 0。

    复用 mood 的天气获取（有日缓存），不重复发请求。
    """
    try:
        from .config import config
        from .mood import today_weather

        city = config.mood_city
        if not city:
            return 0
        weather, _ = today_weather(city)
        # 压抑天气 → 精力下降；晴朗 → 略升；其余中性
        _DOWN = {"雨": -8, "雷": -10, "霾": -9, "沙尘": -8, "雾": -6, "阴": -4, "雪": -3}
        _UP = {"晴": 6, "多云": 3, "风": 1}
        if weather in _DOWN:
            return _DOWN[weather]
        if weather in _UP:
            return _UP[weather]
        return 0
    except Exception:
        return 0


# ---- 状态演化：收到一条用户消息后，感知结果驱动状态变化 ----
def apply_impulse(
    user_id: str,
    *,
    emotion_delta: int = 0,
    energy_delta: int = 0,
    affection_delta: int = 0,
    affection_reason: str = "",
    emotional_hit: str | None = None,
    emotional_weight: float = 1.0,
    text: str = "",
    reply: str = "",
) -> AgentState:
    """应用一次「情绪冲击」：由 perception 计算出的各维度 delta 驱动状态演化。

    情绪 delta 落到 users.mood_value；好感度 delta 落到 users.affection；
    若命中情绪冲击（emotional_hit 非空），追加进情绪记忆（让她短期记得这次情绪），
    同时按强度把「事件级记忆（带原文）」写入长期（让她能精确记得这件事）。
    返回演化后的状态快照。
    """
    from .userdb import db

    db.ensure_user(user_id)

    # 情绪：读取当前值（带漂移），叠加 delta 后写回
    if emotion_delta != 0:
        emotion, _ = db.get_mood(user_id)
        new_emotion = max(0, min(100, emotion + emotion_delta))
        db.set_mood(user_id, new_emotion)

    # 好感度：叠加 delta（update_affection 内部已 clamp 0-100 并记流水）
    if affection_delta != 0:
        db.update_affection(user_id, affection_delta, affection_reason or "拟人状态演化")

    # 情绪记忆：记录一次冲击（让她短期「还记得刚刚的情绪」）
    if emotional_hit:
        memory = _decay_emotion_memory(_load_emotion_memory(user_id), datetime.now())
        memory.append({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "hit": emotional_hit,
            "weight": round(emotional_weight, 3),
        })
        memory = memory[-_EMOTION_MEMORY_MAX:]
        _save_emotion_memory(user_id, memory)

        # 长期情绪档案：按主题聚合，让「上周被冒犯/总被夸」这类长期情感积累可召回。
        # valence 由情绪/好感 delta 方向推断（正=正向情绪，负=负向情绪）。
        try:
            valence = 1 if (emotion_delta + affection_delta) >= 0 else -1
            record_emotion_archive(
                user_id,
                topic=emotional_hit,
                valence=valence,
                weight=max(0.1, float(emotional_weight)),
            )
        except Exception:
            logger.exception("[state] 长期情绪档案写入失败")

        # 事件级长期记忆：足够强的冲击才带原文记下（让她能精确引用这件事）。
        try:
            valence = 1 if (emotion_delta + affection_delta) >= 0 else -1
            record_event_memory(
                user_id,
                text=text,
                reply=reply,
                valence=valence,
                weight=float(emotional_weight),
            )
        except Exception:
            logger.exception("[state] 事件级记忆写入失败")

        # 冲突形成独立的关系张力。它不会跟随 mood 的自然漂移自动清零，必须由
        # 后续道歉、承担责任、倾听或安抚逐步修复。
        negative_words = ("冒犯", "冷落", "伤害", "失望", "轻视", "侮辱", "过早表白")
        if (emotion_delta + affection_delta) < 0 or any(w in emotional_hit for w in negative_words):
            tension = _load_tension(user_id)
            old_level = int(tension.get("level", 0) or 0)
            added = max(12, round(max(0.1, float(emotional_weight)) * 55))
            tension.update({
                "level": min(100, old_level + added),
                "reason": emotional_hit,
                "started_at": tension.get("started_at") or datetime.now().isoformat(timespec="seconds"),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "last_repair": "",
            })
            _save_json_state(user_id, _TENSION_KEY, tension)

    return load_state(user_id)


def pending_emotion_hits(user_id: str) -> list[dict]:
    """返回当前仍有效（权重>0）的情绪冲击，供 behavior 判断「她还在不在情绪里」。"""
    return [
        m for m in _decay_emotion_memory(_load_emotion_memory(user_id), datetime.now())
        if m.get("weight", 0) > 0.2
    ]


# ======================================================================
# 长期情绪档案（Long-term Emotion Archive）
# ----------------------------------------------------------------------
# 短时情绪记忆（上文）只解决「她还在不在刚才那个情绪里」（半衰期 3 小时）。
# 长期档案解决另一个问题：「她记得你上周让她难过 / 上个月总夸她」——这是
# 更接近真人的「对这段关系的长期情感积累」，能影响她的长期态度。
#
# 设计：
# - 按「情绪主题」聚合（hit 字符串即主题，如「被冒犯了」「被夸了」「被冷落了」）。
# - 每条记录：topic / valence(正负) / weight(当前强度) / count(累计次数) /
#   first_ts(首次) / last_ts(最近)。相同主题命中则 count+1、weight 叠加。
# - 长期衰减：半衰期 30 天，远慢于短时的 3 小时——她不会永久记仇，但会记得一阵。
# - 召回：返回 weight 仍显著的长期主题，供 behavior 注入「长期态度」。
# ======================================================================

_EMOTION_ARCHIVE_MAX = 20          # 最多保留 20 个长期情绪主题
_EMOTION_ARCHIVE_HALF_LIFE_H = 720  # 半衰期 30 天（720 小时）


def _archive_key(user_id: str) -> str:
    return "state:emotion_archive"


def _load_archive(user_id: str) -> list[dict]:
    """读长期情绪档案（失败返回空）。"""
    try:
        from .userdb import kv_get
        import json

        raw = kv_get(user_id, _archive_key(user_id))
        if not raw:
            return []
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_archive(user_id: str, archive: list[dict]) -> None:
    """长期情绪档案写回 kv_store（失败静默）。"""
    try:
        from .userdb import kv_set
        import json

        kv_set(user_id, _archive_key(user_id), json.dumps(archive, ensure_ascii=False))
    except Exception:
        logger.exception("[state] 长期情绪档案写入失败")


def _decay_archive(archive: list[dict], now: datetime) -> list[dict]:
    """按长期半衰期（30 天）衰减档案权重，清理已淡忘的主题。"""
    kept = []
    for item in archive:
        try:
            ts = datetime.fromisoformat(item.get("last_ts", ""))
        except Exception:
            continue
        hours = max(0.0, (now - ts).total_seconds() / 3600)
        weight = float(item.get("weight", 0)) * (0.5 ** (hours / _EMOTION_ARCHIVE_HALF_LIFE_H))
        if weight < 0.08:  # 影响已可忽略 → 淡忘，丢弃
            continue
        item = dict(item)
        item["weight"] = round(weight, 3)
        kept.append(item)
    return kept


def record_emotion_archive(
    user_id: str,
    *,
    topic: str,
    valence: int,
    weight: float,
) -> None:
    """把一次情绪冲击写入长期档案（按主题聚合，供「长期记得」）。

    - topic：情绪主题（hit 字符串，如「被冒犯了」「被夸了」）。
    - valence：正负（+1 正向 / -1 负向 / 0 中性）。
    - weight：本次强度 0-1，叠加进该主题的累计权重。
    """
    if not topic:
        return
    archive = _decay_archive(_load_archive(user_id), datetime.now())
    now_iso = datetime.now().isoformat(timespec="seconds")

    # 相同主题聚合：count+1，weight 叠加（封顶 1.0，避免无限涨）
    for item in archive:
        if item.get("topic") == topic:
            item["count"] = int(item.get("count", 0)) + 1
            item["weight"] = round(min(1.0, float(item.get("weight", 0)) + weight), 3)
            item["last_ts"] = now_iso
            # valence 以最近一次为准
            item["valence"] = valence
            _save_archive(user_id, archive)
            return

    # 新主题：追加（超限则丢弃最久远/最淡的一条）
    archive.append({
        "topic": topic,
        "valence": valence,
        "weight": round(min(1.0, weight), 3),
        "count": 1,
        "first_ts": now_iso,
        "last_ts": now_iso,
    })
    if len(archive) > _EMOTION_ARCHIVE_MAX:
        # 丢弃 weight 最小的一条（最淡忘的），保留有意义的长期记忆
        archive.sort(key=lambda x: float(x.get("weight", 0)))
        archive = archive[-(_EMOTION_ARCHIVE_MAX):]
    _save_archive(user_id, archive)


def recall_emotion_archive(user_id: str) -> list[dict]:
    """召回仍显著的长期情绪主题（weight >= 0.3），供 behavior 注入长期态度。

    返回按 weight 降序的列表，最多 3 条（别一次把陈年旧账全翻出来）。
    """
    archive = _decay_archive(_load_archive(user_id), datetime.now())
    significant = [a for a in archive if float(a.get("weight", 0)) >= 0.3]
    significant.sort(key=lambda x: float(x.get("weight", 0)), reverse=True)
    return significant[:3]


# ======================================================================
# 事件级长期记忆（Event-level Long-term Memory）
# ----------------------------------------------------------------------
# 情绪档案（上文的 record_emotion_archive）只按「主题」聚合、不带原文，
# 解决的是「她对你这个人长期的态度基调」（更防备/更亲近），不翻旧账。
# 事件级记忆解决的是另一层：让菟菚能**精确引用**「你上周说的某句话」——
# 记录情绪冲击发生时的原文（用户说了什么、菟菚回了什么），带时间戳与
# 长期半衰期衰减。这是从「态度」到「记得具体的事」的进一步拟人。
#
# 设计要点：
# - 只在「有情绪冲击」（emotional_hit 非空且够强）时记录，避免把鸡毛蒜皮
#   都存下来（控制噪音与体积）。
# - 每条：text(用户原文) / reply(菟菚当时的回复) / valence(正负) /
#   ts(发生时间) / weight(初始强度，随半衰期衰减)。
# - 召回时按「显著 + 新旧」权衡：太淡（weight 低）的不翻出来，太老的也
#   自然衰减掉；最多召回少数几条，避免翻旧账刷屏。
# - 与情绪档案互补：档案管「基调」，事件管「具体的事」，behavior 分别消费。
# ======================================================================

_EVENT_MEMORY_MAX = 40              # 最多保留 40 条事件记忆
_EVENT_MEMORY_HALF_LIFE_H = 720     # 半衰期 30 天（与情绪档案一致）
_EVENT_MEMORY_MIN_WEIGHT = 0.6      # 只有强度 >= 0.6 的冲击才值得记原文（太淡的不记）


def _event_key(user_id: str) -> str:
    return "state:event_memory"


def _load_event_memory(user_id: str) -> list[dict]:
    """读事件级长期记忆（失败返回空）。"""
    try:
        from .userdb import kv_get
        import json

        raw = kv_get(user_id, _event_key(user_id))
        if not raw:
            return []
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_event_memory(user_id: str, memory: list[dict]) -> None:
    """事件级记忆写回 kv_store（失败静默）。"""
    try:
        from .userdb import kv_set
        import json

        kv_set(user_id, _event_key(user_id), json.dumps(memory, ensure_ascii=False))
    except Exception:
        logger.exception("[state] 事件级记忆写入失败")


def _decay_event_memory(memory: list[dict], now: datetime) -> list[dict]:
    """按长期半衰期（30 天）衰减事件记忆权重，清理已淡忘的。"""
    kept = []
    for item in memory:
        try:
            ts = datetime.fromisoformat(item.get("ts", ""))
        except Exception:
            continue
        hours = max(0.0, (now - ts).total_seconds() / 3600)
        weight = float(item.get("weight", 0)) * (0.5 ** (hours / _EVENT_MEMORY_HALF_LIFE_H))
        if weight < 0.1:  # 已淡忘 → 丢弃
            continue
        item = dict(item)
        item["weight"] = round(weight, 3)
        kept.append(item)
    return kept


def record_event_memory(
    user_id: str,
    *,
    text: str,
    reply: str = "",
    valence: int,
    weight: float,
) -> None:
    """把一次「值得记住的情绪事件」写入事件级记忆（带原文）。

    - text：用户当时说的原文（截断防过长）。
    - reply：菟菚当时的回复（可选，截断）。
    - valence：正负（+1 正向 / -1 负向）。
    - weight：初始强度 0-1（只有够强的才记，见 _EVENT_MEMORY_MIN_WEIGHT）。
    """
    if not text or float(weight) < _EVENT_MEMORY_MIN_WEIGHT:
        return
    memory = _decay_event_memory(_load_event_memory(user_id), datetime.now())
    now_iso = datetime.now().isoformat(timespec="seconds")
    memory.append({
        "text": text[:120],
        "reply": (reply or "")[:120],
        "valence": valence,
        "weight": round(min(1.0, float(weight)), 3),
        "ts": now_iso,
    })
    # 超限丢最旧
    memory = memory[-_EVENT_MEMORY_MAX:]
    _save_event_memory(user_id, memory)


def recall_event_memory(user_id: str) -> list[dict]:
    """召回仍显著的事件级记忆（weight >= 0.35），供 behavior 做「精确引用」。

    返回按时间新→旧排序，最多 3 条（别一次翻太多旧账）。
    """
    memory = _decay_event_memory(_load_event_memory(user_id), datetime.now())
    significant = [m for m in memory if float(m.get("weight", 0)) >= 0.35]
    significant.sort(key=lambda x: x.get("ts", ""), reverse=True)
    return significant[:3]
