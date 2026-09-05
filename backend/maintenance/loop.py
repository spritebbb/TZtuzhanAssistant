# -*- coding: utf-8 -*-
"""后台维护：WAL checkpoint + 定时备份 + 生图磁盘上限清理。

从原 maintenance.py 迁移，调整为相对导入。
"""
from __future__ import annotations

import asyncio
import json
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
_SCREENSHOTS = _DATA / "screenshots"
_BACKUPS = _DATA / "backups"

# 周期：checkpoint+备份间隔 / 图片清理间隔（秒）
CHECKPOINT_INTERVAL = 6 * 3600      # 6 小时
CLEAN_IMGS_INTERVAL = 1 * 3600      # 1 小时
BACKUP_KEEP = 7                      # 保留最近 7 份备份
IMGS_MAX_MB = 300                    # 生图目录软上限（MB）
SCREENSHOTS_MAX_MB = 200             # 截图目录软上限（MB）
SCREENSHOTS_KEEP = 50                # 截图目录保留的最新份数（防只增不减）
LONG_MEMORY_KEEP = 2000              # long_memory 表保留的最新条数（防无限增长）
# pinned 行（memory_add 显式写入）永不随普通轮转清理，但若任其无限累积同样膨胀。
# 设一个宽松上限：每用户 pinned 超过该值时，最旧的超量 pinned 降级为普通行
# （内容保留，转为受 LONG_MEMORY_KEEP 约束），既保住显式记住的内容，又防病态增长。
PINNED_MEMORY_KEEP = 800
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
        if _SCREENSHOTS.exists():
            shutil.copytree(_SCREENSHOTS, dest / "screenshots", dirs_exist_ok=True)
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
    """所有会话里仍被 bot/user 消息引用的图片文件名集合。

    同时扫描 messages 表（当前会话）和 archives.messages_json（已归档会话），
    避免归档后图片引用从 messages 挪进 archives 却被判定为「无引用」误删，
    导致旧归档里的图片 404。
    """
    names: set[str] = set()

    def _add(url: str) -> None:
        if not url:
            return
        try:
            names.add(Path(url).name)
        except ValueError:
            pass

    try:
        conn = sqlite3.connect(str(_SESSIONS_DB), timeout=5)
        try:
            # 1) 当前会话 messages 表的 image 字段
            rows = conn.execute(
                "SELECT image FROM messages WHERE image IS NOT NULL AND image != ''"
            ).fetchall()
            for (url,) in rows:
                _add(url)
            # 2) 归档 archives.messages_json 里的 image 字段（含 user 识图 + bot 生图）
            rows = conn.execute(
                "SELECT messages_json FROM archives WHERE messages_json IS NOT NULL AND messages_json != ''"
            ).fetchall()
            for (blob,) in rows:
                try:
                    msgs = json.loads(blob)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(msgs, list):
                    for m in msgs:
                        if isinstance(m, dict):
                            _add(m.get("image") or "")
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


def clean_old_screenshots(max_mb: int = SCREENSHOTS_MAX_MB, keep: int = SCREENSHOTS_KEEP) -> int:
    """清理截图目录：超上限或超份数时，按时间从旧到新删除最旧的截图。

    截图是任务代理调用的瞬时产物，无持久引用，直接按「软上限 + 保留份数」
    双重约束裁剪，避免截图目录只增不减、永久占盘。
    """
    if not _SCREENSHOTS.exists():
        return 0
    files = sorted(
        (f for f in _SCREENSHOTS.iterdir() if f.is_file()),
        key=lambda f: f.stat().st_mtime,
    )
    if not files:
        return 0
    size = sum(f.stat().st_size for f in files) / (1024 * 1024)
    removed = 0
    # 从最旧的开始删，直到「体积 ≤ 上限」且「份数 ≤ 保留数」
    for f in files:
        if size <= max_mb and len(files) - removed <= keep:
            break
        try:
            size -= f.stat().st_size / (1024 * 1024)
            f.unlink()
            removed += 1
        except OSError:
            continue
    if removed:
        logger.info(f"[维护] 清理旧截图 {removed} 张（当前 {size:.0f}MB / 上限 {max_mb}MB，保留最近 {keep} 张）")
    return removed


def _demote_overflowing_pinned(conn) -> int:
    """把每用户超出 PINNED_MEMORY_KEEP 的最旧 pinned 记忆降级为普通行。

    返回降级条数。pinned 行受显式保护不随普通轮转清理，但若 LLM/用户高频调用
    memory_add，会让 pinned 无限累积。这里在维护周期内给 pinned 设宽松上限，
    超量的最旧行置 pinned=0（内容仍在表内，转入普通配额，超旧后被正常轮转）。
    逐用户 Python 处理：维护任务低频（小时级），数据量小，成本可忽略。
    """
    over = conn.execute(
        "SELECT user_id, COUNT(*) AS n FROM long_memory WHERE pinned=1 "
        "GROUP BY user_id HAVING n > ?",
        (PINNED_MEMORY_KEEP,),
    ).fetchall()
    demoted = 0
    for row in over:
        # 该用户应降级的最旧条数 = 超出上限的部分
        excess = row["n"] - PINNED_MEMORY_KEEP
        cur = conn.execute(
            "UPDATE long_memory SET pinned=0 WHERE id IN ("
            "  SELECT id FROM long_memory WHERE user_id=? AND pinned=1 "
            "  ORDER BY id ASC LIMIT ?)",
            (row["user_id"], excess),
        )
        demoted += cur.rowcount or 0
    return demoted


def clean_old_long_memory(keep: int = LONG_MEMORY_KEEP) -> int:
    """清理 long_memory 表中过旧的记录，返回删除条数（防表无限增长）。

    与向量库同步：SQLite 删除前先删对应的 Chroma 向量（按 user 分组批量删），
    避免向量库残留孤儿条目——检索端若回表校验只是白费召回，若直接返回
    则被删的旧内容会「复活」。
    保护：pinned=1（用户显式要求记住的记忆）永不清理。
    """
    try:
        conn = sqlite3.connect(str(_BOT_DB), timeout=5)
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            # pinned 上限防线：每用户 pinned 数超过 PINNED_MEMORY_KEEP 时，把最旧的
            # 超量 pinned 降级为普通行（pinned=0，内容保留）。否则 memory_add 高频调用
            # 会让 pinned 行永久绕过所有配额、表与向量无限增长（审查「需关注」项）。
            # 降级后这些行落入下方「pinned=0 保留最近 keep 条」的普通配额，超旧的会
            # 随本轮清理被删，与对话流水记忆一致，不会单独堆积。
            demoted = _demote_overflowing_pinned(conn)
            # 选出待删行（保留最近 keep 条非 pinned 行；pinned 行降级后同按普通配额）
            rows = conn.execute(
                "SELECT id, user_id FROM long_memory WHERE pinned=0 AND id NOT IN ("
                "  SELECT id FROM long_memory WHERE pinned=0 ORDER BY id DESC LIMIT ?)",
                (keep,),
            ).fetchall()
            if not rows:
                if demoted:
                    conn.commit()
                    logger.info("[维护] pinned 记忆超上限，已降级 {} 条为普通行（内容保留）", demoted)
                return 0
            # 先删向量（按用户分组批量），再删 SQLite 行
            try:
                from ..core.memory import vector_store as vec

                by_user: dict[str, list[int]] = {}
                for r in rows:
                    by_user.setdefault(r["user_id"], []).append(r["id"])
                vec_removed = 0
                for uid, ids in by_user.items():
                    vec_removed += vec.delete_many(uid, "lm", ids)
                if vec_removed:
                    logger.info("[维护] 同步删除 {} 条旧长期记忆向量", vec_removed)
            except Exception as e:
                # 向量删除失败不阻断 SQLite 清理（孤儿向量可由重建流程兜底）
                logger.warning(f"[维护] 旧记忆向量同步删除失败: {e}")
            cur = conn.executemany(
                "DELETE FROM long_memory WHERE id=?",
                [(r["id"],) for r in rows],
            )
            conn.commit()
            n = cur.rowcount or len(rows)
            if n:
                logger.info("[维护] 清理旧长期记忆 {} 条（保留最近 {} 条，pinned 记忆受保护）", n, keep)
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
                await asyncio.to_thread(clean_old_screenshots)
                next_clean = now + CLEAN_IMGS_INTERVAL
        except Exception as e:
            logger.warning(f"[维护] 周期任务异常: {e}")
        await asyncio.sleep(60)


def health() -> dict:
    """健康检查：db 可达性、磁盘占用、备份状态。

    加固：除 db 文件是否存在外，额外暴露真实健康信号，避免「假绿灯」——
    LLM 未配 key / persona 缺失 / 插件加载失败 / 记忆引擎降级都应在健康里反映，
    而非等到首条消息才报 RuntimeError。
    """
    checks = {}
    for name in ("bot.db", "sessions.db"):
        p = _DATA / name
        checks[name] = {"exists": p.exists(), "size_kb": round(p.stat().st_size / 1024, 1) if p.exists() else 0}
    checks["imgs_mb"] = round(_dir_size_mb(_IMGS), 1)
    checks["screenshots_mb"] = round(_dir_size_mb(_SCREENSHOTS), 1)
    backups = sorted(p.name for p in _BACKUPS.iterdir() if p.is_dir()) if _BACKUPS.exists() else []
    checks["backups"] = backups

    extra: dict = {}
    # 1) LLM_API_KEY 是否已配置（非空）
    try:
        from .config import config as _cfg

        extra["llm_configured"] = bool(_cfg.llm_api_key and _cfg.llm_api_key.strip())
    except Exception:
        extra["llm_configured"] = False
    # 2) persona 人格源文件是否存在
    try:
        from ..core.persona_profiles import active_card_path

        persona_path = active_card_path()
        extra["persona_ok"] = bool(persona_path and persona_path.exists())
    except Exception:
        extra["persona_ok"] = False
    # 3) 插件加载失败数（plugin_states 里 error 非空者）
    try:
        from ..plugins import plugin_states

        states = plugin_states()
        extra["plugin_load_failures"] = sum(1 for s in states.values() if s.get("error"))
    except Exception:
        extra["plugin_load_failures"] = 0
    # 4) 记忆引擎当前模式：model:<名称> / hash（用不触发模型加载的接口，
    #    避免健康检查顺带触发一次模型下载；预热完成后状态才从 hash 切到 model）
    try:
        from ..core.memory.embedding import is_loaded, current_model

        if is_loaded():
            _m = current_model()
            extra["memory_mode"] = f"model:{_m}" if _m else "model"
        else:
            extra["memory_mode"] = "hash"
    except Exception:
        extra["memory_mode"] = "unknown"

    return {
        "ok": checks["bot.db"]["exists"] and checks["sessions.db"]["exists"],
        "ts": time.time(),
        **checks,
        **extra,
    }
