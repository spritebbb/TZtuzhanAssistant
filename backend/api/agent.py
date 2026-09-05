# -*- coding: utf-8 -*-
"""Agent 长任务 API：创建计划、后台执行、SSE 进度与确认通道。"""
from __future__ import annotations

import asyncio
import contextvars
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..agent import session as agent_session
from ..core.log import logger
from ..core.persona_profiles import active_user_id

router = APIRouter(prefix="/api/agent", tags=["agent"])

# 运行中的 Agent 后台任务强引用（防 GC 静默取消）
_agent_bg_tasks: set[asyncio.Task] = set()
# task_id → asyncio.Task；取消接口必须能定位并真正取消对应协程。
_agent_bg_by_id: dict[str, asyncio.Task] = {}

# 延迟清理通道的后台任务强引用（防 _drop_channel_later 的 sleep 任务被 GC 回收，
# 导致对应 channel 永不清理、_task_channels 无限增长）
_channel_cleanup_tasks: set[asyncio.Task] = set()

# 每个任务的确认/进度通道（task_id → asyncio.Queue），由 POST /run 创建、
# GET stream 消费；任务结束后保留最近事件供迟到连接补看
_task_channels: dict[str, asyncio.Queue] = {}


def _sse(obj: dict) -> str:
    import json
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _channel(task_id: str) -> asyncio.Queue:
    return _task_channels.setdefault(task_id, asyncio.Queue(maxsize=200))


async def _drop_channel_later(task_id: str, delay: float = 300.0) -> None:
    """任务结束后延迟删除事件通道，避免 _task_channels 无限增长；
    延迟期间迟到的 SSE 连接仍能看到最近事件。"""
    try:
        await asyncio.sleep(delay)
        _task_channels.pop(task_id, None)
    except asyncio.CancelledError:
        pass


@router.post("/tasks")
async def api_agent_create(request: Request):
    """创建长任务：LLM 生成计划，返回任务与计划。

    兼容三种传参：query、form body（前端 AgentPanel 的方式）、JSON body。
    历史 bug：普通类型参数只从 query 绑定，form 传参被忽略 → 恒 400
    "缺少目标"，与 /api/confirm 同类问题（HTTP 层无测试覆盖导致漏网）。
    """
    objective = (request.query_params.get("objective") or "").strip()
    user_id = (request.query_params.get("user_id") or "").strip()
    if not objective:
        ctype = (request.headers.get("content-type") or "").lower()
        try:
            if "json" in ctype:
                body = await request.json()
                body = body if isinstance(body, dict) else {}
            else:
                form = await request.form()
                body = {k: v for k, v in form.items()}
        except Exception:
            body = {}
        objective = str(body.get("objective") or "").strip()
        user_id = user_id or str(body.get("user_id") or "").strip()
    if not objective:
        return JSONResponse({"ok": False, "error": "缺少目标"}, status_code=400)
    if len(objective) > 20_000 or len(user_id) > 64:
        return JSONResponse({"ok": False, "error": "目标或用户标识过长"}, status_code=413)
    from ..core.reset import reset_in_progress
    if reset_in_progress():
        return JSONResponse({"ok": False, "error": "正在重置，请稍后再试"}, status_code=409)
    uid = user_id or active_user_id()
    try:
        task = await agent_session.create_task(uid, objective)
        return {"ok": True, "task": agent_session.to_dict(task)}
    except Exception as e:
        logger.exception("[Agent] 创建任务失败")
        return JSONResponse({"ok": False, "error": f"创建失败：{e}"}, status_code=500)


@router.get("/tasks")
async def api_agent_list(user_id: str = ""):
    """任务列表。"""
    uid = user_id or active_user_id()
    return {"ok": True, "tasks": agent_session.list_tasks(uid)}


@router.get("/tasks/{task_id}")
async def api_agent_get(task_id: str):
    """查询单个任务状态。"""
    task = agent_session._load(task_id)
    if task is None:
        return JSONResponse({"ok": False, "error": "任务不存在"}, status_code=404)
    return {"ok": True, "task": agent_session.to_dict(task)}


@router.post("/tasks/{task_id}/confirm-step")
async def api_agent_confirm_step(task_id: str, step_index: int = -1, allow: bool = True):
    """确认/拒绝计划中的某一步（step_index 从 0 开始）。"""
    if step_index < 0:
        return JSONResponse({"ok": False, "error": "缺少 step_index"}, status_code=400)
    try:
        task = agent_session.confirm_step(task_id, step_index, allow)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    if task is None:
        return JSONResponse({"ok": False, "error": "任务不存在"}, status_code=404)
    return {"ok": True, "task": agent_session.to_dict(task)}


@router.post("/tasks/{task_id}/confirm-all")
async def api_agent_confirm_all(task_id: str, allow: bool = True):
    """整体放行/拒绝计划所有步骤。"""
    task = agent_session.confirm_all(task_id, allow)
    if task is None:
        return JSONResponse({"ok": False, "error": "任务不存在"}, status_code=404)
    return {"ok": True, "task": agent_session.to_dict(task)}


@router.post("/tasks/{task_id}/run")
async def api_agent_run(task_id: str):
    """开始执行任务（后台）。确认事件经 GET /tasks/{id}/stream 推送。"""
    from ..core.reset import reset_epoch, reset_in_progress
    if reset_in_progress():
        return JSONResponse({"ok": False, "error": "正在重置，请稍后再试"}, status_code=409)
    request_epoch = reset_epoch()
    task = agent_session._load(task_id)
    if task is None:
        return JSONResponse({"ok": False, "error": "任务不存在"}, status_code=404)
    if task.status in ("running", "done"):
        return JSONResponse({"ok": False, "error": f"任务已在 {task.status} 状态"}, status_code=409)
    if task.status == "cancelled":
        # 取消后旧的后台执行可能仍在收尾，重跑同一任务会互相覆盖状态；
        # 明确拒绝并提示新建任务，避免"返回 running 但实际什么都没执行"
        return JSONResponse(
            {"ok": False, "error": "任务已取消，无法重新执行，请新建任务"},
            status_code=409,
        )

    queue = _channel(task_id)

    async def push(event: dict) -> None:
        # 丢弃已满的旧事件，保留最新（确认请求很重要，尽力推）
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(event)
            except Exception:
                pass

    # 执行上下文里注入 SSE 推送器（供 confirm_hook 使用）
    from ..core.current_user import current_user_id
    from ..tools.confirm import current_sse_push
    ctx = contextvars.copy_context()
    ctx.run(current_sse_push.set, push)
    # 任务可能在创建后才执行；固定使用任务创建时的人格用户空间，避免切换后
    # 工具调用、用量统计或记忆写入落进另一个人格。
    ctx.run(current_user_id.set, task.user_id)

    async def _run():
        try:
            from ..core.reset import epoch_is_current
            if not epoch_is_current(request_epoch):
                return
            # 总超时保护：任务卡死（LLM 挂起等）到点自动取消
            await asyncio.wait_for(
                agent_session.run_task(task_id),
                timeout=agent_session.TASK_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("[Agent] 任务 {} 执行超时（{}s），自动取消", task_id, agent_session.TASK_TIMEOUT)
            agent_session.cancel_task(task_id)
        except asyncio.CancelledError:
            agent_session.cancel_task(task_id)
            raise
        except Exception as e:
            logger.exception("[Agent] 执行任务 {} 异常", task_id)
        finally:
            current = asyncio.current_task()
            if _agent_bg_by_id.get(task_id) is current:
                _agent_bg_by_id.pop(task_id, None)
            await push({"type": "task_done", "task_id": task_id})
            _t = asyncio.create_task(_drop_channel_later(task_id))
            _channel_cleanup_tasks.add(_t)
            _t.add_done_callback(_channel_cleanup_tasks.discard)

    # 在 ctx 上下文内创建任务（Task 拷贝此刻上下文，确认钩子能读到 push）。
    # 历史 bug：写成 asyncio.create_task(ctx.run(asyncio.create_task, _run()))——
    # 内层 create_task 返回 Task，外层 create_task 需要 coroutine → 恒 TypeError，
    # POST /run 恒 500，Agent 任务从未被 HTTP 端点真正启动过（HTTP 层无测试漏网）。
    bg = ctx.run(asyncio.create_task, _run())
    _agent_bg_tasks.add(bg)
    _agent_bg_by_id[task_id] = bg
    bg.add_done_callback(_agent_bg_tasks.discard)
    return {"ok": True, "status": "running"}


@router.post("/tasks/{task_id}/cancel")
async def api_agent_cancel(task_id: str):
    """取消任务。"""
    task = agent_session.cancel_task(task_id)
    if task is None:
        return JSONResponse({"ok": False, "error": "任务不存在"}, status_code=404)
    bg = _agent_bg_by_id.get(task_id)
    if bg is not None and not bg.done():
        bg.cancel()
    return {"ok": True, "task": agent_session.to_dict(task)}


@router.get("/tasks/{task_id}/stream")
async def api_agent_stream(task_id: str) -> StreamingResponse:
    """SSE 通道：推送该任务的确认请求与进度事件。"""
    queue = _channel(task_id)

    async def gen() -> AsyncGenerator[str, None]:
        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=25)
            except asyncio.TimeoutError:
                # 心跳：保持连接
                yield _sse({"type": "ping"})
                continue
            if ev.get("type") == "task_done":
                yield _sse(ev)
                break
            yield _sse(ev)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
