# -*- coding: utf-8 -*-
"""插件管理 API：列表 / 启用 / 禁用 / 重载 + 插件 HTTP 路由网关。

网关：/plugins/{plugin}/{path} → 按 (插件, METHOD, path) 分发到插件注册的 handler，
插件热装卸后立即生效，无需重启。
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..core.log import logger
from ..plugins import loader
from ..plugins.context import dispatch_route

router = APIRouter(prefix="/api", tags=["plugins"])

# 插件路由网关（根级路径，不走 /api 前缀）
gateway = APIRouter(tags=["plugins"])


@router.get("/plugins")
async def api_plugins_list():
    """插件列表：元信息 + 状态 + 注册的能力 + 错误信息。"""
    return {"ok": True, "plugins": list(loader.plugin_states().values())}


@router.post("/plugins/{name}/enable")
async def api_plugin_enable(name: str):
    """启用插件：从禁用集合移除并立即加载。"""
    if not loader.plugin_name_ok(name):
        return JSONResponse({"ok": False, "error": f"插件不存在: {name}"}, status_code=404)
    if not (loader.PLUGINS_DIR / f"{name}.py").exists():
        return JSONResponse({"ok": False, "error": f"插件不存在: {name}"}, status_code=404)
    loader.set_disabled(name, False)
    path = loader.PLUGINS_DIR / f"{name}.py"
    ok = loader.load_plugin(path)
    st = loader.plugin_states().get(name, {})
    if not ok:
        return JSONResponse({
            "ok": False,
            "error": st.get("error") or "加载失败",
            "plugin": st,
        }, status_code=500)
    return {"ok": True, "plugin": st}


@router.post("/plugins/{name}/disable")
async def api_plugin_disable(name: str):
    """禁用插件：卸载并持久化禁用状态（重启后仍禁用）。"""
    if not loader.plugin_name_ok(name):
        return JSONResponse({"ok": False, "error": f"插件不存在: {name}"}, status_code=404)
    if not loader.set_disabled(name, True):
        return JSONResponse({"ok": False, "error": f"插件不存在: {name}"}, status_code=404)
    return {"ok": True, "plugin": loader.plugin_states().get(name, {})}


@router.post("/plugins/{name}/reload")
async def api_plugin_reload(name: str):
    """手动重载插件（禁用状态下返回 400）。"""
    if not loader.plugin_name_ok(name):
        return JSONResponse({"ok": False, "error": f"插件不存在: {name}"}, status_code=404)
    path = loader.PLUGINS_DIR / f"{name}.py"
    if not path.exists():
        return JSONResponse({"ok": False, "error": f"插件不存在: {name}"}, status_code=404)
    if name in loader.plugin_states() and loader.plugin_states()[name].get("disabled"):
        return JSONResponse({"ok": False, "error": "插件已禁用，请先启用"}, status_code=400)
    ok = loader.load_plugin(path)
    st = loader.plugin_states().get(name, {})
    if not ok:
        return JSONResponse({
            "ok": False,
            "error": st.get("error") or "加载失败",
            "plugin": st,
        }, status_code=500)
    return {"ok": True, "plugin": st}


@router.get("/plugins/{name}/source")
async def api_plugin_source(name: str):
    """查看插件源码（只读；仅限 plugins/ 目录内的 .py，防路径穿越）。"""
    if not loader.plugin_name_ok(name):
        return JSONResponse({"ok": False, "error": f"插件不存在: {name}"}, status_code=404)
    plugins_root = loader.PLUGINS_DIR.resolve()
    path = (plugins_root / f"{name}.py").resolve()
    # 用 is_relative_to 做目录归属判断（旧实现 startswith 缺少尾部分隔符，
    # 遇到 plugins_xxx 同前缀目录时存在前缀绕过风险）
    if not path.is_relative_to(plugins_root):
        return JSONResponse({"ok": False, "error": "非法路径"}, status_code=400)
    if not path.exists():
        return JSONResponse({"ok": False, "error": f"插件不存在: {name}"}, status_code=404)
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"读取失败: {e}"}, status_code=500)
    return {"ok": True, "name": name, "source": source[:100_000]}


@gateway.api_route("/plugins/{plugin}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def plugin_gateway(plugin: str, path: str, request: Request):
    """插件 HTTP 路由网关：按 (插件, METHOD, /path) 分发到插件 handler。"""
    handler = dispatch_route(plugin, request.method, "/" + path)
    if handler is None:
        return JSONResponse({"ok": False, "error": "插件路由不存在"}, status_code=404)
    try:
        result = handler(request)
        if hasattr(result, "__await__"):
            result = await result
    except Exception as e:
        logger.exception("[插件网关] {} {} 执行失败", plugin, path)
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500)
    if isinstance(result, JSONResponse) or hasattr(result, "status_code"):
        return result
    if isinstance(result, (dict, list)):
        return JSONResponse(result)
    return JSONResponse({"ok": True, "data": str(result)})
