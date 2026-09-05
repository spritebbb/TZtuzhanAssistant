# -*- coding: utf-8 -*-
"""记忆管理（C7 第一步）：facts 的查看/改写/删除，删改同步清理向量索引。

人工纠偏通路——用户在「记忆管理」页直接修正菟菚记错的事；
对话内自动纠偏（检测"你记错了"→ LLM 仲裁删除）见 core/memory_correction.py。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse

from .chat import _user_id
from ..core.fact_lifecycle import (
    delete_fact_everywhere,
    resolve_fact_conflict_everywhere,
    update_fact_everywhere,
)
from ..core.log import logger
from ..core.userdb import (
    list_facts,
    update_fact_surface_policy,
)
from ..core.persona_profiles import active_user_id

router = APIRouter(prefix="/api/memory", tags=["memory"])

@router.get("/facts")
async def api_list_facts(limit: int = Query(200, ge=1, le=500)):
    return {"ok": True, "facts": list_facts(active_user_id(), limit)}


@router.put("/facts/{fact_id}")
async def api_update_fact(fact_id: int, content: str = Body(..., embed=True)):
    uid = active_user_id()
    content = content.strip()
    if not content:
        return JSONResponse({"ok": False, "error": "内容不能为空"}, status_code=400)
    if not await asyncio.to_thread(update_fact_everywhere, uid, fact_id, content):
        return JSONResponse({"ok": False, "error": "这条记忆不存在"}, status_code=404)
    logger.info("[记忆管理] 改写事实 #{}: {}", fact_id, content[:40])
    return {"ok": True}


@router.delete("/facts/{fact_id}")
async def api_delete_fact(fact_id: int):
    uid = active_user_id()
    if not await asyncio.to_thread(delete_fact_everywhere, uid, fact_id):
        return JSONResponse({"ok": False, "error": "这条记忆不存在"}, status_code=404)
    logger.info("[记忆管理] 删除事实 #{}", fact_id)
    return {"ok": True}


@router.patch("/facts/{fact_id}/surface-policy")
async def api_update_fact_surface_policy(
    fact_id: int,
    surface_policy: str = Body(..., embed=True),
):
    uid = active_user_id()
    if surface_policy not in {"normal", "do_not_proactively_surface"}:
        return JSONResponse({"ok": False, "error": "不支持的呈现策略"}, status_code=400)
    if not update_fact_surface_policy(uid, fact_id, surface_policy):
        return JSONResponse({"ok": False, "error": "这条记忆不存在"}, status_code=404)
    logger.info("[记忆管理] 修改事实 #{} 呈现策略: {}", fact_id, surface_policy)
    return {"ok": True}


@router.post("/facts/{fact_id}/resolve-conflict")
async def api_resolve_fact_conflict(
    fact_id: int,
    action: str = Body(..., embed=True),
):
    if action not in {"accept_new", "keep_existing"}:
        return JSONResponse({"ok": False, "error": "不支持的确认操作"}, status_code=400)
    uid = active_user_id()
    result = await asyncio.to_thread(
        resolve_fact_conflict_everywhere,
        uid,
        fact_id,
        accept_new=action == "accept_new",
    )
    if result is None:
        return JSONResponse({"ok": False, "error": "待确认记忆不存在"}, status_code=404)
    logger.info("[记忆管理] 冲突事实 #{} 已处理: {}", fact_id, action)
    return {"ok": True, "resolution": result}
