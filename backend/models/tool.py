# -*- coding: utf-8 -*-
"""工具调用相关数据模型。"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ToolSpec(BaseModel):
    """工具描述（供 LLM 与 MCP 使用）。"""

    name: str
    description: str
    input_schema: dict = Field(default_factory=dict, description="JSON Schema")
    # ---- Agent 协议增强 ----
    category: str = "read"           # read/write/run/external
    danger_level: str = "normal"     # info/normal/high/critical
    needs_confirm: bool = False      # 是否需弹确认
    max_output_chars: int = 4000     # 结果截断上限


class ToolResult(BaseModel):
    """工具执行结果。"""

    ok: bool = True
    tool: str
    output: str = ""
    error: Optional[str] = None
    meta: dict = Field(default_factory=dict)
    # ---- Agent 支持 ----
    confirmed: str = "auto"      # auto/allow/deny/blocked/timeout
    elapsed_ms: int = 0          # 执行耗时（毫秒）
