# -*- coding: utf-8 -*-
"""识图接口。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api", tags=["vision"])


@router.post("/vision")
async def api_vision(request: Request):
    """识图：上传图片，视觉模型描述内容。multipart 字段 file。"""
    from fastapi import UploadFile

    form = await request.form()
    up: UploadFile | None = form.get("file")
    if up is None:
        return JSONResponse({"ok": False, "error": "缺少图片"}, status_code=400)
    data = await up.read()
    if len(data) > 8 * 1024 * 1024:
        return JSONResponse({"ok": False, "error": "图片过大（>8MB）"}, status_code=413)
    from ..core.vision import describe_bytes

    text = await describe_bytes(data, filename=up.filename or "image.png")
    if not text:
        return JSONResponse({"ok": False, "error": "识图失败（视觉模型不可用）"}, status_code=502)
    return {"ok": True, "description": text}