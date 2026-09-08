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
from ..core.her_profile import her_profile
from ..core.log import logger
from ..core import pending_thoughts
from ..core import user_preferences as _prefs
from ..core.userdb import (
    db,
    list_facts,
    update_fact_pinned,
    update_fact_surface_policy,
)
from ..core.persona_profiles import active_user_id

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("/her-profile")
async def api_her_profile():
    """双向了解：她稳定可被了解的一面（来自人格卡，运行时不可改写）。"""
    return {"ok": True, "sections": her_profile()}


@router.get("/interaction-style")
async def api_interaction_style():
    """自动形成的互动偏好（说话风格提炼 + 共同语言），可查看、可重置。"""
    uid = active_user_id()
    return {
        "ok": True,
        "style": await asyncio.to_thread(db.get_style, uid),
        "terms": await asyncio.to_thread(db.get_terms, uid, 30),
    }


@router.delete("/interaction-style")
async def api_reset_interaction_style():
    """重置自动形成的说话风格偏好；共同语言逐条删除。"""
    uid = active_user_id()
    await asyncio.to_thread(db.set_style, uid, "")
    logger.info("[记忆管理] 已重置互动偏好（说话风格）: {}", uid)
    return {"ok": True}


@router.get("/pending-thoughts")
async def api_pending_thoughts():
    """她的未完成心事：用户可见可放下（M5 用户主权 + 可观测统计）。"""
    uid = active_user_id()
    listing = await asyncio.to_thread(_list_thoughts, uid)
    stats = await asyncio.to_thread(pending_thoughts.stats, uid)
    return {"ok": True, "thoughts": listing, "stats": stats}


def _list_thoughts(uid: str) -> list[dict]:
    from ..core.pending_thoughts import due_thoughts

    return due_thoughts(uid, limit=10)


@router.post("/pending-thoughts/{thought_id}/dismiss")
async def api_dismiss_thought(thought_id: int):
    uid = active_user_id()
    if not await asyncio.to_thread(pending_thoughts.dismiss_thought, uid, thought_id):
        return JSONResponse({"ok": False, "error": "这条心事不存在或已处理"}, status_code=404)
    logger.info("[记忆管理] 用户放下了心事 #{}", thought_id)
    return {"ok": True}


@router.delete("/terms/{term_id}")
async def api_delete_term(term_id: int):
    uid = active_user_id()
    # L04：删共同语言时同步退役对应梗（侧表与偏好一致，删除后不能再讲）。
    from ..core.humor_memory import retire_term

    await asyncio.to_thread(retire_term, uid, term_id)
    if not await asyncio.to_thread(db.del_term, uid, term_id):
        return JSONResponse({"ok": False, "error": "这条共同语言不存在"}, status_code=404)
    logger.info("[记忆管理] 删除共同语言 #{}", term_id)
    return {"ok": True}

@router.get("/facts")
async def api_list_facts(limit: int = Query(200, ge=1, le=500)):
    return {"ok": True, "facts": list_facts(active_user_id(), limit)}


def _version_conflict(uid: str, fact_id: int, expected: str | None) -> bool:
    """F07 版本化编辑：客户端带了 version 且与现状不符 → 409。"""
    if not expected:
        return False
    from ..core.memory_salience import fact_version

    with db._lock:
        row = db.conn.execute(
            "SELECT * FROM facts WHERE user_id=? AND id=?", (uid, int(fact_id))
        ).fetchone()
    if row is None:
        return False  # 不存在交给 404 分支
    return fact_version(row) != str(expected)


@router.put("/facts/{fact_id}")
async def api_update_fact(fact_id: int, content: str = Body(..., embed=True),
                          expected_version: str | None = Body(None, embed=True)):
    uid = active_user_id()
    content = content.strip()
    if not content:
        return JSONResponse({"ok": False, "error": "内容不能为空"}, status_code=400)
    if await asyncio.to_thread(_version_conflict, uid, fact_id, expected_version):
        return JSONResponse({"ok": False, "error": "这条记忆已变化，请刷新后再改"}, status_code=409)
    if not await asyncio.to_thread(update_fact_everywhere, uid, fact_id, content):
        return JSONResponse({"ok": False, "error": "这条记忆不存在"}, status_code=404)
    logger.info("[记忆管理] 改写事实 #{}: {}", fact_id, content[:40])
    return {"ok": True}


@router.delete("/facts/{fact_id}")
async def api_delete_fact(fact_id: int, expected_version: str | None = None):
    uid = active_user_id()
    if await asyncio.to_thread(_version_conflict, uid, fact_id, expected_version):
        return JSONResponse({"ok": False, "error": "这条记忆已变化，请刷新后再删"}, status_code=409)
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


@router.patch("/facts/{fact_id}/pinned")
async def api_update_fact_pinned(
    fact_id: int,
    pinned: bool = Body(..., embed=True),
):
    uid = active_user_id()
    if not await asyncio.to_thread(update_fact_pinned, uid, fact_id, pinned):
        return JSONResponse({"ok": False, "error": "这条记忆不存在"}, status_code=404)
    logger.info("[记忆管理] {}事实 #{}", "固定" if pinned else "取消固定", fact_id)
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


# ---- P2-02 用户偏好教学：查看 / 新增 / 更新 / 撤销 ----


@router.get("/preferences")
async def api_list_preferences(include_revoked: bool = Query(False)):
    uid = active_user_id()
    items = await asyncio.to_thread(_prefs.list_preferences, uid, include_revoked)
    return {"ok": True, "items": items}


@router.get("/address-candidates")
async def api_address_candidates():
    """可查看的称呼候选与形成来源；不展示等级或数值进度。"""
    from ..core.affection import stage_of
    from ..core.conversation_rhythm import address_candidates

    uid = active_user_id()
    user = await asyncio.to_thread(db.get_user, uid)
    stage = stage_of((user["affection"] if user else 0) or 0)
    data = await asyncio.to_thread(address_candidates, uid, stage=stage)
    return {"ok": True, **data}


@router.post("/preferences")
async def api_create_preference(
    category: str = Body(...),
    value: dict = Body(...),
    source_message_id: int | None = Body(None),
):
    uid = active_user_id()
    try:
        row = await asyncio.to_thread(
            _upsert_preference, uid, category, value, source_message_id
        )
    except _prefs.PreferenceError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
    return {"ok": True, "item": row}


def _upsert_preference(uid: str, category: str, value: dict, source_message_id):
    return _prefs._upsert(uid, category, value, origin="user_teaching",
                          source_message_id=source_message_id, status="active",
                          confidence=1.0)


@router.put("/preferences/{pref_id}")
async def api_update_preference(
    pref_id: int,
    value: dict = Body(...),
    expected_version: int = Body(...),
):
    uid = active_user_id()
    try:
        row = await asyncio.to_thread(
            _prefs.update_preference_value, uid, pref_id, value, expected_version
        )
    except _prefs.PreferenceError as exc:
        status = 409 if "版本" in str(exc) else 404 if "不存在" in str(exc) else 422
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=status)
    return {"ok": True, "item": row}


@router.delete("/preferences/{pref_id}")
async def api_revoke_preference(pref_id: int):
    uid = active_user_id()
    try:
        row = await asyncio.to_thread(_prefs.revoke_preference, uid, pref_id)
    except _prefs.PreferenceError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    logger.info("[偏好教学] 撤销偏好 #{}", pref_id)
    return {"ok": True, "item": row}


@router.get("/domain-trust")
async def api_domain_trust_get():
    """L05 领域信任：高层可依赖程度 + 每域最多 2 条来源（不含内部权重）。"""
    from ..core.domain_trust import get_snapshot

    uid = active_user_id()
    return {"ok": True, **await asyncio.to_thread(get_snapshot, uid)}


@router.delete("/domain-trust/{domain}")
async def api_domain_trust_reset(domain: str):
    from ..core.domain_trust import DOMAINS, reset_domain

    if domain not in DOMAINS:
        return JSONResponse({"ok": False, "error": "未知领域"}, status_code=422)
    uid = active_user_id()
    await asyncio.to_thread(reset_domain, uid, domain)
    logger.info("[领域信任] 用户重置 {} 域", domain)
    return {"ok": True}


@router.get("/relationship-style")
async def api_relationship_style_get():
    """L03 长期关系气质：高层描述 + 最多 2 条形成原因（不返回分数）。"""
    from ..core.relationship_style import derive_style, style_label

    uid = active_user_id()
    result = await asyncio.to_thread(derive_style, uid)
    result["style_labels"] = [style_label(s) for s in result.get("style_ids", [])]
    return {"ok": True, **result}


@router.post("/relationship-style/{style}/block")
async def api_relationship_style_block(style: str):
    """「不要这种气质」：走 P2-02 偏好屏蔽（category=style）。"""
    from fastapi import HTTPException

    from ..core.relationship_style import (
        RelationshipStyleError,
        block_style,
        style_label,
    )

    uid = active_user_id()
    try:
        ok = await asyncio.to_thread(block_style, uid, style)
    except RelationshipStyleError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not ok:
        return {"ok": True, "note": "已屏蔽过"}
    logger.info("[关系气质] 用户屏蔽 {}（{}）", style, style_label(style))
    return {"ok": True}


@router.delete("/relationship-style/{style}/block")
async def api_relationship_style_unblock(style: str):
    """撤销气质屏蔽。"""
    from ..core.relationship_style import unblock_style

    uid = active_user_id()
    ok = await asyncio.to_thread(unblock_style, uid, style)
    return {"ok": True, "removed": bool(ok)}
