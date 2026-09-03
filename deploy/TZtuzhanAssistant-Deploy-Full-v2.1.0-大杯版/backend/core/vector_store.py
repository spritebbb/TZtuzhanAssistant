"""兼容薄壳：转发到 memory/vector_store（v2 重构）。

保持旧版接口签名，调用方（pipeline / daily / tools）零改动：
- index(user_id, record_id, text, kind='lm') → bool
- search(user_id, query, top_k=5, kind=None) → [(record_id, distance)]
- enabled() / backfill() / embed() / indexed_count()
"""
import asyncio


def _new():
    from .memory import vector_store

    return vector_store


def enabled() -> bool:
    return _new().enabled()


def index(user_id: str, record_id: int, text: str, kind: str = "lm") -> bool:
    """建向量索引（转发到 v2 add，参数顺序适配）。"""
    return _new().add(user_id, kind, record_id, text)


def delete(user_id: str, kind: str, record_id: int) -> bool:
    """删除一条向量（转发到 v2 delete）。"""
    return _new().delete(user_id, kind, record_id)


def search(
    user_id: str, query: str, top_k: int = 5, kind: str | None = None
) -> list[tuple[int, float]]:
    """向量检索，返回 [(record_id, distance)]（与旧版一致的元组格式）。"""
    hits = _new().search(user_id, query, top_k, kind)
    return [(h.record_id, h.distance) for h in hits]


def indexed_count() -> int:
    return _new().count()


def count(kind: str | None = None) -> int:
    return _new().count(kind)


def backfill(user_id: str = "", limit: int = 1000) -> int:
    """存量回填（v2 由 migration 承担，这里保留接口并转发）。"""
    try:
        from .memory.migration import migrate

        return sum(v for k, v in migrate().items() if isinstance(v, int) and k != "skipped")
    except Exception:
        return 0


def embed(text: str) -> list[float] | None:
    """文本 → 向量（本地 BGE embedding）。"""
    from .memory.embedding import embed as _embed

    return _embed(text)
