# -*- coding: utf-8 -*-
"""远程任务 API：允许外部系统（如 DSH、其他 AI Agent）通过 HTTP 调用菟菚执行任务。

安全：请求需携带 AGENT_REMOTE_TOKEN（与 config.agent_remote_token 匹配）。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..core.log import logger
from ..tools.base import ToolRegistry
from ..tools.safety import remote_token_ok_by_peer

router = APIRouter(prefix="/api/remote", tags=["remote"])

# 任务状态与运行协程分别保存：状态保留给查询，协程索引用于取消和重置隔离。
_remote_tasks: dict[str, dict] = {}
# 保留上限：只淘汰已结束任务；绝不能把仍在执行的任务从状态表中静默丢弃。
_REMOTE_TASKS_MAX = 100
_REMOTE_TASK_TTL = 2 * 3600  # 2 小时
# 运行中的后台任务强引用（防 GC 静默取消，与 app.py _spawn_bg 一致）
_remote_bg_tasks: set[asyncio.Task] = set()
_remote_bg_by_id: dict[str, asyncio.Task] = {}
_MAX_TASK_LENGTH = 20_000
_MAX_USER_ID_LENGTH = 64


def _extract_token(request: Request, form_or_json: dict | None = None) -> str:
    """从 Authorization 头或 body 中取 token。"""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    if form_or_json:
        tok = form_or_json.get("token")
        if tok:
            return str(tok).strip()
    return request.query_params.get("token", "").strip()


@router.post("/task")
async def api_remote_task(request: Request):
    """远程执行一次任务（通过工具循环）。

    调用方式：JSON body（{"task": "...", "user_id": "..."}）或 form/query；
    token 可用 body/query 的 token 字段，或 Authorization: Bearer <token> 头。
    user_id（可选，默认 remote，用于记忆/待办/好感度按身份隔离）。
    返回 task_id，客户端可通过 GET /api/remote/task/{id} 查询结果。
    任务异步执行，耗时较长。
    """
    # 解析请求体（兼容 JSON / form / query）
    body: dict = {}
    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        try:
            body = await request.json()
        except Exception:
            body = {}
    else:
        try:
            form = await request.form()
            body = {k: v for k, v in form.items()}
        except Exception:
            body = {}
    task = str(body.get("task") or request.query_params.get("task") or "").strip()
    token = _extract_token(request, body)
    user_id = str(body.get("user_id") or request.query_params.get("user_id") or "").strip()

    if not task.strip():
        return JSONResponse({"ok": False, "error": "缺少 task"}, status_code=400)
    if len(task) > _MAX_TASK_LENGTH:
        return JSONResponse({"ok": False, "error": "任务内容过长"}, status_code=413)
    if len(user_id) > _MAX_USER_ID_LENGTH:
        return JSONResponse({"ok": False, "error": "user_id 过长"}, status_code=413)
    if not remote_token_ok_by_peer(
        token, request.client.host if request.client else None
    ):
        return JSONResponse({"ok": False, "error": "token 无效"}, status_code=403)

    task_id = uuid.uuid4().hex[:12]
    _prune_remote_tasks()
    if len(_remote_tasks) >= _REMOTE_TASKS_MAX:
        # 所有可淘汰的结束任务都已经在 _prune_remote_tasks 中清掉；若仍满，
        # 说明并发任务过多，拒绝新任务而非遗失一个仍在运行的任务状态。
        return JSONResponse({"ok": False, "error": "远程任务过多，请稍后重试"}, status_code=429)
    uid = (user_id or "remote").strip()[:64]
    _remote_tasks[task_id] = {
        "status": "running", "result": "", "user_id": uid, "created_at": time.time(),
    }

    async def _run():
        try:
            # 设置执行身份：工具（记忆/待办/好感度）按 user_id 隔离，
            # 避免远程任务污染主用户数据
            from ..core.current_user import current_user_id
            current_user_id.set(uid)
            # 调用工具循环执行任务
            from ..core.llm import chat, chat_native
            from ..tools.service import run_tool_round

            result = await run_tool_round(
                [
                    {"role": "system", "content": "你是菟菚助手，请执行用户的任务。"},
                    {"role": "user", "content": task},
                ],
                chat=lambda ms: chat(ms),
                chat_native=lambda ms, tools: chat_native(ms, tools),
                max_loops=4,
            )
            _remote_tasks[task_id] = {
                "status": "done",
                "result": result,
                "user_id": uid,
                "created_at": time.time(),
            }
        except asyncio.CancelledError:
            # 取消可能来自客户端、重置或应用关闭。保留可查询的终态，避免客户端
            # 永远看到 running；随后重新抛出以遵守 asyncio 取消语义。
            info = _remote_tasks.get(task_id, {})
            _remote_tasks[task_id] = {
                "status": "cancelled",
                "result": "（任务已取消）",
                "user_id": uid,
                "created_at": info.get("created_at", time.time()),
            }
            raise
        except Exception as e:
            logger.exception("[远程任务] {} 失败", task_id)
            _remote_tasks[task_id] = {
                "status": "failed",
                "result": f"（执行失败：{type(e).__name__}: {e}）",
                "user_id": uid,
                "created_at": time.time(),
            }

    bg = asyncio.create_task(_run())
    _remote_bg_tasks.add(bg)
    bg.add_done_callback(_remote_bg_tasks.discard)
    _remote_bg_by_id[task_id] = bg

    def _drop_task(done: asyncio.Task) -> None:
        if _remote_bg_by_id.get(task_id) is done:
            _remote_bg_by_id.pop(task_id, None)

    bg.add_done_callback(_drop_task)
    return {"ok": True, "task_id": task_id, "status": "running"}


def _prune_remote_tasks() -> None:
    """清理已结束的超龄/最旧任务，运行中的任务始终保留可查询、可取消状态。"""
    now = time.time()
    expired = [tid for tid, info in _remote_tasks.items()
               if info.get("status") != "running"
               and now - info.get("created_at", 0) > _REMOTE_TASK_TTL]
    for tid in expired:
        _remote_tasks.pop(tid, None)
    if len(_remote_tasks) >= _REMOTE_TASKS_MAX:
        # 按创建时间升序淘汰结束任务；运行中的任务不可淘汰。
        finished = [item for item in _remote_tasks.items()
                    if item[1].get("status") != "running"]
        for tid, _ in sorted(finished, key=lambda kv: kv[1].get("created_at", 0))[
            : max(0, len(_remote_tasks) - _REMOTE_TASKS_MAX + 1)
        ]:
            _remote_tasks.pop(tid, None)


@router.get("/task/{task_id}")
async def api_remote_task_status(task_id: str, request: Request):
    """查询远程任务状态（同样需要 token 校验，防止本地进程/网页窃读结果）。"""
    token = _extract_token(request, None)
    if not remote_token_ok_by_peer(
        token, request.client.host if request.client else None
    ):
        return JSONResponse({"ok": False, "error": "token 无效"}, status_code=403)
    info = _remote_tasks.get(task_id)
    if info is None:
        return JSONResponse({"ok": False, "error": "任务不存在"}, status_code=404)
    return {"ok": True, "task_id": task_id, **info}


@router.post("/task/{task_id}/cancel")
async def api_remote_task_cancel(task_id: str, request: Request):
    """取消仍在运行的远程任务（与状态查询使用相同的来源+token 鉴权）。"""
    token = _extract_token(request, None)
    if not remote_token_ok_by_peer(
        token, request.client.host if request.client else None
    ):
        return JSONResponse({"ok": False, "error": "token 无效"}, status_code=403)
    task = _remote_bg_by_id.get(task_id)
    if task is None or task.done():
        return JSONResponse({"ok": False, "error": "任务不存在或已结束"}, status_code=404)
    task.cancel()
    return {"ok": True, "task_id": task_id, "status": "cancelling"}
