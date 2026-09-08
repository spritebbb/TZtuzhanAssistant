# -*- coding: utf-8 -*-
"""P2-02 用户偏好教学与双向校准：四类封闭偏好的候选→确认→撤销。

契约（docs/Zcode技术指导.md §6 P2-02 / §14.6）：
- 类别固定 comfort / address / reminder / humor，value_json 各自封闭 schema；
- 解析优先级：用户明确禁令 > 最近已确认教学 > 有效场景偏好（legacy）> 核心默认；
- 明确指令（「以后叫我 X」「别拿 X 开玩笑」）按本轮用户指令直接确认；
  疑似推断只能产出 candidate 草稿，等待确认——不把「嗯/哦」写成心理侧写；
- 撤销立即失效并清缓存；负反馈只降被明确指向策略的权重，临时轮不学习；
- 表进迁移 / 双 reset / E03 导出（user_real 类）；撤销无向量残留。

默认不开永久敏感侧写：本模块只存用户显式教过的偏好与低频 legacy 迁移。
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from .userdb import db

CATEGORIES = ("comfort", "address", "reminder", "humor", "style")
ORIGINS = ("user_teaching", "legacy", "observed")
STATUS = ("candidate", "active", "revoked")

# 每类 value_json 的封闭 schema 校验（浅层）
_VALUE_VALIDATORS = {
    "comfort": lambda v: isinstance(v.get("style"), str) and 0 < len(v["style"]) <= 60,
    "address": lambda v: (isinstance(v.get("allowed"), list) or isinstance(v.get("forbidden"), list)),
    "reminder": lambda v: v.get("intensity") in (0, 1, 2) if "intensity" in v else True,
    "humor": lambda v: isinstance(v.get("forbidden_topics"), list) or isinstance(v.get("allow_teasing"), bool),
    # L03 关系气质屏蔽：value={"style": "companion|playful|confidant|growth|romantic"}
    "style": lambda v: isinstance(v.get("style"), str) and v.get("style") in (
        "companion", "playful", "confidant", "growth", "romantic"),
}


class PreferenceError(ValueError):
    """用户可见的偏好操作错误。"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _validate(category: str, value: dict) -> dict:
    if category not in CATEGORIES:
        raise PreferenceError(f"类别只能是 {CATEGORIES}")
    if not isinstance(value, dict) or not _VALUE_VALIDATORS[category](value):
        raise PreferenceError("偏好值不符合该类别的结构")
    return value


# ---- 确定性提取（明确指令直接确认；疑似推断出草稿）----

_TEACH_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    # 以后/请 叫我 X → address 允许（明确）
    ("address", re.compile(r"以后叫我(.{1,12}?)(?:了吧|吧|了|好了|行吗|$)|请叫我(.{1,12}?)(?:了吧|吧|了|好了|$)"), "explicit"),
    # 别叫我 X / 不许叫我 X → address 禁止（明确）
    ("address", re.compile(r"别叫我(.{1,12}?)(?:了|了，|，|。|$)|不许叫我(.{1,12}?)(?:了|，|。|$)"), "forbidden"),
    # 别拿 X 开玩笑 → humor 禁区（明确）
    ("humor", re.compile(r"别拿(.{1,12}?)开玩笑|不要拿(.{1,12}?)开玩笑"), "forbidden_topic"),
    # 安慰时别 X / 安慰我就 X → comfort（明确）
    ("comfort", re.compile(r"安慰我(?:的?时候)?别(.{1,20}?)(?:，|。|$)"), "avoid"),
    ("comfort", re.compile(r"安慰我(?:的?时候)?(?:就|只要)(.{1,20}?)(?:，|。|$)"), "style"),
    # 提醒别太啰嗦 / 提醒轻一点 → reminder（明确）
    ("reminder", re.compile(r"提醒(?:我)?别(?:太)?(?:啰嗦|唠叨|烦)"), "quiet"),
    ("reminder", re.compile(r"提醒(?:我)?(?:要)?(?:多|勤)一点"), "strong"),
)


def propose_preference(user_id: str, text: str, source_message_id: int | None = None) -> list[dict]:
    """从本轮消息提取教学候选。明确指令直接 active；其余返回草稿（candidate）。

    幂等：同 user+category+同规范化值不重复建行。
    """
    results: list[dict] = []
    if not text:
        return results
    for category, pattern, kind in _TEACH_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        captured = next((g for g in match.groups() if g), "").strip()
        if not captured and kind != "quiet" and kind != "strong":
            continue
        if category == "address" and kind == "explicit":
            value = {"allowed": [captured]}
        elif category == "address":
            value = {"forbidden": [captured]}
        elif category == "humor":
            value = {"forbidden_topics": [captured]}
        elif category == "comfort" and kind == "avoid":
            value = {"style": f"别{captured}"}
        elif category == "comfort":
            value = {"style": captured}
        else:  # reminder
            value = {"intensity": 0 if kind == "quiet" else 2}
        row = _upsert(user_id, category, value, origin="user_teaching",
                      source_message_id=source_message_id, status="active",
                      confidence=1.0)
        if row is not None:
            results.append(row)
    return results


def _normalize_key(category: str, value: dict) -> str:
    """偏好去重键：类别 + 排序后的 (k, v) 对（顺序无关）。"""
    items = sorted((k, json.dumps(v, ensure_ascii=False, sort_keys=True)
                    if isinstance(v, (list, dict)) else str(v)) for k, v in value.items())
    return category + "|" + "&".join(f"{k}={v}" for k, v in items)


def _upsert(user_id: str, category: str, value: dict, *, origin: str,
            source_message_id: int | None, status: str, confidence: float) -> dict | None:
    value = _validate(category, value)
    key = _normalize_key(category, value)
    with db._lock:
        existing = db.conn.execute(
            "SELECT id, status FROM user_preferences WHERE user_id=? AND category=? "
            "AND value_json=? AND origin=? ORDER BY id DESC LIMIT 1",
            (user_id, category, json.dumps(value, ensure_ascii=False, sort_keys=True), origin),
        ).fetchone()
        if existing is not None:
            if existing["status"] == status:
                return None  # 幂等：同值同状态已存在
            # 撤销后重新教学：复活原行
            db.conn.execute(
                "UPDATE user_preferences SET status=?, confidence=?, updated_at=?, "
                "revoked_at=NULL, version=version+1 WHERE id=?",
                (status, confidence, _now(), existing["id"]),
            )
            db.conn.commit()
            return _get_row(existing["id"])
        cur = db.conn.execute(
            "INSERT INTO user_preferences (user_id, category, value_json, origin, "
            "source_message_id, confidence, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, category, json.dumps(value, ensure_ascii=False, sort_keys=True),
             origin, source_message_id, confidence, status, _now(), _now()),
        )
        db.conn.commit()
        return _get_row(int(cur.lastrowid))


def _get_row(pref_id: int) -> dict:
    row = db.conn.execute(
        "SELECT * FROM user_preferences WHERE id=?", (pref_id,)
    ).fetchone()
    result = dict(row)
    result["value"] = json.loads(result.pop("value_json"))
    return result


def confirm_preference(user_id: int | str, pref_id: int, expected_version: int) -> dict:
    """确认草稿激活（CAS：版本不符拒绝）。"""
    with db._lock:
        row = db.conn.execute(
            "SELECT * FROM user_preferences WHERE id=? AND user_id=?",
            (pref_id, user_id),
        ).fetchone()
        if row is None:
            raise PreferenceError("偏好不存在")
        if row["status"] == "revoked":
            raise PreferenceError("偏好已撤销")
        if int(row["version"]) != int(expected_version):
            raise PreferenceError("版本已变化，请刷新后重试")
        db.conn.execute(
            "UPDATE user_preferences SET status='active', confidence=1.0, "
            "updated_at=?, version=version+1 WHERE id=?",
            (_now(), pref_id),
        )
        db.conn.commit()
    return _get_row(pref_id)


def revoke_preference(user_id: int | str, pref_id: int) -> dict:
    """撤销：立即失效并清运行缓存；恢复次高优先级由 resolver 自然回退。"""
    with db._lock:
        row = db.conn.execute(
            "SELECT id FROM user_preferences WHERE id=? AND user_id=? AND status!='revoked'",
            (pref_id, user_id),
        ).fetchone()
        if row is None:
            raise PreferenceError("偏好不存在或已撤销")
        db.conn.execute(
            "UPDATE user_preferences SET status='revoked', revoked_at=?, updated_at=?, "
            "version=version+1 WHERE id=?",
            (_now(), _now(), pref_id),
        )
        db.conn.commit()
    _clear_cache()
    return _get_row(pref_id)


def update_preference_value(user_id: int | str, pref_id: int, value: dict,
                            expected_version: int) -> dict:
    """更新偏好值（CAS：版本不符 409 语义）。"""
    value = _validate(
        db.conn.execute("SELECT category FROM user_preferences WHERE id=?", (pref_id,)
                        ).fetchone()["category"], value)
    with db._lock:
        row = db.conn.execute(
            "SELECT version FROM user_preferences WHERE id=? AND user_id=? AND status!='revoked'",
            (pref_id, user_id),
        ).fetchone()
        if row is None:
            raise PreferenceError("偏好不存在或已撤销")
        if int(row["version"]) != int(expected_version):
            raise PreferenceError("版本已变化，请刷新后重试")
        db.conn.execute(
            "UPDATE user_preferences SET value_json=?, updated_at=?, version=version+1 "
            "WHERE id=?",
            (json.dumps(value, ensure_ascii=False, sort_keys=True), _now(), pref_id),
        )
        db.conn.commit()
    _clear_cache()
    return _get_row(pref_id)


def record_feedback(user_id: str, category: str, *, negative: bool = True) -> None:
    """用户反馈「不喜欢这样哄」：只降该类已确认教学的权重（置信度）。

    临时对话路径不调用本函数（临时轮不学习）。
    """
    if category not in CATEGORIES:
        return
    with db._lock:
        db.conn.execute(
            "UPDATE user_preferences SET confidence=MAX(0.1, confidence-0.3), "
            "updated_at=? WHERE user_id=? AND category=? AND status='active' "
            "AND origin='user_teaching'",
            (_now(), user_id, category),
        )
        db.conn.commit()
    _clear_cache()


# ---- resolver：解析优先级 → 合并视图与短行为约束 ----

_resolve_cache: dict[str, dict] = {}


def _clear_cache() -> None:
    _resolve_cache.clear()


def resolve(user_id: str) -> dict:
    """当前生效的合并偏好（明确禁令 > 最近已确认教学 > legacy > 默认）。"""
    cached = _resolve_cache.get(user_id)
    if cached is not None:
        return cached
    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM user_preferences WHERE user_id=? AND status='active' "
            "ORDER BY origin DESC, updated_at DESC, id DESC",
            (user_id,),
        ).fetchall()
    merged: dict[str, dict] = {c: {} for c in CATEGORIES}
    # origin DESC：user_teaching > legacy > observed（同键后写覆盖 = 高优先级胜出）
    for row in rows:
        category = row["category"]
        value = json.loads(row["value_json"])
        for key, item in value.items():
            if key == "allowed" or key == "forbidden" or key == "forbidden_topics":
                existing = set(merged[category].get(key, []))
                merged[category][key] = sorted(existing | set(item))
            elif row["origin"] == "user_teaching":
                merged[category][key] = item  # 教学 style/intensity 单值覆盖
            elif key not in merged[category]:
                merged[category][key] = item
    _resolve_cache[user_id] = merged
    return merged


def resolve_constraints(user_id: str) -> list[str]:
    """编译为注入行为帧的短行为约束（自然语言，无内部术语）。"""
    prefs = resolve(user_id)
    lines: list[str] = []
    addr = prefs["address"]
    if addr.get("forbidden"):
        lines.append("这些称呼他明确说过不要用：" + "、".join(addr["forbidden"]) + "，一个都不碰")
    if addr.get("allowed"):
        lines.append("他认可过的称呼：" + "、".join(addr["allowed"]) + "（仍以已确认的主叫法为准）")
    humor = prefs["humor"]
    if humor.get("forbidden_topics"):
        lines.append("这些话题不开玩笑：" + "、".join(humor["forbidden_topics"]))
    if humor.get("allow_teasing") is False:
        lines.append("他不喜欢被调侃，玩笑收敛到事情本身")
    comfort = prefs["comfort"]
    if comfort.get("style"):
        lines.append(f"安慰他的时候{comfort['style']}")
    reminder = prefs["reminder"]
    intensity = reminder.get("intensity")
    if intensity == 0:
        lines.append("提醒点到即止，说一次就不再重复")
    elif intensity == 2:
        lines.append("他在意的事可以多提醒一句，但别变成催促")
    return lines


# ---- legacy 迁移：旧称呼配置 → origin=legacy 的已确认记录 ----

def migrate_legacy(user_id: str) -> None:
    """把 users.nickname_pref 一次性迁移为 address 的 legacy 已确认记录。"""
    u = db.get_user(user_id)
    if not u:
        return
    nickname = (u["nickname_pref"] or "").strip()
    if not nickname or nickname == "你":
        return
    with db._lock:
        row = db.conn.execute(
            "SELECT id FROM user_preferences WHERE user_id=? AND category='address' "
            "AND origin='legacy' LIMIT 1",
            (user_id,),
        ).fetchone()
    if row is not None:
        return
    _upsert(user_id, "address", {"allowed": [nickname]}, origin="legacy",
            source_message_id=None, status="active", confidence=1.0)


def list_preferences(user_id: str, include_revoked: bool = False) -> list[dict]:
    with db._lock:
        sql = "SELECT * FROM user_preferences WHERE user_id=?"
        if not include_revoked:
            sql += " AND status!='revoked'"
        sql += " ORDER BY category, id"
        rows = db.conn.execute(sql, (user_id,)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["value"] = json.loads(item.pop("value_json"))
        result.append(item)
    return result
