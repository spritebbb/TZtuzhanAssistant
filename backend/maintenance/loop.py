# -*- coding: utf-8 -*-
"""后台维护：WAL checkpoint + 定时备份 + 生图磁盘上限清理。

从原 maintenance.py 迁移，调整为相对导入。
"""
from __future__ import annotations

import asyncio
import shutil
import sqlite3
import time
from pathlib import Path

from ..core.config import config
from ..core.log import logger

_DATA = config.data_dir
_SESSIONS_DB = _DATA / "sessions.db"
_BOT_DB = _DATA / "bot.db"
_IMGS = _DATA / "imgs"
_BACKUPS = _DATA / "backups"

# 周期：checkpoint+备份间隔 / 图片清理间隔（秒）
CHECKPOINT_INTERVAL = 6 * 3600      # 6 小时
CLEAN_IMGS_INTERVAL = 1 * 3600      # 1 小时
BACKUP_KEEP = 7                      # 保留最近 7 份备份
IMGS_MAX_MB = 300                    # 生图目录软上限（MB）
LONG_MEMORY_KEEP = 2000              # long_memory 表保留的最新条数（防无限增长）
AUDIT_LOG_MAX_BYTES = 5 * 1024 * 1024  # 审计日志单文件上限（5MB）
AUDIT_LOG_KEEP = 3                   # 轮转保留份数


def _checkpoint_one(path: Path) -> None:
    """对单个 SQLite 库执行 WAL checkpoint（TRUNCATE 模式）。"""
    try:
        conn = sqlite3.connect(str(path), timeout=5)
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        logger.debug(f"[维护] checkpoint: {path.name}")
    except sqlite3.Error as e:
        logger.warning(f"[维护] checkpoint 失败 {path.name}: {e}")


def checkpoint_all() -> None:
    for p in (_BOT_DB, _SESSIONS_DB):
        if p.exists():
            _checkpoint_one(p)


def backup() -> Path | None:
    """快照 db + imgs 到 backups/<时间戳>/，并清理超龄备份。"""
    try:
        _BACKUPS.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = _BACKUPS / stamp
        dest.mkdir(parents=True, exist_ok=True)
        for name in ("bot.db", "sessions.db"):
            src = _DATA / name
            if src.exists():
                # 用 SQLite 在线备份 API 替代直接拷贝：WAL 模式下能拿到
                # 一致快照，不会拷到一半的脏数据
                _backup_sqlite(src, dest / name)
        if _IMGS.exists():
            shutil.copytree(_IMGS, dest / "imgs", dirs_exist_ok=True)
        snaps = sorted(p for p in _BACKUPS.iterdir() if p.is_dir())
        for old in snaps[:-BACKUP_KEEP]:
            shutil.rmtree(old, ignore_errors=True)
        logger.info(f"[维护] 备份完成: {dest.name}（共 {len(snaps)} 份，保留最近 {BACKUP_KEEP} 份）")
        return dest
    except Exception as e:
        logger.warning(f"[维护] 备份失败: {e}")
        return None


def _backup_sqlite(src: Path, dst: Path) -> None:
    """用 sqlite3 在线备份 API 拷贝单个库（一致快照）。"""
    import sqlite3

    src_conn = sqlite3.connect(str(src))
    dst_conn = sqlite3.connect(str(dst))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()


def _referenced_image_names() -> set[str]:
    """所有会话里仍被 bot 消息引用的图片文件名集合。"""
    names: set[str] = set()
    try:
        conn = sqlite3.connect(str(_SESSIONS_DB), timeout=5)
        try:
            rows = conn.execute(
                "SELECT image FROM messages WHERE image IS NOT NULL AND image != ''"
            ).fetchall()
            for (url,) in rows:
                try:
                    names.add(Path(url).name)
                except ValueError:
                    pass
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.warning(f"[维护] 读取引用图片失败: {e}")
    return names


def _dir_size_mb(d: Path) -> float:
    total = 0
    try:
        for f in d.iterdir():
            if f.is_file():
                total += f.stat().st_size
    except OSError:
        pass
    return total / (1024 * 1024)


def clean_orphan_images(max_mb: int = IMGS_MAX_MB) -> int:
    """删除超上限的无引用图片，返回删除数量。"""
    if not _IMGS.exists():
        return 0
    size = _dir_size_mb(_IMGS)
    if size <= max_mb:
        return 0
    referenced = _referenced_image_names()
    removed = 0
    files = sorted(
        (f for f in _IMGS.iterdir() if f.is_file() and f.name not in referenced),
        key=lambda f: f.stat().st_mtime,
    )
    for f in files:
        if size <= max_mb:
            break
        try:
            size -= f.stat().st_size / (1024 * 1024)
            f.unlink()
            removed += 1
        except OSError:
            continue
    if removed:
        logger.info(f"[维护] 清理孤儿图片 {removed} 张（当前 {size:.0f}MB / 上限 {max_mb}MB）")
    else:
        logger.info(f"[维护] 图片 {size:.0f}MB 超上限但均被引用，未删除")
    return removed


def clean_old_long_memory(keep: int = LONG_MEMORY_KEEP) -> int:
    """清理 long_memory 表中过旧的记录，返回删除条数（防表无限增长）。"""
    try:
        conn = sqlite3.connect(str(_BOT_DB), timeout=5)
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            cur = conn.execute(
                "DELETE FROM long_memory WHERE id NOT IN ("
                "  SELECT id FROM long_memory ORDER BY id DESC LIMIT ?)",
                (keep,),
            )
            conn.commit()
            n = cur.rowcount
            if n:
                logger.info("[维护] 清理旧长期记忆 {} 条（保留最近 {} 条）", n, keep)
            return n
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.warning(f"[维护] 清理旧记忆失败: {e}")
        return 0


def rotate_audit_log(max_bytes: int = AUDIT_LOG_MAX_BYTES, keep: int = AUDIT_LOG_KEEP) -> bool:
    """审计日志超限时轮转（tool_log.jsonl → tool_log.1.jsonl，保留 keep 份）。"""
    log_path = _DATA / "tool_log.jsonl"
    try:
        if not log_path.exists() or log_path.stat().st_size <= max_bytes:
            return False
        # 旧文件依次后移
        for i in range(keep - 1, 0, -1):
            src = _DATA / f"tool_log.{i}.jsonl"
            dst = _DATA / f"tool_log.{i + 1}.jsonl"
            if src.exists():
                dst.unlink(missing_ok=True)
                src.rename(dst)
        rotated = _DATA / "tool_log.1.jsonl"
        rotated.unlink(missing_ok=True)
        log_path.rename(rotated)
        logger.info("[维护] 审计日志已轮转（保留最近 {} 份）", keep)
        return True
    except OSError as e:
        logger.warning(f"[维护] 审计日志轮转失败: {e}")
        return False


async def maintenance_loop() -> None:
    """后台周期任务：先跑一轮，再按各自间隔循环。"""
    next_ckpt = time.time() + CHECKPOINT_INTERVAL
    next_clean = time.time() + CLEAN_IMGS_INTERVAL
    while True:
        now = time.time()
        try:
            if now >= next_ckpt:
                await asyncio.to_thread(checkpoint_all)
                await asyncio.to_thread(backup)
                try:
                    from ..core.tts import clean_cache_async
                    await clean_cache_async()
                except Exception as e:
                    logger.warning(f"[维护] TTS 缓存清理异常: {e}")
                await asyncio.to_thread(clean_old_long_memory)
                await asyncio.to_thread(rotate_audit_log)
                next_ckpt = now + CHECKPOINT_INTERVAL
            if now >= next_clean:
                await asyncio.to_thread(clean_orphan_images)
                next_clean = now + CLEAN_IMGS_INTERVAL
        except Exception as e:
            logger.warning(f"[维护] 周期任务异常: {e}")
        await asyncio.sleep(60)


def health() -> dict:
    """健康检查：db 可达性、磁盘占用、备份状态。"""
    checks = {}
    for name in ("bot.db", "sessions.db"):
        p = _DATA / name
        checks[name] = {"exists": p.exists(), "size_kb": round(p.stat().st_size / 1024, 1) if p.exists() else 0}
    checks["imgs_mb"] = round(_dir_size_mb(_IMGS), 1)
    backups = sorted(p.name for p in _BACKUPS.iterdir() if p.is_dir()) if _BACKUPS.exists() else []
    checks["backups"] = backups
    return {
        "ok": checks["bot.db"]["exists"] and checks["sessions.db"]["exists"],
        "ts": time.time(),
        **checks,
    }
