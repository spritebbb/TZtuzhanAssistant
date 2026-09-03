"""兼容薄壳：转发到 memory/triple_memory（v2 重构）。"""
from .memory.triple_memory import (  # noqa: F401
    extract_triples,
    format_triples,
    query_triples,
    save_triples,
)
