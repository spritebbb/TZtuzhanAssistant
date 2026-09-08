# -*- coding: utf-8 -*-
"""L16 可选择共享 API：分享 / 撤销 / 我分享的清单。

只在既有详情入口被用户明确操作时调用；默认一切隔离，无批量授权。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from ..core import shared_resources as sr
from ..core.persona_profiles import active_user_id
from ..core.log import logger

router = APIRouter(prefix="/api/shared", tags=["shared-resources"])


@router.post("")
async def api_share(body: dict = Body(...)):
    """分享一件资源给另一个人格（首期只读）。

    grantee 可传人格 id（推荐，服务端换算命名空间）或完整命名空间串。
    """
    resource_type = str(body.get("resource_type") or "")
    resource_id = body.get("resource_id")
    grantee = str(body.get("grantee") or "").strip()
    persona_id = str(body.get("grantee_persona") or "").strip()
    if persona_id:
        from ..core.persona_profiles import DEFAULT_USER_ID, scoped_user_id

        grantee = scoped_user_id(DEFAULT_USER_ID, persona_id)
    if not resource_type or resource_id is None or not grantee:
        return JSONResponse({"ok": False, "error": "缺少 resource_type/resource_id/grantee"},
                            status_code=422)
    uid = active_user_id()
    try:
        result = await asyncio.to_thread(
            sr.share, uid, resource_type, int(resource_id), grantee
        )
    except sr.SharedResourceError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
    logger.info("[共享] API 分享 {}#{} → {}", resource_type, resource_id, grantee)
    return {"ok": True, **result}


@router.delete("/{resource_type}/{resource_id}")
async def api_revoke(resource_type: str, resource_id: int,
                     grantee: str | None = None):
    """撤销共享；不带 grantee 撤销全部授权。"""
    uid = active_user_id()
    removed = await asyncio.to_thread(
        sr.revoke, uid, resource_type, int(resource_id), grantee
    )
    return {"ok": True, "revoked": removed}


@router.get("")
async def api_list():
    """owner 视角：我分享出去的东西与当前授权状态。"""
    uid = active_user_id()
    items = await asyncio.to_thread(sr.list_shares, uid)
    return {"ok": True, "shares": items}
