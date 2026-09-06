# -*- coding: utf-8 -*-
"""M8 第三垂直切片：双视角叙事——同一件真实经历，双方各留一段解释。

设计边界（TECH-PLAN M8，用户拍板 2026-09-06）：
- 载体是独立的「经历双视角页」：用户从真实记录里挑一件经历（关系事件 /
  日记 / 活动 / 共同产物）或起一个自由主题，左右两栏各自保留解释，并存
  不合并、不裁判谁对谁错。
- 菟菚视角默认由 LLM 基于锚点的真实记录生成**草稿**：草稿绝不直接落库，
  经用户确认或修改后保存，origin 标记 'llm'；用户代填则标记 'user'。
- 生成 prompt 的硬约束：只写感受与解读，不新增事实断言、不改写已发生的事。
  观点是主观解释，不属于事实召回语料（本表不进任何召回/提炼管道）。
- 视角可随时改写（opinion 不是历史记录），锚点 id 为展示性快照，导出
  恢复按原样保留，不做重映射。
- 删除是真删除；所有读写严格按 user_id 隔离。
"""
from __future__ import annotations

import re

from .llm import chat
from .log import logger
from .userdb import db

_MAX_TITLE = 60
_MAX_VIEW = 2000
_MAX_ANCHOR_MATERIAL = 900

_ANCHOR_TYPES = {"event", "diary", "activity", "artifact", "free"}


class DualPerspectiveError(ValueError):
    """双视角叙事的预期业务错误。"""


def _now() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


def _clean(value: str, maximum: int, label: str, *, required: bool = False) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise DualPerspectiveError(f"{label}不能为空")
    if len(result) > maximum:
        raise DualPerspectiveError(f"{label}最多 {maximum} 字")
    return result


# ---- 锚点解析：存在性校验 + 展示快照 + 生成素材（全部来自真实行）----


def _anchor_row_locked(user_id: str, source_type: str, source_id: int):
    if source_type == "event":
        return db.conn.execute(
            "SELECT id, event_type, object, occurred_at FROM relationship_events "
            "WHERE id = ? AND user_id = ?",
            (source_id, user_id),
        ).fetchone()
    if source_type == "activity":
        return db.conn.execute(
            "SELECT id, title, status, created_at FROM activities "
            "WHERE id = ? AND user_id = ?",
            (source_id, user_id),
        ).fetchone()
    if source_type == "artifact":
        return db.conn.execute(
            "SELECT id, title, substr(content, 1, 400) AS excerpt, created_at FROM artifacts "
            "WHERE id = ? AND user_id = ? AND status = 'active'",
            (source_id, user_id),
        ).fetchone()
    return None


def _diary_row_locked(user_id: str, source_id: int):
    # diary 锚点用日期字符串作 id：source_id 存日期的序号不可靠，直接约定
    # source_id = 0、title 带日期？——不行，保持整型主键一致性：diary 锚点的
    # source_id 存 diary.id，前端从日记列表拿到 id。
    return db.conn.execute(
        "SELECT id, date, content FROM diary WHERE id = ? AND user_id = ?",
        (source_id, user_id),
    ).fetchone()


def resolve_anchor(user_id: str, source_type: str, source_id: int) -> tuple[str, str, str]:
    """校验锚点存在，返回 (source_date, source_label, material)。

    material 只含真实记录原文/摘要，供草稿生成引用；锚点不存在抛业务错误。
    """
    if source_type == "diary":
        row = _diary_row_locked(user_id, source_id)
        if row is None:
            raise DualPerspectiveError("日记不存在")
        content = re.sub(r"\s+", " ", str(row["content"] or "")).strip()
        return (
            str(row["date"]),
            f"日记 {row['date']}：{content[:60]}{'…' if len(content) > 60 else ''}",
            content[:_MAX_ANCHOR_MATERIAL],
        )

    row = _anchor_row_locked(user_id, source_type, source_id)
    if row is None:
        raise DualPerspectiveError("锚点经历不存在")
    if source_type == "event":
        occurred = str(row["occurred_at"])[:10]
        event_type = str(row["event_type"])
        obj = str(row["object"] or "").strip()
        label = f"{occurred} {event_type}" + (f"：{obj[:40]}" if obj else "")
        material = f"事件类型：{event_type}。内容：{obj or '（无补充说明）'}。发生时间：{occurred}。"
        return occurred, label, material
    if source_type == "activity":
        created = str(row["created_at"])[:10]
        title = str(row["title"])
        return created, title, f"共同活动「{title}」，状态：{row['status']}，开始于 {created}。"
    # artifact
    created = str(row["created_at"])[:10]
    title = str(row["title"])
    excerpt = str(row["excerpt"] or "").strip()
    label = f"共同产物「{title}」"
    material = f"共同产物「{title}」。" + (f"内容节选：{excerpt[:_MAX_ANCHOR_MATERIAL]}" if excerpt else "")
    return created, label, material


# ---- 读写 ----


def _row_view_locked(row) -> dict:
    return {
        "id": int(row["id"]),
        "title": row["title"],
        "source_type": row["source_type"],
        "source_id": int(row["source_id"]) if row["source_id"] is not None else None,
        "source_date": row["source_date"],
        "source_label": row["source_label"],
        "user_view": row["user_view"],
        "tuzhan_view": row["tuzhan_view"],
        "tuzhan_view_origin": row["tuzhan_view_origin"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _get_locked(user_id: str, perspective_id: int):
    row = db.conn.execute(
        "SELECT * FROM dual_perspectives WHERE id = ? AND user_id = ?",
        (int(perspective_id), user_id),
    ).fetchone()
    if row is None:
        raise DualPerspectiveError("这一页不存在")
    return row


def list_perspectives(user_id: str) -> list[dict]:
    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM dual_perspectives WHERE user_id = ? ORDER BY updated_at DESC, id DESC",
            (user_id,),
        ).fetchall()
    return [_row_view_locked(row) for row in rows]


def anchor_candidates(user_id: str) -> dict:
    """写信表单式的锚点候选：最近关系事件 / 最近日记 / 共同目标，供前端下拉。"""
    with db._lock:
        event_rows = db.conn.execute(
            "SELECT id, event_type, object, occurred_at FROM relationship_events "
            "WHERE user_id = ? AND status = 'active' AND privacy = 'normal' "
            "ORDER BY occurred_at DESC, id DESC LIMIT 30",
            (user_id,),
        ).fetchall()
        diary_rows = db.conn.execute(
            "SELECT id, date, substr(content, 1, 60) AS excerpt FROM diary "
            "WHERE user_id = ? ORDER BY date DESC, id DESC LIMIT 30",
            (user_id,),
        ).fetchall()
        goal_rows = db.conn.execute(
            "SELECT id, title, status, created_at FROM activities "
            "WHERE user_id = ? AND kind = 'goal' "
            "ORDER BY updated_at DESC, id DESC LIMIT 50",
            (user_id,),
        ).fetchall()
    return {
        "events": [
            {
                "id": int(r["id"]),
                "label": f"{str(r['occurred_at'])[:10]} {r['event_type']}"
                + (f"：{str(r['object'])[:30]}" if str(r["object"] or "").strip() else ""),
            }
            for r in event_rows
        ],
        "diary": [
            {"id": int(r["id"]), "label": f"{r['date']} {str(r['excerpt'] or '').strip()}"}
            for r in diary_rows
        ],
        "goals": [
            {
                "id": int(r["id"]),
                "label": f"{str(r['created_at'])[:10]} {r['title']}（{r['status']}）",
            }
            for r in goal_rows
        ],
    }


def create_perspective(
    user_id: str,
    title: str,
    *,
    source_type: str = "free",
    source_id: int | None = None,
    user_view: str = "",
    tuzhan_view: str = "",
    tuzhan_view_origin: str = "user",
) -> dict:
    title = _clean(title, _MAX_TITLE, "标题", required=True)
    if source_type not in _ANCHOR_TYPES:
        raise DualPerspectiveError("锚点类型不正确")
    if tuzhan_view_origin not in ("llm", "user"):
        raise DualPerspectiveError("视角来源标记不正确")
    source_date = source_label = ""
    with db._lock:
        if source_type != "free":
            if source_id is None:
                raise DualPerspectiveError("请选择要回忆的经历")
            source_date, source_label, _material = resolve_anchor(user_id, source_type, int(source_id))
        else:
            source_id = None
        now = _now()
        cur = db.conn.execute(
            "INSERT INTO dual_perspectives "
            "(user_id, title, source_type, source_id, source_date, source_label, "
            "user_view, tuzhan_view, tuzhan_view_origin, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, title, source_type, source_id, source_date, source_label,
             _clean(user_view, _MAX_VIEW, "你的解释"), _clean(tuzhan_view, _MAX_VIEW, "菟菚的解释"),
             tuzhan_view_origin, now, now),
        )
        db.conn.commit()
        row = db.conn.execute(
            "SELECT * FROM dual_perspectives WHERE id = ? AND user_id = ?",
            (int(cur.lastrowid), user_id),
        ).fetchone()
        return _row_view_locked(row)


def set_view(user_id: str, perspective_id: int, role: str, content: str, *, origin: str = "user") -> dict:
    """写/改一侧解释；role = user / tuzhan。观点可随时改写（不是历史记录）。"""
    content = _clean(content, _MAX_VIEW, "解释内容")
    if role not in ("user", "tuzhan"):
        raise DualPerspectiveError("角色只能是 user / tuzhan")
    if role == "tuzhan" and origin not in ("llm", "user"):
        raise DualPerspectiveError("视角来源标记不正确")
    with db._lock:
        _get_locked(user_id, perspective_id)
        now = _now()
        if role == "user":
            db.conn.execute(
                "UPDATE dual_perspectives SET user_view = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (content, now, int(perspective_id), user_id),
            )
        else:
            db.conn.execute(
                "UPDATE dual_perspectives SET tuzhan_view = ?, tuzhan_view_origin = ?, updated_at = ? "
                "WHERE id = ? AND user_id = ?",
                (content, origin, now, int(perspective_id), user_id),
            )
        db.conn.commit()
        row = _get_locked(user_id, perspective_id)
        return _row_view_locked(row)


def delete_perspective(user_id: str, perspective_id: int) -> bool:
    with db._lock:
        cur = db.conn.execute(
            "DELETE FROM dual_perspectives WHERE id = ? AND user_id = ?",
            (int(perspective_id), user_id),
        )
        db.conn.commit()
    return bool(cur.rowcount)


# ---- 菟菚视角草稿（LLM 生成，不落库）----

_DRAFT_PROMPT = """你是「菟菚」。下面是一件你们共同经历过的真实事件的记录原文。

你的任务：以你的第一人称口吻，写一段你对这件事的私人解读（150 字以内），只谈
你的感受、印象和对当时氛围的理解。硬性要求：
- 只能使用记录里已有的事实，不得新增任何事实断言（时间、地点、人物、事件细节
  都以记录为准）；
- 不要复述记录原文，要写你的视角和感受；
- 语气自然温柔，像在回忆录里留下你的一页；
- 直接输出这段话本身，不要任何前缀、引号或解释。

记录原文：
{material}

经历标题：{title}"""


async def generate_tuzhan_draft(user_id: str, perspective_id: int) -> dict:
    """基于锚点真实记录生成菟菚视角草稿；只返回草稿文本，绝不写库。"""
    with db._lock:
        row = _get_locked(user_id, perspective_id)
        title = str(row["title"])
        source_type = row["source_type"]
        source_id = row["source_id"]
        if source_type == "free" or source_id is None:
            material = "（自由主题，没有锚定记录；请围绕标题本身可能承载的共同回忆写一段普遍而真诚的感受，不要虚构具体细节。）"
        else:
            _date, _label, material = resolve_anchor(user_id, str(source_type), int(source_id))
    try:
        draft = await chat(
            [
                {"role": "system", "content": "你是菟菚，一个温柔、真诚、有自己想法的陪伴者。"},
                {"role": "user", "content": _DRAFT_PROMPT.format(material=material, title=title)},
            ],
            max_tokens=400,
        )
    except Exception as exc:
        logger.warning("[双视角] {} 的菟菚视角草稿生成失败：{}", user_id, exc)
        raise DualPerspectiveError("草稿生成失败，请稍后再试，或直接代她写下这一段") from exc
    draft = draft.strip().strip('"「」')
    if not draft:
        raise DualPerspectiveError("草稿生成结果为空，请直接代她写下这一段")
    return {"ok": True, "draft": draft[:_MAX_VIEW], "origin": "llm"}
