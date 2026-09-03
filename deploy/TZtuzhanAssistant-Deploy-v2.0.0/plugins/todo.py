# -*- coding: utf-8 -*-
"""工具插件：任务/目标追踪（对标 Harness 的 goal + todo 系统）。

LLM 可调用这些工具来创建、更新、完成、列出、删除任务。
任务按当前会话用户身份隔离（current_user_id ContextVar）。
"""
from __future__ import annotations

PLUGIN_META = {
    "name": "任务追踪",
    "version": "1.0.0",
    "description": "todo_create / todo_list / todo_get / todo_update / todo_complete / todo_delete",
    "author": "tuzhan",
}

from datetime import datetime

from backend.tools.base import ToolRegistry, tool_failure
from backend.core import userdb
from backend.core.current_user import current_user_id


def _uid() -> str:
    return current_user_id.get()


async def _todo_create(content: str = "", priority: str = "P1", phase: str = "") -> str:
    """创建新的任务/目标。"""
    if not content:
        return tool_failure("（缺少任务描述）")
    priority = priority.upper()
    if priority not in ("P0", "P1", "P2", "P3"):
        priority = "P1"
    task_id = userdb.db.create_task(_uid(), content, priority, phase)
    return f"✅ 已创建任务 #{task_id}：{content[:50]}{'…' if len(content) > 50 else ''}（优先级: {priority}）"


async def _todo_list(status: str = "") -> str:
    """列出所有任务，可按状态过滤。"""
    status_map = {
        "pending": "待处理",
        "in_progress": "进行中",
        "inprogress": "in_progress",
        "completed": "已完成",
        "done": "completed",
        "blocked": "受阻",
    }
    actual = status_map.get(status.lower().replace(" ", "_"), status or None)
    if actual is not None and actual not in ("pending", "in_progress", "completed", "blocked"):
        # LLM 常把整句自然语言塞进 status 参数（如"帮我查一下我的待办"），
        # 此时忽略无效状态，返回全部待办
        actual = None
    tasks = userdb.db.list_tasks(_uid(), actual)
    if not tasks:
        return "📋 暂无任务" + (f"（状态: {status}）" if status else "")

    # 按优先级排序展示
    lines = []
    for t in tasks:
        sid = t["id"]
        s = t["status"]
        p = t["priority"]
        c = t["content"][:60]
        icon = {"pending": "⏳", "in_progress": "🔄", "completed": "✅", "blocked": "🚫"}.get(s, "📌")
        lines.append(f"  #{sid} {icon} [{p}] {c}")

    total = len(tasks)
    summary = f"📋 共 {total} 个任务"
    if status:
        summary += f"（状态: {status}）"
    return summary + "\n" + "\n".join(lines)


async def _todo_get(task_id: int = 0) -> str:
    """查看单个任务详情。"""
    if not task_id:
        return tool_failure("（缺少任务 ID）")
    uid = _uid()
    t = userdb.db.get_task(uid, task_id)
    if not t:
        return tool_failure(f"（任务 #{task_id} 不存在）")
    status_icon = {"pending": "⏳", "in_progress": "🔄", "completed": "✅", "blocked": "🚫"}.get(t["status"], "📌")
    lines = [
        f"#{t['id']} {status_icon} {t['content']}",
        f"  状态: {t['status']}  |  优先级: {t['priority']}  |  阶段: {t['phase'] or '无'}",
        f"  创建: {t['created_at'][:19]}",
        f"  更新: {t['updated_at'][:19]}",
    ]
    if t.get("completed_at"):
        lines.append(f"  完成: {t['completed_at'][:19]}")
    if t.get("progress"):
        lines.append(f"  进度: {t['progress']}")
    if t.get("blocked_reason"):
        lines.append(f"  受阻原因: {t['blocked_reason']}")
    return "\n".join(lines)


async def _todo_update(task_id: int = 0, content: str = "", status: str = "",
                       priority: str = "", phase: str = "", progress: str = "",
                       blocked_reason: str = "") -> str:
    """更新任务属性。"""
    if not task_id:
        return tool_failure("（缺少任务 ID）")
    uid = _uid()
    t = userdb.db.get_task(uid, task_id)
    if not t:
        return tool_failure(f"（任务 #{task_id} 不存在）")
    kwargs: dict[str, str] = {}
    if content:
        kwargs["content"] = content
    if status:
        status = status.strip().lower().replace(" ", "_")
        if status not in ("pending", "in_progress", "completed", "blocked"):
            return tool_failure(f"（不支持的状态: {status}，可选: pending / in_progress / completed / blocked）")
        kwargs["status"] = status
    if priority:
        priority = priority.upper()
        if priority not in ("P0", "P1", "P2", "P3"):
            return tool_failure(f"（不支持的优先级: {priority}，可选: P0/P1/P2/P3）")
        kwargs["priority"] = priority
    if phase:
        kwargs["phase"] = phase
    if progress:
        kwargs["progress"] = progress
    if blocked_reason:
        kwargs["blocked_reason"] = blocked_reason
        if "status" not in kwargs:  # 未显式指定状态时才强制 blocked
            kwargs["status"] = "blocked"
    ok = userdb.db.update_task(uid, task_id, **kwargs)
    if not ok:
        return tool_failure("（无有效更新字段）")
    return f"✅ 任务 #{task_id} 已更新"


async def _todo_complete(task_id: int = 0) -> str:
    """标记任务为已完成。"""
    if not task_id:
        return tool_failure("（缺少任务 ID）")
    uid = _uid()
    ok = userdb.db.update_task(uid, task_id, status="completed")
    if not ok:
        return tool_failure(f"（任务 #{task_id} 不存在）")
    return f"✅ 任务 #{task_id} 已完成"


async def _todo_delete(task_id: int = 0) -> str:
    """删除任务。"""
    if not task_id:
        return tool_failure("（缺少任务 ID）")
    uid = _uid()
    ok = userdb.db.delete_task(uid, task_id)
    if not ok:
        return tool_failure(f"（任务 #{task_id} 不存在）")
    return f"🗑️ 已删除任务 #{task_id}"


def register(ctx=None) -> None:
    specs = [
        ("todo_create", "创建新任务/目标（对标 Harness 的 create_goal）。可指定优先级和阶段标签",
         _todo_create, {
             "content": {"type": "string", "description": "任务描述"},
             "priority": {"type": "string", "description": "优先级: P0/P1/P2/P3（默认 P1）"},
             "phase": {"type": "string", "description": "阶段/分组标签（如 v3）"},
         }, ["content"]),
        ("todo_list", "列出所有任务。可按状态过滤（pending / in_progress / completed / blocked）",
         _todo_list, {
             "status": {"type": "string", "description": "状态过滤（留空显示全部）"},
         }, []),
        ("todo_get", "查看单个任务详情，含优先级、阶段、进度、受阻原因等",
         _todo_get, {
             "task_id": {"type": "integer", "description": "任务 ID"},
         }, ["task_id"]),
        ("todo_update", "更新任务属性（内容/状态/优先级/阶段/进度/受阻原因）",
         _todo_update, {
             "task_id": {"type": "integer", "description": "任务 ID"},
             "content": {"type": "string", "description": "新内容"},
             "status": {"type": "string", "description": "新状态: pending/in_progress/completed/blocked"},
             "priority": {"type": "string", "description": "新优先级: P0/P1/P2/P3"},
             "phase": {"type": "string", "description": "新阶段标签"},
             "progress": {"type": "string", "description": "进度说明"},
             "blocked_reason": {"type": "string", "description": "受阻原因（同时自动设为 blocked 状态）"},
         }, ["task_id"]),
        ("todo_complete", "标记任务为已完成（等价于 todo_update status=completed）",
         _todo_complete, {
             "task_id": {"type": "integer", "description": "任务 ID"},
         }, ["task_id"]),
        ("todo_delete", "删除任务",
         _todo_delete, {
             "task_id": {"type": "integer", "description": "任务 ID"},
         }, ["task_id"]),
    ]
    for name, desc, func, props, req in specs:
        # 写类操作（创建/更新/完成/删除）默认需确认；列表/详情只读
        write_ops = {"todo_create", "todo_update", "todo_complete", "todo_delete"}
        ToolRegistry.register_func(
            name=name,
            description=desc,
            func=func,
            owner="todo",
            input_schema={
                "type": "object",
                "properties": props,
                "required": req,
            },
            category="write" if name in write_ops else "read",
            needs_confirm=name in write_ops,
        )
