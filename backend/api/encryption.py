# -*- coding: utf-8 -*-
"""P3-04 E：「启用加密」一键 API——停写落门 → 引擎迁移 → 向量重建。

流程（全部在已解锁的前提下；明文部署 + 无槽时先初始化密钥槽）：
1. 落迁移持久化门：后台循环跳过、非本组 API 一律 503；
2. 关闭库连接（引擎自会 checkpoint + 快照 + 导出）；
3. 线程内执行 ``migrate_data_root``（状态机含隔离进程校验与原子切换）；
4. 刷新加密态缓存、放后台重建向量索引（SQLite 是权威，索引可重建）；
5. 返回 journal（cleanup_pending）与明文目录路径——删除由用户确认后调
   ``/cleanup``，永不自动发生。

真实数据迁移是重操作：本端点同步执行（本地小数据秒级；大量媒体为分钟级），
前端以加载态等待。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..core.config import config
from ..core.key_broker import broker
from ..core.keyslots import KeySlotError, initialize as initialize_slots
from ..core.log import logger
from ..storage import runtime
from ..storage.migration import (
    MigrationError,
    finish_cleanup,
    migrate_data_root,
    read_journal,
)

router = APIRouter(prefix="/api/encryption", tags=["encryption"])


class EnableBody(BaseModel):
    passphrase: str | None = None
    passphrase_repeat: str | None = None


def _slot_root():
    return config.data_dir / "keyslots"


@router.get("/status")
async def encryption_status() -> JSONResponse:
    journal = read_journal(config.data_dir)
    slots = _slot_root()
    return JSONResponse({
        "ok": True,
        "data_encrypted": runtime.encrypted_mode(),
        "slots_initialized": (slots / "local.dpapi").is_file() or (slots / "recovery.json").is_file(),
        "journal_state": (journal or {}).get("state"),
        "plaintext_keep": (journal or {}).get("plaintext_keep"),
        "gate_engaged": runtime.migration_gate_engaged(),
    })


@router.post("/enable")
async def enable_encryption(body: EnableBody) -> JSONResponse:
    if runtime.encrypted_mode():
        return JSONResponse({"ok": False, "error": "数据已是加密态"}, status_code=409)
    if runtime.migration_gate_engaged():
        return JSONResponse({"ok": False, "error": "迁移进行中"}, status_code=409)

    slots_ready = broker().status()["state"] == "unlocked"
    if not slots_ready:
        # 无槽：现场初始化密钥槽并接管 MK（明文部署的首启路径）
        if not body.passphrase or not body.passphrase_repeat:
            return JSONResponse({"ok": False, "error": "缺少恢复口令（两次输入）"}, status_code=400)
        try:
            mk = initialize_slots(_slot_root(), passphrase=body.passphrase,
                                  passphrase_repeat=body.passphrase_repeat)
        except KeySlotError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        broker().install_for_testing(mk)
    elif body.passphrase:
        return JSONResponse({"ok": False, "error": "密钥槽已存在，无需口令（已按当前密钥迁移）"},
                            status_code=409)

    runtime.engage_migration_gate()
    try:
        # 关库：迁移引擎自会做 WAL 收敛 + 一致性快照；句柄释放由引擎负责
        await asyncio.to_thread(runtime.close_all_databases)
        journal = await asyncio.to_thread(
            migrate_data_root, config.data_dir, broker().database_key())
    except MigrationError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    finally:
        runtime.reset_cache()
        runtime.release_migration_gate()

    # 向量索引：chroma 未随迁（可重建）——后台从 SQLite 全量重灌
    asyncio.create_task(_rebuild_vectors())

    logger.info("[加密] 启用完成，明文目录保留于 {}", journal.get("plaintext_keep"))
    return JSONResponse({"ok": True, "journal": journal})


async def _rebuild_vectors() -> None:
    try:
        from ..core.memory import vector_store

        rebuilt = await asyncio.to_thread(vector_store.rebuild_all, "encryption-migration")
        logger.info("[加密] 向量索引重建完成（{} 条）", rebuilt)
    except Exception:
        logger.exception("[加密] 向量索引重建失败（可稍后手动重试；SQLite 仍为权威）")


@router.post("/cleanup")
async def cleanup_plaintext() -> JSONResponse:
    """用户确认迁移成功后删除明文目录（显式动作，永不自动）。"""
    try:
        journal = await asyncio.to_thread(finish_cleanup, config.data_dir)
    except MigrationError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
    logger.info("[加密] 明文目录已删除（用户确认）")
    return JSONResponse({"ok": True, "journal": journal})
