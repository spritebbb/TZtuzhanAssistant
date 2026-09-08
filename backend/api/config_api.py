# -*- coding: utf-8 -*-
"""配置编辑接口（查询/保存 .env 配置 + 功能开关读写）。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..core.config import config, update_env_file
from ..core.features import all_flags, set_flag

router = APIRouter(prefix="/api", tags=["config"])

# 功能开关说明（设置页展示用）：键名 → 一句话用途。
_FLAG_LABELS = {
    "output_hygiene_enabled": "输出卫生：回复发送前统一安全检查（关闭时保持旧流式契约）",
    "context_registry_enabled": "语境注册表：统一管理注入对话的语境（含知识观点的召回）",
    "profile_enabled": "用户画像：从对话中提炼用户特征用于回复",
    "life_templates_enabled": "生活模板：她会低频出门或换活动（精力有限选择，可在对话中喊停）",
    "relationship_style_enabled": "关系气质：由真实共同经历形成长期气质（可在了解她/我们之间查看与屏蔽）",
}


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


@router.get("/flags")
async def api_flags_get():
    """功能开关面板：返回全部开关当前值（含默认）与用途说明。"""
    return {"ok": True, "flags": all_flags(), "labels": _FLAG_LABELS}


@router.post("/flags")
async def api_flags_set(request: Request):
    """功能开关面板：写入单个开关，立即生效（pipeline 注入前动态读取）。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "JSON 解析失败"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "请求体必须是对象"}, status_code=400)
    name = str(body.get("name", "")).strip()
    value = body.get("value")
    if not name or not isinstance(value, bool):
        return JSONResponse({"ok": False, "error": "需要 name 与布尔 value"}, status_code=400)
    if name not in _FLAG_LABELS:
        return JSONResponse({"ok": False, "error": f"未知开关: {name}"}, status_code=400)
    set_flag(name, value)
    return {"ok": True, "name": name, "value": value, "flags": all_flags()}
