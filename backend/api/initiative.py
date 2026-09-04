# -*- coding: utf-8 -*-
"""主动性接口：轮询拉取 + SSE 长连接推送菟菚的主动消息。"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ..api.chat import _user_id
from ..core.initiative import (
    dequeue_proactive_message,
    poll_message_for,
    sse_event_stream,
)

router = APIRouter(prefix="/api", tags=["initiative"])


@router.get("/initiative")
async def api_initiative(session_id: str = ""):
    """前端轮询（保留兼容）：检查菟菚是否「想主动找你」，是则返回一条主动消息。

    优先级：
    1. 待投递队列——后台 loop 已生成、用户离线时入队的消息（先取这个，不丢）；
    2. 即时生成——队列为空时按当前状态即时判断是否该主动（poll_for 兜底）。

    仅在「关系够近 + 久未聊天 + 今天还没主动过」时返回非空；
    否则返回 None，前端据此决定是否展示。
    """
    if not session_id:
        return {"ok": True, "initiative": None, "message": None}
    uid = _user_id(session_id)
    # 优先取后台已生成的待投递消息
    message = dequeue_proactive_message(uid)
    if message is None:
        message = await poll_message_for(uid)
    # initiative 保留纯文本兼容旧版桌面端；新版使用 message 获取可选图片。
    return {
        "ok": True,
        "initiative": message["text"] if message else None,
        "message": message,
    }


@router.get("/initiative/stream")
async def api_initiative_stream(session_id: str = ""):
    """SSE 长连接：订阅菟菚的主动消息，后台生成时秒级推送（替代 30s 轮询）。

    事件：`event: initiative`，data 为 {"text": "...", "image": "..." | null}。
    心跳：`:` 开头的注释帧，前端忽略即可（保活用）。
    """
    if not session_id:
        # 无会话仍返回一个会立即结束的空流，避免挂死
        return StreamingResponse(_empty_stream(), media_type="text/event-stream")
    uid = _user_id(session_id)
    return StreamingResponse(
        sse_event_stream(uid),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 关代理缓冲，保证实时
        },
    )


async def _empty_stream():
    yield ": no session\n\n"
