# -*- coding: utf-8 -*-
"""Agent 长任务执行器。

把一个复杂目标拆成计划，再在计划上下文中让 LLM 自主调用工具逐步完成。
每步工具调用都会经过全局确认钩子（confirm_hook）——用户逐条批准/拒绝，
实现"每步确认"。状态持久化到 SQLite（data/agent_tasks.db），可查询进度。
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import config
from ..core.log import logger
from ..core.persona import build_system_prompt
from ..core.llm import chat, chat_native
from ..maintenance.schema_backup import create_pre_upgrade_backup, mark_schema_current
from ..storage.connect import connect_database
from ..tools.service import run_tool_round

_DB: Path = config.data_dir / "agent_tasks.db"
# 任务产物落地目录（用户可见的工作区；write_file 的允许根之内）
_REPORT_DIR: Path = Path(__file__).resolve().parents[2] / "workspace" / "agent-reports"
_SCHEMA_VERSION = 3

# 单次执行的最大工具轮数（一个任务内 LLM 可自主调用工具的上限）
MAX_TOOL_ROUNDS = 8
# 执行超时（秒）
TASK_TIMEOUT = 300


def _mcp_filter_for(objective: str):
    """Agent 任务同样按需注入 MCP 工具（任务目标命中触发词才暴露）。"""
    try:
        from ..tools.mcp_server import mcp_tool_filter

        return mcp_tool_filter(str(objective or ""), [])
    except Exception:
        logger.exception("[Agent] MCP 工具可见性判定失败，按全部可见处理")
        return None


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
    artifact_path: str = ""     # 任务报告落盘路径（空=未落盘）
    scheduled_at: float = 0.0   # 定时执行时间戳（0=不定时）
    attempt: int = 0            # 已尝试次数
    max_attempts: int = 2       # 最多尝试次数（失败可重试）


def _connect() -> sqlite3.Connection:
    # P3-04 E：建表初始化从模块导入期（原 _init() 在模块尾执行）改为首次
    # 连接前惰性执行——加密模式下锁定时 MK 不在内存，库无法在启动期打开。
    global _initialized
    if not _initialized:
        with _init_lock:
            if not _initialized:
                _init()
                _initialized = True
    return _connect_raw()


def _connect_raw() -> sqlite3.Connection:
    """打开连接（不含建表初始化；_init 内部也走这里，避免递归死锁）。"""
    from ..storage import runtime

    key = runtime.database_key_or_none()
    conn = connect_database(_DB, row_factory=True, encrypted_key=key)
    conn.execute("PRAGMA journal_mode=WAL")
    # 与 store.py / userdb.py 对齐：设置 busy_timeout，避免 run_task（后台线程）
    # 与 cancel_task / confirm_step（HTTP 请求）独立连接并发读写 WAL 单写者库时
    # 直接撞 "database is locked"，而非短暂等待重试。
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


_init_initialized = False
_init_lock = threading.Lock()


def _init() -> None:
    from ..storage import runtime

    config.data_dir.mkdir(parents=True, exist_ok=True)
    if not runtime.encrypted_mode():
        # 加密库的明文升级前快照无意义，备份由 F 片接管
        create_pre_upgrade_backup(_DB, config.data_dir / "backups", _SCHEMA_VERSION)
    conn = _connect_raw()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_tasks ("
            "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, objective TEXT NOT NULL,"
            "plan TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'planned',"
            "step_confirmations TEXT NOT NULL DEFAULT '{}',"
            "log TEXT NOT NULL DEFAULT '[]', result TEXT NOT NULL DEFAULT '',"
            "created_at REAL NOT NULL, updated_at REAL NOT NULL,"
            "artifact_path TEXT NOT NULL DEFAULT '',"
            "scheduled_at REAL NOT NULL DEFAULT 0,"
            "attempt INTEGER NOT NULL DEFAULT 0,"
            "max_attempts INTEGER NOT NULL DEFAULT 2)"
        )
        # 迁移：旧表无 step_confirmations 列时补列
        cols = {r[1] for r in conn.execute("PRAGMA table_info(agent_tasks)").fetchall()}
        if "step_confirmations" not in cols:
            conn.execute("ALTER TABLE agent_tasks ADD COLUMN step_confirmations TEXT NOT NULL DEFAULT '{}'")
            conn.commit()
            logger.info("[Agent] 已迁移 agent_tasks 表：补充 step_confirmations 列")
        if "artifact_path" not in cols:
            conn.execute("ALTER TABLE agent_tasks ADD COLUMN artifact_path TEXT NOT NULL DEFAULT ''")
            conn.commit()
            logger.info("[Agent] 已迁移 agent_tasks 表：补充 artifact_path 列")
        if "scheduled_at" not in cols:
            conn.execute("ALTER TABLE agent_tasks ADD COLUMN scheduled_at REAL NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE agent_tasks ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE agent_tasks ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 2")
            conn.commit()
            logger.info("[Agent] 已迁移 agent_tasks 表：补充定时/重试列")
        mark_schema_current(conn, _SCHEMA_VERSION)
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
            artifact_path=(row["artifact_path"] if "artifact_path" in row.keys() else "") or "",
            scheduled_at=float(row["scheduled_at"] or 0) if "scheduled_at" in row.keys() else 0.0,
            attempt=int(row["attempt"] or 0) if "attempt" in row.keys() else 0,
            max_attempts=int(row["max_attempts"] or 2) if "max_attempts" in row.keys() else 2,
        )
        return task
    finally:
        conn.close()


def _save(task: AgentTask) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO agent_tasks (id, user_id, objective, plan, status,"
            " step_confirmations, log, result, created_at, updated_at, artifact_path,"
            " scheduled_at, attempt, max_attempts)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task.id, task.user_id, task.objective,
             json.dumps([s.__dict__ for s in task.plan], ensure_ascii=False),
             task.status,
             json.dumps(task.step_confirmations, ensure_ascii=False),
             json.dumps(task.log, ensure_ascii=False), task.result,
             task.created_at, task.updated_at, task.artifact_path,
             task.scheduled_at, task.attempt, task.max_attempts),
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



# ---- 定时调度与重试 ----

def schedule_task(task_id: str, when_ts: float) -> AgentTask | None:
    """把任务排到指定时间执行（用户显式定时 = 计划步骤视为已批准）。

    步骤确认是防「她自己乱动」的闸门；用户主动定时属于明确授权，故自动放行，
    但工具级确认钩子仍独立把关每一次调用。
    """
    task = _load(task_id)
    if task is None:
        return None
    if task.status in ("running", "done", "cancelled"):
        return task
    task.scheduled_at = float(when_ts)
    for i in range(len(task.plan)):
        if task.step_confirmations.get(str(i), "pending") == "pending":
            task.step_confirmations[str(i)] = "allowed"
    task.updated_at = time.time()
    _save(task)
    logger.info("[Agent] 任务 {} 已排到 {}", task_id,
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(when_ts)))
    return task


def due_tasks(now: float | None = None) -> list[str]:
    """到点该跑的定时任务 id（status=planned 且 scheduled_at 已到）。"""
    moment = time.time() if now is None else float(now)
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id FROM agent_tasks WHERE status='planned'"
            " AND scheduled_at > 0 AND scheduled_at <= ? ORDER BY scheduled_at",
            (moment,),
        ).fetchall()
        return [str(r["id"]) for r in rows]
    finally:
        conn.close()


def prepare_retry(task_id: str) -> AgentTask | None:
    """把失败任务准备为下一次执行；实际启动由调用方决定。"""
    task = _load(task_id)
    if task is None:
        return None
    if task.status not in ("failed",):
        return task
    if task.attempt >= task.max_attempts:
        logger.info("[Agent] 任务 {} 已达重试上限 {}，不再重试", task_id, task.max_attempts)
        return task
    task.attempt += 1
    task.status = "planned"
    task.scheduled_at = 0.0
    task.log.append({"ts": time.time(), "type": "retry",
                     "content": f"第 {task.attempt} 次重试"})
    task.updated_at = time.time()
    _save(task)
    return task


async def retry_task(task_id: str) -> AgentTask | None:
    """兼容直接调用：准备失败任务并立即执行。HTTP 层使用统一后台启动器。"""
    current = _load(task_id)
    if current is None or current.status != "failed":
        return current
    task = prepare_retry(task_id)
    if task is None or task.status != "planned":
        return task
    return await run_task(task_id)


# ---- 执行 ----

def _claim_running(task_id: str) -> bool:
    """原子抢占任务执行权：仅当 status=='planned' 时才置为 running。

    用单条 UPDATE ... WHERE id=? AND status='planned' 把「检查→置 running」
    合并成原子操作，并用 rowcount==1 判定抢占是否成功，避免两次 POST /run
    并发通过「先读 status 再写 running」的竞态导致同一任务被执行两遍。
    """
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE agent_tasks SET status='running', scheduled_at=0, updated_at=? "
            "WHERE id=? AND status='planned'",
            (time.time(), task_id),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()



# ---- 产物落地：任务报告写进工作区（用户可自行查看/带走）----

def _write_report(task: AgentTask) -> str:
    """把任务目标/计划/结果写成 markdown 落到 workspace/agent-reports/。

    失败静默返回空串（落盘是加分项，不能影响任务本身的状态）。
    """
    try:
        _REPORT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(task.created_at or time.time()))
        safe = "".join(ch for ch in task.objective[:20] if ch not in '\\/:*?"<>|').strip()
        path = _REPORT_DIR / f"{stamp}-{task.id}-{safe or 'task'}.md"
        lines = [
            f"# {task.objective}", "",
            f"- 任务 ID：{task.id}",
            f"- 状态：{task.status}",
            f"- 创建时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(task.created_at or time.time()))}",
            "", "## 计划", "",
        ]
        for i, step in enumerate(task.plan):
            mark = {"allowed": "[已批准]", "denied": "[已拒绝]"}.get(
                task.step_confirmations.get(str(i), "pending"), "[未确认]"
            )
            lines.append(f"{i + 1}. {mark} {step.title}" + (f"：{step.detail}" if step.detail else ""))
        lines += ["", "## 结果", "", task.result or "（无结果）", ""]
        path.write_text("\n".join(lines), encoding="utf-8")
        return str(path)
    except Exception:
        logger.exception("[Agent] 任务报告落盘失败（不影响任务结果）")
        return ""


# ---- 聊天派活：从对话里识别「多步任务」意图 ----

_DISPATCH_PATTERNS = (
    re.compile(r"(?:帮我)?(?:分|按)(?:几步|步骤|多步)(?:来)?(?:做|完成|处理|搞|整理|查|写|安排)(?:一下)?[:：]?(.{2,60})"),
    re.compile(r"(?:把|将)(.{2,40}?)(?:拆成|拆分为|分成)(?:几步|步骤)"),
    re.compile(r"(?:用|走)(?:任务代理|agent)(?:来)?(?:做|处理|完成|整理|查|写|安排|跟进)[:：]?(.{2,60})"),
    re.compile(r"(?:派|建|开)(?:一个|个)?(?:多步)?任务(?:给你|给菟菚)?[:：]?(.{2,60})"),
)


def detect_dispatch_request(text: str) -> str | None:
    """识别「把这个当多步任务来做」的明确说法，返回任务目标（无则 None）。

    只认封闭句式，避免把普通聊天误判成派活（宁缺毋滥）。
    """
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean or len(clean) > 200:
        return None
    for pattern in _DISPATCH_PATTERNS:
        match = pattern.search(clean)
        if not match:
            continue
        objective = match.group(1).strip(" 　，,。.：:；;")
        if len(objective) >= 2:
            return objective
    return None


async def run_task(task_id: str, *, max_rounds: int = MAX_TOOL_ROUNDS,
                   already_claimed: bool = False) -> AgentTask:
    """执行任务：在计划上下文里让 LLM 自主调用工具逐步完成。

    所有工具调用经全局确认钩子（confirm_hook），用户逐条批准。
    返回执行后的任务对象（status/result 已更新并落库）。
    """
    task = _load(task_id)
    if task is None:
        raise ValueError(f"任务不存在: {task_id}")
    if task.status in ("done", "cancelled"):
        return task
    # 原子抢占执行权：两次并发 /run 只会有一个 rowcount==1 成功，另一个读到
    # 最新状态（已被置 running / 或已 done / cancelled）直接返回，不重复执行。
    if already_claimed:
        if task.status != "running":
            return task
    else:
        if task.status == "running" or not _claim_running(task_id):
            return _load(task_id)

    # 所有入口（HTTP、定时器、重试、测试/内部直接调用）都必须绑定任务创建时的
    # 用户命名空间。不能只依赖 HTTP 层设置，否则后台入口会回落到 assistant-main。
    from ..core.current_user import current_user_id

    user_token = current_user_id.set(task.user_id)

    try:
        # 组装任务上下文：人格 + 目标 + 计划 + 当前进度
        try:
            from ..core.userdb import db as _user_db
            from ..core import affection as _affection

            user = _user_db.ensure_user(task.user_id)
            pref = user["nickname_pref"] or "你"
            stage = _affection.stage_of(user["affection"])
            system = build_system_prompt(
                stage=stage, address=pref,
                lover_confirm=bool(user["lover_confirm"]),
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
                tool_filter=_mcp_filter_for(task.objective),
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
        task.artifact_path = _write_report(task) or task.artifact_path
        if task.artifact_path:
            task.result = f"{final}\n\n（完整报告已存到工作区：{task.artifact_path}）"
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
    finally:
        current_user_id.reset(user_token)



def recover_stale_tasks(*, now: float | None = None,
                        stale_after: float = TASK_TIMEOUT + 30) -> int:
    """把超过任务总时限仍为 running 的中断任务转成可重试的 failed。

    只回收超过 TASK_TIMEOUT+缓冲期的行，避免第二个只读进程导入模块时误伤
    正在执行的任务；调度循环每轮调用，因此崩溃后无需再次重启即可恢复。
    """
    moment = time.time() if now is None else float(now)
    cutoff = moment - max(float(stale_after), float(TASK_TIMEOUT))
    conn = _connect()
    recovered = 0
    try:
        rows = conn.execute(
            "SELECT id, log FROM agent_tasks WHERE status='running' AND updated_at<=?",
            (cutoff,),
        ).fetchall()
        for row in rows:
            try:
                logs = json.loads(row["log"] or "[]")
                if not isinstance(logs, list):
                    logs = []
            except (TypeError, json.JSONDecodeError):
                logs = []
            logs.append({
                "ts": moment,
                "type": "interrupted",
                "content": "服务中断，任务已转为失败，可手动重试",
            })
            cur = conn.execute(
                "UPDATE agent_tasks SET status='failed', scheduled_at=0, result=?, log=?, updated_at=? "
                "WHERE id=? AND status='running' AND updated_at<=?",
                ("（上次执行因服务中断而停止，可重试）",
                 json.dumps(logs, ensure_ascii=False), moment, row["id"], cutoff),
            )
            recovered += max(0, int(cur.rowcount or 0))
        conn.commit()
    finally:
        conn.close()
    if recovered:
        logger.warning("[Agent] 已恢复 {} 个服务中断遗留任务为 failed", recovered)
    return recovered


async def agent_scheduler_loop(interval: int = 60) -> None:
    """定时任务调度：每 interval 秒检查一次到点的 planned 任务并开跑。

    与主动性引擎同属后台 loop；失败只记日志，绝不影响主服务。
    """
    from ..core.reset import reset_in_progress

    while True:
        try:
            if not reset_in_progress():
                recover_stale_tasks()
                # 复用 HTTP 层的统一启动器：身份、确认通道、取消句柄、reset epoch
                # 和完成事件必须与手动运行保持同一份契约。
                from ..api.agent import _start_agent_task

                for task_id in due_tasks():
                    task = _load(task_id)
                    if task is None or task.status != "planned":
                        continue
                    logger.info("[Agent] 定时任务到点，开始执行：{}", task_id)
                    _start_agent_task(task_id)
        except Exception:
            logger.exception("[Agent] 定时任务调度检查失败")
        await asyncio.sleep(max(10, int(interval)))


def cancel_task(task_id: str) -> AgentTask | None:
    task = _load(task_id)
    if task is None:
        return None
    if task.status in ("planned", "running"):
        task.status = "cancelled"
        task.scheduled_at = 0.0
        task.updated_at = time.time()
        _save(task)
        logger.info("[Agent] 任务 {} 已取消（后台执行将不再写入结果）", task_id)
    return task


def clear_all_tasks() -> int:
    """删除全部持久化 Agent 任务（单用户“彻底失忆”使用）。"""
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM agent_tasks")
        conn.commit()
        return max(0, int(cur.rowcount or 0))
    finally:
        conn.close()


def clear_user_tasks(user_id: str) -> int:
    """只删除当前人格命名空间的 Agent 任务。"""
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM agent_tasks WHERE user_id=?", (user_id,))
        conn.commit()
        return max(0, int(cur.rowcount or 0))
    finally:
        conn.close()


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
        "artifact_path": task.artifact_path,
        "scheduled_at": task.scheduled_at,
        "attempt": task.attempt,
        "max_attempts": task.max_attempts,
        "step_confirmations": task.step_confirmations,
        "pending_steps": pending_steps(task),
        "log": task.log[-20:],
        "result": task.result,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


# 启动时初始化改为惰性（_connect 首次调用时确保），加密锁定态不打开库
_initialized = False
_init_lock = threading.Lock()
