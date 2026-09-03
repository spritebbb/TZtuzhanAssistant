# -*- coding: utf-8 -*-
"""识图接口。"""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse

from ..core.config import config

router = APIRouter(prefix="/api", tags=["vision"])


def _detect_ext(data: bytes, filename: str) -> str:
    """按文件真实内容（魔数）判断图片扩展名，回落到上传文件名后缀，再回落 .png。

    魔数优先：浏览器按响应头的 MIME/扩展名渲染，若扩展名与真实内容不符，
    部分严格浏览器会拒绝渲染。识别顺序：png / jpeg / gif / webp。
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    low = filename.lower()
    if low.endswith((".jpg", ".jpeg")):
        return ".jpg"
    if low.endswith(".gif"):
        return ".gif"
    if low.endswith(".webp"):
        return ".webp"
    return ".png"


@router.post("/vision")
async def api_vision(request: Request):
    """识图：上传图片，视觉模型描述内容。multipart 字段 file。

    除返回描述外，把图片落盘到 data/imgs/（与生图同目录）并返回 image_url，
    供前端作为 user 消息的 image 字段持久化，避免「识图不落图、刷新后文案错位」。
    """
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

    # 落盘图片：文件名 = md5 前缀 + 扩展名（复用 /api/images/ 服务的命名白名单）。
    # 扩展名按文件真实内容（魔数）判断，而非上传文件名，避免「.jpg 名存 PNG 内容」
    # 导致某些严格浏览器不渲染。
    ext = _detect_ext(data, up.filename or "")
    digest = hashlib.md5(data).hexdigest()[:16]
    fname = f"vision_{digest}{ext}"
    img_dir = config.data_dir / "imgs"
    try:
        img_dir.mkdir(parents=True, exist_ok=True)
        (img_dir / fname).write_bytes(data)
    except OSError:
        # 落盘失败不阻塞识图：仅返回描述，前端退化为纯文本
        return {"ok": True, "description": text}
    return {"ok": True, "description": text, "image_url": f"/api/images/{fname}"}