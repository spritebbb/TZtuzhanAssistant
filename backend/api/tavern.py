# -*- coding: utf-8 -*-
"""酒馆同玩 API（第一切片：点名通路）。

调用方是本机 SillyTavern 的「菟菚同伴」扩展；回环来源按既有语义免 token。
卡公开面与世界书片段只作为场景材料进入 prompt（core 层 wrap_untrusted 包裹）。
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..core import tavern
from ..core.features import flag
from ..core.persona_profiles import active_user_id

router = APIRouter(prefix="/api/tavern", tags=["tavern"])


class TavernMessageIn(BaseModel):
    who: Literal["user", "card", "tuzhan", "narrator"] = "card"
    name: str = Field(default="", max_length=40)
    # 字数无上限（用户拍板 2026-09-14）：总量由条数上限与模型上下文兜底。
    text: str = ""


class TavernTurnBody(BaseModel):
    session_id: str = Field(default="", max_length=32)
    card_name: str = Field(default="", max_length=60)
    card_summary: str = ""
    world_text: str = ""
    transcript: list[TavernMessageIn] = Field(default_factory=list, max_length=40)
    user_text: str = ""
    role_mode: Literal["self", "costume"] = "self"
    costume_name: str = Field(default="", max_length=40)
    presence: Literal["full", "moderate", "shy"] = "moderate"
    auto: bool = False
    mock: bool = False


class TavernEndBody(BaseModel):
    session_id: str = Field(min_length=1, max_length=32)


class TavernSaveBody(BaseModel):
    session_id: str = Field(default="", max_length=32)
    card_name: str = Field(default="", max_length=60)
    transcript: list[TavernMessageIn] = Field(default_factory=list, max_length=80)
    mock: bool = False


def _gate() -> JSONResponse | None:
    if not flag("tavern_enabled"):
        return JSONResponse({"ok": False, "error": "酒馆同玩未开启"}, status_code=403)
    return None


@router.post("/turn")
async def api_tavern_turn(body: TavernTurnBody):
    gate = _gate()
    if gate is not None:
        return gate
    try:
        result = await tavern.tavern_turn(
            active_user_id(),
            session_id=body.session_id,
            card_name=body.card_name,
            card_summary=body.card_summary,
            world_text=body.world_text,
            transcript=[item.model_dump() for item in body.transcript],
            user_text=body.user_text,
            role_mode=body.role_mode,
            costume_name=body.costume_name,
            presence=body.presence,
            auto=body.auto,
            mock=body.mock,
        )
    except tavern.TavernError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception:
        return JSONResponse(
            {"ok": False, "error": "她这轮没接上话，稍后再试"}, status_code=502,
        )
    return {"ok": True, **result}


@router.post("/end")
async def api_tavern_end(body: TavernEndBody):
    gate = _gate()
    if gate is not None:
        return gate
    return {"ok": True, "removed": tavern.end_session(body.session_id)}


@router.post("/save")
async def api_tavern_save(body: TavernSaveBody):
    """收局沉淀：忠实摘要 → 长期记忆 + 剧情原文落库。"""
    gate = _gate()
    if gate is not None:
        return gate
    try:
        result = await tavern.save_session(
            active_user_id(),
            session_id=body.session_id,
            card_name=body.card_name,
            transcript=[item.model_dump() for item in body.transcript],
            mock=body.mock,
        )
    except tavern.TavernError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception:
        return JSONResponse(
            {"ok": False, "error": "这一局没能记下来，稍后再收一次"}, status_code=502,
        )
    return {"ok": True, **result}


@router.get("/sessions")
async def api_tavern_sessions():
    gate = _gate()
    if gate is not None:
        return gate
    return {"ok": True, "sessions": tavern.list_sessions(active_user_id())}


@router.get("/session/{session_id}")
async def api_tavern_session(session_id: str):
    gate = _gate()
    if gate is not None:
        return gate
    info = tavern.session_info(session_id)
    if info is None:
        return JSONResponse({"ok": False, "error": "这一局已经不在了"}, status_code=404)
    return {"ok": True, "session": info}
