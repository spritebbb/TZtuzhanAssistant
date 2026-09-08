# -*- coding: utf-8 -*-
"""网页监视工具：让菟菚能「盯着某个页面，变了告诉我」。

对应监控 Agent（缺口⑤）：L11 世界感知来源层的轻量落地——只做「网页变化」，
不保存正文，变化时经主动引擎通知（消耗共享主动额度）。
"""
from __future__ import annotations

from backend.core.log import logger
from backend.core import watchers
from backend.tools.base import ToolRegistry, tool_failure


async def watch_add(url: str = "", label: str = "", interval_minutes: int = 360) -> str:
    """新增一个网页监视。"""
    if not url:
        return tool_failure("（缺少要监视的网址）")
    from backend.core.current_user import current_user_id

    uid = current_user_id.get()
    try:
        item = watchers.add_watch(uid, url, label, interval_minutes)
    except watchers.WatchError as exc:
        return tool_failure(f"（无法监视：{exc}）")
    return (f"已开始监视：{item['url']}"
            f"{'（' + item['label'] + '）' if item['label'] else ''}，"
            f"每 {item['interval_minutes']} 分钟检查一次；有变化我会告诉你。")


async def watch_list() -> str:
    """列出正在监视的页面。"""
    from backend.core.current_user import current_user_id

    items = watchers.list_watches(current_user_id.get())
    if not items:
        return "（目前没有在监视任何页面）"
    lines = [
        f"#{item['id']} {item['label'] or item['url']}（每 {item['interval_minutes']} 分钟）"
        f"{' · 上次变化 ' + item['last_changed_at'][:16] if item.get('last_changed_at') else ''}"
        for item in items
    ]
    return "\n".join(lines)


async def watch_remove(watch_id: int = 0) -> str:
    """停止监视某个页面。"""
    from backend.core.current_user import current_user_id

    if not watch_id:
        return tool_failure("（缺少监视编号，可先用 watch_list 查）")
    ok = watchers.remove_watch(current_user_id.get(), int(watch_id))
    return f"已停止监视 #{watch_id}" if ok else tool_failure(f"（没找到监视 #{watch_id}）")


async def watch_check() -> str:
    """立刻检查一遍（不等间隔），用于演示或手动确认。"""
    from backend.core.current_user import current_user_id

    uid = current_user_id.get()
    try:
        changes = watchers.check_due_watches(uid, force=True)
    except Exception as exc:
        logger.exception("[监视] 手动检查失败")
        return tool_failure(f"（检查失败：{type(exc).__name__}）")
    if not changes:
        return "检查完了：目前没有发现变化。"
    lines = [f"发现 {len(changes)} 处变化："]
    for item in changes:
        lines.append(f"- {item['label'] or item['url']}（{item['url']}）")
    return "\n".join(lines)


def register(ctx=None) -> None:
    ToolRegistry.register_func(
        name="watch_add",
        description="新增一个网页监视：页面有变化时菟菚会主动告诉你（只比对内容指纹，不保存网页正文）",
        func=watch_add,
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要监视的网页地址（公网 http/https）"},
                "label": {"type": "string", "description": "给这个监视起个名字，如「官网公告」"},
                "interval_minutes": {"type": "integer", "description": "检查间隔分钟数，默认 360（6 小时），最短 30"},
            },
            "required": ["url"],
        },
        category="write", needs_confirm=True,
    )
    ToolRegistry.register_func(
        name="watch_list",
        description="列出正在监视的网页",
        func=watch_list,
        input_schema={"type": "object", "properties": {}},
        category="read",
    )
    ToolRegistry.register_func(
        name="watch_remove",
        description="停止监视某个网页（按编号）",
        func=watch_remove,
        input_schema={
            "type": "object",
            "properties": {"watch_id": {"type": "integer", "description": "监视编号"}},
            "required": ["watch_id"],
        },
        category="write", needs_confirm=True,
    )
    ToolRegistry.register_func(
        name="watch_check",
        description="立刻检查一遍所有监视的网页，返回发生变化的内容",
        func=watch_check,
        input_schema={"type": "object", "properties": {}},
        category="read",
    )
