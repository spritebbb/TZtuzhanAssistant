# -*- coding: utf-8 -*-
"""知识库（D2 RAG）：用户投喂 pdf/txt/md，菟菚"读过"并能在对话中自然引用。

设计：
- 文档原文落盘 data/documents/{user_id}/，元数据与分块进 userdb
  （kb_documents / kb_chunks 两表），向量进 Chroma 的 kb 分区（kind="kb"，
  record_id = kb_chunks.id），与记忆系统同一套语义检索设施。
- 检索纯本地（BGE-M3 embedding），不走云端 LLM，无额外 token 成本。
- 召回有距离阈值门控（kb_recall_max_distance）：不够像就一条都不注入，
  避免无关内容硬凑进 prompt 带偏回复。
- 所有写/删失败静默降级（记日志、返回空），绝不阻塞对话主流程。
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path

from .log import logger

_SUPPORTED_FORMATS = {"pdf", "txt", "md"}


class KnowledgeError(ValueError):
    """可预期的业务错误（格式不支持/超限/内容为空），API 层转成 400。"""


# ---------- 解析 ----------

def detect_format(filename: str) -> str:
    suffix = Path(filename).suffix.lower().lstrip(".")
    if suffix not in _SUPPORTED_FORMATS:
        raise KnowledgeError(f"不支持的格式 .{suffix}，目前只读 pdf / txt / md")
    return suffix


def parse_document(fmt: str, data: bytes) -> str:
    """把文件字节解析成纯文本。解析失败抛 KnowledgeError。"""
    if fmt == "pdf":
        try:
            import io

            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            pages = [(page.extract_text() or "") for page in reader.pages]
            text = "\n".join(pages)
        except KnowledgeError:
            raise
        except Exception as e:
            raise KnowledgeError(f"PDF 解析失败：{str(e)[:80]}") from e
    else:
        for encoding in ("utf-8", "gb18030"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise KnowledgeError("文本编码识别失败（试过 utf-8 / gb18030）")
    # 统一空白：压缩连续空行，去掉行尾空格
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if not text:
        raise KnowledgeError("文档里没有可读的文本内容")
    return text


# ---------- 分块 ----------

def chunk_text(text: str, size: int = 600, overlap: int = 120) -> list[str]:
    """按字符窗口重叠分块，尽量在段落/句子边界切断。

    size/overlap 以中文字符计；overlap 必须小于 size。最后一块不足
    size//3 时并入前一块，避免产生碎渣块。
    """
    if overlap >= size:
        overlap = size // 5
    if len(text) <= size:
        return [text] if text else []

    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        if end < n:
            # 在窗口后半段里找自然边界（段落 > 句号/换行 > 逗号类）
            window = text[start:end]
            cut = -1
            for sep in ("\n\n", "\n", "。", "！", "？", ";", "；", "，", ","):
                idx = window.rfind(sep, size // 2)
                if idx != -1:
                    cut = idx + len(sep)
                    break
            if cut > 0:
                end = start + cut
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
        start = end - overlap
    # 碎渣合并：最后一块太短时并回前一块
    if len(chunks) >= 2 and len(chunks[-1]) < size // 3:
        chunks[-2] = chunks[-2] + chunks[-1]
        chunks.pop()
    return chunks


# ---------- 入库 / 删除 / 列表 ----------

def _documents_dir(user_id: str) -> Path:
    from .config import config

    safe = re.sub(r"[^\w-]", "_", user_id)
    path = config.data_dir / "documents" / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


def ingest_document(user_id: str, filename: str, data: bytes) -> dict:
    """解析 → 分块 → 落盘 + 入库 + 向量化。返回文档元信息。

    向量化失败（Chroma 不可用/embedding 未就绪）不影响入库：文档仍可读可列，
    只是暂时检索不到——与向量库"写入失败静默"的设计一致。
    """
    from .config import config
    from .userdb import db

    fmt = detect_format(filename)
    if len(data) > config.kb_max_file_mb * 1024 * 1024:
        raise KnowledgeError(f"文件超过 {config.kb_max_file_mb}MB 上限")
    text = parse_document(fmt, data)
    chunks = chunk_text(text, config.kb_chunk_size, config.kb_chunk_overlap)
    if not chunks:
        raise KnowledgeError("文档分块结果为空")

    ts = datetime.now().isoformat(timespec="seconds")
    with db._lock:
        count = db.conn.execute(
            "SELECT COUNT(*) FROM kb_documents WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
        if count >= config.kb_max_documents:
            raise KnowledgeError(f"知识库最多存 {config.kb_max_documents} 份文档，先删掉一些")
        cur = db.conn.execute(
            "INSERT INTO kb_documents (user_id, filename, stored_path, format, size_bytes, chunk_count, ts) "
            "VALUES (?, ?, '', ?, ?, ?, ?)",
            (user_id, filename, fmt, len(data), len(chunks), ts),
        )
        doc_id = cur.lastrowid
        stored = _documents_dir(user_id) / f"{doc_id}_{Path(filename).name}"
        db.conn.execute(
            "UPDATE kb_documents SET stored_path = ? WHERE id = ?", (str(stored), doc_id)
        )
        chunk_ids: list[int] = []
        for seq, piece in enumerate(chunks):
            cur = db.conn.execute(
                "INSERT INTO kb_chunks (user_id, doc_id, seq, text, ts) VALUES (?, ?, ?, ?, ?)",
                (user_id, doc_id, seq, piece, ts),
            )
            chunk_ids.append(cur.lastrowid)
        db.conn.commit()
    try:
        stored.write_bytes(data)
    except Exception:
        logger.warning("[知识库] 原文落盘失败：{}", stored)

    indexed = 0
    try:
        from .memory import vector_store

        for chunk_id, piece in zip(chunk_ids, chunks):
            if vector_store.add(user_id, "kb", chunk_id, piece,
                                extra={"doc_id": doc_id, "filename": filename}):
                indexed += 1
    except Exception:
        logger.warning("[知识库] 向量化失败（文档已入库，暂不可检索）：doc_id={}", doc_id)
    logger.info(
        "[知识库] 文档入库：{}（{} 块，向量化 {}/{}）", filename, len(chunks), indexed, len(chunks)
    )
    return {
        "id": doc_id,
        "filename": filename,
        "format": fmt,
        "size_bytes": len(data),
        "chunk_count": len(chunks),
        "indexed": indexed,
        "ts": ts,
    }


def list_documents(user_id: str) -> list[dict]:
    from .userdb import db

    with db._lock:
        rows = db.conn.execute(
            "SELECT id, filename, format, size_bytes, chunk_count, ts "
            "FROM kb_documents WHERE user_id = ? ORDER BY id DESC",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_document(user_id: str, doc_id: int) -> dict | None:
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT id, filename, format, size_bytes, chunk_count, ts, stored_path "
            "FROM kb_documents WHERE user_id = ? AND id = ?",
            (user_id, doc_id),
        ).fetchone()
    return dict(row) if row else None


def delete_document(user_id: str, doc_id: int) -> bool:
    """删除文档：分块向量 + 分块行 + 文档行 + 落盘原文。文档不存在返回 False。"""
    from .userdb import db

    doc = get_document(user_id, doc_id)
    if doc is None:
        return False
    with db._lock:
        rows = db.conn.execute(
            "SELECT id FROM kb_chunks WHERE user_id = ? AND doc_id = ?", (user_id, doc_id)
        ).fetchall()
        chunk_ids = [row["id"] for row in rows]
        db.conn.execute(
            "DELETE FROM kb_chunks WHERE user_id = ? AND doc_id = ?", (user_id, doc_id)
        )
        db.conn.execute(
            "DELETE FROM kb_documents WHERE user_id = ? AND id = ?", (user_id, doc_id)
        )
        db.conn.commit()
    try:
        from .memory import vector_store

        for chunk_id in chunk_ids:
            vector_store.delete(user_id, "kb", chunk_id)
    except Exception:
        logger.warning("[知识库] 向量删除失败：doc_id={}", doc_id)
    try:
        stored = Path(doc.get("stored_path") or "")
        if stored.is_file():
            stored.unlink()
    except Exception:
        logger.warning("[知识库] 原文删除失败：{}", doc.get("stored_path"))
    logger.info("[知识库] 文档已删除：{}（{} 块）", doc["filename"], len(chunk_ids))
    return True


def clear_user_documents(user_id: str) -> int:
    """清空某用户全部知识库文档（供彻底重置调用），返回删除份数。"""
    docs = list_documents(user_id)
    for doc in docs:
        delete_document(user_id, doc["id"])
    return len(docs)


# ---------- 检索 ----------

def recall_knowledge(user_id: str, query: str, top_k: int | None = None) -> list[dict]:
    """语义检索知识库分块，按距离阈值门控。

    返回 [{"text", "filename", "doc_id", "distance"}]，按相关度升序（最相关在前）。
    知识库为空 / 向量库不可用 / 全超阈值 → 返回 []（调用方按无知识注入继续）。
    """
    from .config import config

    if not config.kb_enabled or not query or not query.strip():
        return []
    top_k = top_k or config.kb_recall_top_k
    started = time.monotonic()
    try:
        from .memory import vector_store

        hits = vector_store.search(user_id, query, top_k=top_k * 2, kind="kb")
    except Exception:
        logger.warning("[知识库] 检索失败：{}", query[:30])
        return []
    results: list[dict] = []
    for hit in hits:
        if hit.distance > config.kb_recall_max_distance:
            continue
        results.append({
            "text": hit.text,
            "filename": str(hit.meta.get("filename", "")),
            "doc_id": int(hit.meta.get("doc_id", 0) or 0),
            "distance": hit.distance,
        })
        if len(results) >= top_k:
            break
    if results:
        logger.info(
            "[知识库] 命中 {} 段（{}，耗时 {:.2f}s）：{}",
            len(results), ", ".join(r["filename"] for r in results),
            time.monotonic() - started, query[:30],
        )
    return results
