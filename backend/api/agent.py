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
_channel_cleanup_by_id: dict[str, asyncio.Task] = {}

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


def _start_agent_task(task_id: str, *, request_epoch: int | None = None) -> asyncio.Task | None:
    """原子认领并后台启动任务，统一身份、确认、取消、reset 与 SSE 契约。"""
    from ..core.current_user import current_user_id
    from ..core.reset import epoch_is_current, reset_epoch
    from ..tools.confirm import current_sse_push

    task = agent_session._load(task_id)
    if task is None or task.status != "planned":
        return None
    # 在创建后台协程前认领。双击/并发请求中的失败者不会创建协程，因此不会
    # 伪造 task_done、覆盖取消句柄或让两个请求都返回“已启动”。
    if not agent_session._claim_running(task_id):
        return None

    epoch = reset_epoch() if request_epoch is None else int(request_epoch)
    queue = _channel(task_id)
    # 重试/再次执行前丢弃上一轮的 task_done，并取消上一轮的延迟清理；否则旧帧会
    # 让新 SSE 立刻结束，旧清理任务也可能在新一轮执行中删掉正在使用的通道。
    previous_cleanup = _channel_cleanup_by_id.pop(task_id, None)
    if previous_cleanup is not None and not previous_cleanup.done():
        previous_cleanup.cancel()
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            break

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

    ctx = contextvars.copy_context()
    ctx.run(current_sse_push.set, push)
    ctx.run(current_user_id.set, task.user_id)

    async def _run() -> None:
        try:
            if not epoch_is_current(epoch):
                agent_session.cancel_task(task_id)
                return
            await agent_session.run_task(task_id, already_claimed=True)
        except asyncio.CancelledError:
            agent_session.cancel_task(task_id)
            raise
        except Exception:
            logger.exception("[Agent] 执行任务 {} 异常", task_id)
        finally:
            current = asyncio.current_task()
            if _agent_bg_by_id.get(task_id) is current:
                _agent_bg_by_id.pop(task_id, None)
            await push({"type": "task_done", "task_id": task_id})
            cleanup = asyncio.create_task(_drop_channel_later(task_id))
            _channel_cleanup_tasks.add(cleanup)
            _channel_cleanup_by_id[task_id] = cleanup

            def _cleanup_done(done: asyncio.Task) -> None:
                _channel_cleanup_tasks.discard(done)
                if _channel_cleanup_by_id.get(task_id) is done:
                    _channel_cleanup_by_id.pop(task_id, None)

            cleanup.add_done_callback(_cleanup_done)

    bg = ctx.run(asyncio.create_task, _run())
    _agent_bg_tasks.add(bg)
    _agent_bg_by_id[task_id] = bg
    bg.add_done_callback(_agent_bg_tasks.discard)
    return bg


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
    if task.status != "planned":
        return JSONResponse({"ok": False, "error": f"任务已在 {task.status} 状态"}, status_code=409)
    bg = _start_agent_task(task_id, request_epoch=request_epoch)
    if bg is None:
        current = agent_session._load(task_id)
        state = current.status if current else "missing"
        return JSONResponse({"ok": False, "error": f"任务未启动，当前状态 {state}"}, status_code=409)
    return {"ok": True, "status": "running"}



@router.post("/tasks/{task_id}/schedule")
async def api_agent_schedule(task_id: str, request: Request):
    """给任务定时：body {delay_minutes: 30} 或 {at_ts: 1788900000}。

    用户主动定时 = 明确授权 → 计划步骤自动放行（工具级确认钩子仍然生效）。
    """
    import time as _time

    try:
        body = await request.json()
    except Exception:
        body = {}
    task = agent_session._load(task_id)
    if task is None:
        return JSONResponse({"ok": False, "error": "任务不存在"}, status_code=404)
    if task.status in ("running", "done", "cancelled"):
        return JSONResponse({"ok": False, "error": f"任务已在 {task.status} 状态"},
                            status_code=409)
    at_ts = body.get("at_ts")
    delay = body.get("delay_minutes")
    if at_ts is not None:
        when = float(at_ts)
    elif delay is not None:
        when = _time.time() + max(0, float(delay)) * 60
    else:
        return JSONResponse({"ok": False, "error": "缺少 delay_minutes 或 at_ts"},
                            status_code=422)
    updated = agent_session.schedule_task(task_id, when)
    return {"ok": True, "task": agent_session.to_dict(updated or task)}


@router.post("/tasks/{task_id}/retry")
async def api_agent_retry(task_id: str):
    """失败重试：重置为 planned 并立即重跑（受 max_attempts 限制）。"""
    from ..core.reset import reset_in_progress

    if reset_in_progress():
        return JSONResponse({"ok": False, "error": "正在重置，请稍后再试"}, status_code=409)
    task = agent_session._load(task_id)
    if task is None:
        return JSONResponse({"ok": False, "error": "任务不存在"}, status_code=404)
    if task.status != "failed":
        return JSONResponse({"ok": False, "error": f"任务当前是 {task.status}，只有失败任务可重试"},
                            status_code=409)
    if task.attempt >= task.max_attempts:
        return JSONResponse({"ok": False, "error": f"已达重试上限（{task.max_attempts} 次）"},
                            status_code=409)
    prepared = agent_session.prepare_retry(task_id)
    if prepared is None or prepared.status != "planned":
        return JSONResponse({"ok": False, "error": "任务未能进入重试状态"}, status_code=409)
    bg = _start_agent_task(task_id)
    if bg is None:
        return JSONResponse({"ok": False, "error": "重试任务未能启动"}, status_code=409)
    current = agent_session._load(task_id) or prepared
    return {"ok": True, "status": "running", "task": agent_session.to_dict(current)}


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
