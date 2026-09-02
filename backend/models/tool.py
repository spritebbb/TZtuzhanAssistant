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


class ToolCall(BaseModel):
    """一次工具调用请求。"""

    tool: str
    args: dict = Field(default_factory=dict)


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


class McpServerInfo(BaseModel):
    """已注册的外部 MCP 服务器信息。"""

    name: str
    transport: str  # stdio / sse / streamable-http
    command: Optional[str] = None
    args: list = Field(default_factory=list)
    url: Optional[str] = None
    tools_count: int = 0
    status: str = "connected"  # connected / error / not_connected
