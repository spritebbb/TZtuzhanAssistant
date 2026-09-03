"""兼容薄壳：转发到 memory/topic_memory（v2 重构）。"""
from .memory.topic_memory import (  # noqa: F401
    build_continuation,
    extract_topic,
    last_topic,
    last_topic_ts,
)
