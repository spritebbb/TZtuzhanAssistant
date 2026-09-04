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
_KINDS = {"lm", "facts", "triples", "profile", "topic", "diary", "summary", "sticker", "mem", "kb"}

_lock = threading.RLock()
# 独立锁：串行化对 Chroma PersistentClient（底层 SQLite）的实际读写调用。
# 之所以不与 _lock 复用：_collection() 内部已持 _lock 返回 collection 对象，
# 若把 Chroma 调用也放进 _lock 临界区会与 _collection() 的取锁区间重叠，且
# PersistentClient 的底层 SQLite 在 WAL 模式下仍是单写者，多线程并发 upsert/query
# 会抛 "database is locked" 甚至损坏索引。这里用独立 RLock 包住 col.* 调用，
# 既能串行化写入，又不会和「取 collection 对象」的锁区间嵌套死锁（先取对象、再取本锁）。
_CHROMA_LOCK = threading.RLock()
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
        def __init__(self):
            pass

        @staticmethod
        def name() -> str:
            return "tztuzhan-local"

        def get_config(self) -> dict:
            return {}

        @staticmethod
        def build_from_config(config: dict):
            return _LocalEF()

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


# 本模块标志：最近一次写入是否因「collection 维度已锁定且与当前维度不符」被跳过。
# 由 vector_store.migrate / 外部检测：为 True 时说明 embedding 模型曾以不同维度
# （哈希 768）抢写过，或模型切换过；触发一次全量重建（migrate 重扫）即可对齐。
_skip_dim_mismatch = False


def _is_dim_mismatch(exc: Exception) -> bool:
    """判断是否 chroma 的「collection 期望维度 vs 实际维度」不匹配错误。

    collection 底层维度在首次写入时被 Chroma 锁定、无法更改；当 upsert 以其他维度
    （如 embedding 模型未就绪时走 768 维哈希回退）去写已锁 1024 维的 collection，
    chroma 抛 InvalidArgumentError，消息含 "expecting embedding with dimension of N,
    got M"。识别它 → 明确告警 + 全局置标志，而不是当通用"写入失败"噪音吞掉。
    """
    global _skip_dim_mismatch
    text = str(exc)
    if "expecting embedding with dimension" not in text:
        return False
    # 归因到真正的维度 mismatch（模型未就绪哈希回退 / 模型已切换）
    _skip_dim_mismatch = True
    return True


def add(user_id: str, kind: str, record_id: int, text: str, extra: dict | None = None) -> bool:
    """写入/更新一条向量。成功返回 True，失败返回 False（不阻塞）。"""
    if not text or not text.strip():
        return False
    try:
        col = _collection(kind)
        if col is None:
            return False
        kid = _kid(user_id, kind, record_id)
        meta = {"user_id": user_id, "kind": kind, "record_id": record_id}
        if extra:
            meta.update(extra)
        # upsert：id 相同覆盖（保证索引与源表一致）。
        # 若 embedding 维度与 collection 已锁定维度不一致（启动竞态期模型未就绪走
        # 768 维哈希、或模型切换），chroma 抛 InvalidArgumentError；这里单独识别并
        # 明确告警，避免与其它失败混成一条无信息的「写入失败」日志。
        # 整段写入加 _CHROMA_LOCK，串行化对 PersistentClient 的写，避免并发 upsert
        # （如 pipeline 的 _vectorize_memory_async 用 asyncio.gather 并发写两条长期
        # 记忆）撞 "database is locked"。
        try:
            with _CHROMA_LOCK:
                col.upsert(ids=[kid], documents=[text], metadatas=[meta])
        except Exception as e:
            if _is_dim_mismatch(e):
                logger.warning(
                    "[向量] collection {} 维度与当前 embedding 不一致，跳过写入 {}；"
                    "若 embedding 模型刚切换过，请清空 data/chroma 后重启以重建索引",
                    kind, kid,
                )
                return False
            raise
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
                with _CHROMA_LOCK:
                    got = col.query(
                        query_texts=[query],
                        n_results=min(top_k * 6, 100),
                        where={"user_id": user_id},
                    )
            except Exception:
                # where 过滤缺失 user_id 时（个别 chroma 版本对空 where 报错）兜底重查；
                # 同样加 _CHROMA_LOCK 串行化。
                with _CHROMA_LOCK:
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
            with _CHROMA_LOCK:
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
                # upsert 同名新 id 后删除旧 id（Chroma 无 rename，只能删+写）；
                # 整段加 _CHROMA_LOCK，保证 get→upsert→delete 这三条调用不被其它
                # 并发写（add/delete）交错，避免底层 SQLite 写冲突。
                with _CHROMA_LOCK:
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
        with _CHROMA_LOCK:
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
