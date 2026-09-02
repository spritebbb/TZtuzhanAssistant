# -*- coding: utf-8 -*-
"""SSE 流式对话接口。"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter, Form
from fastapi.responses import JSONResponse, StreamingResponse

from ..core.log import logger
from ..core.pipeline import process
from ..core.current_user import current_user_id
from ..session.store import CURRENT_SESSION_ID, append_messages, get_messages

# 从 session_id 派生用户身份（每个会话完全隔离，互不影响）
def _user_id(session_id: str) -> str:
    return f"session_{session_id}"

router = APIRouter(prefix="/api", tags=["chat"])


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@router.post("/chat")
async def api_chat(text: str = Form(""), session_id: str = Form(""), mock: bool = Form(False)):
    """SSE 流式对话：逐字推送 data: {"piece": "..."}，结束时发 {"done": "完整回复"}。

    单一会话模式：session_id 固定为 'current'。不传则自动使用固定会话；
    传了则校验是否为 'current'，其余 id 一律视为不存在。
    """
    text = text.strip()
    if not text:
        return JSONResponse({"ok": False, "error": "消息为空"}, status_code=400)

    # 单一会话：无 session_id 时落到固定会话 'current'（首次写入时 store 已保证存在）
    if not session_id:
        session_id = CURRENT_SESSION_ID

    msgs = await get_messages(session_id)
    if msgs is None:
        return JSONResponse({"ok": False, "error": "会话不存在，请刷新页面"}, status_code=404)

    # 记录用户消息（立即持久化）
    saved = await append_messages(session_id, [{"role": "user", "content": text, "ts": time.time()}])
    if not saved:
        # 会话可能在校验后被删除：用户消息不落库会静默丢失，这里明确报错
        from ..core.log import logger as _lg

        _lg.warning("[chat] 用户消息持久化失败（会话 {} 可能已不存在）", session_id)
        return JSONResponse({"ok": False, "error": "会话不存在或已删除，请刷新页面"}, status_code=410)

    q: asyncio.Queue = asyncio.Queue()
    # 共享状态：在 _runner（后台任务）和 SSE 生成器之间传递
    _state: dict = {"pending_img": None}

    async def _cb(piece: str) -> None:
        await q.put(piece)

    async def _image_cb(local_path: str) -> None:
        # 生图成功：把本地路径转成 Web URL 推给前端（前端据此渲染 <img>）
        name = Path(local_path).name
        url = f"/api/images/{name}"
        _state["pending_img"] = url
        await q.put(("__image__", url))

    async def _runner() -> None:
        """后台生成任务：完成时自行持久化，不依赖 SSE 连接生命周期。"""
        # 设置当前会话的用户身份（工具/记忆/待办按此隔离）
        current_user_id.set(_user_id(session_id))
        # 设置当前会话的 SSE 推送器（供确认钩子把 confirm_request 推给前端）
        from ..tools.confirm import current_sse_push

        async def _push_event(event: dict) -> None:
            await q.put(("__confirm__", event))

        current_sse_push.set(_push_event)
        # 总超时：process 内部虽有 LLM/子进程等各环节超时，但 to_thread 内的
        # 同步调用（embedding 下载/DNS 解析等）可能远超单环节超时，这里兜底，
        # 防止客户端断开后后台任务无限累积
        _PROCESS_TOTAL_TIMEOUT = 300
        try:
            reply = await asyncio.wait_for(
                process(_user_id(session_id), text, mock=mock, stream_cb=_cb, image_cb=_image_cb),
                timeout=_PROCESS_TOTAL_TIMEOUT,
            )
            # 后台完成：持久化 bot 消息到原会话（即使客户端已断开）
            bot_msg = {"role": "bot", "content": reply, "ts": time.time()}
            if _state.get("pending_img"):
                bot_msg["image"] = _state["pending_img"]
            await append_messages(session_id, [bot_msg])
            await q.put(("__done__", reply))
        except asyncio.TimeoutError:
            logger.warning(
                "[chat] 处理超时（{}s），会话 {} 已中止。副作用核对：sessions 已存用户消息；"
                "userdb 已存用户消息（assistant 回复与长期记忆未写入，后台向量任务未调度）",
                _PROCESS_TOTAL_TIMEOUT, session_id,
            )
            note = f"处理超时（>{_PROCESS_TOTAL_TIMEOUT}s），请重试或换个说法"
            # 失败也补存一条 bot 消息：避免会话历史出现"只有用户消息、没有回复"的残缺回合
            await append_messages(session_id, [{"role": "bot", "content": note, "ts": time.time()}])
            await q.put(("__error__", note))
        except Exception as e:
            logger.exception("[chat] 处理用户消息失败（会话 {}）", session_id)
            note = f"{type(e).__name__}: {e}"
            # 同上：错误也落一条 bot 消息，保证每条 user 消息都有对应回复记录
            await append_messages(session_id, [{"role": "bot", "content": note, "ts": time.time()}])
            await q.put(("__error__", note))
        finally:
            current_sse_push.set(None)

    async def sse() -> AsyncGenerator[str, None]:
        task = asyncio.create_task(_runner())
        try:
            while True:
                item = await q.get()
                if isinstance(item, tuple) and item[0] == "__confirm__":
                    # 工具确认请求：转发给前端，用户批准后 POST /api/confirm
                    yield _sse({"confirm_request": item[1]})
                    continue
                if isinstance(item, tuple) and item[0] == "__image__":
                    yield _sse({"image_url": item[1]})
                    continue
                if isinstance(item, tuple) and item[0] == "__done__":
                    yield _sse({"done": item[1]})
                    break
                if isinstance(item, tuple) and item[0] == "__error__":
                    yield _sse({"error": item[1]})
                    break
                if item == "\x00RESET\x00":
                    # 重复回复重写：让前端清空当前气泡重新累积
                    yield _sse({"reset": True})
                    continue
                if item == "\x00IMAGESTART\x00":
                    # 生图开始：前端显示占位提示（生图较慢）
                    yield _sse({"image_start": True})
                    continue
                yield _sse({"piece": item})
        finally:
            # 客户端断开 / 生成器关闭：不 cancel 任务，
            # 让后台继续完成生成并持久化到原会话
            pass

    return StreamingResponse(sse(), media_type="text/event-stream")
