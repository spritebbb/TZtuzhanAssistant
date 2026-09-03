# -*- coding: utf-8 -*-
"""FastAPI 应用工厂：注册全部路由 + CORS + 后台任务。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import (
    agent,
    audit,
    chat,
    config_api,
    confirm,
    greeting,
    health,
    images,
    initiative,
    mcp_servers,
    meta,
    plugins as plugins_api,
    remote,
    sessions,
    tts,
    vision,
)
from .maintenance.loop import checkpoint_all, maintenance_loop
from .session import store as session_store
from .tools import mcp_server
from .core.log import logger


def create_app() -> FastAPI:
    app = FastAPI(title="菟菚 桌面助手")

    # CORS：只允许本机可信来源。不包含 "null"——file:// / data: / sandboxed iframe
    # 的 Origin 均为 null，放行它等于给恶意本地 HTML/嵌 iframe 的网页开 CORS 读取。
    # Electron 生产环境已改为加载 http://127.0.0.1:8801（同源，见 electron/main.ts）；
    # Vite dev 是 localhost:5173。收窄后，恶意网页无法借 CORS 读本机 API 响应。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8801",
            "http://127.0.0.1:8801",
        ],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _host_guard(request, call_next):
        """Host 校验：只接受本机来源，防 DNS rebinding/外部直连。

        默认白名单为本机地址；监听 0.0.0.0 供局域网访问时，可在
        AGENT_ALLOWED_HOSTS 里追加（分号分隔，如 192.168.1.10:8801）。
        """
        from .core.config import config

        host = (request.headers.get("host") or "").strip().lower()
        allowed = {h.strip().lower() for h in getattr(config, "agent_allowed_hosts", [])}
        allowed.add("testserver")  # Starlette TestClient 的标准 Host（测试用）
        if host not in allowed:
            return JSONResponse({"ok": False, "error": "来源 Host 不被允许"}, status_code=403)
        return await call_next(request)

    # 可信浏览器来源（与 CORS 白名单一致；Vite dev → 127.0.0.1:8801 在浏览器
    # 眼里是 cross-site，须凭 Origin 白名单放行）
    _TRUSTED_ORIGINS = {
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:8801", "http://127.0.0.1:8801",
    }

    @app.middleware("http")
    async def _origin_guard(request, call_next):
        """Origin/Sec-Fetch-Site 校验：跨站写请求一律 403（CSRF 第二道防线）。

        第一道防线是 CORS 白名单（无 null），已挡住浏览器侧的响应读取；
        这里再按请求方法拦截：
        - Sec-Fetch-Site: cross-site 且 Origin 不在可信白名单 → 恶意网页
        - Origin: null（本地恶意 HTML / 沙箱 iframe）→ 一律拒绝
        同源页面（Electron 生产走 127.0.0.1:8801）与可信 dev 来源不受影响；
        无 Origin 的非浏览器客户端（curl/DSH/测试）不受影响。
        """
        method = request.method.upper()
        if method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)
        site = (request.headers.get("sec-fetch-site") or "").strip().lower()
        origin = (request.headers.get("origin") or "").strip().lower().rstrip("/")
        if site == "same-origin" or site == "same-site":
            return await call_next(request)
        if site == "cross-site" and origin not in _TRUSTED_ORIGINS:
            logger.warning("[安全] 拦截跨站写请求：method={} origin={} path={}",
                           method, origin or "-", request.url.path)
            return JSONResponse({"ok": False, "error": "跨站请求被拒绝"}, status_code=403)
        if origin == "null":
            # 本地 HTML / 沙箱 iframe：Origin 为 null 但仍可能是恶意来源。
            # 仅放行明确的 Electron file:// 场景没有可靠特征，直接拒绝（写请求）。
            logger.warning("[安全] 拦截 null Origin 写请求：method={} path={}",
                           method, request.url.path)
            return JSONResponse({"ok": False, "error": "跨站请求被拒绝"}, status_code=403)
        return await call_next(request)

    # 受控端点统一鉴权（来源 IP 语义，见 tools/safety.remote_token_ok_by_peer）：
    # - 回环来源免 token（本机 UI/AgentPanel/聊天不受影响，无论服务绑在哪）；
    # - 非回环来源必须携带有效 token（局域网/公网裸调一律 403）。
    # 覆盖范围：
    #   /mcp/*、/api/mcp/*、/api/agent/*（含 GET，工具/任务元数据也敏感）；
    #   /api/plugins/*（含 GET——插件源码/管理信息对局域网也敏感）；
    #   /api/* 的全部写方法（POST/PUT/PATCH/DELETE——/api/config、/api/chat、
    #     /api/sessions、/api/audit 等不再裸奔）；
    #   /plugins/*（插件网关可执行任意插件路由，含 GET）。
    _WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")

    @app.middleware("http")
    async def _remote_auth_guard(request, call_next):
        path = request.url.path
        method = request.method.upper()
        protected = (
            path.startswith("/plugins/")
            or path.startswith("/api/plugins/")
            or path.startswith("/mcp/")
            or path.startswith("/api/mcp/")
            or path.startswith("/api/agent/")
            or (path.startswith("/api/") and method in _WRITE_METHODS)
        )
        if not protected:
            return await call_next(request)
        from .tools.safety import remote_token_ok_by_peer, request_token

        peer_ip = request.client.host if request.client else None
        if not remote_token_ok_by_peer(request_token(request), peer_ip):
            logger.warning("[安全] 拦截未授权受控端点请求：method={} path={}",
                           request.method, path)
            if path.startswith("/mcp/"):
                return JSONResponse(
                    {"jsonrpc": "2.0", "error": {"code": -32001, "message": "unauthorized"}},
                    status_code=403,
                )
            return JSONResponse({"ok": False, "error": "token 无效"}, status_code=403)
        return await call_next(request)

    # 注册路由
    app.include_router(health.router)
    app.include_router(sessions.router)
    app.include_router(chat.router)
    app.include_router(confirm.router)
    app.include_router(agent.router)
    app.include_router(vision.router)
    app.include_router(images.router)
    app.include_router(meta.router)
    app.include_router(audit.router)
    app.include_router(remote.router)
    app.include_router(config_api.router)
    app.include_router(greeting.router)
    app.include_router(initiative.router)
    app.include_router(tts.router)
    app.include_router(mcp_servers.router)
    app.include_router(mcp_server.router)
    app.include_router(plugins_api.router)
    app.include_router(plugins_api.gateway)

    # 生产环境由后端直接服务前端构建产物：Electron 主进程 loadURL(http://127.0.0.1:8801)
    # （同源请求，Origin 可信；原 file:// 方式的 Origin 是 null，已被 CORS/守卫拒绝）。
    # 挂载在全部 API 路由之后，不影响 /api/* 与 /mcp/*。
    _DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if _DIST.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="frontend")
        logger.info("[前端] 静态资源已挂载: {}", _DIST)

    @app.on_event("startup")
    async def _startup() -> None:
        session_store.init()
        # 记忆引擎 v2 初始化（后台：Chroma + embedding + 存量迁移，不阻塞启动）
        try:
            from .core.memory.engine import ensure_ready

            needs_migration = await asyncio.to_thread(ensure_ready)
            if needs_migration:
                # 迁移（embedding 批量推理）在工作线程执行，但调度必须在事件循环线程
                from .core.memory.migration import migrate
                from .core.memory.engine import _background_tasks

                task = asyncio.create_task(asyncio.to_thread(migrate))
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
        except Exception:
            logger.exception("[记忆] 记忆引擎初始化失败（降级为旧版检索）")
        # 用户身份统一迁移：把向量库里旧身份（session_current）的向量改挂
        # assistant-main，与 userdb 的 SQLite 身份迁移保持一致（幂等，无旧数据则无副作用）
        try:
            from .core.memory import vector_store as _vec

            _vec.migrate_user_id("session_current", "assistant-main")
        except Exception:
            logger.exception("[记忆] 向量用户身份迁移失败（不影响主流程）")
        # embedding 模型后台预热：下载/加载可能耗时数分钟，绝不能阻塞启动；
        # 预热失败会进哈希降级态并打明确告警（见 embedding.warmup）
        try:
            from .core.config import config as _cfg
            from .core.memory.embedding import warmup as _warmup

            async def _prewarm_embedding() -> None:
                try:
                    # wait_for 只负责"不再等待"，to_thread 里的下载线程无法被杀，
                    # 但服务已可用，预热完成与否都不影响主流程
                    await asyncio.wait_for(asyncio.to_thread(_warmup), timeout=600)
                except asyncio.TimeoutError:
                    logger.warning("[记忆] embedding 预热超时（>600s），服务已放行，后台线程继续加载")

            if _cfg.memory_v2:
                _spawn_bg(_prewarm_embedding())
        except Exception:
            logger.exception("[记忆] embedding 预热任务启动失败")
        # 注册内置工具（仅记忆系统；其余工具已插件化，由下方插件系统加载）
        from .tools.builtin.register_all import register_all as register_builtin_tools
        register_builtin_tools()
        # 注册全局确认钩子（每步确认机制）
        from .tools.base import ToolRegistry
        from .tools.confirm import default_confirm_hook
        ToolRegistry.set_confirm_hook(default_confirm_hook)
        # 自动加载插件（plugins/*.py → 注册工具/任务/钩子/路由）
        try:
            from .plugins import load_all_plugins, watch_plugins

            loaded = load_all_plugins()
            if loaded:
                logger.info("[插件] 已加载 {} 个插件: {}", len(loaded), ", ".join(loaded))
            # 启动热加载：监听 plugins/ 目录变化，自动加载/重载/卸载插件
            _spawn_bg(watch_plugins())
        except Exception:
            logger.exception("[插件] 插件加载失败（不影响主服务）")
        # 恢复持久化的外部 MCP 服务器（后台重连，不阻塞启动）
        try:
            _spawn_bg(_restore_mcp())
        except Exception:
            logger.exception("[MCP] 外部服务器恢复任务启动失败")
        _spawn_bg(maintenance_loop())
        checkpoint_all()  # 启动时先合并一次 WAL
        # 主动性引擎：后台 loop 定时检查「久未聊 + 关系够近」的用户，生成主动消息
        # 并写入待投递队列（kv_store），前端轮询 /api/initiative 时取走（离线不丢）。
        # 默认不注册实时投递 hook，避免污染全局会话；如需实时推（如桌面通知），
        # 可 initiative.set_deliver_hook(...) 注册后再决定是否改为实时投递。
        try:
            from .core import initiative as _initiative

            _spawn_bg(_initiative.initiative_loop())
        except Exception:
            logger.exception("[主动性] 后台引擎启动失败")

    _bg_tasks: set = set()

    def _spawn_bg(coro) -> None:
        """创建后台任务并持有强引用（无引用的 task 可能被 GC 静默取消）。"""
        task = asyncio.create_task(coro)
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)

    async def _restore_mcp() -> None:
        try:
            from .tools.mcp_server import restore_persisted_servers

            n = await restore_persisted_servers()
            if n:
                logger.info("[MCP] 已恢复 {} 个外部服务器", n)
        except Exception:
            logger.exception("[MCP] 外部服务器恢复失败")

    return app


app = create_app()
