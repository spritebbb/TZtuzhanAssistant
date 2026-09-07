# -*- coding: utf-8 -*-
"""P1-05 纪念日/自然季节中度换挡：按当地日期的确定性轻调制。

契约（docs/Zcode技术指导.md §5 P1-05 / §14.5 / ADR 见模块 docstring 末尾）：
- 自然季节（natural）与关系季节（seasons.py 的 celebrate/create/…）是两种
  不同 kind，绝不同名覆盖；解释只显示已生效的来源；
- 阶段 id 稳定幂等：``cal:{date_row_id}:{year}:{pre|day|post}``，预热窗口
  [-3,-1] 天每天只产生一次候选入选，当天一次；重复 tick / 重复读取零增量；
- 只调基线（mood ±5 / energy ±3 / 主动候选 priority ±0.1），不直接增发任何
  消息；预热候选只入结构化状态，发送仍受统一仲裁与额度（首版裁剪：候选
  落 kv 待表达层消费，不自行推送）；
- 并发确定性优先级：边界（未修复冲突 > 勿扰）优先于日历调制；低精力把
  调制幅度减半；长期离线不因此扣分；
- 结束后恢复原基线，不累积永久偏移；改日期/删除纪念日后对应阶段自然失效
  （阶段按当日 important_dates 实时计算，不缓存旧实例）；
- 角色纪念日（character_fiction，如菟菚醒来日 8-27）复用 important_dates
  表的 namespace 列（ADR：复用+加列，独立存储会造第二套日子体系）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .userdb import db, kv_get, kv_set

KV_STATE = "state:calendar"

# 工程默认（14.5）：调制幅度上限
MOOD_SWING = 5
ENERGY_SWING = 3
PRIORITY_SWING = 0.1
PREHEAT_DAYS = 3  # 预热窗口 [-3,-1]


@dataclass(frozen=True)
class CalendarPhase:
    phase_id: str          # cal:{date_id}:{year}:{pre|day|post}
    kind: str              # anniversary / birthday / other / natural_season
    namespace: str         # user_real / character_fiction
    label: str
    stage: str             # pre / day / post
    mood_offset: int
    energy_offset: int
    priority_offset: float
    tone_hint: str         # 行为帧自然语言提示（无术语无数值）
    topic_hint: str        # 当天话题池提示


_NATURAL_SEASON_HINTS = {
    "spring": "春", "summer": "夏", "autumn": "秋", "winter": "冬",
}


def natural_season_of(day: date) -> str:
    """自然季节（气象季节，按本地月份）：与关系季节不同 kind。"""
    m = day.month
    if 3 <= m <= 5:
        return "spring"
    if 6 <= m <= 8:
        return "summer"
    if 9 <= m <= 11:
        return "autumn"
    return "winter"


def _delta(day: date, md: str) -> int:
    """今天距目标 MM-DD 的天数（同年内；跨年由日期算术自然处理）。"""
    try:
        target = date(day.year, *(int(x) for x in md.split("-")))
    except ValueError:
        return 999  # 非法日期（如平年 02-29 目标）：本年不触发
    return (target - day).days


def _row_phase(row: dict, day: date) -> CalendarPhase | None:
    md = str(row["date"])
    diff = _delta(day, md)
    if diff > PREHEAT_DAYS or diff < 0:
        # 当天已过 → 结束阶段只做恢复标记（无调制；post 不产生 tone）
        if diff == -1 or diff == -2:
            return CalendarPhase(
                phase_id=f"cal:{row['id']}:{day.year}:post", kind=str(row["kind"]),
                namespace=str(row.get("namespace", "user_real")),
                label=str(row["label"]), stage="post",
                mood_offset=0, energy_offset=0, priority_offset=0.0,
                tone_hint="", topic_hint="",
            )
        return None
    stage = "pre" if diff >= 1 else "day"
    is_canon = str(row.get("namespace", "user_real")) == "character_fiction"
    if stage == "day":
        if is_canon:
            tone = "今天是属于你自己的一个日子，可以轻轻带一点自嘲的仪式感，不索要庆祝、不长篇感慨"
        else:
            tone = "今天对对方来说是个特殊日子，记住这件事就好；语气自然一点，别刻意煽情或硬造仪式感"
        topic = str(row["label"])
        return CalendarPhase(
            phase_id=f"cal:{row['id']}:{day.year}:day", kind=str(row["kind"]),
            namespace=str(row.get("namespace", "user_real")), label=str(row["label"]),
            stage="day", mood_offset=MOOD_SWING, energy_offset=ENERGY_SWING,
            priority_offset=PRIORITY_SWING, tone_hint=tone, topic_hint=topic,
        )
    # 预热：提前 2–3 天只产生候选，轻度期待感，不保证每天必提
    tone = (
        "快到一个对对方来说有点特别的日子了，你心里记着这件事；"
        "不用现在说破，语气里最多带一点点期待"
    )
    return CalendarPhase(
        phase_id=f"cal:{row['id']}:{day.year}:pre", kind=str(row["kind"]),
        namespace=str(row.get("namespace", "user_real")), label=str(row["label"]),
        stage="pre", mood_offset=0, energy_offset=0, priority_offset=0.0,
        tone_hint=tone, topic_hint="",
    )


def active_phases(user_id: str, today: date) -> list[CalendarPhase]:
    """当前生效的日历阶段（预热/当天/结束标记），按日期实时计算不缓存。"""
    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM important_dates WHERE user_id = ? "
            "AND (kind IN ('birthday', 'anniversary') OR year IS NULL OR year = ?)",
            (user_id, today.year),
        ).fetchall()
    phases = []
    for row in rows:
        phase = _row_phase(dict(row), today)
        if phase is not None:
            phases.append(phase)
    # 当天 > 预热 > 结束标记：稳定排序
    order = {"day": 0, "pre": 1, "post": 2}
    phases.sort(key=lambda p: (order.get(p.stage, 3), p.phase_id))
    return phases


@dataclass
class Modulation:
    phase_id: str
    source: str              # calendar:{label} / natural_season:{name}
    kind: str
    mood_offset: int
    energy_offset: int
    priority_offset: float
    tone_hint: str
    topic_hint: str


def effective_modulation(
    user_id: str, today: date, *,
    energy: int = 80, tension: int = 0, quiet: bool = False,
) -> Modulation | None:
    """确定性合并：日历阶段 > 自然季节基线；并发时边界优先。

    - 未修复冲突（tension>0）：日历语气调制让位（只保留话题池），边界优先；
    - 勿扰：主动候选不加成（priority 归零）；
    - 低精力（<35）：调制幅度减半，不强行节日气氛。
    """
    phases = active_phases(user_id, today)
    phase = next((p for p in phases if p.stage in ("day", "pre")), None)
    season = natural_season_of(today)

    if phase is not None and phase.stage == "day":
        mod = Modulation(
            phase_id=phase.phase_id, source=f"calendar:{phase.label}", kind="calendar_day",
            mood_offset=phase.mood_offset, energy_offset=phase.energy_offset,
            priority_offset=phase.priority_offset,
            tone_hint=phase.tone_hint, topic_hint=phase.topic_hint,
        )
    else:
        # 无日历当天：自然季节基线（±2 轻档）或预热提示（零调制）
        mood = 2 if season in ("spring", "summer") else -2
        tone = "" if phase is None else phase.tone_hint
        mod = Modulation(
            phase_id=phase.phase_id if phase else f"natural:{season}:{today.isoformat()}",
            source=f"natural_season:{season}", kind="natural_season",
            mood_offset=mood, energy_offset=0, priority_offset=0.0,
            tone_hint=tone, topic_hint="",
        )

    # 确定性并发规则：边界优先
    if tension > 0:
        # 冲突期：语气与数值调制全部让位，只保留当天话题池（不装没事人也不翻脸加倍）
        mod = Modulation(
            phase_id=mod.phase_id, source=mod.source, kind=mod.kind,
            mood_offset=0, energy_offset=0, priority_offset=0.0,
            tone_hint="", topic_hint=mod.topic_hint,
        )
    if quiet:
        mod.priority_offset = 0.0
    if energy < 35:
        mod.mood_offset = int(mod.mood_offset / 2)
        mod.energy_offset = int(mod.energy_offset / 2)
        mod.priority_offset = round(mod.priority_offset / 2, 2)
        if mod.kind == "calendar_day":
            mod.tone_hint = "今天有点没力气搞气氛，把这份心意收着，简短自然就好"
    return mod


def compose_line(mod: Modulation | None) -> str:
    """把调制渲染成行为帧一行（自然语言，无数值无术语）。"""
    if mod is None or not mod.tone_hint:
        return ""
    return mod.tone_hint + "。"


def ensure_canon_dates(user_id: str, persona_id: str = "default") -> None:
    """播种正典角色纪念日（仅菟菚本人格；幂等）。

    醒来日八月二十七（世界正典 §4，年份虚化）：character_fiction 私人
    纪念日，口径为自嘲式轻提，不向用户索要庆祝。
    """
    if persona_id != "default":
        return
    with db._lock:
        row = db.conn.execute(
            "SELECT id FROM important_dates WHERE user_id = ? AND date = '08-27' "
            "AND namespace = 'character_fiction'",
            (user_id,),
        ).fetchone()
        if row:
            return
        db.conn.execute(
            "INSERT INTO important_dates (user_id, date, label, kind, year, ts, namespace) "
            "VALUES (?, '08-27', '她的醒来日', 'anniversary', NULL, ?, 'character_fiction')",
            (user_id, datetime.now().isoformat(timespec="seconds")),
        )
        db.conn.commit()


def advance_calendar(user_id: str, today: date, *, persona_id: str = "default") -> dict:
    """tick 入口：播种正典日期 + 记录周期幂等状态（不推送任何消息）。

    kv state:calendar 只存「今天已见过的 phase id 集合」——重复 tick 幂等；
    改日期/删除日子后阶段实时消失，无残留实例。
    """
    ensure_canon_dates(user_id, persona_id)
    phases = active_phases(user_id, today)
    key = KV_STATE
    raw = kv_get(user_id, key)
    try:
        state = dict(json.loads(raw)) if raw else {}
    except (ValueError, TypeError):
        state = {}
    today_key = today.isoformat()
    seen: dict = state.get("days", {})
    seen_today = set(seen.get(today_key, []))
    fresh = [p.phase_id for p in phases if p.phase_id not in seen_today]
    # 只保留最近 8 天的状态（有界，不累积）
    seen = {k: v for k, v in sorted(seen.items()) if k >= (today - timedelta(days=8)).isoformat()}
    seen[today_key] = sorted(seen_today | {p.phase_id for p in phases})
    kv_set(user_id, key, json.dumps(
        {"format_version": 1, "days": seen}, ensure_ascii=False))
    return {"fresh_phases": fresh, "active": [p.phase_id for p in phases]}
