# -*- coding: utf-8 -*-
"""Agent 长任务执行器。

把一个复杂目标拆成计划，再在计划上下文中让 LLM 自主调用工具逐步完成。
每步工具调用都会经过全局确认钩子（confirm_hook）——用户逐条批准/拒绝，
实现"每步确认"。状态持久化到 SQLite（data/agent_tasks.db），可查询进度。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import config
from ..core.log import logger
from ..core.persona import build_system_prompt
from ..core.llm import chat, chat_native
from ..tools.service import run_tool_round

_DB: Path = config.data_dir / "agent_tasks.db"

# 单次执行的最大工具轮数（一个任务内 LLM 可自主调用工具的上限）
MAX_TOOL_ROUNDS = 8
# 执行超时（秒）
TASK_TIMEOUT = 300


@dataclass
class TaskStep:
    title: str
    detail: str = ""
    status: str = "pending"   # pending/running/done/failed
    result: str = ""
    ts: float = 0.0


@dataclass
class AgentTask:
    id: str
    user_id: str
    objective: str
    plan: list[TaskStep] = field(default_factory=list)
    status: str = "planned"   # planned/running/done/failed/cancelled
    step_confirmations: dict[str, str] = field(default_factory=dict)
    # ^ {step_index: "pending"/"allowed"/"denied"}，前端可逐条确认
    log: list[dict] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    result: str = ""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init() -> None:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    conn = _connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_tasks ("
            "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, objective TEXT NOT NULL,"
            "plan TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'planned',"
            "step_confirmations TEXT NOT NULL DEFAULT '{}',"
            "log TEXT NOT NULL DEFAULT '[]', result TEXT NOT NULL DEFAULT '',"
            "created_at REAL NOT NULL, updated_at REAL NOT NULL)"
        )
        # 迁移：旧表无 step_confirmations 列时补列
        cols = {r[1] for r in conn.execute("PRAGMA table_info(agent_tasks)").fetchall()}
        if "step_confirmations" not in cols:
            conn.execute("ALTER TABLE agent_tasks ADD COLUMN step_confirmations TEXT NOT NULL DEFAULT '{}'")
            conn.commit()
            logger.info("[Agent] 已迁移 agent_tasks 表：补充 step_confirmations 列")
        conn.commit()
    finally:
        conn.close()


def _load(id: str) -> AgentTask | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM agent_tasks WHERE id=?", (id,)).fetchone()
        if row is None:
            return None
        try:
            step_cfg = json.loads(row["step_confirmations"]) if row["step_confirmations"] else {}
        except (KeyError, json.JSONDecodeError):
            step_cfg = {}
        task = AgentTask(
            id=row["id"], user_id=row["user_id"], objective=row["objective"],
            plan=[TaskStep(**s) for s in json.loads(row["plan"])],
            step_confirmations=step_cfg,
            status=row["status"], log=json.loads(row["log"]),
            result=row["result"], created_at=row["created_at"], updated_at=row["updated_at"],
        )
        return task
    finally:
        conn.close()


def _save(task: AgentTask) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO agent_tasks (id, user_id, objective, plan, status,"
            " step_confirmations, log, result, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (task.id, task.user_id, task.objective,
             json.dumps([s.__dict__ for s in task.plan], ensure_ascii=False),
             task.status,
             json.dumps(task.step_confirmations, ensure_ascii=False),
             json.dumps(task.log, ensure_ascii=False), task.result,
             task.created_at, task.updated_at),
        )
        conn.commit()
    finally:
        conn.close()


def list_tasks(user_id: str, limit: int = 20) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, objective, status, created_at, updated_at FROM agent_tasks"
            " WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---- 计划生成 ----

_PLAN_PROMPT = """你是一个任务规划器。请把用户的目标拆解成可执行的步骤计划。

要求：
- 3~6 步，每步是"做一件具体的事"
- 每步给出：title（简短标题）、detail（详细做什么、用什么工具）
- 只输出 JSON 数组，格式：[{"title": "...", "detail": "..."}]
- 不要输出任何其他内容"""


async def create_task(user_id: str, objective: str) -> AgentTask:
    """创建任务：LLM 生成计划，落库。"""
    task = AgentTask(
        id=uuid.uuid4().hex[:12], user_id=user_id,
        objective=objective,
        created_at=time.time(), updated_at=time.time(),
    )
    # 生成计划（失败降级为单步）
    try:
        plan_text = await chat(
            [
                {"role": "system", "content": _PLAN_PROMPT},
                {"role": "user", "content": objective},
            ],
            temperature=0.2, max_tokens=1024,
        )
        steps = json.loads(plan_text)
        if isinstance(steps, list) and steps:
            task.plan = [
                TaskStep(title=str(s.get("title", f"步骤{i+1}")), detail=str(s.get("detail", "")))
                for i, s in enumerate(steps[:6])
            ]
    except Exception:
        logger.warning("[Agent] 计划生成失败，降级为单步")
        task.plan = [TaskStep(title="执行任务", detail=objective)]
    if not task.plan:
        task.plan = [TaskStep(title="执行任务", detail=objective)]
    # 初始化步骤确认状态（全部 pending）
    for i in range(len(task.plan)):
        task.step_confirmations[str(i)] = "pending"
    _save(task)
    return task


# ---- 执行 ----

async def run_task(task_id: str, *, max_rounds: int = MAX_TOOL_ROUNDS) -> AgentTask:
    """执行任务：在计划上下文里让 LLM 自主调用工具逐步完成。

    所有工具调用经全局确认钩子（confirm_hook），用户逐条批准。
    返回执行后的任务对象（status/result 已更新并落库）。
    """
    task = _load(task_id)
    if task is None:
        raise ValueError(f"任务不存在: {task_id}")
    if task.status in ("running", "done"):
        return task
    if task.status == "cancelled":
        return task
    task.status = "running"
    task.updated_at = time.time()
    _save(task)

    try:
        # 组装任务上下文：人格 + 目标 + 计划 + 当前进度
        try:
            from ..core.userdb import db as _user_db
            from ..core import affection as _affection

            user = _user_db.ensure_user(task.user_id)
            pref = user.get("nickname_pref") or "你"
            stage = _affection.stage_of(user["affection"])
            system = build_system_prompt(
                stage=stage, address=pref,
                lover_confirm=bool(user.get("lover_confirm", False)),
                first_chat=False, affection=user["affection"],
                user_id=task.user_id,
            )
        except Exception:
            # 降级：无用户上下文时用基础人格
            system = build_system_prompt(
                stage="初识", address="你", lover_confirm=False,
                first_chat=False, affection=0, user_id=task.user_id,
            )
        # 计划：只有用户明确允许（allowed）的步骤进入执行上下文；
        # pending（未确认）与 denied 都不进入——让步骤级确认真正生效
        #（工具级确认钩子仍然独立把关每一次调用）。
        kept = [s for i, s in enumerate(task.plan)
                if task.step_confirmations.get(str(i), "pending") == "allowed"]
        if not kept:
            # 全部步骤未确认：不启动执行，提示先走确认流程
            task.status = "planned"
            task.result = "（等待步骤确认：请先在计划面板允许要执行的步骤）"
            task.updated_at = time.time()
            _save(task)
            return task
        plan_desc = "\n".join(
            f"{i+1}. {s.title}" + (f"：{s.detail}" if s.detail else "")
            for i, s in enumerate(kept)
        )
        exec_prompt = (
            f"[你的任务目标]\n{task.objective}\n\n"
            f"[计划步骤]\n{plan_desc}\n\n"
            "请按计划逐步完成这个任务。你可以调用工具（文件/命令/进程/窗口/截图等）来实际执行，"
            "每调用一个工具前系统都会请你确认。每一步做完后简要说明进展，"
            "全部完成或确定无法继续时，给出最终总结。保持你的说话风格，但以完成任务为主。"
        )
        messages = [{"role": "system", "content": system}]
        messages.append({"role": "user", "content": exec_prompt})

        final = await asyncio.wait_for(
            run_tool_round(
                messages,
                chat=lambda ms: chat(ms),
                chat_native=lambda ms, tools: chat_native(ms, tools),
                max_loops=max_rounds,
            ),
            timeout=TASK_TIMEOUT,
        )
        # 执行期间用户可能点了取消：保留 cancelled 状态，不覆盖为 done。
        # （正在进行的 LLM 调用无法中断，但最终状态以用户选择为准。）
        fresh = _load(task_id)
        if fresh is not None and fresh.status == "cancelled":
            return fresh
        task.result = final
        task.status = "done"
        task.log.append({"ts": time.time(), "type": "result", "content": final[:500]})
        task.updated_at = time.time()
        _save(task)
        return task
    except asyncio.TimeoutError:
        # 总超时：中止长任务（TASK_TIMEOUT 定义了就该兑现——防失控轮询/命令挂死）
        logger.warning("[Agent] 任务 {} 超时（>{}s），中止", task_id, TASK_TIMEOUT)
        task.status = "failed"
        task.result = f"（任务超时：超过 {TASK_TIMEOUT} 秒总时限，已中止执行）"
        task.log.append({"ts": time.time(), "type": "timeout", "content": f"超时 {TASK_TIMEOUT}s"})
        task.updated_at = time.time()
        _save(task)
        return task
    except Exception as e:
        logger.exception("[Agent] 任务 {} 执行失败", task_id)
        task.status = "failed"
        task.result = f"（任务执行失败：{type(e).__name__}: {e}）"
        task.updated_at = time.time()
        _save(task)
        return task


def cancel_task(task_id: str) -> AgentTask | None:
    task = _load(task_id)
    if task is None:
        return None
    if task.status == "running":
        task.status = "cancelled"
        task.updated_at = time.time()
        _save(task)
        logger.info("[Agent] 任务 {} 已取消（后台执行将不再写入结果）", task_id)
    return task


def confirm_step(task_id: str, step_index: int, allow: bool) -> AgentTask | None:
    """确认/拒绝计划中的某一步。返回更新后的任务，或 None（任务不存在）。"""
    task = _load(task_id)
    if task is None:
        return None
    if not (0 <= step_index < len(task.plan)):
        raise ValueError(f"步骤序号越界: {step_index}")
    task.step_confirmations[str(step_index)] = "allowed" if allow else "denied"
    task.updated_at = time.time()
    _save(task)
    return task


def confirm_all(task_id: str, allow: bool) -> AgentTask | None:
    """整体放行/拒绝计划的所有步骤。"""
    task = _load(task_id)
    if task is None:
        return None
    val = "allowed" if allow else "denied"
    for i in range(len(task.plan)):
        task.step_confirmations[str(i)] = val
    task.updated_at = time.time()
    _save(task)
    return task


def pending_steps(task: AgentTask) -> list[int]:
    """尚未确认（pending）的步骤序号列表。"""
    return [i for i in range(len(task.plan))
            if task.step_confirmations.get(str(i), "pending") == "pending"]


def to_dict(task: AgentTask) -> dict:
    return {
        "id": task.id,
        "objective": task.objective,
        "plan": [s.__dict__ for s in task.plan],
        "status": task.status,
        "step_confirmations": task.step_confirmations,
        "pending_steps": pending_steps(task),
        "log": task.log[-20:],
        "result": task.result,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


_init()
