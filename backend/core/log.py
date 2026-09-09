"""日志：loguru 写入 data/bot.log，并镜像到 stderr（NoneBot 控制台）。"""
import sys

from loguru import logger

from .config import config
from .privacy import redact_log_record

logger.remove()
config.data_dir.mkdir(parents=True, exist_ok=True)
logger.add(
    config.data_dir / "bot.log",
    rotation="10 MB",
    retention=7,
    encoding="utf-8",
    level="INFO",
    filter=redact_log_record,
    # 在沙箱环境下 enqueue=True 会触发 multiprocessing pipe 创建失败
    # 单进程模式不需要 enqueue
)
logger.add(sys.stderr, level="INFO", filter=redact_log_record)

__all__ = ["logger"]
