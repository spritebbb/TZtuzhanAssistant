# -*- coding: utf-8 -*-
"""知识库（D2 RAG）：文档上传 / 列表 / 删除。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..core import knowledge
from ..core.config import config
from ..core.log import logger
from ..core.persona_profiles import active_user_id

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class OpinionCreate(BaseModel):
    stance: str = Field(min_length=1, max_length=2000)
    source_spans: list[dict]
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

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


class ImportUrlPayload(BaseModel):
    url: str = Field(..., max_length=2048)


@router.post("/import-url")
async def api_import_url(payload: ImportUrlPayload):
    """L01 网页导入：校验通过后返回 202 + job_id（worker 抓取解析，可取消）。"""
    from ..core import document_import

    uid = active_user_id()
    try:
        job = await asyncio.to_thread(document_import.start_url_import, uid, payload.url)
    except document_import.DocumentImportError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
    return JSONResponse({"ok": True, **job}, status_code=202)


@router.get("/import-jobs/{job_id}")
async def api_import_job(job_id: str):
    from ..core import document_import

    state = document_import.job_state(job_id)
    if state is None:
        return JSONResponse({"ok": False, "error": "任务不存在"}, status_code=404)
    return {"ok": True, **state}


@router.post("/import-jobs/{job_id}/cancel")
async def api_cancel_import_job(job_id: str):
    from ..core import document_import

    if not document_import.cancel_job(job_id):
        return JSONResponse({"ok": False, "error": "任务不存在或已结束"}, status_code=404)
    return {"ok": True, "job_id": job_id, "status": "cancelled"}


@router.get("/documents")
async def api_knowledge_list():
    documents = await asyncio.to_thread(knowledge.list_documents, active_user_id())
    return {"ok": True, "documents": documents}


@router.delete("/documents/{doc_id}")
async def api_knowledge_delete(doc_id: int):
    from ..core import document_import

    uid = active_user_id()
    if not await asyncio.to_thread(knowledge.delete_document, uid, doc_id):
        return {"ok": False, "error": "文档不存在"}
    # L01：源删清阅读段（F05 地图由活动删除路径处理）
    await asyncio.to_thread(document_import.forget_for_document, uid, doc_id)
    return {"ok": True}


@router.get("/opinions")
async def api_opinion_list(document_id: int | None = None):
    opinions = await asyncio.to_thread(
        knowledge.list_opinions, active_user_id(), document_id,
    )
    return {"ok": True, "opinions": opinions}


@router.post("/documents/{doc_id}/opinions")
async def api_opinion_create(doc_id: int, payload: OpinionCreate):
    try:
        opinion = await asyncio.to_thread(
            knowledge.save_opinion,
            active_user_id(), doc_id, payload.stance, payload.source_spans,
            origin="user", confidence=payload.confidence,
        )
    except knowledge.KnowledgeError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
    return {"ok": True, "opinion": opinion}


@router.post("/documents/{doc_id}/extract-opinions")
async def api_opinion_extract(doc_id: int):
    """书架面板「读出观点」：LLM 通读该文档，提炼带来源的观点（0-2 条）。"""
    try:
        opinions = await knowledge.extract_opinions(active_user_id(), doc_id)
    except knowledge.KnowledgeError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
    except Exception:
        logger.exception("[知识库] 观点提炼端点异常：doc {}", doc_id)
        return {"ok": False, "error": "她还没读出什么观点，过会儿再试"}
    if not opinions:
        return {"ok": True, "opinions": [],
                "note": "她还没读出什么想说的，过会儿再试试"}
    return {"ok": True, "opinions": opinions}


@router.delete("/opinions/{opinion_id}")
async def api_opinion_revoke(opinion_id: int):
    ok = await asyncio.to_thread(knowledge.revoke_opinion, active_user_id(), opinion_id)
    if not ok:
        return JSONResponse(status_code=404, content={"ok": False, "error": "观点不存在"})
    return {"ok": True}
