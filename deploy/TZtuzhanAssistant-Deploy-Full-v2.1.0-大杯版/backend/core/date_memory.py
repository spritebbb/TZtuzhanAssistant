"""兼容薄壳：转发到 memory/date_memory（v2 重构）。"""
from .memory.date_memory import (  # noqa: F401
    extract_from_message,
    extract_from_transcript,
)
