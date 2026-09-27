# -*- coding: utf-8 -*-
"""数据护栏（NP-12）：手动备份与恢复演习（只读校验）。

设计：复用 maintenance.loop.backup()（已处理明文/加密/锁定态与轮转），
不新写备份逻辑；"恢复演习"对最新一份备份做 verify_files 全量校验
（sha256 + SQLite integrity_check），让用户亲眼看到"她记得你"有底。
"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..core.config import config

router = APIRouter(prefix="/api/backup", tags=["backup"])


def _backup_root() -> Path:
    return config.data_dir / "backups"


def _latest_backup() -> tuple[Path, dict] | None:
    """明文与加密备份中 completed_at_epoch 最新的一份；解析失败的目录跳过。"""
    from ..maintenance.backup_manifest import valid_backups
    from ..maintenance.encrypted_backup import valid_encrypted_backups

    candidates: list[tuple[Path, dict]] = []
    try:
        candidates.extend(valid_backups(_backup_root()))
    except Exception:  # noqa: BLE001 - 单侧失败不遮蔽另一侧
        pass
    try:
        candidates.extend(valid_encrypted_backups(_backup_root()))
    except Exception:  # noqa: BLE001
        pass
    if not candidates:
        return None
    return max(candidates, key=lambda pair: float(pair[1].get("completed_at_epoch", 0)))


@router.get("/status")
async def api_backup_status():
    """最新备份概况 + 完整性校验（恢复演习的只读检查，不动任何数据）。"""
    from ..storage import runtime

    latest = _latest_backup()
    if latest is None:
        return {
            "ok": True,
            "has_backup": False,
            "encrypted_mode": runtime.encrypted_mode(),
            "hint": "还没有任何备份，点「立即备份」生成一份",
        }
    folder, manifest = latest
    verify = "pass"
    verify_error = ""
    try:
        if manifest.get("kind") == "encrypted_backup":
            from ..maintenance.encrypted_backup import load_encrypted_manifest

            if runtime.encrypted_mode() and not runtime.locked():
                # 解锁态：verify_files 会校验 sha256 与加密容器结构
                load_encrypted_manifest(folder, verify_files=True)
            else:
                # 加密备份在明文态/锁定态无法校验文件内容：manifest 结构仍然可查
                load_encrypted_manifest(folder, verify_files=False)
                verify = "skip"
                verify_error = "加密备份需在解锁态校验文件内容"
        else:
            from ..maintenance.backup_manifest import load_manifest

            load_manifest(folder, verify_files=True)
    except Exception as exc:  # noqa: BLE001 - 校验失败正是演习要暴露的
        verify = "fail"
        verify_error = str(exc)

    completed = float(manifest.get("completed_at_epoch", 0))
    age_days = max(0.0, (time.time() - completed) / 86400) if completed else None
    return {
        "ok": True,
        "has_backup": True,
        "encrypted_mode": runtime.encrypted_mode(),
        "name": folder.name,
        "completed_at": completed,
        "age_days": round(age_days, 2) if age_days is not None else None,
        "verify": verify,
        "verify_error": verify_error,
        "file_count": len(manifest.get("files", [])),
    }


@router.post("/run")
async def api_backup_run():
    """立即备份：走与每日维护完全相同的 backup() 入口（含加密态与轮转）。"""
    if runtime_locked():
        return JSONResponse({"ok": False, "error": "应用处于锁定态，解锁后再备份"}, status_code=423)
    from ..maintenance import loop

    dest = await _run_backup_async()
    if dest is None:
        return JSONResponse({"ok": False, "error": "备份失败，详见后端日志"}, status_code=500)
    return {"ok": True, "name": Path(dest).name}


def runtime_locked() -> bool:
    from ..storage import runtime

    return runtime.encrypted_mode() and runtime.locked()


async def _run_backup_async():
    """backup() 是同步重 IO，丢线程池避免阻塞事件循环。"""
    import asyncio

    from ..maintenance import loop

    return await asyncio.to_thread(loop.backup)
