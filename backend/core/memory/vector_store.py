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
import time
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


def _collection(kind: str, _allow_during_rebuild: bool = False):
    """获取/创建 kind 对应 collection（惰性）。

    `_allow_during_rebuild`：仅供 migration 重灌（add 的 _allow_during_rebuild=True
    放行链路）内部使用。rebuild_all 清空 _collections 缓存后置 _rebuilding=True，
    若此处仍无条件拦截，重灌的每条 add 都会因缓存 miss 撞上本闸门返回 None、
    在 add() 中 `col is None` 直接失败——即使 add() 内层闸门已放行也会在
    第二道闸门被拒（P1 自锁只修一半）。重建重灌是写方自身，删除阶段已结束，
    此时放行 get_or_create 是安全的。外部调用一律不传（默认 False，重建期间被拦）。
    """
    if kind not in _KINDS:
        kind = "lm"
    with _lock:
        if kind in _collections:
            return _collections[kind]
        # 重建期间暂停建库：rebuild_all 正在删除全部 collection，此时 get_or_create
        # 新建的同名 collection 会被重建流程一并删掉（竞态窗口）。无锁原子读标志，
        # 避免与 rebuild_all 的锁序（_rebuild_lock→_lock）倒置。
        if _rebuilding and not _allow_during_rebuild:
            return None
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


# 最近一次检测到「collection 维度与当前 embedding 不一致」的时间（monotonic 秒）。
# 由 _is_dim_mismatch 置位，rebuild_all 成功重建后清零；经 stats() 暴露给 /api/meta，
# 让「语义检索曾静默降级」可被 UI 感知，而非仅藏在日志里。
_last_dim_mismatch_ts = 0.0

# 哈希降级期跳过写入的限频日志（每 60s 最多一条，避免每条记忆都刷屏）
_last_hash_skip_log = 0.0


def _embedding_ready_for_write() -> bool:
    """当前 embedding 是否适合写向量库。

    哈希回退向量（768 维）会在首次写入时把 Chroma collection 维度锁死；
    模型就绪后（1024 维）所有 upsert/query 都将因维度不符被跳过/报错，
    语义检索静默死亡。因此「模型尚未加载」的过渡期一律不写向量——
    数据仍在 SQLite，模型就绪后由 migration/回填任务补写。

    例外：MEMORY_EMBED_FORCE=1（调试用，永久哈希模式）时允许写入——
    该模式下维度恒为 768，不会出现混写。
    """
    from . import embedding as emb

    if emb.is_loaded():
        return True
    try:
        from ..config import config

        if config.memory_embed_force:
            return True
    except Exception:
        pass
    return False


def _is_dim_mismatch(exc: Exception) -> bool:
    """判断是否 chroma 的「collection 期望维度 vs 实际维度」不匹配错误。

    collection 底层维度在首次写入时被 Chroma 锁定、无法更改；当 upsert 以其他维度
    （如 embedding 模型未就绪时走 768 维哈希回退）去写已锁 1024 维的 collection，
    chroma 抛 InvalidArgumentError，消息含 "expecting embedding with dimension of N,
    got M"。识别它 → 明确告警 + 记录 mismatch 时间（供 stats 暴露）+ 自动调度重建，
    而不是当通用"写入失败"噪音吞掉。
    """
    global _last_dim_mismatch_ts
    text = str(exc)
    if "expecting embedding with dimension" not in text:
        return False
    # 归因到真正的维度 mismatch（模型未就绪哈希回退 / 模型已切换）
    _last_dim_mismatch_ts = time.monotonic()
    return True


# ---------------------------------------------------------------------------
# 自动重建：检测到维度 mismatch 后，删除全部 collection 并从 SQLite 重灌。
# 防止「旧维度锁库 → 新维度写入全被跳过 → 语义检索静默失效」的死亡螺旋。
# ---------------------------------------------------------------------------
_rebuild_lock = threading.Lock()
_rebuilding = False


def _schedule_rebuild(reason: str) -> None:
    """后台线程触发一次全量重建（并发调用只触发一次；重建中再触发排队一条日志）。"""
    with _rebuild_lock:
        if _rebuilding:
            logger.info("[向量] 重建已在进行中，跳过重复调度（原因：{}）", reason[:80])
            return

    def _run() -> None:
        try:
            rebuild_all(reason)
        except Exception:
            logger.exception("[向量] 自动重建执行失败（可手动清空 data/chroma 后重启兜底）")

    threading.Thread(target=_run, daemon=True, name="vec-auto-rebuild").start()


def rebuild_all(reason: str = "") -> int:
    """删除全部向量 collection，并从 SQLite 源表全量重灌（走 migration）。

    只在 embedding 真模型已加载时执行——哈希模式重建只会把库再锁回 768 维。
    返回重灌条数（不可执行时返回 -1）。

    并发安全：删除期间置 _rebuilding 标志，add()/_collection() 检查该标志暂停
    写入/建库，避免「并发 add get_or_create 新 collection 后又被重建删除」的
    竞态窗口导致刚写入的索引丢失。
    """
    from . import embedding as emb

    if not enabled():
        logger.warning("[向量] Chroma 不可用，跳过重建")
        return -1
    if not emb.is_loaded():
        logger.warning(
            "[向量] embedding 模型未就绪，暂不自动重建（模型就绪后首次写入会再次触发）；原因：{}",
            reason[:80],
        )
        return -1

    global _rebuilding, _last_dim_mismatch_ts
    # 置重建标志：删除→重灌期间暂停 add/search 的写路径（数据仍在 SQLite，
    # 重建的 migrate 会全量补回，不丢数据）
    with _rebuild_lock:
        if _rebuilding:
            logger.info("[向量] 重建已在进行中，跳过（原因：{}）", reason[:80])
            return -1
        _rebuilding = True
    try:
        logger.warning("[向量] 检测到维度不一致，开始自动重建向量库（原因：{}）", reason[:120])
        client = _client_instance()
        if client is None:
            return -1
        # 1) 丢弃 collection 对象缓存（后续调用会 get_or_create 新建）
        with _lock:
            names = list(_collections.keys())
            _collections.clear()
        # 2) 逐 kind 删除底层 collection（串行化，防底层 SQLite 写冲突）
        removed = 0
        with _CHROMA_LOCK:
            for kind in sorted(set(_KINDS) | set(names)):
                try:
                    col = client.get_or_create_collection(
                        name=f"tzt_{kind}", embedding_function=_ef(), metadata={"hnsw:space": "cosine"}
                    )
                    removed += col.count()
                    client.delete_collection(f"tzt_{kind}")
                except Exception:
                    # collection 不存在（该 kind 从未写入）等情形：跳过即可
                    continue
        _last_dim_mismatch_ts = 0.0
        logger.info("[向量] 已删除 {} 条旧向量，开始从 SQLite 重灌", removed)
        # 3) 从 SQLite 源表全量重灌（复用 migration 的断点/幂等逻辑）
        from .migration import migrate

        stats = migrate()
        migrated = sum(v for v in stats.values() if isinstance(v, int))
        logger.info("[向量] 自动重建完成：重灌 {} 条（明细：{}）", migrated, stats)
        return migrated
    finally:
        with _rebuild_lock:
            _rebuilding = False


def add(
    user_id: str,
    kind: str,
    record_id: int,
    text: str,
    extra: dict | None = None,
    _allow_during_rebuild: bool = False,
) -> bool:
    """写入/更新一条向量。成功返回 True，失败返回 False（不阻塞）。

    `_allow_during_rebuild`：仅供 migration 重灌时内部使用。rebuild_all 置
    _rebuilding=True 后调用 migrate() 从 SQLite 全量重灌——若走 add() 的
    「重建期间暂停写入」闸门，每一条都会被拒，导致清库后重灌 0 条、
    语义检索静默失效（P1 自锁）。重建的重灌正是写方自身，绝不会被重建流程
    误删，故放行。外部业务写入一律不传此参数（默认 False，重建期间被拦截）。
    """
    global _last_hash_skip_log
    if not text or not text.strip():
        return False
    # 维度锁防线：embedding 模型未就绪（过渡期哈希回退）时禁止写入——
    # 否则首笔 768 维哈希向量会把 collection 维度锁死，模型就绪后所有
    # 1024 维 upsert/search 全部失败，语义检索静默死亡。数据仍在 SQLite，
    # 模型就绪后由 migration 补写。
    if not _embedding_ready_for_write():
        now = time.monotonic()
        if now - _last_hash_skip_log > 60:
            _last_hash_skip_log = now
            logger.info(
                "[向量] embedding 模型未就绪，暂缓向量写入（SQLite 已落库，"
                "模型就绪后自动回填）；kind={}", kind,
            )
        return False
    # 重建期间暂停写入：rebuild_all 正在删库重灌，此刻业务写入的向量会被删掉
    # 或与重灌交错。数据仍在 SQLite，重建的 migrate 会全量补回。重建自身的
    # 重灌写经 _allow_during_rebuild=True 放行（见函数 docstring）。
    if _rebuilding and not _allow_during_rebuild:
        return False
    try:
        # _allow_during_rebuild=True 时同步放行 _collection() 的第二道闸门
        # （缓存 miss 后若仍按 _rebuilding 拦截，重灌写一条都进不去）
        col = _collection(kind, _allow_during_rebuild=_allow_during_rebuild)
        if col is None:
            return False
        kid = _kid(user_id, kind, record_id)
        meta = {"user_id": user_id, "kind": kind, "record_id": record_id}
        if extra:
            meta.update(extra)
        # upsert：id 相同覆盖（保证索引与源表一致）。
        # 若 embedding 维度与 collection 已锁定维度不一致（历史哈希抢写、
        # 或模型切换），chroma 抛 InvalidArgumentError；这里单独识别并
        # 自动调度全量重建（从 SQLite 重灌），不再要求用户手动清 data/chroma。
        # 整段写入加 _CHROMA_LOCK，串行化对 PersistentClient 的写，避免并发 upsert
        # （如 pipeline 的 _vectorize_memory_async 用 asyncio.gather 并发写两条长期
        # 记忆）撞 "database is locked"。
        try:
            with _CHROMA_LOCK:
                col.upsert(ids=[kid], documents=[text], metadatas=[meta])
        except Exception as e:
            if _is_dim_mismatch(e):
                logger.warning(
                    "[向量] collection {} 维度与当前 embedding 不一致，已调度自动重建；"
                    "本次写入 {} 由重建流程从 SQLite 补回",
                    kind, kid,
                )
                _schedule_rebuild(f"add {kind}:{kid} 维度不匹配: {str(e)[:120]}")
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
            except Exception as e:
                if _is_dim_mismatch(e):
                    # 检索侧维度不匹配：当前 embedding 与 collection 锁定维度不一致，
                    # 明确告警并调度自动重建（此前被当通用失败吞掉，检索静默返回空）
                    logger.warning(
                        "[向量] 检索维度不匹配（kind={}，当前 embedding 与库内维度不一致），"
                        "已调度自动重建；本次检索退化为 TF-IDF", k,
                    )
                    _schedule_rebuild(f"search {k} 维度不匹配: {str(e)[:120]}")
                    continue
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


def clear_user(user_id: str) -> int:
    """Delete one persona/user namespace from every vector collection."""
    if not enabled() or not user_id:
        return 0
    removed = 0
    for kind in sorted(_KINDS):
        col = _collection(kind)
        if col is None:
            continue
        try:
            with _CHROMA_LOCK:
                got = col.get(where={"user_id": user_id})
                ids = list(got.get("ids") or [])
                if ids:
                    col.delete(ids=ids)
                    removed += len(ids)
        except Exception:
            logger.warning("[向量] 清理用户失败：{}:{}", user_id, kind)
    return removed


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


def delete_many(user_id: str, kind: str, record_ids: list[int]) -> int:
    """批量删除同一用户同一 kind 的多条向量，返回成功删除的条数（尽力而为）。

    供维护任务（如 clean_old_long_memory）与 SQLite 删除保持同步，
    避免向量库残留孤儿条目。单次 chroma 调用完成，比逐条 delete 快得多。
    """
    if not record_ids:
        return 0
    try:
        col = _collection(kind)
        if col is None:
            return 0
        ids = [_kid(user_id, kind, rid) for rid in record_ids]
        with _CHROMA_LOCK:
            col.delete(ids=ids)
        return len(ids)
    except Exception:
        logger.warning("[向量] 批量删除失败：{}:{} 共 {} 条", user_id, kind, len(record_ids))
        return 0


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
    result: dict = {"enabled": True, **out}
    # 暴露最近一次维度 mismatch（若存在）——语义检索曾静默降级的可观测信号
    if _last_dim_mismatch_ts:
        result["dim_mismatch"] = True
    return result
