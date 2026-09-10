# -*- coding: utf-8 -*-
"""Q3 本地可观测性查询与用户清理入口。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query

from ..core.persona_profiles import active_user_id

router = APIRouter(prefix="/api/telemetry", tags=["telemetry"])

_EVENT_LABELS = {
    "chat_started": "开始处理消息",
    "tool_started": "开始使用工具",
    "tool_finished": "工具执行完成",
    "output_checked": "回复已完成发送前检查",
    "session_persisted": "消息已保存到本地会话",
    "outbox_persisted": "主动消息已进入本地待发送队列",
    "chat_completed": "消息处理完成",
    "chat_failed": "消息处理未完成",
    "job_started": "后台任务开始",
    "job_finished": "后台任务完成",
}
_OUTCOME_LABELS = {
    "started": "进行中", "success": "成功", "failure": "失败",
    "cancelled": "已取消", "timeout": "超时", "accept": "通过",
    "rewrite": "已安全重写", "fallback": "已使用安全兜底",
}


def _public_trace_item(item: dict) -> dict:
    sources = []
    for source in item.get("source_ids") or []:
        if source.startswith("tool:"):
            sources.append("工具：" + source.removeprefix("tool:"))
        elif source == "session:user":
            sources.append("本地会话中的用户消息")
        elif source == "session:assistant":
            sources.append("本地会话中的助手回复")
        elif source.startswith("rule:"):
            sources.append("发送前安全规则")
    return {
        "event": _EVENT_LABELS.get(item.get("event_name"), "本地处理步骤"),
        "sources": sources,
        "result": _OUTCOME_LABELS.get(item.get("outcome"), ""),
        "duration": item.get("duration_bucket") or "",
        "time": item.get("created_at") or "",
    }


@router.get("")
async def api_telemetry_summary(days: int = Query(30, ge=1, le=365)):
    from ..core.telemetry import enabled, summary

    if not enabled():
        return {"ok": True, "enabled": False, "items": []}
    items = await asyncio.to_thread(summary, active_user_id(), days=days)
    return {"ok": True, "enabled": True, "items": items}


@router.get("/trace/{request_id}")
async def api_telemetry_trace(request_id: str):
    from ..core.telemetry import enabled, trace_events

    if not enabled():
        return {"ok": True, "enabled": False, "events": []}
    events = await asyncio.to_thread(trace_events, active_user_id(), request_id)
    return {
        "ok": True,
        "enabled": True,
        "request_id": request_id,
        "events": [_public_trace_item(item) for item in events],
    }


@router.delete("")
async def api_telemetry_clear():
    from ..core.telemetry import clear_user

    removed = await asyncio.to_thread(clear_user, active_user_id())
    return {"ok": True, "removed": removed}
