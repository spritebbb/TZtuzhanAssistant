# -*- coding: utf-8 -*-
"""M8 未来信件 API：写给未来的我们。锁定态正文绝不离开后端。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..core import future_letters
from ..core.config import config

router = APIRouter(prefix="/api/future-letters", tags=["future-letters"])


def _active_user_id() -> str:
    """Use the active persona namespace when that optional feature is present."""
    try:
        from ..core.persona_profiles import active_user_id
    except ImportError:
        return "assistant-main"
    return active_user_id()


class CreateLetterBody(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    unlock_type: str = Field(pattern="^(date|goal|event)$")
    title: str = Field(default="", max_length=60)
    unlock_at: str | None = None
    goal_id: int | None = None
    event_type: str | None = None


def _error(exc: future_letters.FutureLetterError, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=status_code)


def _disabled() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "未来信件未开启"}, status_code=403)


@router.get("")
async def api_list_letters():
    if not config.future_letters_enabled:
        return _disabled()
    user_id = _active_user_id()
    letters, options = await asyncio.gather(
        asyncio.to_thread(future_letters.list_letters, user_id),
        asyncio.to_thread(future_letters.letter_options, user_id),
    )
    return {"ok": True, "letters": letters, **options}


@router.post("")
async def api_create_letter(body: CreateLetterBody):
    if not config.future_letters_enabled:
        return _disabled()
    try:
        letter = await asyncio.to_thread(
            future_letters.create_letter,
            _active_user_id(),
            body.body,
            body.unlock_type,
            title=body.title,
            unlock_at=body.unlock_at,
            goal_id=body.goal_id,
            event_type=body.event_type,
        )
    except future_letters.FutureLetterError as exc:
        return _error(exc)
    return {"ok": True, "letter": letter}


@router.post("/{letter_id}/open")
async def api_open_letter(letter_id: int):
    if not config.future_letters_enabled:
        return _disabled()
    try:
        letter = await asyncio.to_thread(
            future_letters.open_letter, _active_user_id(), letter_id
        )
    except future_letters.FutureLetterError as exc:
        return _error(exc, 404)
    return {"ok": True, "letter": letter}


@router.delete("/{letter_id}")
async def api_delete_letter(letter_id: int):
    if not config.future_letters_enabled:
        return _disabled()
    deleted = await asyncio.to_thread(
        future_letters.delete_letter, _active_user_id(), letter_id
    )
    if not deleted:
        return JSONResponse({"ok": False, "error": "这封信不存在"}, status_code=404)
    return {"ok": True}
