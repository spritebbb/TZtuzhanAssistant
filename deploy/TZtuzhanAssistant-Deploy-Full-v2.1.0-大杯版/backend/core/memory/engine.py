"""记忆引擎：编排记忆系统 v2 的初始化、回填、写入、检索。

对外职责：
- ensure_ready()：启动时初始化 Chroma 并触发存量迁移（embedding 模型预热由
  app 启动流程在后台单独调度，这里不强制加载，避免阻塞启动）
- on_message(user_id, user_text, reply)：对话一轮结束后调用——落 SQLite + 建向量索引 + 提炼画像/事实
- on_startup()：后台回填（进程启动时）
"""
import asyncio

from ..config import config
from ..log import logger

# 持有后台任务强引用，避免 pending task 被 GC 销毁
_background_tasks: set[asyncio.Task] = set()
# 只记录会写用户记忆的任务；启动预热/回填不应阻塞“彻底失忆”。
_message_tasks: set[asyncio.Task] = set()


def _spawn(coro, *, user_write: bool = False) -> asyncio.Task:
    """创建后台任务并持有强引用（与 app.py / agent 的修法一致）。

    只 `ensure_future` 不保存引用的任务在 await 慢操作时可能被 GC 静默丢弃，
    画像提炼 / Mem0 写入 / 启动回填会因此悄悄丢失。
    """
    task = asyncio.ensure_future(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    if user_write:
        _message_tasks.add(task)
        task.add_done_callback(_message_tasks.discard)
    return task


def ensure_ready() -> bool:
    """初始化记忆 v2 全部组件（幂等，可反复调用）。

    返回是否还有存量记忆待迁移（由调用方在事件循环线程调度迁移任务，
    避免本函数在工作线程里误用 asyncio.get_event_loop）。
    """
    if not config.memory_v2:
        logger.info("[记忆引擎] MEMORY_V2=0，使用旧版检索")
        return False
    try:
        from . import vector_store as vec

        if not vec.enabled():
            logger.warning("[记忆引擎] Chroma 不可用，向量检索降级为 TF-IDF")
            return False
        # 明确记录 embedding 状态，方便排查检索质量（模型 vs 哈希回退）。
        # 注意：这里不再调用 emb.mode()（那会强制触发模型加载/下载）；
        # 预热由启动流程在后台单独调度，避免阻塞应用启动。
        try:
            from . import embedding as emb

            if emb.is_loaded():
                logger.info("[记忆引擎] embedding 状态：model:{}", emb.current_model())
            else:
                logger.info("[记忆引擎] embedding 状态：未加载（待后台预热或哈希降级）")
        except Exception:
            pass
        # 判断是否有存量待迁移（只做判断与日志，调度交给调用方）
        try:
            from .migration import _needs_migration, migrate

            if _needs_migration():
                logger.info("[记忆引擎] 检测到存量记忆待迁移，已交由启动流程调度")
                return True
        except Exception as e:
            logger.warning("[记忆引擎] 存量迁移触发失败：{}", str(e)[:100])
        return False
    except Exception as e:
        logger.warning("[记忆引擎] 初始化失败：{}", str(e)[:100])
        return False


def on_message(user_id: str, user_text: str, reply: str, mock: bool = False) -> None:
    """对话一轮结束后的记忆写入（同步；内部异步派发，不阻塞回复）。

    现有 pipeline 已处理 SQLite 落库 + lm 向量索引；这里补充：
    - Mem0 记忆添加（后台）

    用户画像提炼不再逐条触发：pipeline 1.6 已有批量游标路径（≥10 条消息
    或空闲补提），逐条再调一次 LLM 会形成双通道重复开销（S2/R6）。
    """
    if not config.memory_v2:
        return
    try:
        if user_text and user_text.strip():
            if config.memory_mem0 and not mock:
                _spawn(_mem0_add_task(user_id, user_text), user_write=True)
    except Exception:
        pass


async def _mem0_add_task(user_id: str, text: str) -> None:
    """后台：把用户话语的关键信息交给 Mem0 管理。失败静默。"""
    try:
        from .memory_manager import manager

        await asyncio.to_thread(manager.add, user_id, text)
    except Exception:
        pass


def startup_backfill() -> None:
    """启动时后台回填向量索引（旧 sqlite-vec 的 backfill 职责迁移到这里）。"""
    if not config.memory_v2:
        return
    try:
        from ..userdb import db
        from . import vector_store as vec

        def _scan_and_index():
            # 工作线程读共享连接：用 UserDB 写锁串行化，避免与事件循环线程并发交错
            with db._lock:
                conn = db.conn
                for table, kind in (("long_memory", "lm"), ("facts", "facts")):
                    rows = conn.execute(
                        f"SELECT id, user_id, content FROM {table} WHERE id > 0 ORDER BY id"
                    ).fetchall()
                    for r in rows:
                        vec.add(r["user_id"], kind, r["id"], r["content"])
            logger.info("[记忆引擎] 向量索引回填完成")

        import asyncio as _asyncio

        _spawn(_asyncio.to_thread(_scan_and_index))
    except Exception:
        logger.warning("[记忆引擎] 向量回填失败（下次启动重试）")
