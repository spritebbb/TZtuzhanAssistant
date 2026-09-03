# -*- coding: utf-8 -*-
"""工具状态与元信息接口。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..core.config import config
from ..core.mood import current_mood
from ..tools.base import ToolRegistry

router = APIRouter(prefix="/api", tags=["meta"])


def _tool_status() -> dict:
    return {
        "search": bool(config.search_enabled and config.search_api_key),
        "weather": bool(config.mood_city),
        "image": bool(config.image_api_key),
        "vision": bool(config.vision_api_key or config.image_api_key),
        "memory": True,
        "mcp": True,  # MCP 工具始终可用
    }


def _memory_status() -> dict:
    """记忆引擎运行状态（向量库可用性/embedding 模式/条目数），失败静默。"""
    try:
        from ..core.memory import vector_store as vec
        from ..core.memory import embedding as emb

        stats = vec.stats()
        return {
            "vector_enabled": bool(stats.get("enabled")),
            "vector_count": sum(v for k, v in stats.items() if k != "enabled" and isinstance(v, int)),
            "embed_mode": emb.mode(),
            "mem0": bool(config.memory_mem0),
        }
    except Exception:
        return {"vector_enabled": False, "vector_count": 0, "embed_mode": "unknown", "mem0": False}


@router.get("/meta")
async def api_meta(session_id: str = ""):
    """工具开关状态 + 完整工具清单 + 基本信息 + 心情。session_id 可选：传入时按会话隔离用户身份。"""
    from ..api.chat import _user_id
    from ..core.search import last_error as search_last_error

    uid = _user_id(session_id) if session_id else "assistant-main"
    mood_val, mood_label = current_mood(uid, city=config.mood_city)
    # 心情 emoji 映射
    if mood_val >= 85:
        mood_emoji = "🤩"
    elif mood_val >= 65:
        mood_emoji = "😄"
    elif mood_val >= 45:
        mood_emoji = "🙂"
    elif mood_val >= 25:
        mood_emoji = "😐"
    else:
        mood_emoji = "😞"
    return {
        "ok": True,
        "uid": uid,
        "tools": _tool_status(),
        "memory": _memory_status(),
        "search_last_error": search_last_error(),
        "tool_list": [t.model_dump() for t in ToolRegistry.list()],
        "mood": {"value": mood_val, "label": mood_label, "emoji": mood_emoji},
    }
