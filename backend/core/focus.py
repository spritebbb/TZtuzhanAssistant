# -*- coding: utf-8 -*-
"""M3.2 专注陪伴：25/50 分钟安静模式的完整状态机。

设计要点（对齐 TECH-PLAN M3.2 与架构原则）：
- 复用 activities 表（kind='focus'，document_id=0 哨兵），不新建生命周期表；
  reset/备份/迁移清单因此自动覆盖，无需新增条目。
- 状态机：active ↔ paused → completed / cancelled；中断取消只留状态，
  不产生事件与复盘；自然到点采用惰性结算（任意读取时发现超时就自动完成），
  不依赖后台定时器，重启后按持久化的 ends_at 继续。
- 专注进行中主动引擎静默：initiative 的三处仲裁点查询 focus_in_progress，
  已暂停/已超时（等同结束）的专注不再静默。
- 结束只做简短复盘、不绩效评判：complete（自然到点或手动结束且实际
  专注满 _WRAPUP_MIN_ELAPSED_SEC）时记录 focus_finished 事件，并由 API 层
  异步触发一句她的收尾；复盘是用户发起的活动闭环消息，不占用每日主动额度。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from .activities import ActivityError, pause_all_active_locked
from .log import logger
from .userdb import db

ALLOWED_MINUTES = (25, 50)
_SENTINEL_DOC_ID = 0
_WRAPUP_MIN_ELAPSED_SEC = 300  # 实际专注不足 5 分钟不触发复盘（避免秒开秒关也凑一句）
_FOCUS_CUE_RE = re.compile(
    r"专注|番茄钟|番茄工作|计时|陪我(?:学习|工作|写|赶|看)|"
    r"安静一会|别打扰我|我要(?:学习|工作|赶稿|写作业)了?"
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _row_to_detail(row) -> dict:
    planned = int(row["planned_minutes"] or 0)
    status = row["status"]
    ends_at = row["ends_at"]
    if status == "active" and ends_at:
        remaining = max(0, int((_parse_ts(ends_at) - datetime.now()).total_seconds()))
    else:
        remaining = max(0, int(row["remaining_seconds"] or 0))
    return {
        "id": int(row["id"]),
        "kind": "focus",
        "title": row["title"],
        "status": status,
        "planned_minutes": planned,
        "remaining_seconds": remaining,
        "elapsed_seconds": max(0, planned * 60 - remaining),
        "ends_at": ends_at if status == "active" else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }


def _get_row_locked(user_id: str, activity_id: int):
    return db.conn.execute(
        "SELECT * FROM activities WHERE user_id = ? AND id = ? AND kind = 'focus'",
        (user_id, activity_id),
    ).fetchone()


def get_focus(user_id: str, activity_id: int) -> dict | None:
    with db._lock:
        row = _get_row_locked(user_id, activity_id)
        return _row_to_detail(row) if row else None


def list_focus(user_id: str, limit: int = 10) -> list[dict]:
    limit = max(1, min(30, int(limit)))
    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM activities WHERE user_id = ? AND kind = 'focus' "
            "ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [_row_to_detail(row) for row in rows]


def start_focus(user_id: str, minutes: int) -> dict:
    """开始一段专注；同时只活跃一场，其它进行中的活动（含共读）被暂停。"""
    if int(minutes) not in ALLOWED_MINUTES:
        raise ActivityError(f"专注时长只支持 {' / '.join(map(str, ALLOWED_MINUTES))} 分钟")
    minutes = int(minutes)
    now = _now()
    ends_at = (datetime.now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    with db._lock:
        pause_all_active_locked(user_id, now)
        cur = db.conn.execute(
            "INSERT INTO activities "
            "(user_id, kind, document_id, title, status, position, "
            "planned_minutes, remaining_seconds, ends_at, created_at, updated_at) "
            "VALUES (?, 'focus', ?, ?, 'active', 0, ?, ?, ?, ?, ?)",
            (
                user_id,
                _SENTINEL_DOC_ID,
                f"专注 {minutes} 分钟",
                minutes,
                minutes * 60,
                ends_at,
                now,
                now,
            ),
        )
        activity_id = int(cur.lastrowid)
        db.conn.commit()
        row = _get_row_locked(user_id, activity_id)
    return _row_to_detail(row)


def pause_focus(user_id: str, activity_id: int) -> dict:
    """进行中 → 暂停：结算剩余时间并清空 ends_at。"""
    now = _now()
    with db._lock:
        row = _get_row_locked(user_id, activity_id)
        if row is None:
            raise ActivityError("这段专注不存在")
        if row["status"] != "active":
            raise ActivityError("只有进行中的专注才能暂停")
        remaining = max(
            0, int((_parse_ts(row["ends_at"]) - datetime.now()).total_seconds())
        )
        db.conn.execute(
            "UPDATE activities SET status = 'paused', remaining_seconds = ?, "
            "ends_at = NULL, updated_at = ? WHERE id = ?",
            (remaining, now, activity_id),
        )
        db.conn.commit()
        row = _get_row_locked(user_id, activity_id)
    return _row_to_detail(row)


def resume_focus(user_id: str, activity_id: int) -> dict:
    """暂停 → 继续：按结算过的剩余时间重算 ends_at；互斥暂停其它活动。"""
    now = _now()
    with db._lock:
        row = _get_row_locked(user_id, activity_id)
        if row is None:
            raise ActivityError("这段专注不存在")
        if row["status"] != "paused":
            raise ActivityError("只有已暂停的专注才能继续")
        remaining = max(0, int(row["remaining_seconds"] or 0))
        if remaining <= 0:
            raise ActivityError("这段专注时间已到，直接结束就好")
        pause_all_active_locked(user_id, now)
        ends_at = (datetime.now() + timedelta(seconds=remaining)).isoformat(timespec="seconds")
        db.conn.execute(
            "UPDATE activities SET status = 'active', ends_at = ?, updated_at = ? "
            "WHERE id = ?",
            (ends_at, now, activity_id),
        )
        db.conn.commit()
        row = _get_row_locked(user_id, activity_id)
    return _row_to_detail(row)


def wrapup_eligible(detail: dict) -> bool:
    """是否该由她说一句收尾：完成了、且实际专注过一小段（防秒开秒关）。"""
    return (
        detail.get("status") == "completed"
        and int(detail.get("elapsed_seconds") or 0) >= _WRAPUP_MIN_ELAPSED_SEC
    )


def _record_focus_finished_locked(user_id: str, activity_id: int, detail: dict, now: str) -> None:
    """幂等写入 focus_finished 事件（同一活动只留一条 active 事件）。"""
    from .relationship_events import record

    record(
        user_id,
        "focus_finished",
        "activity",
        activity_id,
        subject=user_id,
        obj=detail["title"],
        payload={
            "planned_minutes": detail["planned_minutes"],
            "elapsed_seconds": detail["elapsed_seconds"],
        },
        confidence=1.0,
        occurred_at=now,
        commit=False,
    )


def complete_focus(user_id: str, activity_id: int) -> dict:
    """结束（完成）：进行中或暂停都可主动收尾；记录事件，复盘资格由 wrapup_eligible 判定。"""
    now = _now()
    with db._lock:
        row = _get_row_locked(user_id, activity_id)
        if row is None:
            raise ActivityError("这段专注不存在")
        if row["status"] not in ("active", "paused"):
            raise ActivityError("这段专注已经结束了")
        # 结算剩余时间：进行中的按 ends_at 实算，暂停的用冻结值——
        # 否则 completed 行的 elapsed_seconds 会按开始时的初始剩余错算。
        if row["status"] == "active" and row["ends_at"]:
            remaining = max(
                0, int((_parse_ts(row["ends_at"]) - datetime.now()).total_seconds())
            )
        else:
            remaining = max(0, int(row["remaining_seconds"] or 0))
        db.conn.execute(
            "UPDATE activities SET status = 'completed', ends_at = NULL, "
            "remaining_seconds = ?, completed_at = ?, updated_at = ? WHERE id = ?",
            (remaining, now, now, activity_id),
        )
        detail = _row_to_detail(_get_row_locked(user_id, activity_id))
        _record_focus_finished_locked(user_id, activity_id, detail, now)
        # F04：完成事件先写，收尾消息同事务入箱（后台重试，不占主动额度）
        _enqueue_wrapup_locked(user_id, activity_id, detail, now)
        db.conn.commit()
    return detail


def cancel_focus(user_id: str, activity_id: int) -> dict:
    """中断（取消）：只留状态，不产生事件与复盘。"""
    now = _now()
    with db._lock:
        row = _get_row_locked(user_id, activity_id)
        if row is None:
            raise ActivityError("这段专注不存在")
        if row["status"] not in ("active", "paused"):
            raise ActivityError("这段专注已经结束了")
        db.conn.execute(
            "UPDATE activities SET status = 'cancelled', ends_at = NULL, "
            "completed_at = ?, updated_at = ? WHERE id = ?",
            (now, now, activity_id),
        )
        # F04：取消的活动永不发收尾
        db.conn.execute(
            "UPDATE wrapup_outbox SET status = 'cancelled', updated_at = ? "
            "WHERE user_id = ? AND activity_id = ? AND status = 'pending'",
            (now, user_id, activity_id),
        )
        db.conn.commit()
        row = _get_row_locked(user_id, activity_id)
    return _row_to_detail(row)


def export_markdown(user_id: str, activity_id: int) -> str:
    """导出专注记录；只陈述计时事实，不做绩效评价。"""
    detail = get_focus(user_id, activity_id)
    if detail is None:
        raise ActivityError("这段专注不存在")
    status_label = {
        "active": "进行中",
        "paused": "已暂停",
        "completed": "已结束",
        "cancelled": "已中断",
    }.get(detail["status"], str(detail["status"]))
    elapsed_minutes = max(0, round(detail["elapsed_seconds"] / 60))
    lines = [
        f"# {detail['title']}",
        "",
        f"- 状态：{status_label}",
        f"- 计划时长：{detail['planned_minutes']} 分钟",
        f"- 已经过：约 {elapsed_minutes} 分钟",
        f"- 开始：{detail['created_at']}",
    ]
    if detail["completed_at"]:
        lines.append(f"- 结束：{detail['completed_at']}")
    lines.extend(["", "> 这是一段计时记录，不包含完成度、评分或绩效判断。", ""])
    return "\n".join(lines)


def current_focus(user_id: str, *, settle: bool = True) -> tuple[dict | None, bool]:
    """最近一段未结束的专注；超时未结算的在此惰性完成。

    返回 (detail, just_finished)：just_finished=True 表示本次读取触发了
    自然到点结算，调用方（API）可据此安排复盘。
    """
    just_finished = False
    with db._lock:
        row = db.conn.execute(
            "SELECT * FROM activities WHERE user_id = ? AND kind = 'focus' "
            "AND status IN ('active', 'paused') ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if row is None:
            return None, False
        if (
            row["status"] == "active"
            and row["ends_at"]
            and _parse_ts(row["ends_at"]) <= datetime.now()
        ):
            if not settle:
                # 临时对话等只读调用：展示“已结束”的事实，但不在读取时完成结算、
                # 创建 artifact 或关系事件。下一次普通读取仍会走权威结算路径。
                detail = _row_to_detail(row)
                detail.update({
                    "status": "completed",
                    "remaining_seconds": 0,
                    "elapsed_seconds": int(detail["planned_minutes"]) * 60,
                })
                return detail, False
            # 自然到点：惰性完成（不依赖后台定时器，重启后也能正确结算）。
            # 时间已跑完，剩余结算为 0，elapsed 才等于计划时长。
            now = _now()
            activity_id = int(row["id"])
            db.conn.execute(
                "UPDATE activities SET status = 'completed', ends_at = NULL, "
                "remaining_seconds = 0, completed_at = ?, updated_at = ? WHERE id = ?",
                (now, now, activity_id),
            )
            detail = _row_to_detail(_get_row_locked(user_id, activity_id))
            _record_focus_finished_locked(user_id, activity_id, detail, now)
            db.conn.commit()
            return detail, True
        return _row_to_detail(row), just_finished


def focus_in_progress(user_id: str) -> bool:
    """只读判定：此刻是否处于有效专注中（供主动引擎静默）。

    已暂停、已超时（等同结束，待惰性结算）的专注不静默——
    暂停期间用户显然在与她交互，超时则专注已事实结束。
    """
    try:
        with db._lock:
            row = db.conn.execute(
                "SELECT ends_at FROM activities WHERE user_id = ? AND kind = 'focus' "
                "AND status = 'active' ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
    except Exception:
        return False
    if row is None or not row["ends_at"]:
        return False
    return _parse_ts(row["ends_at"]) > datetime.now()


def focus_context(user_id: str, query: str, *, settle: bool = True) -> str:
    """仅在用户明显谈到专注/计时时注入当前专注状态，普通闲聊零注入。"""
    if not query or not _FOCUS_CUE_RE.search(query):
        return ""
    try:
        detail, just_finished = current_focus(user_id, settle=settle)
    except Exception:
        return ""
    if detail is None:
        return ""
    if just_finished or detail["status"] == "completed":
        return (
            f"你们刚结束一段 {detail['planned_minutes']} 分钟的专注陪伴。"
            "对方若提起，自然接一句收尾的话；不要评价他做得好不好、不要打分统计。"
        )
    if detail["status"] == "active":
        minutes_left = max(1, round(detail["remaining_seconds"] / 60))
        return (
            f"你们正在一段 {detail['planned_minutes']} 分钟的专注陪伴里，"
            f"还剩约 {minutes_left} 分钟。你在安静陪他，不主动找话题；"
            "他来找你说话时回复保持简短、安静，别把他聊跑神。"
        )
    return (
        f"你们有一段 {detail['planned_minutes']} 分钟的专注暂停着。"
        "他提到时自然回应，可以提醒他继续或结束。"
    )


MAX_WRAPUP_ATTEMPTS = 2
_WRAPUP_MATERIAL_MAX = 100


def build_wrapup_material(detail: dict, state=None) -> str:
    """收尾素材：只取真实已用时长/是否中断/用户显式目标，≤100 字。

    不评分、不声称用户完成了目标（F04 契约）。
    """
    parts: list[str] = []
    elapsed = int(detail.get("elapsed_seconds") or 0)
    if elapsed:
        parts.append(f"实际专注约 {max(1, round(elapsed / 60))} 分钟")
    if detail.get("status") == "cancelled":
        parts.append("这段是提前结束的")
    goal = str(detail.get("goal") or "").strip()
    if goal:
        parts.append(f"他开始时说要做的是「{goal[:30]}」")
    if state is not None:
        frame_line = str(getattr(state, "mood_line", "") or "").strip()
        if frame_line:
            parts.append(frame_line[:24])
    text = "；".join(parts)
    return text[:_WRAPUP_MATERIAL_MAX]


def _enqueue_wrapup_locked(user_id: str, activity_id: int, detail: dict, now: str) -> None:
    """完成专注时入箱（幂等：同 user/activity 只一条）。"""
    from .features import flag

    if not flag("focus_wrapup_enabled") or not wrapup_eligible(detail):
        return
    db.conn.execute(
        "INSERT OR IGNORE INTO wrapup_outbox "
        "(user_id, activity_id, status, candidate_text, attempt, delivery_id, "
        "created_at, updated_at) VALUES (?, ?, 'pending', ?, 0, ?, ?, ?)",
        (user_id, int(activity_id), build_wrapup_material(detail),
         f"wrapup:{user_id}:{int(activity_id)}", now, now),
    )


def _claim_wrapup(user_id: str, activity_id: int, *, now: str) -> dict | None:
    """认领一条待投递收尾；重试上限 2 次。"""
    with db._lock:
        row = db.conn.execute(
            "SELECT * FROM wrapup_outbox WHERE user_id=? AND activity_id=? "
            "AND status='pending' AND attempt < ?",
            (user_id, int(activity_id), MAX_WRAPUP_ATTEMPTS),
        ).fetchone()
        if row is None:
            return None
        db.conn.execute(
            "UPDATE wrapup_outbox SET attempt = attempt + 1, updated_at=? WHERE id=?",
            (now, int(row["id"])),
        )
        db.conn.commit()
    return dict(row)


def _finish_wrapup(user_id: str, activity_id: int, ok: bool, *, now: str) -> None:
    with db._lock:
        db.conn.execute(
            "UPDATE wrapup_outbox SET status=?, updated_at=? WHERE user_id=? AND activity_id=?",
            ("sent" if ok else "failed", now, user_id, int(activity_id)),
        )
        db.conn.commit()


def wrapup_pending(user_id: str, activity_id: int) -> dict | None:
    with db._lock:
        row = db.conn.execute(
            "SELECT * FROM wrapup_outbox WHERE user_id=? AND activity_id=?",
            (user_id, int(activity_id)),
        ).fetchone()
    return dict(row) if row is not None else None


async def maybe_send_wrapup(user_id: str, activity_id: int) -> bool:
    """结束复盘：由她说一句简短收尾（不绩效评判）。

    经 enqueue_proactive 走既有投递队列（SSE + 落库），失败静默；
    这是用户发起的活动闭环消息，不消耗每日主动额度（语义保留）。
    F04：投递走 wrapup_outbox（唯一 user/activity、重试 ≤2 次、delivery_id 去重）；
    API 超时不回滚已完成的专注——完成事件与入箱都已先提交。
    """
    from .config import config

    if not config.focus_enabled:
        return False
    try:
        detail = get_focus(user_id, activity_id)
        if detail is None or not wrapup_eligible(detail):
            return False
        claim = _claim_wrapup(user_id, activity_id, now=_now())
        if claim is None:
            return False  # 已投递 / 已取消 / 重试用尽
        user = db.get_user(user_id)
        if not user:
            _finish_wrapup(user_id, activity_id, False, now=_now())
            return False
        from .affection import stage_of
        from .llm import chat
        from .persona import build_system_prompt

        affection_val = user["affection"] or 0
        sys_prompt = build_system_prompt(
            stage=stage_of(affection_val),
            address=user["nickname_pref"] or "",
            lover_confirm=bool(user["lover_confirm"]),
            first_chat=False,
            affection=affection_val,
            user_id=user_id,
        )
        material = build_wrapup_material(detail)
        minutes = detail["planned_minutes"]
        msgs = [
            {"role": "system", "content": sys_prompt},
            {
                "role": "user",
                "content": (
                    f"对方刚结束一段 {minutes} 分钟的专注，这段时间你一直安静陪着。"
                    + (f"（可参考的真实情况：{material}）" if material else "")
                    + "现在时间到了，你说一句简短的收尾：告诉他时间到了，"
                    "让他起来活动一下或喝口水。一两句就够。"
                    "不要评价他做得好不好，不要统计、不要打分、不要问成果；"
                    "符合你的人格和说话方式，别加括号动作。"
                ),
            },
        ]
        text = ""
        try:
            text = (await chat(msgs, max_tokens=80, temperature=0.8)).strip()[:160]
        except Exception as e:
            logger.warning("[专注] 复盘文案生成失败，按重试预算处理: {}", e)
        if not text:
            if int(claim["attempt"]) + 1 >= MAX_WRAPUP_ATTEMPTS:
                text = "时间到了，起来动一动，喝口水。"  # 确定性短回顾兜底
            else:
                return False  # 保持 pending，留给下一次重试
        from .initiative import enqueue_proactive

        ok = await enqueue_proactive(user_id, text)
        _finish_wrapup(user_id, activity_id, ok, now=_now())
        return ok
    except Exception as e:
        logger.warning("[专注] 复盘消息生成失败（不影响完成流程）: {}", e)
        return False
