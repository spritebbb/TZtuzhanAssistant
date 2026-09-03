# -*- coding: utf-8 -*-
"""配置编辑接口（查询/保存 .env 配置）。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..core.config import config, update_env_file

router = APIRouter(prefix="/api", tags=["config"])


def _mask_key(key: str) -> str:
    """密钥脱敏：sk-1234567890 → sk-12****7890（过短则整体打码）。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:6] + "****" + key[-4:]


@router.get("/config")
async def api_config_get():
    """设置面板：返回可编辑配置（密钥脱敏）。"""
    return {
        "ok": True,
        "config": {
            "llm_base_url": config.llm_base_url,
            "llm_model": config.llm_model,
            "llm_temperature": config.llm_temperature,
            "llm_max_tokens": config.llm_max_tokens,
            "llm_api_key_masked": _mask_key(config.llm_api_key),
            "search_enabled": config.search_enabled,
            "search_engine": config.search_engine,
            "search_api_key_masked": _mask_key(config.search_api_key),
            "image_base_url": config.image_base_url,
            "image_model": config.image_model,
            "image_api_key_masked": _mask_key(config.image_api_key),
            "vision_base_url": config.vision_base_url,
            "vision_model": config.vision_model,
            "vision_api_key_masked": _mask_key(config.vision_api_key),
            "mood_city": config.mood_city,
            "memory_semantic": config.memory_semantic,
        },
    }


@router.post("/config")
async def api_config_set(request: Request):
    """设置面板：写 .env 并热重载。仅接收白名单字段，密钥留空/保持脱敏值则不修改。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "JSON 解析失败"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "请求体必须是对象"}, status_code=400)

    fields: dict[str, bool] = {
        "llm_base_url": False, "llm_model": False, "llm_temperature": False,
        "llm_max_tokens": False, "llm_api_key": True,
        "search_enabled": False, "search_engine": False, "search_api_key": True,
        "image_base_url": False, "image_model": False, "image_api_key": True,
        "vision_base_url": False, "vision_model": False, "vision_api_key": True,
        "mood_city": False, "memory_semantic": False,
    }
    clear_fields = body.get("clear_fields", [])
    clear_fields = set(clear_fields) if isinstance(clear_fields, list) else set()
    required_nonempty = {"llm_base_url", "llm_model"}
    updates: dict[str, str] = {}
    for field, is_secret in fields.items():
        if field not in body:
            continue
        val = body[field]
        if val is None:
            continue
        val = str(val).strip()
        if field in required_nonempty and not val:
            return JSONResponse({"ok": False, "error": f"{field} 不能为空"}, status_code=400)
        if is_secret and field not in clear_fields and (not val or "****" in val):
            continue
        updates[field.upper()] = val

    try:
        if "LLM_TEMPERATURE" in updates:
            temperature = float(updates["LLM_TEMPERATURE"])
            if not 0 <= temperature <= 2:
                raise ValueError("LLM_TEMPERATURE 必须在 0 到 2 之间")
        if "LLM_MAX_TOKENS" in updates:
            max_tokens = int(updates["LLM_MAX_TOKENS"])
            if not 1 <= max_tokens <= 32768:
                raise ValueError("LLM_MAX_TOKENS 必须在 1 到 32768 之间")
    except (TypeError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    if not updates:
        return {"ok": True, "updated": [], "note": "没有需要保存的变更"}

    updated = update_env_file(updates)
    config.reload()

    # 重置依赖配置的缓存，让新配置立即生效
    try:
        from ..core import llm as _llm
        old_clients = [_llm._client, getattr(_llm.get_perception_client, "_client", None)]
        _llm._client = None
        # 感知层独立 client 也缓存于 get_perception_client._client，改了
        # LLM_PERCEPTION_* 端点/模型后必须一并清掉，否则仍用旧端点。
        _llm.get_perception_client._client = None
        closed: set[int] = set()
        for client in old_clients:
            if client is not None and id(client) not in closed:
                closed.add(id(client))
                try:
                    await client.close()
                except Exception:
                    pass
    except Exception:
        pass
    try:
        from ..core import persona as _persona
        _persona._persona_cache = None
    except Exception:
        pass

    return {"ok": True, "updated": updated}
