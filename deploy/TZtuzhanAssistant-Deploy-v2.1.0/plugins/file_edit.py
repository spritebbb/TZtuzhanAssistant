# -*- coding: utf-8 -*-
"""工具插件：文件编辑（编辑文本文件中的特定内容，对标 Harness 的 edit）。

安全限制：相对路径基于工作目录解析，最终合法性统一走 safety 白名单，
old_string 必须精确匹配。
"""
from __future__ import annotations

PLUGIN_META = {
    "name": "文件编辑",
    "version": "1.0.0",
    "description": "edit 工具：精确替换文本文件内容（写操作，需确认）",
    "author": "tuzhan",
}

import os
from pathlib import Path

from backend.tools.base import ToolRegistry, tool_failure
from backend.tools.safety import check_path

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


async def _edit_file(path: str = "", old_string: str = "", new_string: str = "",
                     replace_all: bool = False) -> str:
    """编辑文件：将 old_string 替换为 new_string（对标 Harness 的 edit）。"""
    if not path or not old_string:
        return tool_failure("（缺少文件路径或待替换内容）")
    p = _resolve_path(path)
    if p is None:
        return tool_failure("（路径不允许：只能在工作目录下操作）")
    try:
        if not p.exists():
            return tool_failure(f"（文件不存在: {path}）")
        if p.is_dir():
            return tool_failure(f"（{path} 是目录，非文件）")
        content = p.read_text(encoding="utf-8")
    except PermissionError:
        return tool_failure("（无权限读取）")
    except Exception as e:
        return tool_failure(f"（读取失败：{e}）")

    if replace_all:
        if old_string not in content:
            return tool_failure(f"（未找到匹配: {old_string[:50]}）")
        count = content.count(old_string)
        new_content = content.replace(old_string, new_string)
        if not new_string:
            msg = f"（已删除 {count} 处匹配）"
        else:
            msg = f"（已替换 {count} 处匹配）"
    else:
        idx = content.find(old_string)
        if idx == -1:
            return tool_failure(f"（未找到匹配: {old_string[:50]}）")
        # 确认只出现一次（除非用户明确 replace_all）
        if content.count(old_string) > 1:
            return tool_failure(f"（匹配出现 {content.count(old_string)} 次，请加 replace_all=true 避免歧义）")
        new_content = content[:idx] + new_string + content[idx + len(old_string):]
        msg = "（已替换 1 处）"

    try:
        p.write_text(new_content, encoding="utf-8")
        return msg
    except PermissionError:
        return tool_failure("（无权限写入）")
    except Exception as e:
        return tool_failure(f"（写入失败：{e}）")


def register(ctx=None) -> None:
    ToolRegistry.register_func(
        name="edit",
        description="编辑文本文件：将 old_string 替换为 new_string（对标 Harness 的 edit 工具）。替换时 old_string 必须在文件中精确匹配",
        func=_edit_file,
        owner="file_edit",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径（相对工作目录或绝对路径）"},
                "old_string": {"type": "string", "description": "待替换的精确字符串"},
                "new_string": {"type": "string", "description": "替换后的新字符串（空字符串=删除）"},
                "replace_all": {"type": "boolean", "description": "是否替换所有匹配（默认 false，仅替换第一个）"}
            },
            "required": ["path", "old_string", "new_string"],
        },
        category="write",
        needs_confirm=True,
    )
