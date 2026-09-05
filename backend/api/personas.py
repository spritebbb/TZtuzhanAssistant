# -*- coding: utf-8 -*-
"""Markdown 人格卡档案与热切换接口。"""
from __future__ import annotations

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse

from ..core import persona as persona_runtime
from ..core import persona_profiles

router = APIRouter(prefix="/api/personas", tags=["personas"])


def _busy_response() -> JSONResponse | None:
    # 切换发生在回复或 Agent 执行期间会让尚未完成的模型调用读取另一张卡，
    # 因此等后台工作结束再切换；已经落库的数据仍按各自 user_id 隔离。
    from . import agent, chat

    busy_chat = any(not task.done() for task in chat._bg_tasks)
    busy_agent = any(not task.done() for task in agent._agent_bg_tasks)
    if busy_chat or busy_agent:
        return JSONResponse(
            {"ok": False, "error": "正在生成回复或执行任务，请结束后再切换人格"},
            status_code=409,
        )
    return None


def _activate(profile_id: str) -> dict:
    profile = persona_profiles.activate(profile_id)
    persona_runtime._persona_cache = None
    return profile


@router.get("")
async def api_personas_list():
    return {
        "ok": True,
        "active": persona_profiles.active_profile(),
        "personas": persona_profiles.list_profiles(),
    }


@router.post("/import")
async def api_personas_import(file: UploadFile):
    busy = _busy_response()
    if busy:
        return busy
    try:
        data = await file.read(1024 * 1024 + 1)
        profile = persona_profiles.import_card(file.filename or "persona.md", data)
        profile = _activate(profile["id"])
        # 触发创建该人格的私有 current 会话，切回时会继续原来的对话。
        from ..session.store import CURRENT_SESSION_ID, get_messages

        await get_messages(CURRENT_SESSION_ID)
        return {"ok": True, "persona": profile}
    except persona_profiles.PersonaProfileError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/{profile_id}/activate")
async def api_personas_activate(profile_id: str):
    busy = _busy_response()
    if busy:
        return busy
    try:
        profile = _activate(profile_id)
        from ..session.store import CURRENT_SESSION_ID, get_messages

        await get_messages(CURRENT_SESSION_ID)
        return {"ok": True, "persona": profile}
    except persona_profiles.PersonaProfileError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)


@router.patch("/{profile_id}")
async def api_personas_update(profile_id: str, request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise persona_profiles.PersonaProfileError("请求体必须是对象")
        profile = persona_profiles.update_profile(profile_id, body)
        return {"ok": True, "persona": profile}
    except persona_profiles.PersonaProfileError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception:
        return JSONResponse({"ok": False, "error": "JSON 解析失败"}, status_code=400)
