"""Chroma 向量库封装：本地持久化，替代原 sqlite-vec 方案。

设计：
- PersistentClient 持久化到 data/chroma/（单目录多 collection 按 kind 分区）
- 自定义 EmbeddingFunction 桥接 memory.embedding（BGE-M3 本地推理）
- 写入失败静默（返回 False），检索失败返回 []，绝不阻塞对话主流程
- 迁移路径：原 bot.db 中 long_memory / facts 等经 migration.py 灌入
- Chroma 不可用时（未安装/损坏）自动降级为纯 SQLite + TF-IDF（原逻辑）

kind 分区：lm（长记忆原文）、facts（事实）、triples（五元组）、profile（画像）、
topic（话题）、diary（日记）、summary（压缩摘要）、sticker（表情描述）。
"""
import threading
from dataclasses import dataclass, field

from ..log import logger

# kind 白名单（防止拼写错误创建一堆空 collection）
_KINDS = {"lm", "facts", "triples", "profile", "topic", "diary", "summary", "sticker", "mem"}

_lock = threading.RLock()
_client = None
_collections: dict[str, object] = {}


@dataclass
class SearchHit:
    record_id: int
    distance: float
    text: str
    meta: dict = field(default_factory=dict)


def _ef():
    """返回 chromadb EmbeddingFunction，桥接本地 BGE embedding。"""
    from chromadb import EmbeddingFunction

    from . import embedding as emb

    class _LocalEF(EmbeddingFunction):
        def __call__(self, input):  # noqa: A002
            if isinstance(input, str):
                texts = [input]
            else:
                texts = list(input)
            vecs = emb.embed_batch(texts)
            if vecs is None:
                return []
            return [list(v) for v in vecs]

    return _LocalEF()


def _client_instance():
    global _client
    with _lock:
        if _client is None:
            import chromadb

            try:
                from ..config import config

                path = str(config.data_dir / "chroma")
                _client = chromadb.PersistentClient(path=path)
                logger.info("[向量] Chroma 已初始化：{}", path)
            except Exception as e:
                logger.warning("[向量] Chroma 初始化失败，向量检索降级为 TF-IDF：{}", str(e)[:120])
                _client = None
    return _client


def enabled() -> bool:
    """Chroma 是否可用。"""
    try:
        return _client_instance() is not None
    except Exception:
        return False


def _collection(kind: str):
    """获取/创建 kind 对应 collection（惰性）。"""
    if kind not in _KINDS:
        kind = "lm"
    with _lock:
        if kind in _collections:
            return _collections[kind]
        client = _client_instance()
        if client is None:
            return None
        try:
            col = client.get_or_create_collection(
                name=f"tzt_{kind}",
                embedding_function=_ef(),
                metadata={"hnsw:space": "cosine"},
            )
            _collections[kind] = col
            return col
        except Exception as e:
            logger.warning("[向量] collection {} 获取失败：{}", kind, str(e)[:120])
            return None


def _kid(user_id: str, kind: str, record_id: int) -> str:
    return f"{user_id}|{kind}|{record_id}"


def add(user_id: str, kind: str, record_id: int, text: str, extra: dict | None = None) -> bool:
    """写入/更新一条向量。成功返回 True，失败返回 False（不阻塞）。"""
    if not text or not text.strip():
        return False
    try:
        col = _collection(kind)
        if col is None:
            return False
        # 维度一致性：模型切换（bge-m3 1024 维 ↔ 哈希 768 维）后若往同一
        # collection 混写不同维度，查询会整体报错。发现不一致时跳过写入并提示，
        # 避免污染已有数据（需要重建时清空 data/chroma 目录）。
        try:
            from . import embedding as emb

            cur_dim = emb.dim()
            col_meta = col.metadata or {}
            meta_dim = col_meta.get("dim")
            if meta_dim is not None and int(meta_dim) != cur_dim:
                logger.warning(
                    "[向量] collection {} 维度 {} 与当前模型维度 {} 不一致，跳过写入；"
                    "如模型已切换，请清空 data/chroma 重建索引", kind, meta_dim, cur_dim
                )
                return False
            if meta_dim is None:
                col.modify(metadata={**col_meta, "dim": cur_dim})
        except Exception:
            pass
        kid = _kid(user_id, kind, record_id)
        meta = {"user_id": user_id, "kind": kind, "record_id": record_id}
        if extra:
            meta.update(extra)
        # upsert：id 相同覆盖（保证索引与源表一致）
        col.upsert(ids=[kid], documents=[text], metadatas=[meta])
        return True
    except Exception:
        logger.warning("[向量] 写入失败：{}:{}:{}", user_id, kind, record_id)
        return False


def search(
    user_id: str,
    query: str,
    top_k: int = 5,
    kind: str | None = None,
) -> list[SearchHit]:
    """向量检索，按距离升序（越小越相似）。kind=None 时跨全部分区检索。"""
    if not enabled() or not query or not query.strip():
        return []
    try:
        kinds = [kind] if kind else sorted(_KINDS)
        results: list[SearchHit] = []
        for k in kinds:
            col = _collection(k)
            if col is None:
                continue
            try:
                got = col.query(
                    query_texts=[query],
                    n_results=min(top_k * 6, 100),
                    where={"user_id": user_id},
                )
            except Exception:
                got = col.query(query_texts=[query], n_results=min(top_k * 6, 100))
            # Chroma 返回的 ids/distances/documents/metadatas 是嵌套列表
            # [[id1,id2,...]], [[d1,d2,...]], [[doc1,doc2,...]], [[meta1,meta2,...]]
            ids_raw = got.get("ids") or []
            dists_raw = got.get("distances") or []
            docs_raw = got.get("documents") or []
            metas_raw = got.get("metadatas") or []
            items = []
            if ids_raw and ids_raw[0]:
                # 取第一个查询文本的结果（query_texts=[query] 只有一个）。
                # 注意不能用 zip 对齐：Chroma 的 distances/documents/metadatas
                # 可能缺失（cosine 距离可省略、metadata 可未返回），zip 会按
                # 最短序列静默截断，导致本可返回的结果被丢弃，甚至因 meta=None
                # 抛 TypeError 被外层 except 吞掉、整次检索静默返回空。
                ids0 = ids_raw[0]
                dists0 = dists_raw[0] if dists_raw and dists_raw[0] else [None] * len(ids0)
                docs0 = docs_raw[0] if docs_raw and docs_raw[0] else [None] * len(ids0)
                metas0 = metas_raw[0] if metas_raw and metas_raw[0] else [None] * len(ids0)
                for i, row_id in enumerate(ids0):
                    items.append((
                        row_id,
                        dists0[i] if i < len(dists0) else None,
                        docs0[i] if i < len(docs0) else None,
                        metas0[i] if i < len(metas0) else None,
                    ))
            for row_id, dist, doc, meta in items:
                if not str(row_id).startswith(f"{user_id}|"):
                    continue
                parts = str(row_id).split("|")
                rid = int(parts[2]) if len(parts) >= 3 else -1
                results.append(
                    SearchHit(record_id=rid, distance=float(dist) if dist is not None else 0.0, text=doc or "", meta=meta or {})
                )
        results.sort(key=lambda h: h.distance)
        return results[:top_k]
    except Exception:
        logger.warning("[向量] 检索失败：{}", query[:30])
        return []


def migrate_user_id(old: str, new: str) -> int:
    """把旧用户身份（old）名下的向量改挂到新身份（new），返回迁移条数。幂等。

    用户身份统一（session_current → assistant-main）后，SQLite 侧记录已改挂
    assistant-main，但向量 id 仍是「old|kind|record_id」三元组且 metadata.user_id=old，
    会导致 where={"user_id": new} 检索不到旧记忆。这里遍历各 collection，把 old 前缀
    的向量 id 与 metadata 同步改为 new（record_id 保持不变，与 SQLite 对齐）。
    """
    if not enabled() or old == new:
        return 0
    moved = 0
    for kind in sorted(_KINDS):
        col = _collection(kind)
        if col is None:
            continue
        try:
            got = col.get(where={"user_id": old})
        except Exception:
            continue
        ids = got.get("ids") or []
        if not ids:
            continue
        docs = got.get("documents") or [None] * len(ids)
        metas = got.get("metadatas") or [None] * len(ids)
        for i, oid in enumerate(ids):
            if not str(oid).startswith(f"{old}|"):
                continue
            nid = f"{new}|" + str(oid).split("|", 1)[1]
            meta = dict(metas[i]) if metas[i] else {}
            meta["user_id"] = new
            try:
                # upsert 同名新 id 后删除旧 id（Chroma 无 rename，只能删+写）
                col.upsert(ids=[nid], documents=[docs[i]], metadatas=[meta])
                col.delete(ids=[oid])
                moved += 1
            except Exception:
                continue
    if moved:
        logger.info("[向量] 用户身份迁移 {} → {}，迁移 {} 条向量", old, new, moved)
    return moved


def delete(user_id: str, kind: str, record_id: int) -> bool:
    """删除一条向量。"""
    try:
        col = _collection(kind)
        if col is None:
            return False
        col.delete(ids=[_kid(user_id, kind, record_id)])
        return True
    except Exception:
        return False


def count(kind: str | None = None) -> int:
    """当前向量总数（kind 可选）。"""
    if not enabled():
        return 0
    try:
        total = 0
        kinds = [kind] if kind else sorted(_KINDS)
        for k in kinds:
            col = _collection(k)
            if col is not None:
                total += col.count()
        return total
    except Exception:
        return 0


def clear(kind: str | None = None) -> int:
    """清空向量库（kind 可选；None 清全部）。返回删除条数。"""
    if not enabled():
        return 0
    try:
        total = 0
        kinds = [kind] if kind else sorted(_KINDS)
        for k in kinds:
            col = _collection(k)
            if col is not None:
                total += col.count()
                try:
                    col.delete(where=None)
                except Exception:
                    ids = col.get()["ids"]
                    if ids:
                        col.delete(ids=ids)
        return total
    except Exception:
        return 0


def stats() -> dict:
    """向量库统计（供 /api/meta 展示）。"""
    out: dict[str, int] = {}
    if not enabled():
        return {"enabled": False}
    for k in sorted(_KINDS):
        col = _collection(k)
        if col is not None:
            out[k] = col.count()
    return {"enabled": True, **out}
