"""日志：loguru 写入 data/bot.log，并镜像到 stderr（NoneBot 控制台）。"""
import sys

from loguru import logger

from .config import config
from .privacy import redact_log_record

logger.remove()
config.data_dir.mkdir(parents=True, exist_ok=True)

_file_sink_id: int | None = None


def _add_file_sink() -> None:
    global _file_sink_id
    _file_sink_id = logger.add(
        config.data_dir / "bot.log",
        rotation="10 MB",
        retention=7,
        encoding="utf-8",
        level="INFO",
        filter=redact_log_record,
        # 在沙箱环境下 enqueue=True 会触发 multiprocessing pipe 创建失败
        # 单进程模式不需要 enqueue
    )


_add_file_sink()
logger.add(sys.stderr, level="INFO", filter=redact_log_record)


def release_file_sink() -> None:
    """P3-04 E：迁移动数据目录前释放 bot.log 句柄（Windows rename 零容忍）。

    由 storage.migration 在目录切换重试时惰性调用；随后 restore_file_sink()
    按原配置重建。幂等。
    """
    global _file_sink_id
    if _file_sink_id is not None:
        try:
            logger.remove(_file_sink_id)
        except Exception:
            pass
        _file_sink_id = None


def restore_file_sink() -> None:
    if _file_sink_id is None:
        _add_file_sink()


__all__ = ["logger", "release_file_sink", "restore_file_sink"]
