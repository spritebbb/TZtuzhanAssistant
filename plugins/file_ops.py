# -*- coding: utf-8 -*-
"""工具插件：文件操作（读/写/列表）。"""
from __future__ import annotations

PLUGIN_META = {
    "name": "文件操作",
    "version": "1.0.0",
    "description": "read_file / write_file / list_dir 工具（路径安全白名单）",
    "author": "tuzhan",
}

import os
from pathlib import Path

from backend.tools.base import ToolRegistry
from backend.tools.safety import check_path

# 工作目录（可配置）：相对路径以此为基准解析，最终合法性由 safety.check_path 判定
_WORK_DIR = Path(os.getcwd())


def _resolve_path(path: str) -> Path | None:
    """解析路径（相对路径基于工作目录），并统一走 safety 白名单校验。"""
    try:
        p = (Path(path).expanduser().resolve() if path.startswith("~") or Path(path).is_absolute()
             else (_WORK_DIR / path).resolve())
    except (ValueError, OSError):
        return None
    ok, _ = check_path(p)
    return p if ok else None


async def _read_file(path: str = "") -> str:
    """读取文件内容。"""
    if not path:
        return "（缺少文件路径）"
    p = _resolve_path(path)
    if p is None:
        return "（路径不允许：只能在工作目录下操作）"
    try:
        if not p.exists():
            return f"（文件不存在: {path}）"
        if p.is_dir():
            return f"（{path} 是目录，非文件）"
        try:
            content = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"（{path} 不是文本文件，请改用 read_file_binary）"
        if len(content) > 10000:
            content = content[:10000] + f"\n...（已截断，全文 {len(content)} 字符）"
        return content
    except PermissionError:
        return "（无权限读取）"
    except Exception as e:
        return f"（读取失败：{e}）"


async def _write_file(path: str = "", content: str = "") -> str:
    """写入文件内容。"""
    if not path:
        return "（缺少文件路径）"
    p = _resolve_path(path)
    if p is None:
        return "（路径不允许：只能在工作目录下操作）"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"（已写入 {len(content)} 字符到 {path}）"
    except PermissionError:
        return "（无权限写入）"
    except Exception as e:
        return f"（写入失败：{e}）"


async def _list_dir(path: str = "") -> str:
    """列出目录内容。"""
    p = _resolve_path(path) if path else _WORK_DIR
    if p is None:
        return "（路径不允许）"
    try:
        if not p.exists():
            return f"（目录不存在: {path}）"
        if not p.is_dir():
            return f"（{path} 不是目录）"
        items = []
        for entry in sorted(p.iterdir()):
            suffix = "/" if entry.is_dir() else ""
            items.append(f"{entry.name}{suffix}")
        return "\n".join(items) if items else "（空目录）"
    except PermissionError:
        return "（无权限访问）"
    except Exception as e:
        return f"（列出目录失败：{e}）"


def register(ctx=None) -> None:
    ToolRegistry.register_func(
        name="read_file",
        description="读取文本文件内容（只读，UTF-8）",
        func=_read_file,
        owner="file_ops",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径（相对工作目录或绝对路径）"}
            },
            "required": ["path"],
        },
    )
    ToolRegistry.register_func(
        name="write_file",
        description="写入文本文件（UTF-8）",
        func=_write_file,
        owner="file_ops",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "文件内容"}
            },
            "required": ["path", "content"],
        },
        category="write",
        needs_confirm=True,
    )
    ToolRegistry.register_func(
        name="list_dir",
        description="列出目录内容",
        func=_list_dir,
        owner="file_ops",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径（留空则当前目录）"}
            },
        },
    )