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
from ..core.log import logger
from ..core.userdb import delete_fact, list_facts, update_fact

router = APIRouter(prefix="/api/memory", tags=["memory"])

_UID = "assistant-main"


def _vec_delete(user_id: str, fact_id: int) -> None:
    """同步删除 facts 向量（失败静默——向量是索引，SQLite 才是事实源）。"""
    try:
        from ..core.vector_store import delete as vec_delete

        vec_delete(user_id, "facts", fact_id)
    except Exception:
        pass


def _vec_reindex(user_id: str, fact_id: int, content: str) -> None:
    """改写后重建向量：先删旧再索引新。"""
    try:
        from ..core.vector_store import delete as vec_delete
        from ..core.vector_store import index as vec_index

        vec_delete(user_id, "facts", fact_id)
        vec_index(user_id, fact_id, content, "facts")
    except Exception:
        pass


@router.get("/facts")
async def api_list_facts(limit: int = Query(200, ge=1, le=500)):
    return {"ok": True, "facts": list_facts(_UID, limit)}


@router.put("/facts/{fact_id}")
async def api_update_fact(fact_id: int, content: str = Body(..., embed=True)):
    content = content.strip()
    if not content:
        return JSONResponse({"ok": False, "error": "内容不能为空"}, status_code=400)
    if not update_fact(_UID, fact_id, content):
        return JSONResponse({"ok": False, "error": "这条记忆不存在"}, status_code=404)
    await asyncio.to_thread(_vec_reindex, _UID, fact_id, content)
    logger.info("[记忆管理] 改写事实 #{}: {}", fact_id, content[:40])
    return {"ok": True}


@router.delete("/facts/{fact_id}")
async def api_delete_fact(fact_id: int):
    if not delete_fact(_UID, fact_id):
        return JSONResponse({"ok": False, "error": "这条记忆不存在"}, status_code=404)
    await asyncio.to_thread(_vec_delete, _UID, fact_id)
    logger.info("[记忆管理] 删除事实 #{}", fact_id)
    return {"ok": True}
