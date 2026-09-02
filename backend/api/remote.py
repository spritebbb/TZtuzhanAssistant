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

# 运行中的任务（用于查询状态、取消）
_remote_tasks: dict[str, dict] = {}
# 保留上限：超过后丢弃最旧任务（避免长期运行内存无限增长）
_REMOTE_TASKS_MAX = 100
_REMOTE_TASK_TTL = 2 * 3600  # 2 小时
# 运行中的后台任务强引用（防 GC 静默取消，与 app.py _spawn_bg 一致）
_remote_bg_tasks: set[asyncio.Task] = set()


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
    if not remote_token_ok_by_peer(
        token, request.client.host if request.client else None
    ):
        return JSONResponse({"ok": False, "error": "token 无效"}, status_code=403)

    task_id = uuid.uuid4().hex[:12]
    _prune_remote_tasks()
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
                "created_at": time.time(),
            }
        except Exception as e:
            logger.exception("[远程任务] {} 失败", task_id)
            _remote_tasks[task_id] = {
                "status": "failed",
                "result": f"（执行失败：{type(e).__name__}: {e}）",
                "created_at": time.time(),
            }

    bg = asyncio.create_task(_run())
    _remote_bg_tasks.add(bg)
    bg.add_done_callback(_remote_bg_tasks.discard)
    return {"ok": True, "task_id": task_id, "status": "running"}


def _prune_remote_tasks() -> None:
    """清理超龄任务与超出上限的最旧任务（保留最近完成/运行中的）。"""
    now = time.time()
    expired = [tid for tid, info in _remote_tasks.items()
               if now - info.get("created_at", 0) > _REMOTE_TASK_TTL]
    for tid in expired:
        _remote_tasks.pop(tid, None)
    if len(_remote_tasks) >= _REMOTE_TASKS_MAX:
        # 按创建时间升序，丢弃最旧的直到低于上限
        for tid, _ in sorted(
            _remote_tasks.items(), key=lambda kv: kv[1].get("created_at", 0)
        )[: max(0, len(_remote_tasks) - _REMOTE_TASKS_MAX + 1)]:
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
