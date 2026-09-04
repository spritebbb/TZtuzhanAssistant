# -*- coding: utf-8 -*-
"""知识库（D2 RAG）：文档上传 / 列表 / 删除。"""
from __future__ import annotations

from fastapi import APIRouter, UploadFile

from ..core import knowledge
from ..core.log import logger

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

_UID = "assistant-main"


@router.post("/upload")
async def api_knowledge_upload(file: UploadFile):
    data = await file.read()
    if not data:
        return {"ok": False, "error": "文件是空的"}
    try:
        doc = knowledge.ingest_document(_UID, file.filename or "未命名", data)
    except knowledge.KnowledgeError as e:
        return {"ok": False, "error": str(e)}
    except Exception:
        logger.exception("[知识库] 上传失败：{}", file.filename)
        return {"ok": False, "error": "文档解析入库失败"}
    return {"ok": True, "document": doc}


@router.get("/documents")
async def api_knowledge_list():
    return {"ok": True, "documents": knowledge.list_documents(_UID)}


@router.delete("/documents/{doc_id}")
async def api_knowledge_delete(doc_id: int):
    if not knowledge.delete_document(_UID, doc_id):
        return {"ok": False, "error": "文档不存在"}
    return {"ok": True}
