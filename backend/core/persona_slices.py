# -*- coding: utf-8 -*-
"""P1-01 人格切片编译器：把定稿的侧写/正典内容按当前状态确定性编译为短行为提示。

设计契约（docs/Zcode技术指导.md §5 P1-01 / §14.3）：
- 资源是版本化 JSON（backend/resources/personas/<人格id>/slices.json），schema=1，
  校验失败整个资源不激活，回退旧人格提示（不抛异常）；
- 编译是确定性选择，不在线调用 LLM；
- kind=core 常驻；其余按状态谓词选至多 ``MAX_DYNAMIC`` 条，总动态预算
  ``DYNAMIC_TOKEN_BUDGET``（保守估算：中文按 1 字 ≈ 1 token，宁少勿超）；
- 谓词是 {"field","op","value"} 的 AND 列表，field 引用封闭状态键集合，
  不支持任意表达式；emotion.<name> 语义 = 该情绪存在且 intensity ≥ value；
- 缓存键 = (人格id, 资源version, state_fingerprint)，指纹只取被引用键的值，
  未引用的键不参与；不跨人格复用。

首版过渡（自行决策，P2-01 落地后替换）：trust/intimacy 暂由旧 affection
单值同时初始化；emotion.<name> 在 P1-03 落地前恒缺失（谓词不满足，
深水区切片自然不激活，不会提前泄露门控内容）。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

# ---- 封闭状态键（state_keys_version=2，新增键必须同步 eval 用例）----
STAGE_ORDER = ("初识", "熟悉", "亲密", "恋人")
EMOTION_NAMES = ("joy", "sadness", "anger", "hurt", "anxiety", "calm", "tenderness")
ENERGY_BANDS = ("high", "normal", "low")
TIME_OF_DAY = ("morning", "afternoon", "evening", "late")
SUBSTAGES = ("早", "中", "晚")
ATTITUDE_AXES = ("patience", "humor", "guard", "directness", "followup")

STATE_KEYS_VERSION = 2
_ALLOWED_BASE_KEYS = frozenset(
    {"derived_stage", "substage", "trust", "intimacy", "energy_band", "time_of_day", "quiet"}
)
_ALLOWED_OPS = frozenset({"eq", "ne", "gte", "lte", "stage_gte", "stage_lte", "in"})
_ALLOWED_KINDS = frozenset({"core", "signature", "tactic", "disclosure", "deep", "canon"})

# 工程默认（14.3）：动态至多 3 条、总预算 800 tokens
MAX_DYNAMIC = 3
DYNAMIC_TOKEN_BUDGET = 800
# 指纹里 emotion 键的取值精度（P1-03 强度 0-1，两位足够区分状态变化）
_RESOURCE_CACHE_SIZE = 8
_COMPILE_CACHE_SIZE = 32

_ROOT = Path(__file__).resolve().parents[1] / "resources" / "personas"


class PersonaSliceError(ValueError):
    """资源结构不合法（加载时抛出；调用方按「不激活该版资源」处理）。"""


def _validate_resource(data: dict, profile_id: str) -> dict:
    """schema=1 结构校验；任何不合法都拒绝整个资源（继续旧版/空注入）。"""
    if not isinstance(data, dict):
        raise PersonaSliceError("资源必须是 JSON 对象")
    if data.get("format_version") != 1:
        raise PersonaSliceError("format_version 必须为 1")
    if data.get("state_keys_version") != STATE_KEYS_VERSION:
        raise PersonaSliceError(f"state_keys_version 必须为 {STATE_KEYS_VERSION}")
    if data.get("persona_id") != profile_id:
        raise PersonaSliceError("persona_id 与目录不一致")
    version = data.get("version")
    if not isinstance(version, int) or version < 1:
        raise PersonaSliceError("version 必须是正整数")
    slices = data.get("slices")
    if not isinstance(slices, list) or not slices:
        raise PersonaSliceError("slices 必须是非空列表")
    seen: set[str] = set()
    for item in slices:
        if not isinstance(item, dict):
            raise PersonaSliceError("切片必须是对象")
        for field in ("id", "kind", "namespace", "instruction", "source_doc"):
            if not isinstance(item.get(field), str) or not item[field]:
                raise PersonaSliceError(f"切片缺少字段 {field}")
        if item["id"] in seen:
            raise PersonaSliceError(f"切片 id 重复: {item['id']}")
        seen.add(item["id"])
        if item["kind"] not in _ALLOWED_KINDS:
            raise PersonaSliceError(f"kind 非法: {item['kind']}")
        if not isinstance(item.get("priority"), (int, float)):
            raise PersonaSliceError("priority 必须是数值")
        if not isinstance(item.get("version"), int) or item["version"] < 1:
            raise PersonaSliceError("切片 version 必须是正整数")
        examples = item.get("examples", [])
        if not isinstance(examples, list) or len(examples) > 3:
            raise PersonaSliceError("examples 最多 3 条")
        for ex in examples:
            if not isinstance(ex, str) or not ex:
                raise PersonaSliceError("example 必须是非空字符串")
        triggers = item.get("trigger_ids", [])
        if not isinstance(triggers, list):
            raise PersonaSliceError("trigger_ids 必须是列表")
        for cond in triggers:
            _validate_predicate(cond)
    return data


def _validate_predicate(cond: dict) -> None:
    if not isinstance(cond, dict):
        raise PersonaSliceError("谓词必须是对象")
    field, op, value = cond.get("field"), cond.get("op"), cond.get("value")
    if not isinstance(field, str):
        raise PersonaSliceError("谓词缺少 field")
    if op not in _ALLOWED_OPS:
        raise PersonaSliceError(f"op 非法: {op}")
    if field.startswith("emotion."):
        if field.split(".", 1)[1] not in EMOTION_NAMES:
            raise PersonaSliceError(f"情绪键非法: {field}")
        if op not in ("gte", "lte"):
            raise PersonaSliceError("emotion 键只支持 gte/lte")
        if not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise PersonaSliceError("emotion 阈值必须在 0-1")
        return
    if field.startswith("attitude."):
        if field.split(".", 1)[1] not in ATTITUDE_AXES:
            raise PersonaSliceError(f"态度键非法: {field}")
        if op not in ("gte", "lte"):
            raise PersonaSliceError("attitude 键只支持 gte/lte")
        if not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise PersonaSliceError("attitude 阈值必须在 0-1")
        return
    if field not in _ALLOWED_BASE_KEYS:
        raise PersonaSliceError(f"状态键未登记: {field}")
    if field in ("derived_stage",) and op in ("stage_gte", "stage_lte", "eq", "ne") and value not in STAGE_ORDER:
        raise PersonaSliceError(f"阶段值非法: {value}")
    if field == "substage" and value not in SUBSTAGES:
        raise PersonaSliceError(f"小档值非法: {value}")
    if field in ("trust", "intimacy") and op in ("gte", "lte") and not isinstance(value, (int, float)):
        raise PersonaSliceError("数值键阈值必须是数值")
    if field == "energy_band" and value not in ENERGY_BANDS:
        raise PersonaSliceError(f"energy_band 值非法: {value}")
    if field == "time_of_day":
        if op == "in":
            if not isinstance(value, list) or not value or not set(value) <= set(TIME_OF_DAY):
                raise PersonaSliceError("time_of_day in 值非法")
        elif value not in TIME_OF_DAY:
            raise PersonaSliceError(f"time_of_day 值非法: {value}")


# ---- 资源加载（mtime 失效缓存：内容定稿后只读，切换/更新资源不必重启进程）----
_resource_lock = threading.Lock()
_resource_cache: dict[str, tuple[float, dict | None]] = {}


def load_resource(profile_id: str) -> dict | None:
    """读取并校验某人格的切片资源；不存在或校验失败返回 None（回退旧提示）。"""
    path = _ROOT / profile_id / "slices.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    with _resource_lock:
        cached = _resource_cache.get(profile_id)
        if cached and cached[0] == mtime:
            return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data = _validate_resource(data, profile_id)
    except (OSError, json.JSONDecodeError, PersonaSliceError, ValueError):
        data = None
    with _resource_lock:
        if len(_resource_cache) >= _RESOURCE_CACHE_SIZE and profile_id not in _resource_cache:
            _resource_cache.pop(next(iter(_resource_cache)))
        _resource_cache[profile_id] = (mtime, data)
    return data


# ---- 状态视图（供 persona.py 组装；P1-03/P2-01 落地后在此接真源）----

def time_of_day_of(hour: int) -> str:
    if 6 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    if 18 <= hour < 23:
        return "evening"
    return "late"


def energy_band_of(energy: int) -> str:
    # 与 state.AgentState.is_tired(<35) 对齐的只读分档（自行决策的过渡映射）
    if energy >= 70:
        return "high"
    if energy >= 35:
        return "normal"
    return "low"


def build_state_view(
    *,
    stage: str,
    affection: int,
    energy: int | None = None,
    now: datetime | None = None,
    emotions: dict[str, float] | None = None,
    trust: int | None = None,
    intimacy: int | None = None,
) -> dict:
    """从既有状态组装封闭键值视图。

    trust/intimacy 首选真源（P2-01 起由调用方传入 state 派生值）；
    缺省时由 affection 过渡初始化（P2-01 前的兼容行为）。
    时间按部署机本地时间计算（与 persona._now_line 同一口径，部署时区 Asia/Shanghai）。
    """
    now = now or datetime.now()
    view: dict = {
        "derived_stage": stage,
        "trust": int(trust if trust is not None else affection),
        "intimacy": int(intimacy if intimacy is not None else affection),
        "time_of_day": time_of_day_of(now.hour),
        "quiet": False,
    }
    if energy is not None:
        view["energy_band"] = energy_band_of(energy)
    if emotions:
        for name, intensity in emotions.items():
            if name in EMOTION_NAMES:
                view[f"emotion.{name}"] = round(float(intensity), 2)
        from .emotion_state import attitude_summary

        attitude = attitude_summary(
            emotions,
            trust=view["trust"],
            intimacy=view["intimacy"],
            low_energy_or_late=(
                view.get("energy_band") == "low" or view["time_of_day"] == "late"
            ),
        )
        for axis, value in attitude.items():
            view[f"attitude.{axis}"] = value
    return view


# ---- 谓词求值与指纹 ----

def _predicate_ok(cond: dict, view: dict) -> bool:
    field, op, value = cond["field"], cond["op"], cond["value"]
    current = view.get(field)
    if current is None:
        # emotion.<name>：情绪不存在 → 不满足；基础键缺失 → 不满足（不猜测默认）
        return False
    if field.startswith(("emotion.", "attitude.")):
        return current >= value if op == "gte" else current <= value
    if op == "eq":
        return current == value
    if op == "ne":
        return current != value
    if op == "gte":
        return current >= value
    if op == "lte":
        return current <= value
    if op == "stage_gte":
        return STAGE_ORDER.index(current) >= STAGE_ORDER.index(value)
    if op == "stage_lte":
        return STAGE_ORDER.index(current) <= STAGE_ORDER.index(value)
    if op == "in":
        return current in value
    return False


def state_fingerprint(slices: list[dict], view: dict) -> str:
    """只取被引用键的值，按固定顺序 canonical JSON 后哈希；未引用键不参与。"""
    keys: set[str] = set()
    for item in slices:
        for cond in item.get("trigger_ids", []):
            keys.add(cond["field"])
    payload = {k: view.get(k) for k in sorted(keys)}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    import hashlib

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---- 编译 ----

def _estimate_tokens(text: str) -> int:
    # 保守估算：中文 1 字 ≈ 1 token（宁少勿超，不引 tokenizer 依赖）
    return len(text)


def _render(item: dict) -> str:
    text = item["instruction"]
    examples = item.get("examples") or []
    if examples:
        text += "（例：" + "／".join(ex.replace("\n", "␤") for ex in examples) + "）"
    return text


class CompileResult:
    __slots__ = ("core_ids", "dynamic_ids", "lines", "fingerprint", "cache_hit")

    def __init__(self, core_ids: list[str], dynamic_ids: list[str], lines: list[str],
                 fingerprint: str, cache_hit: bool):
        self.core_ids = core_ids
        self.dynamic_ids = dynamic_ids
        self.lines = lines
        self.fingerprint = fingerprint
        self.cache_hit = cache_hit


_compile_lock = threading.Lock()
_compile_cache: dict[tuple, CompileResult] = {}


def compile_slices(view: dict, *, profile_id: str, max_dynamic: int = MAX_DYNAMIC,
                   budget: int = DYNAMIC_TOKEN_BUDGET) -> CompileResult | None:
    """确定性编译：core 常驻 + 谓词命中的动态切片（稳定排序、预算截断）。

    资源不存在/不合法返回 None（调用方回退旧人格提示）。
    """
    resource = load_resource(profile_id)
    if resource is None:
        return None
    slices = resource["slices"]
    fp = state_fingerprint(slices, view)
    cache_key = (profile_id, resource["version"], fp)
    with _compile_lock:
        cached = _compile_cache.get(cache_key)
        if cached is not None:
            return CompileResult(cached.core_ids, cached.dynamic_ids, cached.lines,
                                 cached.fingerprint, cache_hit=True)

    core_items = [s for s in slices if s["kind"] in ("core", "signature")]
    dynamic_pool = [
        s for s in slices
        if s["kind"] not in ("core", "signature")
        and all(_predicate_ok(c, view) for c in s.get("trigger_ids", []))
    ]
    # 稳定排序：priority 降序，同分按 id 升序（14.4 同款确定性）
    dynamic_pool.sort(key=lambda s: (-float(s["priority"]), s["id"]))

    picked: list[dict] = []
    used = 0
    for item in dynamic_pool:
        if len(picked) >= max_dynamic:
            break
        cost = _estimate_tokens(_render(item))
        if used + cost > budget:
            continue
        picked.append(item)
        used += cost

    core_ids = [s["id"] for s in core_items]
    dynamic_ids = [s["id"] for s in picked]
    lines = [_render(s) for s in core_items] + [_render(s) for s in picked]
    result = CompileResult(core_ids, dynamic_ids, lines, fp, cache_hit=False)
    with _compile_lock:
        if len(_compile_cache) >= _COMPILE_CACHE_SIZE and cache_key not in _compile_cache:
            _compile_cache.pop(next(iter(_compile_cache)))
        _compile_cache[cache_key] = result
    return result


def compile_prompt_lines(*, profile_id: str, stage: str, affection: int,
                         energy: int | None = None, now: datetime | None = None,
                         emotions: dict[str, float] | None = None,
                         trust: int | None = None, intimacy: int | None = None) -> list[str]:
    """给 persona.py 的便捷入口：任何失败都返回空列表（保持旧 prompt 契约）。"""
    try:
        view = build_state_view(stage=stage, affection=affection, energy=energy,
                                now=now, emotions=emotions,
                                trust=trust, intimacy=intimacy)
        result = compile_slices(view, profile_id=profile_id)
        return result.lines if result else []
    except Exception:
        return []
