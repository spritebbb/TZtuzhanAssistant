# -*- coding: utf-8 -*-
"""工具插件：文件搜索（glob 匹配 / grep 内容检索）。

对标 Harness 的 glob / grep 能力：帮助 LLM 在项目内定位文件。
搜索根目录的合法性统一走 safety 白名单。
"""
from __future__ import annotations

PLUGIN_META = {
    "name": "文件搜索",
    "version": "1.0.0",
    "description": "glob / grep 工具：按模式定位文件名与内容检索",
    "author": "tuzhan",
}

import os
import re
from pathlib import Path

from backend.tools.base import ToolRegistry, tool_failure
from backend.tools.safety import check_path

_WORK_DIR = Path(os.getcwd())


def _check_within(root: Path, target: Path) -> bool:
    """校验 target 是否在 root 目录内（防目录穿越）。

    与 safety.check_path 一致：前缀后必须跟路径分隔符（或恰好相等），
    避免 "D:\\proj" 误匹配 "D:\\project-evil" 这类前缀碰撞。
    """
    try:
        t = str(target.resolve()).casefold().rstrip("\\/")
        r = str(root.resolve()).casefold().rstrip("\\/")
        return t == r or t.startswith(r + os.sep)
    except (ValueError, OSError):
        return False


def _glob_sync(pattern: str = "", path: str = "") -> str:
    """按 glob 模式搜索文件名（默认在项目根内递归搜索）。"""
    if not pattern:
        return tool_failure("（缺少 glob 模式，例如 *.py 或 src/**/*.ts）")
    root = _resolve_root(path)
    if root is None:
        return tool_failure("（路径不允许：只能在工作目录下搜索）")
    try:
        matches = [str(p.relative_to(root)) for p in root.glob(pattern) if p.is_file()]
        matches = [m for m in matches if _check_within(root, root / m)]
        matches.sort()
        if not matches:
            return f"（无匹配: {pattern}）"
        limited = matches[:100]
        result = "\n".join(limited)
        if len(matches) > 100:
            result += f"\n...（共 {len(matches)} 个，已显示前 100）"
        return result
    except Exception as e:
        return tool_failure(f"（搜索失败：{e}）")


def _grep_sync(pattern: str = "", path: str = "", include: str = "") -> str:
    """在文件内容中检索正则表达式，返回 文件:行号:内容（对标 Harness 的 grep）。"""
    if not pattern:
        return tool_failure("（缺少检索模式）")
    root = _resolve_root(path)
    if root is None:
        return tool_failure("（路径不允许：只能在工作目录下检索）")
    try:
        # 默认跳过隐藏目录和常见产物目录
        skip_dirs = {".git", ".venv", "node_modules", "dist", "dist-electron",
                     "release", "__pycache__", ".npm-cache", ".tmp", "data"}
        rx = re.compile(pattern)
        results = []
        include_rx = re.compile(include) if include else None

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for fname in filenames:
                if include_rx and not include_rx.search(fname):
                    continue
                fpath = Path(dirpath) / fname
                if not _check_within(root, fpath):
                    continue
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                        for lineno, line in enumerate(fh, 1):
                            if rx.search(line.rstrip("\n")):
                                rel = str(fpath.relative_to(root))
                                results.append(f"{rel}:{lineno}:{line.rstrip()[:200]}")
                                if len(results) >= 100:
                                    break
                except (OSError, PermissionError):
                    continue
                if len(results) >= 100:
                    break
            if len(results) >= 100:
                break

        if not results:
            return f"（无匹配: {pattern}）"
        body = "\n".join(results)
        return body if len(results) < 100 else body + "\n...（已达上限 100 条）"
    except Exception as e:
        return tool_failure(f"（检索失败：{e}）")


async def _glob(pattern: str = "", path: str = "") -> str:
    """按 glob 模式搜索文件名（同步扫描放线程池，避免阻塞事件循环）。"""
    import asyncio

    return await asyncio.to_thread(_glob_sync, pattern, path)


async def _grep(pattern: str = "", path: str = "", include: str = "") -> str:
    """在文件内容中检索正则表达式（同步扫描放线程池，避免阻塞事件循环）。"""
    import asyncio

    return await asyncio.to_thread(_grep_sync, pattern, path, include)


def _resolve_root(path: str) -> Path | None:
    """解析搜索根目录（默认工作目录，或用户指定子目录）；合法性走统一白名单。"""
    try:
        root = _WORK_DIR if not path else (
            (Path(path).expanduser().resolve() if Path(path).is_absolute() else (_WORK_DIR / path).resolve()))
        ok, _ = check_path(root)
        if ok and root.exists():
            return root
        return None
    except (ValueError, OSError):
        return None


def register(ctx=None) -> None:
    ToolRegistry.register_func(
        name="glob",
        description="按 glob 模式搜索文件名（如 *.py、src/**/*.ts），返回相对路径列表",
        func=_glob,
        owner="file_search",
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "glob 模式，如 *.py"},
                "path": {"type": "string", "description": "搜索根目录（相对工作目录，留空为项目根）"}
            },
            "required": ["pattern"],
        },
    )
    ToolRegistry.register_func(
        name="grep",
        description="在文件内容中检索正则表达式，返回 文件:行号:匹配行（跳过 .git/node_modules 等目录）",
        func=_grep,
        owner="file_search",
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "正则表达式"},
                "path": {"type": "string", "description": "检索目录（相对工作目录，留空为项目根）"},
                "include": {"type": "string", "description": "可选：文件名过滤正则，如 .*\\.py$"}
            },
            "required": ["pattern"],
        },
    )
