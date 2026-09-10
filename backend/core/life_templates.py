# -*- coding: utf-8 -*-
"""L06 低频生活模板池：她低频地「出门/换活动」，精力有限选择。

契约（docs/Zcode技术指导.md §16 L06 + 调度文档批次1 + 2026-09-08 用户拍板）：
- 模板资源每人格 JSON（data/personas/<id>/life_templates.json，schema=1），
  字段白名单 {id,location_id,prerequisites,energy_cost,weight,output_kind,
  cooldown_days,min_stage,activity_hint,description,return_note}；
  全部场所/配角来自世界正典，不发明新地点新配角；
- ``choose_life_event(user_id, now)`` 纯确定性：候选每天最多 1 个、每模板
  cooldown_days 冷却、固定种子概率 0.25（首次没选中当日不反复抽）；
- 精力门控：角色精力 <30 只允许 rest/quiet_reading；>=30 才允许 outing；
  扣能量只在事件提交时一次；
- 用户意见可取消：用户明确「别出门/今天别去」→ 当日候选作废并计入模板
  冷却（不再当天反复抽）；
- 沉默权 + 可见提醒：出门后不强制主动汇报，但事件落 ``character_life_events``
  （kind='outing'，namespace='character_fiction'），状态行/生活流可见，不失踪；
- 无合法模板保持沉默不编；无效资源 fallback rest 模板 + 日志；
- 开关：features "life_templates_enabled"（默认 True，设置页可关）+ env
  FEATURE_LIFE_TEMPLATES_ENABLED 部署初值（13.6 双层口径）。
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import config
from .log import logger

TEMPLATE_SCHEMA_VERSION = 1
KV_LIFE_TEMPLATES = "state:life_templates"
# 候选概率：固定种子下每天 25% 触发（设计原文初值），不是保证四天一次
_TRIGGER_PERCENT = 25
# 精力门槛：低于此值只允许 rest/quiet_reading 类模板（设计原文 30）
_LOW_ENERGY = 30
_LOW_ENERGY_KINDS = {"rest", "quiet_reading"}


class LifeTemplateError(ValueError):
    """模板资源非法。"""


@dataclass(frozen=True)
class LifeTemplate:
    id: str
    location_id: str
    energy_cost: int
    weight: int
    output_kind: str
    cooldown_days: int
    min_stage: str
    activity_hint: str
    description: str
    return_note: str
    prerequisites: tuple[str, ...] = ()

    @property
    def low_energy_ok(self) -> bool:
        return self.output_kind in _LOW_ENERGY_KINDS


_ALLOWED_KINDS = {"outing", "rest", "quiet_reading"}
_ALLOWED_LOCATIONS = {"P-00", "P-01", "P-02"}
_ALLOWED_STAGES = {"初识", "熟悉", "亲密", "恋人"}


def _validate_template(raw: dict, index: int) -> LifeTemplate:
    if not isinstance(raw, dict):
        raise LifeTemplateError(f"模板 #{index} 不是对象")
    tid = str(raw.get("id") or "").strip()
    if not tid:
        raise LifeTemplateError(f"模板 #{index} 缺 id")
    kind = str(raw.get("output_kind") or "").strip()
    if kind not in _ALLOWED_KINDS:
        raise LifeTemplateError(f"{tid}: output_kind 非法 {kind!r}")
    location = str(raw.get("location_id") or "").strip()
    if location not in _ALLOWED_LOCATIONS:
        raise LifeTemplateError(f"{tid}: location_id 非法 {location!r}")
    stage = str(raw.get("min_stage") or "初识").strip()
    if stage not in _ALLOWED_STAGES:
        raise LifeTemplateError(f"{tid}: min_stage 非法 {stage!r}")
    try:
        energy_cost = max(0, min(100, int(raw.get("energy_cost", 0))))
        weight = max(1, min(10, int(raw.get("weight", 1))))
        cooldown = max(0, min(60, int(raw.get("cooldown_days", 7))))
    except (TypeError, ValueError):
        raise LifeTemplateError(f"{tid}: 数值字段非法") from None
    description = str(raw.get("description") or "").strip()
    return_note = str(raw.get("return_note") or "").strip()
    if not description or not return_note:
        raise LifeTemplateError(f"{tid}: description/return_note 不能为空")
    prereq = raw.get("prerequisites") or []
    if not isinstance(prereq, list) or any(not isinstance(p, str) for p in prereq):
        raise LifeTemplateError(f"{tid}: prerequisites 必须是字符串列表")
    return LifeTemplate(
        id=tid, location_id=location, energy_cost=energy_cost, weight=weight,
        output_kind=kind, cooldown_days=cooldown, min_stage=stage,
        activity_hint=str(raw.get("activity_hint") or "weekend_slow"),
        description=description, return_note=return_note,
        prerequisites=tuple(prereq),
    )


def _template_path(persona_id: str) -> Path:
    return config.data_dir / "personas" / persona_id / "life_templates.json"


# 仓库内置默认模板：data/ 不入 git，默认人格没有个人定制文件时回退到这里，
# 保证新装用户开箱即有完整模板池（个人格可在 data/personas/<id>/ 覆盖）。
_BUNDLED_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "assets" / "persona_defaults" / "life_templates.json"


def _bundled_template_path() -> Path:
    return _BUNDLED_TEMPLATE_PATH


def _active_persona_id() -> str:
    try:
        from .persona_profiles import active_id

        return active_id()
    except Exception:
        return "default"


_TEMPLATE_CACHE: dict[str, tuple[float, tuple[LifeTemplate, ...]]] = {}
_CACHE_TTL = 30.0


def load_templates(persona_id: str | None = None) -> tuple[LifeTemplate, ...]:
    """加载并校验当前人格的模板资源；无效条目跳过并告警，全无效时 fallback rest。"""
    pid = persona_id or _active_persona_id()
    path = _template_path(pid)
    now = datetime.now().timestamp()
    cached = _TEMPLATE_CACHE.get(pid)
    if cached and now - cached[0] < _CACHE_TTL and not _stale(path, cached[0]):
        return cached[1]

    templates: list[LifeTemplate] = []
    source = path if path.exists() else _bundled_template_path()
    if source is not None:
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or int(data.get("format_version", 0)) != TEMPLATE_SCHEMA_VERSION:
                raise LifeTemplateError("format_version 非法")
            rows = data.get("templates")
            if not isinstance(rows, list) or not rows:
                raise LifeTemplateError("templates 为空")
            seen: set[str] = set()
            for i, raw in enumerate(rows):
                try:
                    tpl = _validate_template(raw, i)
                    if tpl.id in seen:
                        raise LifeTemplateError(f"模板 id 重复: {tpl.id}")
                    seen.add(tpl.id)
                    templates.append(tpl)
                except LifeTemplateError as exc:
                    logger.warning("[生活模板] 跳过无效模板：{}", exc)
        except (json.JSONDecodeError, OSError, LifeTemplateError) as exc:
            logger.warning("[生活模板] 资源加载失败（fallback rest）：{}", exc)
    if not templates:
        templates = [_fallback_rest_template()]
    result = tuple(templates)
    _TEMPLATE_CACHE[pid] = (now, result)
    return result


def _stale(path: Path, cached_ts: float) -> bool:
    try:
        return path.stat().st_mtime > cached_ts
    except OSError:
        return False


def _fallback_rest_template() -> LifeTemplate:
    return LifeTemplate(
        id="lt-rest-fallback", location_id="P-02", energy_cost=0, weight=1,
        output_kind="rest", cooldown_days=0, min_stage="初识",
        activity_hint="weekend_slow", description="在小屋安静休息",
        return_note="今天哪儿也没去，安安静静待着",
    )


# ---- 冷却/取消状态（kv runtime，reset 覆盖，不导出） ----

def _load_kv(user_id: str) -> dict:
    from .userdb import kv_get

    raw = kv_get(user_id, KV_LIFE_TEMPLATES)
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("format_version") == 1:
                return data
        except (json.JSONDecodeError, TypeError):
            pass
    return {"format_version": 1, "last_used": {}, "vetoed_date": ""}


def _save_kv(user_id: str, data: dict) -> None:
    from .userdb import kv_set

    kv_set(user_id, KV_LIFE_TEMPLATES, json.dumps(data, ensure_ascii=False))


def mark_vetoed(user_id: str, *, now: datetime | None = None) -> bool:
    """用户意见取消：当日候选作废并全部模板计入当日冷却（拍板 #10）。

    返回是否发生了新的取消（同日重复取消幂等）。
    """
    local = (now or datetime.now().astimezone()).date().isoformat()
    data = _load_kv(user_id)
    if data.get("vetoed_date") == local:
        return False
    data["vetoed_date"] = local
    today = datetime.now().date().isoformat()
    last_used = dict(data.get("last_used") or {})
    for tpl in load_templates():
        # 只为今天确实可用的模板记冷却：避免用户一句「别出门」把全年都锁死
        if _cooldown_left(tpl, last_used, today) <= 0:
            last_used[tpl.id] = today
    data["last_used"] = last_used
    _save_kv(user_id, data)
    return True


def _cooldown_left(tpl: LifeTemplate, last_used: dict, today: str) -> int:
    last = str(last_used.get(tpl.id) or "")
    if not last:
        return 0
    try:
        days = (datetime.fromisoformat(today).date()
                - datetime.fromisoformat(last).date()).days
    except ValueError:
        return 0
    return max(0, tpl.cooldown_days - days)


def _stage_allows(tpl: LifeTemplate, stage: str) -> bool:
    order = {"初识": 0, "熟悉": 1, "亲密": 2, "恋人": 3}
    return order.get(stage, 0) >= order.get(tpl.min_stage, 0)


def choose_life_event(user_id: str, now: datetime, *, energy: int | None = None,
                      stage: str | None = None) -> LifeTemplate | None:
    """确定性选择今日生活模板；无合法候选返回 None（保持沉默不编）。

    - 固定种子（user_id+本地日期）_roll 概率与加权抽取；
    - 每模板 cooldown_days 冷却；当日已被用户取消（vetoed）则不再抽；
    - energy=None 时读 state 派生精力；<30 只允许 rest/quiet_reading；
    - 首次没选中当日不反复抽（roll 结果确定性）。
    """
    from .features import flag

    if not flag("life_templates_enabled"):
        return None
    data = _load_kv(user_id)
    local_date = now.astimezone().date()
    today = local_date.isoformat()
    if data.get("vetoed_date") == today:
        return None

    roll = _roll(user_id, local_date)
    if roll >= _TRIGGER_PERCENT:
        return None

    if energy is None:
        energy = _current_energy(user_id)
    if stage is None:
        stage = _current_stage(user_id)

    last_used = dict(data.get("last_used") or {})
    # 日门禁必须先于逐模板冷却：启动补跑会连续处理多个小时；若这里只
    # 排除已用模板，同一天会在候选集缩小时依次抽中其它模板。
    if today in set(last_used.values()):
        return None
    candidates: list[tuple[int, LifeTemplate]] = []
    for tpl in load_templates():
        if not _stage_allows(tpl, stage):
            continue
        if energy < _LOW_ENERGY and not tpl.low_energy_ok:
            continue
        if _cooldown_left(tpl, last_used, today) > 0:
            continue
        candidates.append((tpl.weight, tpl))
    if not candidates:
        return None

    # 加权确定性抽取：种子决定 roll 在总权重上的落点
    total = sum(w for w, _ in candidates)
    pick = _roll(user_id, local_date, extra="pick") % max(1, total)
    acc = 0
    for weight, tpl in candidates:
        acc += weight
        if pick < acc:
            return tpl
    return candidates[-1][1]


def commit_life_event(user_id: str, tpl: LifeTemplate, now: datetime) -> dict | None:
    """把选中的模板落成 character_life_events(kind='outing')，一次性扣能量。

    幂等：同一用户同日最多一个生活模板；成功返回事件 payload，失败/重复返回 None。
    """
    from . import schedule
    from .userdb import db

    local_date = now.astimezone().date()
    occurred = datetime.combine(local_date, datetime.min.time()).astimezone()
    payload = {
        "date": local_date.isoformat(),
        "template_id": tpl.id,
        "description": tpl.description,
        "return_note": tpl.return_note,
        "location_id": tpl.location_id,
        "output_kind": tpl.output_kind,
        "energy_cost": tpl.energy_cost,
    }
    already_committed = db.conn.execute(
        "SELECT 1 FROM character_life_events WHERE user_id=? AND occurrence=? "
        "AND block_id LIKE 'lt-%' LIMIT 1",
        (user_id, local_date.isoformat()),
    ).fetchone()
    if already_committed:
        return None
    ok = schedule.record_life_event(
        user_id, tpl.id, local_date.isoformat(), tpl.output_kind, payload,
        occurred, datetime.now().isoformat(timespec="seconds"),
    )
    if not ok:
        return None
    if tpl.energy_cost > 0:
        _spend_energy(user_id, tpl.energy_cost)
    data = _load_kv(user_id)
    last_used = dict(data.get("last_used") or {})
    last_used[tpl.id] = local_date.isoformat()
    data["last_used"] = last_used
    _save_kv(user_id, data)
    logger.info("[生活模板] {} 触发 {}（{}，-{} 精力）", user_id, tpl.id,
                tpl.description, tpl.energy_cost)
    return payload


def latest_outing(user_id: str, *, limit: int = 3) -> list[dict]:
    """最近的 outing 事件（新→旧，供状态行/主动候选消费）。"""
    from .userdb import db

    rows = db.conn.execute(
        "SELECT block_id, occurrence, payload_json, occurred_at FROM character_life_events "
        "WHERE user_id = ? AND kind = 'outing' ORDER BY occurred_at DESC LIMIT ?",
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
            "return_note": payload.get("return_note", ""),
            "location_id": payload.get("location_id", ""),
            "occurred_at": row["occurred_at"],
        })
    return events


# ---- 内部辅助 ----

def _roll(user_id: str, local_date, extra: str = "") -> int:
    raw = f"life-template|{user_id}|{local_date.isoformat()}|{extra}"
    return int(__import__("hashlib").sha256(raw.encode("utf-8")).hexdigest()[:8], 16) % 100


def _current_energy(user_id: str) -> int:
    try:
        from .state import load_state

        return int(load_state(user_id).energy)
    except Exception:
        return 80


def _current_stage(user_id: str) -> str:
    try:
        from .affection import display

        return str(display(user_id).get("stage") or "初识")
    except Exception:
        return "初识"


def _spend_energy(user_id: str, cost: int) -> None:
    """一次性扣能量：写进当日行程能量记账（schedule 状态），不改昼夜节律基线。"""
    try:
        from . import schedule

        schedule.spend_energy_today(user_id, float(cost))
    except Exception:
        logger.warning("[生活模板] 扣能量失败（不阻塞事件）：user={}", user_id)


def veto_keywords_hit(text: str) -> bool:
    """用户当轮消息是否命中「别出门」类取消意图（确定性关键词，不开 LLM）。"""
    t = (text or "").strip().lower()
    if not t:
        return False
    for key in ("别出门", "不要出门", "别出去", "不要出去", "今天别去", "别走了",
                "别乱跑", "待在家", "待在小屋", "呆在家"):
        if key in t:
            return True
    return False


# ---- 主动候选（拍板 #9）：外出归来可主动说一句，消耗共享额度 ----

_EXPRESSED_OUTING_KEY = "life_templates:outing_expressed:{day}"


def outing_expressed_today(user_id: str) -> bool:
    """今日外出汇报是否已经真正投递。"""
    from .userdb import kv_get

    key = _EXPRESSED_OUTING_KEY.format(day=datetime.now().date().isoformat())
    return bool(kv_get(user_id, key))


def mark_outing_expressed(user_id: str) -> None:
    """仅在主动消息投递成功后登记外出汇报去重键。"""
    from .userdb import kv_set

    key = _EXPRESSED_OUTING_KEY.format(day=datetime.now().date().isoformat())
    kv_set(user_id, key, datetime.now().isoformat(timespec="seconds"))


def maybe_express_outing(user_id: str, *, mark: bool = True) -> str | None:
    """今日有外出事件且尚未表达过 → 返回一句汇报文案（经主动仲裁投递）。

    - 沉默权：这里是「候选」而非必发——仲裁链空闲/额度用尽自然沉默；
    - 可见提醒由事件表+状态行兜底，即使一次也没主动说过，用户也能看到；
    - 每日至多表达一次（kv runtime 去重）。
    """
    from .features import flag
    if not flag("life_templates_enabled"):
        return None
    today = datetime.now().date().isoformat()
    if outing_expressed_today(user_id):
        return None

    outings = latest_outing(user_id, limit=1)
    if not outings or outings[0].get("date") != today:
        return None
    note = str(outings[0].get("return_note") or "").strip()
    if not note:
        return None
    text = f"{note}。"
    if mark:
        # 兼容直接调用方；主动仲裁链使用 mark=False，并在投递成功回调里登记。
        mark_outing_expressed(user_id)
    return text
