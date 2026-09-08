# -*- coding: utf-8 -*-
"""F01 基于真实素材的问候与变体池。

契约（docs/Zcode技术指导.md §15 F01 + 调度文档批次 4）：

**素材（只取授权真实来源，不建新原文表）**
- ``collect_greeting_material(user_id, now)``：近 30 天最多 3 条，进行中的
  活动优先，其次关系事件、她自己的角色生活事件；
- 排除敏感/never_surface/已过期/源已删（事件作废走既有级联，天然不出现）；
- 没有素材就返回空列表——问候走「无素材」类，绝不编「你今天做了 X」。

**变体（人格切片资源，四类 × 3 方向模板）**
- 资源 ``greeting_variants.json``：忙碌后 / 普通归来 / 完成活动 / 无素材；
- ``choose_greeting_variant(user_id, context)``：阶段门控（min_stage）＋
  同 variant 7 天冷却（``greeting_variant_usage``，runtime 不导出）；
- 措辞由模型基于「骨架 + 素材」现场生成，模板本身不预存假事实。

**失败与并发**
- 模型失败由调用方用诚实短回退（无素材类基调）；
- 生成期间用户已发言由调用方丢弃候选，本模块不落使用记录。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import config
from .log import logger

VARIANT_SCHEMA_VERSION = 1
COOLDOWN_DAYS = 7
MATERIAL_WINDOW_DAYS = 30
MATERIAL_LIMIT = 3

# 阶段序：低于 min_stage 的变体不选（初识不撩、不装熟）
_STAGE_ORDER = {"初识": 0, "熟悉": 1, "亲密": 2, "恋人": 3}

CATEGORY_BUSY_RETURN = "busy_return"
CATEGORY_PLAIN_RETURN = "plain_return"
CATEGORY_ACTIVITY_DONE = "activity_done"
CATEGORY_NO_MATERIAL = "no_material"

_CATEGORIES = (
    CATEGORY_BUSY_RETURN,
    CATEGORY_PLAIN_RETURN,
    CATEGORY_ACTIVITY_DONE,
    CATEGORY_NO_MATERIAL,
)

# 关系事件 → 问候素材（只取「值得顺口提一句」的完成类事件）
_EVENT_MATERIAL_TYPES = {
    "reading_finished": "你们一起读完了《{title}》",
    "goal_completed": "你们一起完成了目标「{title}」",
    "promise_completed": "你们完成过一个约定「{title}」",
    "story_finished": "你们一起写完了一个故事《{title}》",
    "list_completed": "你们一起收列了一份清单《{title}》",
    "focus_finished": "你们一起专注过一段时间（{title}）",
}

_ACTIVITY_KIND_LABEL = {
    "reading": "共读《{title}》",
    "goal": "共同目标「{title}」",
    "focus": "专注陪伴（{title}）",
    "writing": "共同创作《{title}》",
    "list": "共同清单《{title}》",
    "observation": "观察日志《{title}》",
}


class GreetingMaterialError(ValueError):
    """问候素材/变体的预期业务错误。"""


@dataclass(frozen=True)
class SourceRef:
    """一条授权问候素材：只带可展示的转述，不带原始正文。"""

    kind: str            # activity / event / life
    source_id: int
    line: str            # 口语化转述（模型据此措辞，不得超出）
    occurred_at: str
    fiction: bool = False  # 角色虚构生活（须明确标注，不当用户事实）


@dataclass(frozen=True)
class GreetingVariant:
    id: str
    category: str
    name: str
    skeleton: str
    example: str
    min_stage: str


def _variant_path(persona_id: str) -> Path:
    return config.data_dir / "personas" / persona_id / "greeting_variants.json"


# 仓库内置默认变体池：data/ 不入 git，默认人格没有个人定制文件时回退到这里。
_BUNDLED_VARIANT_PATH = (
    Path(__file__).resolve().parents[2] / "assets" / "persona_defaults" / "greeting_variants.json"
)

_VARIANT_CACHE: dict[str, tuple[float, tuple[GreetingVariant, ...]]] = {}
_CACHE_TTL = 30.0


def _active_persona_id() -> str:
    try:
        from .persona_profiles import active_id

        return active_id()
    except Exception:
        return "default"


def _validate_variant(raw: object, index: int) -> GreetingVariant:
    if not isinstance(raw, dict):
        raise GreetingMaterialError(f"第 {index} 条变体不是对象")
    vid = str(raw.get("id") or "").strip()
    category = str(raw.get("category") or "").strip()
    if not vid or category not in _CATEGORIES:
        raise GreetingMaterialError(f"变体 id/category 非法：{raw!r}")
    min_stage = str(raw.get("min_stage") or "初识").strip()
    if min_stage not in _STAGE_ORDER:
        raise GreetingMaterialError(f"变体 min_stage 非法：{min_stage}")
    skeleton = str(raw.get("skeleton") or "").strip()
    if not skeleton:
        raise GreetingMaterialError(f"变体 {vid} 缺少骨架")
    return GreetingVariant(
        id=vid,
        category=category,
        name=str(raw.get("name") or "").strip(),
        skeleton=skeleton,
        example=str(raw.get("example") or "").strip(),
        min_stage=min_stage,
    )


def load_variants(persona_id: str | None = None) -> tuple[GreetingVariant, ...]:
    """加载并校验当前人格的变体资源；无效条目跳过，全无效时用内置最小池。"""
    pid = persona_id or _active_persona_id()
    path = _variant_path(pid)
    now = datetime.now().timestamp()
    cached = _VARIANT_CACHE.get(pid)
    if cached and now - cached[0] < _CACHE_TTL and not _stale(path, cached[0]):
        return cached[1]

    variants: list[GreetingVariant] = []
    source = path if path.exists() else _BUNDLED_VARIANT_PATH
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or int(data.get("format_version", 0)) != VARIANT_SCHEMA_VERSION:
            raise GreetingMaterialError("format_version 非法")
        rows = data.get("variants")
        if not isinstance(rows, list) or not rows:
            raise GreetingMaterialError("variants 为空")
        seen: set[str] = set()
        for i, raw in enumerate(rows):
            try:
                variant = _validate_variant(raw, i)
                if variant.id in seen:
                    raise GreetingMaterialError(f"变体 id 重复：{variant.id}")
                seen.add(variant.id)
                variants.append(variant)
            except GreetingMaterialError as exc:
                logger.warning("[问候变体] 跳过无效变体：{}", exc)
    except (json.JSONDecodeError, OSError, GreetingMaterialError) as exc:
        logger.warning("[问候变体] 资源加载失败（用内置回退）：{}", exc)
    if not variants:
        variants = [_fallback_variant()]
    result = tuple(variants)
    _VARIANT_CACHE[pid] = (now, result)
    return result


def _fallback_variant() -> GreetingVariant:
    """资源完全不可用时的最小可用变体（无素材类，全阶段）。"""
    return GreetingVariant(
        id="D1", category=CATEGORY_NO_MATERIAL, name="状态开场",
        skeleton="一句她此刻的真实状态，不给细节、不借素材",
        example="有点困\n你的出现刚好提神，说吧", min_stage="初识",
    )


def _stale(path: Path, cached_ts: float) -> bool:
    try:
        return path.stat().st_mtime > cached_ts
    except OSError:
        return False


# ---- 素材收集（授权真实来源；无素材就返回空，不编） ----

def _event_line(event_type: str, payload: dict, obj: str) -> str:
    template = _EVENT_MATERIAL_TYPES.get(event_type, "")
    title = (
        payload.get("title")
        or payload.get("content")
        or payload.get("filename")
        or obj
        or ""
    )
    if not template or not title:
        return ""
    return template.format(title=str(title)[:40])


def _collect_activities(user_id: str, since: str, limit: int) -> list[SourceRef]:
    """进行中/近期暂停的活动优先——她惦记着对方正在做的事。"""
    from .userdb import db

    with db._lock:
        rows = db.conn.execute(
            "SELECT id, kind, title, status, updated_at FROM activities "
            "WHERE user_id=? AND status IN ('active', 'paused') "
            "ORDER BY updated_at DESC LIMIT ?",
            (user_id, max(1, int(limit))),
        ).fetchall()
    out: list[SourceRef] = []
    for row in rows:
        label = _ACTIVITY_KIND_LABEL.get(str(row["kind"]), "一起做的事「{title}」")
        line = label.format(title=str(row["title"] or "")[:40])
        if line:
            out.append(SourceRef(
                kind="activity", source_id=int(row["id"]), line=line,
                occurred_at=str(row["updated_at"] or ""),
            ))
    return out


def _collect_events(user_id: str, since: str, limit: int) -> list[SourceRef]:
    """近 30 天完成类关系事件；隐私非 normal 与已过期的天然被查询排除。"""
    from .relationship_events import active_events

    events = active_events(user_id, limit=max(1, int(limit)) * 3, within_days=MATERIAL_WINDOW_DAYS)
    out: list[SourceRef] = []
    for event in events:
        if str(event.get("privacy") or "normal") != "normal":
            continue  # 敏感事件不进问候素材
        line = _event_line(str(event["event_type"]), event.get("payload") or {},
                           str(event.get("object") or ""))
        if not line:
            continue
        out.append(SourceRef(
            kind="event", source_id=int(event["id"]), line=line,
            occurred_at=str(event["occurred_at"] or ""),
        ))
        if len(out) >= limit:
            break
    return out


def _collect_life(user_id: str, since: str, limit: int) -> list[SourceRef]:
    """她自己的角色生活事件（虚构，明确标注，不当用户事实）。"""
    from .schedule import latest_life_event

    out: list[SourceRef] = []
    for item in latest_life_event(user_id, limit=max(1, int(limit))):
        description = str(item.get("description") or "").strip()
        if not description:
            continue
        out.append(SourceRef(
            kind="life", source_id=0, line=description[:60],
            occurred_at=str(item.get("occurred_at") or ""), fiction=True,
        ))
        if len(out) >= limit:
            break
    return out


def collect_greeting_material(user_id: str, now: datetime | None = None, *,
                              limit: int = MATERIAL_LIMIT) -> list[SourceRef]:
    """近 30 天最多 limit 条授权素材；进行中的活动优先。

    没有可用素材返回空列表——调用方必须走「无素材」变体，不得编造经历。
    """
    from .features import flag

    if not flag("greeting_material_enabled"):
        return []
    moment = now or datetime.now()
    since = (moment - timedelta(days=MATERIAL_WINDOW_DAYS)).isoformat(timespec="seconds")
    limit = max(1, min(5, int(limit)))
    material: list[SourceRef] = []
    seen_lines: set[str] = set()
    for collector in (_collect_activities, _collect_events, _collect_life):
        if len(material) >= limit:
            break
        try:
            for item in collector(user_id, since, limit - len(material)):
                if item.line in seen_lines:
                    continue
                seen_lines.add(item.line)
                material.append(item)
                if len(material) >= limit:
                    break
        except Exception as exc:  # 单一来源失败不影响其余素材
            logger.warning("[问候素材] {} 收集失败：{}", collector.__name__, exc)
    return material[:limit]


def user_just_finished_focus(user_id: str, now: datetime | None = None, *,
                             within_hours: int = 12) -> bool:
    """用户刚结束一段专注/忙碌（近期有 focus_finished 事件）→ 走「忙碌后」类。"""
    from .relationship_events import active_events

    moment = now or datetime.now()
    try:
        events = active_events(user_id, event_type="focus_finished", limit=1, within_days=1)
    except Exception:
        return False
    if not events:
        return False
    try:
        occurred = datetime.fromisoformat(str(events[0]["occurred_at"]))
    except (TypeError, ValueError):
        return False
    return (moment - occurred) <= timedelta(hours=max(1, int(within_hours)))


# ---- 变体选择（阶段门控 + 7 天冷却） ----

def _stage_of(user_id: str) -> str:
    try:
        from .affection import display

        return str(display(user_id).get("stage") or "初识")
    except Exception:
        return "初识"


def variant_in_cooldown(user_id: str, variant_id: str,
                        now: datetime | None = None) -> bool:
    """同一变体 7 天内用过则冷却。"""
    from .userdb import db

    moment = now or datetime.now()
    cutoff = (moment - timedelta(days=COOLDOWN_DAYS)).isoformat(timespec="seconds")
    with db._lock:
        row = db.conn.execute(
            "SELECT last_used_at FROM greeting_variant_usage "
            "WHERE user_id=? AND variant_id=?",
            (user_id, str(variant_id)),
        ).fetchone()
    if row is None:
        return False
    return str(row["last_used_at"]) > cutoff


def mark_variant_used(user_id: str, variant_id: str, *,
                      source_id: str | int | None = None,
                      now: datetime | None = None) -> None:
    """登记变体使用（7 天冷却起点）；同一变体只保留最近一次。"""
    from .userdb import db

    moment = now or datetime.now()
    with db._lock:
        db.conn.execute(
            "INSERT INTO greeting_variant_usage "
            "(user_id, variant_id, last_used_at, last_source_id) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, variant_id) DO UPDATE SET "
            "last_used_at=excluded.last_used_at, last_source_id=excluded.last_source_id",
            (user_id, str(variant_id), moment.isoformat(timespec="seconds"),
             "" if source_id is None else str(source_id)),
        )
        db.conn.commit()


def _pick_category(material: list[SourceRef], *, busy_return: bool) -> str:
    if not material:
        return CATEGORY_NO_MATERIAL
    if busy_return:
        return CATEGORY_BUSY_RETURN
    if any(item.kind in ("event", "activity") for item in material):
        return CATEGORY_ACTIVITY_DONE
    return CATEGORY_PLAIN_RETURN


def choose_greeting_variant(user_id: str, context: dict | None = None, *,
                            now: datetime | None = None) -> GreetingVariant | None:
    """按阶段与冷却选一个变体；全部冷却时返回 None（调用方保持沉默或走回退）。

    context 可用键：``material``（collect_greeting_material 的结果）、
    ``busy_return``（用户刚从忙碌中回来）、``stage``（覆盖阶段，测试用）。
    """
    ctx = context or {}
    material = list(ctx.get("material") or [])
    stage = str(ctx.get("stage") or _stage_of(user_id))
    order = _STAGE_ORDER.get(stage, 0)
    category = _pick_category(material, busy_return=bool(ctx.get("busy_return")))
    moment = now or datetime.now()

    candidates = [
        v for v in load_variants()
        if v.category == category and _STAGE_ORDER.get(v.min_stage, 0) <= order
    ]
    if not candidates:
        # 该阶段不允许该类时退回无素材类的允许项，避免无变体可用
        candidates = [
            v for v in load_variants()
            if v.category == CATEGORY_NO_MATERIAL and _STAGE_ORDER.get(v.min_stage, 0) <= order
        ]
    for variant in candidates:
        if not variant_in_cooldown(user_id, variant.id, now=moment):
            return variant
    return None


def build_material_hint(material: list[SourceRef]) -> str:
    """把素材转成给模型的约束性提示；虚构素材明确标注。"""
    if not material:
        return (
            "\n\n这次没有可引用的真实素材。只打招呼，不要声称知道对方最近做了什么，"
            "不要编造你们之间的经历。"
        )
    lines = []
    for item in material:
        tag = "（她自己的虚构日常，不是对方的事）" if item.fiction else ""
        lines.append(f"- {item.line}{tag}")
    return (
        "\n\n以下是你们之间真实发生过的、可以顺口提一句的素材（最多用一条，"
        "不要逐条罗列，不要复述原文，不要编造素材之外的事）：\n" + "\n".join(lines)
    )


def build_variant_hint(variant: GreetingVariant | None, *,
                       address: str = "", period: str = "") -> str:
    """把选中的变体转成措辞骨架提示（模板只给方向，不预存假事实）。"""
    if variant is None:
        return ""
    hint = f"\n\n这次用「{variant.name}」这个方向：{variant.skeleton}。"
    example = variant.example.replace("{称呼}", address or "").replace("{时段}", period or "")
    if example.strip():
        hint += f"\n语气参考（不要照抄）：{example}"
    return hint
