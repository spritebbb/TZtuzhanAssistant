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

# 单一会话模式下，用户身份统一为 assistant-main（与 agent 任务代理、meta 兜底、
# contextvar 默认值保持一致），保证聊天与任务代理共用同一份好感度/心情/记忆画像，
# 避免「同一个菟菚」在聊天和任务两条线上割裂成两条互不相通的数据记录。
def _user_id(session_id: str) -> str:
    return "assistant-main"

router = APIRouter(prefix="/api", tags=["chat"])

# 后台生成任务的强引用集合：asyncio.create_task 返回的 Task 若无强引用，
# 可能在任意 await 点被 GC 回收导致 _runner 被静默取消（回复不落库）。
# 与 pipeline._memory_tasks / agent._agent_bg_tasks 同一做法。
_bg_tasks: set[asyncio.Task] = set()


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@router.post("/chat")
async def api_chat(text: str = Form(""), session_id: str = Form(""), mock: bool = Form(False), image: str = Form("")):
    """SSE 流式对话：逐字推送 data: {"piece": "..."}，结束时发 {"done": "完整回复"}。

    单一会话模式：session_id 固定为 'current'。不传则自动使用固定会话；
    传了则校验是否为 'current'，其余 id 一律视为不存在。

    可选 image：识图等场景下 user 消息附带的图片 URL（已落盘的 /api/images/...），
    会随 user 消息一起持久化，保证刷新/归档后仍能看到原图。
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

    # 记录用户消息（立即持久化；识图场景附带 image）
    user_msg = {"role": "user", "content": text, "ts": time.time()}
    if image:
        user_msg["image"] = image
    saved = await append_messages(session_id, [user_msg])
    if not saved:
        # 会话可能在校验后被删除：用户消息不落库会静默丢失，这里明确报错
        from ..core.log import logger as _lg

        _lg.warning("[chat] 用户消息持久化失败（会话 {} 可能已不存在）", session_id)
        return JSONResponse({"ok": False, "error": "会话不存在或已删除，请刷新页面"}, status_code=410)

    q: asyncio.Queue = asyncio.Queue()
    # 共享状态：在 _runner（后台任务）和 SSE 生成器之间传递
    _state: dict = {"pending_img": None}
    # 累积流式已推送的文本片段（不含控制标记 \x00...\x00）：供 _cb 追加、_runner 在
    # 流式中途失败时落库「已生成的部分回复」，避免刷新/归档后这段内容丢失。
    _partial: list[str] = []

    async def _cb(piece: str) -> None:
        # 控制字符包裹的标记（\x00RESET\x00 / \x00IMAGESTART\x00）只用于前端指令，
        # 不计入「已生成的回复正文」；真正的文本片段才累积，供流式中途失败时落库。
        if not (isinstance(piece, str) and piece.startswith("\x00") and piece.endswith("\x00")):
            _partial.append(piece)
        await q.put(piece)

    async def _image_cb(local_path: str) -> None:
        # 生图成功：把本地路径转成 Web URL 推给前端（前端据此渲染 <img>）
        name = Path(local_path).name
        url = f"/api/images/{name}"
        _state["pending_img"] = url
        await q.put(("__image__", url))

    async def _progress_cb(event: dict) -> None:
        # 工具循环阶段进展：把事件透传给前端（前端气泡显示「正在思考/调用 XX」）
        await q.put(("__tool__", event))

    async def _runner() -> None:
        """后台生成任务：完成时自行持久化，不依赖 SSE 连接生命周期。"""
        # 设置当前会话的用户身份（工具/记忆/待办按此隔离）
        current_user_id.set(_user_id(session_id))
        # 设置当前会话的 SSE 推送器（供确认钩子把 confirm_request 推给前端）
        from ..tools.confirm import current_sse_push

        async def _push_event(event: dict) -> None:
            await q.put(("__confirm__", event))

        current_sse_push.set(_push_event)
        # _partial 在 api_chat 作用域定义，_cb 与 _runner 共享（流式片段累积 + 中途失败落库）
        # 总超时：process 内部虽有 LLM/子进程等各环节超时，但 to_thread 内的
        # 同步调用（embedding 下载/DNS 解析等）可能远超单环节超时，这里兜底，
        # 防止客户端断开后后台任务无限累积
        _PROCESS_TOTAL_TIMEOUT = 300
        try:
            reply = await asyncio.wait_for(
                process(_user_id(session_id), text, mock=mock, stream_cb=_cb, image_cb=_image_cb, progress_cb=_progress_cb),
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
            # 流式中途失败：把已流式输出、但 process 未成功返回完整回复的「部分文本」
            # 落库，保证刷新/归档后这段已生成的回复不丢失；内部异常细节只写日志、不外泄。
            partial = "".join(_partial).strip()
            if partial:
                bot_msg = {"role": "bot", "content": partial, "ts": time.time()}
                if _state.get("pending_img"):
                    bot_msg["image"] = _state["pending_img"]
                await append_messages(session_id, [bot_msg])
            # 面向用户的通用错误提示（不泄露内部异常原文），并作为 error 帧回传。
            note = "回复生成中断，请重试"
            await append_messages(session_id, [{"role": "bot", "content": note, "ts": time.time()}])
            await q.put(("__error__", note))
        finally:
            current_sse_push.set(None)

    async def sse() -> AsyncGenerator[str, None]:
        task = asyncio.create_task(_runner())
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
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
                if isinstance(item, tuple) and item[0] == "__tool__":
                    # 工具循环进度事件：转发给前端展示「正在思考/调用 XX」
                    yield _sse({"tool": item[1]})
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
