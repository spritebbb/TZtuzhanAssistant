# -*- coding: utf-8 -*-
"""知识库（D2 RAG）：文档上传 / 列表 / 删除。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, UploadFile
from fastapi.responses import JSONResponse

from ..core import knowledge
from ..core.config import config
from ..core.log import logger
from ..core.persona_profiles import active_user_id

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

@router.post("/upload")
async def api_knowledge_upload(file: UploadFile):
    uid = active_user_id()
    max_size = config.kb_max_file_mb * 1024 * 1024
    if file.size is not None and file.size > max_size:
        return JSONResponse(
            status_code=413,
            content={"ok": False, "error": f"文件超过 {config.kb_max_file_mb}MB 上限"},
        )
    data = await file.read(max_size + 1)
    if len(data) > max_size:
        return JSONResponse(
            status_code=413,
            content={"ok": False, "error": f"文件超过 {config.kb_max_file_mb}MB 上限"},
        )
    if not data:
        return {"ok": False, "error": "文件是空的"}
    try:
        doc = await asyncio.to_thread(
            knowledge.ingest_document, uid, file.filename or "未命名", data
        )
    except knowledge.KnowledgeError as e:
        return {"ok": False, "error": str(e)}
    except Exception:
        logger.exception("[知识库] 上传失败：{}", file.filename)
        return {"ok": False, "error": "文档解析入库失败"}
    return {"ok": True, "document": doc}


@router.get("/documents")
async def api_knowledge_list():
    documents = await asyncio.to_thread(knowledge.list_documents, active_user_id())
    return {"ok": True, "documents": documents}


@router.delete("/documents/{doc_id}")
async def api_knowledge_delete(doc_id: int):
    if not await asyncio.to_thread(knowledge.delete_document, active_user_id(), doc_id):
        return {"ok": False, "error": "文档不存在"}
    return {"ok": True}
