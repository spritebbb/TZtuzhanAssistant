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
import hashlib
import json
import unicodedata
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
        opinion_rows = db.conn.execute(
            "SELECT id FROM knowledge_opinions WHERE user_id=? AND document_id=?",
            (user_id, doc_id),
        ).fetchall()
        opinion_ids = [int(row["id"]) for row in opinion_rows]
        if opinion_ids:
            marks = ",".join("?" for _ in opinion_ids)
            db.conn.execute(
                f"DELETE FROM knowledge_opinion_sources WHERE user_id=? AND opinion_id IN ({marks})",
                [user_id, *opinion_ids],
            )
        db.conn.execute(
            "DELETE FROM knowledge_opinions WHERE user_id=? AND document_id=?",
            (user_id, doc_id),
        )
        activity_rows = db.conn.execute(
            "SELECT id FROM activities WHERE user_id = ? AND document_id = ?",
            (user_id, doc_id),
        ).fetchall()
        from .activities import forget_activity_data

        for activity in activity_rows:
            # 共读观点/共同书摘随源删除，reading_finished 事件作废，不留幽灵回忆
            forget_activity_data(user_id, activity["id"])
            db.conn.execute(
                "DELETE FROM activity_notes WHERE user_id = ? AND activity_id = ?",
                (user_id, activity["id"]),
            )
        db.conn.execute(
            "DELETE FROM activities WHERE user_id = ? AND document_id = ?",
            (user_id, doc_id),
        )
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


# ---------- 有来源的角色观点（P3-02C） ----------

def _normalize_stance(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).strip().split())


def _opinion_hash(stance: str, spans: list[dict]) -> str:
    payload = {
        "stance": _normalize_stance(stance).lower(),
        "spans": [(int(s["chunk_id"]), int(s.get("start", 0)), int(s.get("end", 0))) for s in spans],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def save_opinion(
    user_id: str, document_id: int, stance: str, source_spans: list[dict | int], *,
    origin: str = "user", confidence: float = 1.0,
) -> dict:
    """保存一条角色观点；每个来源必须是该用户该文档的真实知识分块。"""
    from .userdb import db

    stance = _normalize_stance(stance)
    if not stance or len(stance) > 2000:
        raise KnowledgeError("观点内容必须为 1–2000 字")
    if origin not in {"assistant", "user"}:
        raise KnowledgeError("观点来源必须是 assistant 或 user")
    spans: list[dict] = []
    for raw in source_spans or []:
        item = {"chunk_id": int(raw)} if isinstance(raw, int) else dict(raw)
        try:
            spans.append({
                "chunk_id": int(item["chunk_id"]),
                "start": max(0, int(item.get("start", 0))),
                "end": max(0, int(item.get("end", 0))),
            })
        except (KeyError, TypeError, ValueError):
            raise KnowledgeError("来源片段格式无效") from None
    if not spans:
        raise KnowledgeError("观点至少需要一个来源片段")
    # 稳定去重，避免重复来源改变 opinion_hash。
    spans = [dict(zip(("chunk_id", "start", "end"), key)) for key in sorted({
        (item["chunk_id"], item["start"], item["end"]) for item in spans
    })]
    digest = _opinion_hash(stance, spans)
    now = datetime.now().isoformat(timespec="seconds")
    confidence = max(0.0, min(1.0, float(confidence)))

    with db._lock:
        doc = db.conn.execute(
            "SELECT id FROM kb_documents WHERE user_id=? AND id=?", (user_id, int(document_id))
        ).fetchone()
        if doc is None:
            raise KnowledgeError("知识文档不存在")
        source_rows: list[tuple[dict, str]] = []
        for span in spans:
            row = db.conn.execute(
                "SELECT id,text FROM kb_chunks WHERE user_id=? AND doc_id=? AND id=?",
                (user_id, int(document_id), span["chunk_id"]),
            ).fetchone()
            if row is None:
                raise KnowledgeError("来源片段不属于该文档")
            text = str(row["text"])
            end = span["end"] or len(text)
            if span["start"] >= end or end > len(text):
                raise KnowledgeError("来源片段范围越界")
            span["end"] = end
            source_hash = hashlib.sha256(text[span["start"]:end].encode("utf-8")).hexdigest()
            source_rows.append((span, source_hash))
        # end_offset 规范化后重算，保证同一来源的省略 end 与显式末尾同键。
        digest = _opinion_hash(stance, spans)
        existing = db.conn.execute(
            "SELECT id,status,version FROM knowledge_opinions "
            "WHERE user_id=? AND document_id=? AND opinion_hash=?",
            (user_id, int(document_id), digest),
        ).fetchone()
        if existing is not None:
            if existing["status"] != "active":
                db.conn.execute(
                    "UPDATE knowledge_opinions SET status='active',version=version+1,"
                    "confidence=?,updated_at=? WHERE id=? AND user_id=?",
                    (confidence, now, int(existing["id"]), user_id),
                )
                db.conn.commit()
            return get_opinion(user_id, int(existing["id"]))
        cur = db.conn.execute(
            "INSERT INTO knowledge_opinions "
            "(user_id,document_id,stance,opinion_hash,origin,confidence,version,status,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,1,'active',?,?)",
            (user_id, int(document_id), stance, digest, origin, confidence, now, now),
        )
        opinion_id = int(cur.lastrowid)
        for span, source_hash in source_rows:
            db.conn.execute(
                "INSERT INTO knowledge_opinion_sources "
                "(user_id,opinion_id,chunk_id,start_offset,end_offset,source_hash) "
                "VALUES (?,?,?,?,?,?)",
                (user_id, opinion_id, span["chunk_id"], span["start"], span["end"], source_hash),
            )
        db.conn.commit()
    return get_opinion(user_id, opinion_id)


def get_opinion(user_id: str, opinion_id: int) -> dict | None:
    from .userdb import db

    with db._lock:
        row = db.conn.execute(
            "SELECT o.*,d.filename FROM knowledge_opinions o "
            "JOIN kb_documents d ON d.id=o.document_id AND d.user_id=o.user_id "
            "WHERE o.user_id=? AND o.id=?", (user_id, int(opinion_id)),
        ).fetchone()
        if row is None:
            return None
        sources = db.conn.execute(
            "SELECT s.chunk_id,s.start_offset,s.end_offset,s.source_hash,c.text "
            "FROM knowledge_opinion_sources s JOIN kb_chunks c "
            "ON c.id=s.chunk_id AND c.user_id=s.user_id "
            "WHERE s.user_id=? AND s.opinion_id=? ORDER BY s.chunk_id,s.start_offset",
            (user_id, int(opinion_id)),
        ).fetchall()
    valid_sources = []
    for source in sources:
        text = str(source["text"])
        start, end = int(source["start_offset"]), int(source["end_offset"])
        if start < 0 or end > len(text) or start >= end:
            return None
        digest = hashlib.sha256(text[start:end].encode("utf-8")).hexdigest()
        if digest != source["source_hash"]:
            return None
        valid_sources.append({key: source[key] for key in (
            "chunk_id", "start_offset", "end_offset", "source_hash"
        )})
    if not valid_sources:
        return None
    result = dict(row)
    result["source_spans"] = valid_sources
    return result


def list_opinions(user_id: str, document_id: int | None = None, *, active_only: bool = True) -> list[dict]:
    from .userdb import db

    where = ["user_id=?"]
    args: list = [user_id]
    if document_id is not None:
        where.append("document_id=?")
        args.append(int(document_id))
    if active_only:
        where.append("status='active'")
    with db._lock:
        rows = db.conn.execute(
            f"SELECT id FROM knowledge_opinions WHERE {' AND '.join(where)} ORDER BY id DESC",
            args,
        ).fetchall()
    return [opinion for row in rows if (opinion := get_opinion(user_id, int(row["id"]))) is not None]


def revoke_opinion(user_id: str, opinion_id: int) -> bool:
    from .userdb import db

    with db._lock:
        cur = db.conn.execute(
            "UPDATE knowledge_opinions SET status='revoked',version=version+1,updated_at=? "
            "WHERE user_id=? AND id=? AND status='active'",
            (datetime.now().isoformat(timespec="seconds"), user_id, int(opinion_id)),
        )
        db.conn.commit()
    return cur.rowcount == 1


def _terms(text: str) -> set[str]:
    normalized = _normalize_stance(text).lower()
    latin = set(re.findall(r"[a-z0-9_]{2,}", normalized))
    chinese = re.sub(r"[^\u4e00-\u9fff]", "", normalized)
    return latin | {chinese[i:i + 2] for i in range(max(0, len(chinese) - 1))}


def relevant_opinions(user_id: str, query: str, *, limit: int = 2) -> list[dict]:
    """只返回与当前查询有词面交集且来源仍有效的角色观点。"""
    query_terms = _terms(query)
    if not query_terms:
        return []
    scored = []
    for opinion in list_opinions(user_id):
        overlap = len(query_terms & _terms(opinion["stance"]))
        if overlap:
            scored.append((overlap, int(opinion["id"]), opinion))
    scored.sort(key=lambda item: (-item[0], -item[1]))
    return [item[2] for item in scored[:max(0, min(4, int(limit)))]]


# ---------- LLM 观点提炼（体验收口：书架面板「她的观点」一键生成） ----------

_OPINION_EXTRACT_PROMPT = """你是「{persona}」。下面是一份文档的若干片段。
请从这些片段里挑出最多 2 条「她读了之后会形成的观点」——不是摘要，而是她读过并消化后，
愿意在聊天里说出的一句话立场或感受（例如喜欢什么、怀疑什么、对某个说法的判断）。
要求：
- 每条观点必须能在给出的片段里找到直接依据，不得编造文档里没有的内容；
- 用她的口吻写成一句完整的话（30-80 字），不要书名号，不要"我觉得文档说"这类引用腔；
- span 是观点依据所在的片段编号（从 0 开始，对应输入片段的序号）。

只输出 JSON：{{"opinions": [{{"stance": "...", "spans": [0]}}]}}"""


async def extract_opinions(user_id: str, document_id: int) -> list[dict]:
    """LLM 通读文档分块，提炼 0-2 条带来源分块引用的角色观点。

    每条观点经 save_opinion 落库（来源必须是该文档真实分块，hash 去重）。
    LLM 失败 / 无产出 → 返回空列表，不报错。"""
    from .userdb import db

    with db._lock:
        rows = db.conn.execute(
            "SELECT id,text FROM kb_chunks WHERE user_id=? AND doc_id=? ORDER BY seq",
            (user_id, int(document_id)),
        ).fetchall()
        doc = db.conn.execute(
            "SELECT id FROM kb_documents WHERE user_id=? AND id=?",
            (user_id, int(document_id)),
        ).fetchone()
    if doc is None:
        raise KnowledgeError("知识文档不存在")
    if not rows:
        return []

    from .llm import chat
    from .persona_profiles import persona_name_for_user_id

    numbered = "\n".join(f"[{seq}] {row['text']}" for seq, row in enumerate(rows))
    try:
        resp = await chat(
            [
                {"role": "system", "content": _OPINION_EXTRACT_PROMPT.replace(
                    "{persona}", persona_name_for_user_id(user_id))},
                {"role": "user", "content": numbered},
            ],
            temperature=0.3,
            max_tokens=500,
            task="extract",
        )
        start, end = resp.find("{"), resp.rfind("}")
        data = json.loads(resp[start:end + 1]) if 0 <= start < end else {}
    except Exception:
        logger.warning("[知识库] 观点提炼失败：doc {}", document_id)
        return []

    items = data.get("opinions") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    chunk_ids = [int(row["id"]) for row in rows]
    saved: list[dict] = []
    for item in items[:2]:
        if not isinstance(item, dict):
            continue
        stance = str(item.get("stance") or "").strip()
        span_idx = item.get("spans") if isinstance(item.get("spans"), list) else []
        spans = [chunk_ids[int(i)] for i in span_idx
                 if isinstance(i, int) and 0 <= int(i) < len(chunk_ids)]
        if not stance or not spans:
            continue
        try:
            saved.append(save_opinion(user_id, document_id, stance, spans, origin="assistant"))
        except KnowledgeError:
            continue
        if len(saved) >= 2:
            break
    return saved


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
