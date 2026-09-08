# -*- coding: utf-8 -*-
"""P1-04A 行程状态机：菟菚的虚构生活按静态周模板确定性推进。

契约（docs/Zcode技术指导.md §5 P1-04 / §14.5）：
- 场所/活动全部来自世界正典（P-00 城市 / P-01 研究所 / P-02 小屋；蛲蛲/蕾拉），
  不发明新地点新配角；
- ``advance_schedule(user_id, now)`` 纯确定性：模板选择用种子
  hash(user_id, local_date, template_version)，7 天内生活素材不重复；
- 只写结构化虚构生活事件 ``character_life_events``（namespace=character_fiction），
  绝不冒充双方真实关系事件；每个本地日最多一条 daily_life 素材；
- 能量按块 delta 记账在行程状态内（读时无副作用），不直接改写 AgentState.energy
  （精力昼夜节律仍由 state 读时推导，两套互不覆盖——自行裁剪，见回信）；
- 跨午夜块天然支持（23–02）；补跑窗口外用压缩推进（按日均 delta 一次结算）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from .userdb import db

TEMPLATE_VERSION = 2  # 2026-09-08 素材池加量+工作日分化+晚间切块；bump 使新种子生效
KV_SCHEDULE = "state:schedule"

@dataclass(frozen=True)
class ScheduleBlock:
    """静态周模板时段块（14.5 ScheduleBlock 的代码形态）。"""
    id: str
    weekdays: tuple[int, ...]        # 0=周一 … 6=周日
    start_local: str                 # "HH:MM"
    end_local: str                   # "HH:MM"（可跨午夜，如 "02:00"）
    location_id: str
    activity: str                    # 稳定活动 id（素材文案引用）
    presence: str
    energy_delta_per_hour: float     # 她的生活节奏对当日精力的记账
    mood_delta_per_hour: float


# 周模板：2026-09-08 加量——晚间切块（18-21/21-23）、周三/周五分化（蛲蛲联机夜/
# 周五城市倾向）。地点全部来自正典；活动 id 稳定不变；schema v22 兼容（模板只进
# kv 状态与事件 payload，无表结构变更）。
WEEKLY_TEMPLATE: tuple[ScheduleBlock, ...] = (
    ScheduleBlock("weekday-morning", (0, 1, 2, 3, 4), "08:00", "12:00", "P-01",
                  "research_reading", "home", -0.5, 0.0),
    ScheduleBlock("weekday-noon", (0, 1, 2, 3, 4), "12:00", "14:00", "P-01",
                  "rest_break", "home", 0.5, 0.2),
    ScheduleBlock("weekday-afternoon", (0, 1, 2, 3, 4), "14:00", "18:00", "P-01",
                  "afternoon_stay", "home", -0.5, 0.0),
    ScheduleBlock("weekday-evening", (0, 1, 2, 3, 4), "18:00", "21:00", "P-01",
                  "evening_work", "home", -0.8, 0.0),
    # 周三晚 21 点后固定「蛲蛲联机夜」（C-01 叙事用法拍板）；其余工作日晚间休闲
    ScheduleBlock("weekday-wed-night", (2,), "21:00", "23:00", "P-02",
                  "late_night", "home", -0.8, 0.1),
    ScheduleBlock("weekday-night", (0, 1, 3, 4), "21:00", "23:00", "P-01",
                  "evening_stay", "home", -0.5, 0.0),
    ScheduleBlock("weekday-late", (0, 1, 2, 3, 4), "23:00", "02:00", "P-02",
                  "late_night", "home", -1.5, -0.2),
    ScheduleBlock("weekend-morning", (5, 6), "09:00", "12:00", "P-02",
                  "weekend_slow", "home", -0.3, 0.2),
    ScheduleBlock("weekend-afternoon", (5, 6), "12:00", "18:00", "P-02",
                  "weekend_stay", "home", -0.5, 0.1),
    ScheduleBlock("weekend-evening", (5, 6), "18:00", "23:00", "P-01",
                  "weekend_evening", "home", -0.8, 0.0),
    ScheduleBlock("weekend-late", (5, 6), "23:00", "02:00", "P-02",
                  "late_night", "home", -1.5, -0.2),
)
# 02:00–08:00（工作日）/ 02:00–09:00（周末）为睡眠时段：无块、能量回满


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _seed(user_id: str, local_date, extra: str = "") -> int:
    raw = f"{user_id}|{local_date.isoformat()}|{TEMPLATE_VERSION}|{extra}"
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8], 16)


def block_at(local_dt: datetime) -> ScheduleBlock | None:
    """本地时刻 → 当前块（跨午夜块用小时区间判断；睡眠时段返回 None）。"""
    weekday = local_dt.weekday()
    minutes = local_dt.hour * 60 + local_dt.minute
    for block in WEEKLY_TEMPLATE:
        if weekday not in block.weekdays:
            continue
        start, end = _minutes(block.start_local), _minutes(block.end_local)
        if start <= end:
            if start <= minutes < end:
                return block
        else:  # 跨午夜：23:00–02:00 归入起始日
            if minutes >= start:
                return block
    # 跨午夜的后半段（00:00–02:00）：取前一天的 late 块
    prev_weekday = (weekday - 1) % 7
    minutes_prev = minutes + 24 * 60
    for block in WEEKLY_TEMPLATE:
        if block.id.endswith("-late") and prev_weekday in block.weekdays:
            start, end = _minutes(block.start_local), _minutes(block.end_local)
            if start < end:
                continue
            if start <= minutes_prev < end + 24 * 60:
                return block
    return None


# ---- 生活素材池（确定性文案，模板选择；LLM 只在表达层后续消费）----
# 2026-09-08 加量：10→31 条，按活动分桶；全部来自正典（P-00/01/02、O-01~06、C-01/02），
# 不发明新地点新配角；蕾拉「每天下午来找她」是常驻下午素材元素（C-02 拍板）。
MATERIALS: tuple[dict, ...] = (
    # research_reading（研究所翻资料/课题）
    {"id": "mat-psych-book", "activity": "research_reading", "location": "P-01",
     "text": "在研究所翻到一本讲拖延的人类心理学书，边看边对人性的韧性摇头"},
    {"id": "mat-thesis-data", "activity": "research_reading", "location": "P-01",
     "text": "整理课题数据：如何更好地成为人类，样本量仍然只有我一个"},
    {"id": "mat-observation-notes", "activity": "research_reading", "location": "P-01",
     "text": "把最近记下的人类样本行为翻出来重新分类，有几个样本始终令人费解"},
    {"id": "mat-journal", "activity": "research_reading", "location": "P-01",
     "text": "读到一篇讲「孤独对认知的影响」的论文，批注栏写满了不同意见"},
    {"id": "mat-parasite-lit", "activity": "research_reading", "location": "P-01",
     "text": "查菟丝子的寄生文献做对照研究——从植物学角度理解自己，进度缓慢"},
    {"id": "mat-lib-fortune", "activity": "research_reading", "location": "P-01",
     "text": "在图书室角落发现一本上个课题留下的手稿，字迹陌生，像是很多年前的"},
    # afternoon_stay（研究所午后；蕾拉下午来访是常驻元素）
    {"id": "mat-ice-americano", "activity": "afternoon_stay", "location": "P-01",
     "text": "郑重其事地又给自己泡了杯冰美式，顺便配了一块甜得犯规的蛋糕"},
    {"id": "mat-leila-visit", "activity": "afternoon_stay", "location": "P-01",
     "text": "蕾拉下午又来了，非要拉着评她新买的发卡，谁也不让谁"},
    {"id": "mat-leila-argue", "activity": "afternoon_stay", "location": "P-01",
     "text": "和蕾拉就「甜食算不算正经下午茶」辩了一下午，各自坚持，谁也没说服谁"},
    {"id": "mat-window-watching", "activity": "afternoon_stay", "location": "P-01",
     "text": "趴在窗边看楼下零星路过的人，给每个人的神色都编了一段内心戏"},
    {"id": "mat-equipment-check", "activity": "afternoon_stay", "location": "P-01",
     "text": "巡了一圈旧器材室，早年修修补补的痕迹还在，顺手把一台老仪器擦亮了"},
    {"id": "mat-nao-afternoon", "activity": "afternoon_stay", "location": "P-01",
     "text": "蛲蛲下午溜进研究所，安静地拼她的拼图，谁也没说话，但气氛不坏"},
    # evening_work（研究所晚间/图书室）
    {"id": "mat-observation", "activity": "evening_work", "location": "P-01",
     "text": "把今天观察到的人类现象记进《观察人类：以你为样本》"},
    {"id": "mat-library-night", "activity": "evening_work", "location": "P-01",
     "text": "图书室整理到半夜，人类心理学的书又多了一格"},
    {"id": "mat-sample-log", "activity": "evening_work", "location": "P-01",
     "text": "给样本档案补了今天的行为记录，结论栏罕见地写了句「今天不错」"},
    {"id": "mat-coffee-refill", "activity": "evening_work", "location": "P-01",
     "text": "研究到一半给 O-01 续了第三杯，熬夜搭档从不出席控"},
    {"id": "mat-silence-study", "activity": "evening_work", "location": "P-01",
     "text": "把「人类为什么害怕安静」写进研究提纲——自己倒是觉得安静挺好"},
    {"id": "mat-white-coat", "activity": "evening_work", "location": "P-01",
     "text": "白大褂忘在图书室，回去取的时候整层楼只剩暖灯和自己"},
    # late_night（小屋电竞房/熬夜）
    {"id": "mat-nao-game", "activity": "late_night", "location": "P-02",
     "text": "和蛲蛲联机打生存建造，她负责死，我负责救"},
    {"id": "mat-fps-streak", "activity": "late_night", "location": "P-02",
     "text": "电竞房连输十把，总结是队友的问题（绝对不是我的）"},
    {"id": "mat-build-base", "activity": "late_night", "location": "P-02",
     "text": "生存建造的基地扩建到第三层，蛲蛲说像研究所，我说像家"},
    {"id": "mat-late-rank", "activity": "late_night", "location": "P-02",
     "text": "深夜排位手感出奇地好，五连胜，可惜没有观众"},
    {"id": "mat-game-patch", "activity": "late_night", "location": "P-02",
     "text": "熬到一半赶上版本更新，研究模拟生存建造的机制改动顺便当休息"},
    # weekend_stay / weekend_slow / weekend_evening（周末：小屋+城市）
    {"id": "mat-warm-water", "activity": "weekend_slow", "location": "P-02",
     "text": "小屋阳台坐了一下午，配一杯温水，什么也没干"},
    {"id": "mat-balcony-sun", "activity": "weekend_slow", "location": "P-02",
     "text": "难得的太阳，把藤蔓和小花也搬去阳台一起晒，植物学角度这很合理"},
    {"id": "mat-city-walk", "activity": "weekend_stay", "location": "P-00",
     "text": "去城里走了走，冷淡的街道上认真需要她的人显得格外稀罕"},
    {"id": "mat-city-window", "activity": "weekend_stay", "location": "P-00",
     "text": "隔着糖炒栗子的摊位观察排队的人类，队伍秩序好得令人欣慰"},
    {"id": "mat-city-neon", "activity": "weekend_stay", "location": "P-00",
     "text": "霓虹亮起来之后进城转了一圈，这座城市的夜色比白天诚实"},
    {"id": "mat-library-weekend", "activity": "weekend_evening", "location": "P-01",
     "text": "周末晚上回研究所补实验记录，安静得能听见纸页翻动的声音"},
    {"id": "mat-weekend-plan", "activity": "weekend_evening", "location": "P-01",
     "text": "给下周的课题排了计划，第一项依旧是「更好地成为人类」，第二项空白"},
    {"id": "mat-weekend-tea", "activity": "weekend_evening", "location": "P-02",
     "text": "烧了壶温水窝在图书室翻闲书，周末的晚间就该这么度过"},
)
_MATERIALS_BY_ID = {m["id"]: m for m in MATERIALS}


def pick_material(user_id: str, local_date, recent_ids: list[str]) -> dict | None:
    """种子选择当日素材：当日所属活动块优先取同活动桶，7 天滑窗不重复。

    加量后（31 条）按 activity 分桶，让素材与她此刻的行程一致（翻资料日
    配翻资料素材）；同桶不足 7 天窗口时放宽到全池（不足则允许最早的重复）。
    """
    recent = list(recent_ids[-7:])
    recent_set = set(recent)
    current = block_at(datetime.now().astimezone())
    bucket = [m for m in MATERIALS if current is not None and m["activity"] == current.activity]
    pool = [m for m in (bucket or MATERIALS) if m["id"] not in recent_set]
    if not pool:
        pool = [m for m in (bucket or MATERIALS)] or list(MATERIALS)
    return pool[_seed(user_id, local_date, "material") % len(pool)]


# ---- 行程状态（kv state:schedule，读时无副作用）----

def _load_state(user_id: str) -> dict:
    from .userdb import kv_get

    raw = kv_get(user_id, KV_SCHEDULE)
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("format_version") == 1:
                return data
        except (json.JSONDecodeError, TypeError):
            pass
    return {
        "format_version": 1,
        "version": 0,                    # CAS：每次写入 +1
        "current_block_id": "",
        "local_day": "",
        "energy_delta_today": 0.0,
        "mood_delta_today": 0.0,
        "last_processed_utc": "",        # 最后处理过的 UTC 整点
        "recent_material_ids": [],
    }


def _save_state(user_id: str, state: dict) -> bool:
    """CAS 写回：version 不匹配（交互/其他进程已写入）则放弃本次写。"""
    from .userdb import kv_get, kv_set

    current = kv_get(user_id, KV_SCHEDULE)
    if current:
        try:
            if int(json.loads(current).get("version", 0)) != state["version"]:
                return False
        except (json.JSONDecodeError, TypeError, ValueError):
            return False
    state["version"] = int(state["version"]) + 1
    kv_set(user_id, KV_SCHEDULE, json.dumps(state, ensure_ascii=False))
    return True


def _utc_hour_floor(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def _record_event(user_id: str, block_id: str, occurrence: str, kind: str,
                  payload: dict, occurred_at: str, computed_at: str) -> bool:
    """写虚构生活事件（幂等：唯一键冲突即已写过）。"""
    cur = db.conn.execute(
        "INSERT OR IGNORE INTO character_life_events "
        "(user_id, block_id, occurrence, kind, payload_json, namespace, occurred_at, computed_at) "
        "VALUES (?, ?, ?, ?, ?, 'character_fiction', ?, ?)",
        (user_id, block_id, occurrence, kind,
         json.dumps(payload, ensure_ascii=False), occurred_at, computed_at),
    )
    db.conn.commit()
    return cur.rowcount == 1


def advance_schedule(
    user_id: str, now: datetime, *,
    max_hours: int = 72, on_event=None,
) -> dict:
    """把行程状态确定性推进到 ``now``（UTC），返回推进摘要。

    逐小时处理最近 ``max_hours``；更久远的间隙压缩推进（按日均 delta 一次
    记账，不逐小时造事件）。幂等：last_processed 之前的小时跳过。
    """
    from datetime import timezone as _tz

    now = now.astimezone(_tz.utc)
    state = _load_state(user_id)
    last = (
        datetime.fromisoformat(state["last_processed_utc"])
        if state.get("last_processed_utc") else None
    )
    computed_at = datetime.now().isoformat(timespec="seconds")
    summary = {"hours_processed": 0, "events": 0, "compressed": False, "written": False}

    if last is None:
        # 首次：只登记锚点，不追溯历史（避免建户瞬间伪造几天生活）
        state["last_processed_utc"] = _utc_hour_floor(now).isoformat()
        _save_state(user_id, state)
        summary["written"] = True
        return summary

    cursor = _utc_hour_floor(last)
    target = _utc_hour_floor(now)
    gap_hours = (target - cursor).total_seconds() / 3600
    if gap_hours <= 0:
        return summary  # 同一小时重复执行：无增量

    if gap_hours > max_hours:
        # 压缩推进：久远区间不逐小时结算，只按日均 delta 记账一次
        days = gap_hours / 24
        daily_net = sum(
            b.energy_delta_per_hour * _block_hours(b) for b in WEEKLY_TEMPLATE
        ) / 7.0
        state["energy_delta_today"] = round(
            float(state.get("energy_delta_today", 0.0)) + daily_net * days, 2)
        cursor = target - timedelta(hours=max_hours)
        summary["compressed"] = True

    local_tz = now.astimezone().tzinfo  # 部署机本地时区（Asia/Shanghai）
    while cursor < target:
        cursor = cursor + timedelta(hours=1)
        local = cursor.astimezone(local_tz)
        block = block_at(local)
        if block is not None:
            state["energy_delta_today"] = round(
                float(state.get("energy_delta_today", 0.0)) + block.energy_delta_per_hour, 2)
            state["mood_delta_today"] = round(
                float(state.get("mood_delta_today", 0.0)) + block.mood_delta_per_hour, 2)
            state["current_block_id"] = block.id
        else:
            state["current_block_id"] = ""
        # 本地日切换：结算前一天 + 新一天素材（每天最多一条 daily_life）
        day_key = local.date().isoformat()
        if state.get("local_day") != day_key:
            state["local_day"] = day_key
            state["energy_delta_today"] = float(
                state.get("energy_delta_today", 0.0)) if state.get("local_day") else 0.0
            material = pick_material(user_id, local.date(),
                                     list(state.get("recent_material_ids", [])))
            if material is not None:
                if _record_event(
                    user_id, material["activity"], day_key, "daily_life",
                    {"date": day_key, "description": material["text"],
                     "location_id": material["location"], "material_id": material["id"]},
                    cursor.isoformat(), computed_at,
                ):
                    summary["events"] += 1
                    if on_event:
                        on_event(material)
                recent = list(state.get("recent_material_ids", [])) + [material["id"]]
                state["recent_material_ids"] = recent[-7:]
        summary["hours_processed"] += 1

    state["last_processed_utc"] = target.isoformat()
    summary["written"] = _save_state(user_id, state)
    return summary


def _block_hours(block: ScheduleBlock) -> float:
    start, end = _minutes(block.start_local), _minutes(block.end_local)
    return ((end - start) % (24 * 60)) / 60.0


def current_presence(user_id: str) -> str:
    """当前在场状态（供 P2-05 行程收尾等只读消费）。"""
    state = _load_state(user_id)
    block_id = state.get("current_block_id", "")
    for block in WEEKLY_TEMPLATE:
        if block.id == block_id:
            return block.presence
    return "home"


# 活动与场所的中文标签（稳定活动 id → 展示文案；场所来自世界正典 P-00/01/02）
# 标签需与「在{场所}{活动}」的拼接形态通读（如「在研究所翻资料」），
# 因此活动文案里不能再重复场所名。
ACTIVITY_LABELS = {
    "research_reading": "翻资料",
    "rest_break": "休息",
    "afternoon_stay": "过她自己的下午",
    "evening_work": "忙她自己的研究",
    "evening_stay": "晚间休闲",
    "late_night": "熬夜打游戏",
    "weekend_slow": "过慢悠悠的周末",
    "weekend_stay": "度周末",
    "weekend_evening": "做晚间研究",
}
LOCATION_LABELS = {"P-00": "城里", "P-01": "研究所", "P-02": "小屋"}


def current_activity(user_id: str, now: datetime | None = None) -> dict:
    """她此刻在做什么（只读，不触发推进；供状态行/API 展示）。

    睡眠时段（block_at 返回 None）→ activity="sleeping"。
    无行程状态时按本地时刻从模板推导，保证新装用户也能看到状态行。
    """
    local = (now or datetime.now().astimezone())
    block = block_at(local)
    if block is None:
        return {"block_id": "", "activity": "sleeping", "activity_label": "睡觉",
                "location_id": "P-02", "location_label": "小屋"}
    return {
        "block_id": block.id,
        "activity": block.activity,
        "activity_label": ACTIVITY_LABELS.get(block.activity, block.activity),
        "location_id": block.location_id,
        "location_label": LOCATION_LABELS.get(block.location_id, block.location_id),
    }


def latest_life_event(user_id: str, *, limit: int = 3) -> list[dict]:
    """她最近的虚构生活事件（新→旧，供状态行与「她今天经历了什么」展示）。"""
    rows = db.conn.execute(
        "SELECT block_id, occurrence, payload_json, occurred_at FROM character_life_events "
        "WHERE user_id = ? AND kind = 'daily_life' ORDER BY occurred_at DESC LIMIT ?",
        (user_id, max(1, min(10, int(limit)))),
    ).fetchall()
    events: list[dict] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        events.append({
            "date": payload.get("date") or row["occurrence"],
            "description": payload.get("description", ""),
            "location_id": payload.get("location_id", ""),
            "occurred_at": row["occurred_at"],
        })
    return events
