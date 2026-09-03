# -*- coding: utf-8 -*-
"""工具审计查询 API：供前端面板查看 / 过滤 / 导出审计日志。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..tools.audit import recent_log, query_log, count_log, clear_log

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/log")
async def api_audit_log(
    limit: int = 100,
    offset: int = 0,
    tool: str = "",
    confirmed: str = "",
    ok: str = "",
    q: str = "",
):
    """查询审计日志，支持过滤。

    query/form 参数：
    - limit: 返回条数（默认 100，最大 500）
    - offset: 偏移
    - tool: 按工具名过滤（可逗号分隔多个）
    - confirmed: 按确认状态过滤（auto/allow/deny/timeout/blocked）
    - ok: 按成功状态过滤（true/false）
    - q: 按工具/结果关键词模糊过滤
    """
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    rows = query_log(
        limit=limit, offset=offset,
        tool=tool or None, confirmed=confirmed or None,
        ok=None if ok == "" else ok.lower() == "true",
        q=q or None,
    )
    return {
        "ok": True,
        "total": count_log(tool=tool or None, confirmed=confirmed or None,
                           ok=None if ok == "" else ok.lower() == "true", q=q or None),
        "offset": offset,
        "limit": limit,
        "rows": rows,
    }


@router.delete("/log")
async def api_audit_clear():
    """清空审计日志（谨慎操作）。"""
    cleared = clear_log()
    return {"ok": True, "cleared": cleared}
