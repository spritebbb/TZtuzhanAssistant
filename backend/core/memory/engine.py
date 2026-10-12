"""记忆引擎：编排记忆系统 v2 的初始化、回填、写入、检索。

对外职责：
- ensure_ready()：启动时初始化 Chroma 并触发存量迁移（embedding 模型预热由
  app 启动流程在后台单独调度，这里不强制加载，避免阻塞启动）
"""
from ..config import config
from ..log import logger

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
