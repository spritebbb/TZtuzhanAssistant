# -*- coding: utf-8 -*-
"""每步确认接口：用户对工具操作做出允许/拒绝决定。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..tools.confirm import ConfirmService

router = APIRouter(prefix="/api", tags=["confirm"])


def _to_bool(v: object) -> bool:
    """宽松布尔解析：true/1/yes/y/on 视为真；缺失/其他一律 False（默认拒绝，安全）。"""
    if v is None:
        return False
    return str(v).strip().lower() in ("true", "1", "yes", "y", "on")


async def _extract_confirm_params(request: Request) -> tuple[str, bool]:
    """从 query / form body / JSON body 三种来源提取 request_id 与 allow。

    前端 ConfirmPanel/AgentPanel 以 form body（application/x-www-form-urlencoded）
    发送；FastAPI 普通类型参数只从 query 绑定，导致 form 传参被忽略（历史 bug：
    用户点允许/拒绝无响应，只能等超时拒绝）。这里手动解析，三种方式都接受。
    """
    request_id = (request.query_params.get("request_id") or "").strip()
    allow_raw: object = request.query_params.get("allow")

    if not request_id:
        ctype = (request.headers.get("content-type") or "").lower()
        if "json" in ctype:
            try:
                body = await request.json()
            except Exception:
                body = {}
            if isinstance(body, dict):
                request_id = str(body.get("request_id") or "").strip()
                allow_raw = body.get("allow", allow_raw)
        else:
            try:
                form = await request.form()
            except Exception:
                form = {}
            request_id = str(form.get("request_id") or "").strip()
            allow_raw = form.get("allow", allow_raw)
    return request_id, _to_bool(allow_raw)


@router.post("/confirm")
async def api_confirm(request: Request):
    """响应对应 request_id 的确认请求。

    request_id（必须）、allow（True=允许执行，False=拒绝）。
    兼容三种传参：query（?request_id=...&allow=true）、
    form body（前端 ConfirmPanel 的方式）、JSON body。
    allow 缺失时按 False（拒绝）处理，安全默认。
    返回 ok 表示已受理；请求不存在/已超时返回 ok=False。
    """
    request_id, allow = await _extract_confirm_params(request)
    if not request_id:
        return JSONResponse({"ok": False, "error": "缺少 request_id"}, status_code=400)
    found = await ConfirmService.resolve(request_id, allow)
    if not found:
        return JSONResponse(
            {"ok": False, "error": "确认请求不存在或已超时"},
            status_code=404,
        )
    return {"ok": True, "allow": allow}


@router.get("/confirm/pending")
async def api_confirm_pending():
    """当前挂起的确认请求数（供调试/前端状态指示）。"""
    return {"ok": True, "pending": ConfirmService.pending_count()}
