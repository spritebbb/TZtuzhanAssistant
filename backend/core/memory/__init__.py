"""记忆系统 v2 包入口。

统一导出与旧版 memory.py 兼容的 API（recall / recall_facts / short_term_messages /
compact_context / load_compact_summary / looks_like_recall 等），调用方无需感知内部重构。

新能力：
- Chroma 向量库（本地持久化）+ BGE-M3 本地 embedding
- Mem0 记忆管理器（回溯/冲突解决/重要性/遗忘）
- 更强 LLM 事实抽取（置信度/类别）与冲突调和
"""
from .compress import (
    COMPACT_SECTIONS,
    compact_context,
    load_compact_summary,
    save_compact_summary,
)
from .fact_extractor import extract_profile, extract_triples, reconcile
from .long_term import (
    expand_query,
    looks_like_recall,
    recall,
    recall_facts,
)

# 兼容旧版私有函数名（tools/builtin/memory.py 等仍引用）
from .long_term import _with_expansion as _recall_with_expansion
from .long_term import _with_expansion as _facts_with_expansion
from .memory_manager import manager
from .short_term import short_term_messages

__all__ = [
    "recall",
    "recall_facts",
    "short_term_messages",
    "looks_like_recall",
    "expand_query",
    "compact_context",
    "load_compact_summary",
    "save_compact_summary",
    "COMPACT_SECTIONS",
    "extract_triples",
    "extract_profile",
    "reconcile",
    "manager",
    "_recall_with_expansion",
    "_facts_with_expansion",
]
