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
    "compact_ui_enabled": "简洁界面：收纳工具入口，正常状态不显示工具灯",
    "aesthetics_enabled": "共同审美：记录视觉偏好，在房间中摆放共同作品",
    "memory_lifecycle_enabled": "记忆保留：让短期状态随时间淡出，固定的记忆始终保留",
    "memory_salience_enabled": "记忆重要性：整理你明确重视和反复提及的记忆线索",
    "output_hygiene_enabled": "输出卫生：回复发送前统一安全检查（关闭时保持旧流式契约）",
    "context_registry_enabled": "语境注册表：统一管理注入对话的语境（含知识观点的召回）",
    "profile_enabled": "用户画像：从对话中提炼用户特征用于回复",
    "life_templates_enabled": "生活模板：她会低频出门或换活动（精力有限选择，可在对话中喊停）",
    "relationship_style_enabled": "关系气质：由真实共同经历形成长期气质（可在了解她/我们之间查看与屏蔽）",
    "greeting_material_enabled": "问候变体：用你们真实的近况开口（关闭后回到固定兜底问候）",
    "companion_requests_enabled": "她的请求：关系够近时她偶尔请你帮个小忙（挑歌/挑书）",
    "humor_memory_enabled": "幽默记忆：只有你明确认可的梗她才会反复玩（不认可的立刻收起来）",
    "focus_wrapup_enabled": "专注收尾：结束专注时她按真实情况说一句收尾（不评分）",
    "activity_drafts_enabled": "意图预填：你说想一起做什么时，她先给一张可确认的草稿",
    "telemetry_enabled": "本地诊断：记录无正文的事件链（7天）与日聚合（30天）",
    "style_map_enabled": "表达习惯观察：她记住你在不同场合的说话调子，回应更合拍（可在记忆管理删除）",
    "experience_metrics_enabled": "质量统计：本地计数回复延迟、规则失败、重复与你的反馈（不记录正文，可清除）",
    "shared_resources_enabled": "跨角色共享：允许把你的文档或共同产物明确分享给另一个角色看（默认全部隔离）",
    "tavern_enabled": "酒馆同玩：允许本机酒馆扩展点名她一起玩故事（关闭后相关接口一律拒绝）",
    "willingness_enabled": "主动意愿：值得说的主动消息她还是会想想此刻想不想说（约定到点与你点名的必应不受影响）",
    "situation_enabled": "局势档案：一份常驻的世界快照让她随时记得你们正在做的事和没解开的疑问",
    "offline_recap_enabled": "离线补算：你不在的这段时间她照常过日子，回来后逐条讲给你听（可跳过）",
    "remote_gateway_enabled": "远程网关：手机等外部设备经公网安全通道访问（需域名与 Access 配置，默认关闭）",
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
            "proactive_new_user_days": config.proactive_new_user_days,
            "proactive_new_user_idle_hours": config.proactive_new_user_idle_hours,
            "proactive_surprise_min_gap_days": config.proactive_surprise_min_gap_days,
            "proactive_surprise_chance_percent": config.proactive_surprise_chance_percent,
            "proactive_surprise_idle_minutes": config.proactive_surprise_idle_minutes,
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
        "proactive_new_user_days": False,
        "proactive_new_user_idle_hours": False,
        "proactive_surprise_min_gap_days": False,
        "proactive_surprise_chance_percent": False,
        "proactive_surprise_idle_minutes": False,
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
        integer_ranges = {
            "PROACTIVE_NEW_USER_DAYS": (1, 30),
            "PROACTIVE_NEW_USER_IDLE_HOURS": (1, 24),
            "PROACTIVE_SURPRISE_MIN_GAP_DAYS": (1, 365),
            "PROACTIVE_SURPRISE_CHANCE_PERCENT": (0, 100),
            "PROACTIVE_SURPRISE_IDLE_MINUTES": (15, 10080),
        }
        for name, (minimum, maximum) in integer_ranges.items():
            if name not in updates:
                continue
            value = int(updates[name])
            if not minimum <= value <= maximum:
                raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
    except (TypeError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    if not updates:
        return {"ok": True, "updated": [], "note": "没有需要保存的变更"}

    updated = update_env_file(updates)
    config.reload()

    # 重置依赖配置的缓存，让新配置立即生效
    try:
        from ..core import llm as _llm
        # 必须先清空按路由缓存的 client 表：下面 close() 掉的实例若仍留在
        # _client_cache 里，_client_for_route 会按同一 cache key 命中并复用，
        # 之后每次调用都抛「Cannot send a request, as the client has been
        # closed」，直到重启后端（2026-09-09 实证）。
        cached_clients = list(_llm._client_cache.values())
        _llm._client_cache.clear()
        old_clients = [
            _llm._client,
            getattr(_llm.get_perception_client, "_client", None),
            *cached_clients,
        ]
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
