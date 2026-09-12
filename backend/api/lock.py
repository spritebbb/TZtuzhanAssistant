# -*- coding: utf-8 -*-
"""P3-04 D 片：应用锁 API——锁定即忘钥（clear-on-lock）。

行为（§21.1「应用锁与磁盘加密是两条独立控制」）：
- 锁定：key broker 立即清零 MK；之后除解锁端点与健康检查外的全部
  ``/api/**``、``/plugins/**``、``/mcp/**``、``/persona*`` 一律 423 Locked。
  前端收到 423 负责清空敏感 store、停止 TTS/流播放并显示解锁界面——
  「仅遮住窗口不算锁定」，后端侧的锁定语义是忘掉钥匙。
- 解锁：本机槽（DPAPI，无口令交互）或恢复口令。恢复口令失败统一错误、
  限速退避（防在线爆破；离线爆破由 Argon2id 成本承担）。
- 未初始化 keyslots 时（E 片迁移前的现状），锁定功能不可用并如实说明——
  不假装锁住了。

本端点自身必须始终可达（否则锁死无门），因此不注册在锁中间件之后，
而是中间件对 ``/api/lock`` 路径白名单放行。
"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..core.config import config
from ..core.key_broker import KeyBrokerError, broker, slots_available
from ..core.keyslots import KeySlotError, initialize
from ..core.log import logger

router = APIRouter(prefix="/api/lock", tags=["lock"])

# 恢复口令失败限速：窗口 15 分钟内最多 5 次尝试，超出后按剩余时间退避。
_FAIL_WINDOW_SEC = 15 * 60
_FAIL_MAX = 5
_fail_times: list[float] = []


class UnlockBody(BaseModel):
    method: str  # "local" | "recovery"
    passphrase: str | None = None


class InitBody(BaseModel):
    passphrase: str
    passphrase_repeat: str


def _slot_root():
    return config.data_dir / "keyslots"


def _prune_failures(now: float) -> None:
    global _fail_times
    _fail_times = [t for t in _fail_times if now - t < _FAIL_WINDOW_SEC]


def _recovery_rate_limited(now: float) -> float:
    """返回需等待秒数；0 = 放行。"""
    _prune_failures(now)
    if len(_fail_times) >= _FAIL_MAX:
        return _FAIL_WINDOW_SEC - (now - _fail_times[0])
    return 0.0


@router.get("")
async def lock_status() -> JSONResponse:
    """锁与密钥槽状态（无密钥材料）。"""
    from ..core.config import config
    from ..core.key_broker import broker as b

    bk = b()
    bk.engage_if_slots(config.data_dir / "keyslots")
    st = bk.status()
    return JSONResponse({
        "ok": True,
        "state": st["state"],
        "unlocked": st["unlocked"],
        "key_id": st["key_id"],
        "locked_at": st["locked_at"],
        "slots": slots_available(_slot_root()),
        # E 片迁移前数据仍为明文——如实告知，不把应用锁说成磁盘加密
        "data_encrypted": False,
    })


@router.post("")
async def lock_now() -> JSONResponse:
    """锁定：立即忘掉主密钥并关门（幂等）。"""
    result = broker().lock()
    # 忘钥匙的存储侧落实：关闭已打开的库连接，解锁后由惰性开库重连
    from ..storage import runtime

    await asyncio.to_thread(runtime.close_all_databases)
    logger.info("[应用锁] 已锁定（key_id 清零，库连接已关闭）")
    return JSONResponse({"ok": True, **result})


@router.post("/unlock")
async def unlock(body: UnlockBody) -> JSONResponse:
    now = time.time()
    method = body.method.strip()
    if method == "local":
        try:
            result = broker().unlock_local(_slot_root())
        except KeyBrokerError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        logger.info("[应用锁] 本机槽解锁 key_id={}", result["key_id"])
        return JSONResponse({"ok": True, **result})

    if method == "recovery":
        wait = _recovery_rate_limited(now)
        if wait > 0:
            return JSONResponse(
                {"ok": False, "error": f"尝试过于频繁，请 {int(wait) + 1} 秒后再试"},
                status_code=429,
            )
        if not body.passphrase:
            return JSONResponse({"ok": False, "error": "缺少恢复口令"}, status_code=400)
        try:
            result = broker().unlock_recovery(_slot_root(), body.passphrase)
        except KeyBrokerError as exc:
            _fail_times.append(now)
            # 统一错误信息：不区分口令错/槽坏，减少可探测性
            return JSONResponse({"ok": False, "error": "解锁失败：口令不正确或密钥槽不可用"},
                                status_code=401)
        logger.info("[应用锁] 恢复口令解锁 key_id={}", result["key_id"])
        return JSONResponse({"ok": True, **result})

    return JSONResponse({"ok": False, "error": "未知解锁方式"}, status_code=400)


@router.post("/initialize")
async def initialize_keyslots(body: InitBody) -> JSONResponse:
    """首次初始化密钥槽（生成 MK；写本机槽与恢复槽）。

    仅在完全没有槽时可用；返回的 MK 由 broker 立即接管安装——不经 API
    响应外传。
    """
    from ..core.key_broker import broker as b

    root = _slot_root()
    try:
        slots = slots_available(root)
        if slots["initialized"]:
            return JSONResponse({"ok": False, "error": "已存在密钥槽，拒绝重复初始化"},
                                status_code=409)
        master_key = initialize(root, passphrase=body.passphrase,
                                passphrase_repeat=body.passphrase_repeat)
    except KeySlotError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    b().install_for_testing(master_key)
    del master_key  # broker 之外的引用立即丢弃
    logger.info("[应用锁] 密钥槽初始化完成（MK 已由 broker 接管）")
    return JSONResponse({"ok": True, **b().status()})
