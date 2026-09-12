# -*- coding: utf-8 -*-
"""P3-04 D 片：进程内 key broker——MK 的唯一运行时持有者 + 应用锁会话门。

职责边界（§21.1）：
- 后端进程内单例持有已解锁的 MK，**零知识**：只对注册过的调用方暴露
  「按域派生 data key / 按 key 语义取 SQLCipher key」的能力，不把 MK
  本体交给业务模块（防日志/异常误泄）；
- 应用锁锁定时立即清零并丢弃 MK，之后一切取 key 请求失败；解锁重新从
  槽（本机 DPAPI 或恢复口令）装载。「仅遮住窗口不算锁定」——锁 = 忘钥匙；
- 密钥只经进程内存传递；bytes 尽力缩短生命周期（del + 不可变引用置空），
  不宣称绝对内存清零（CPython 对象模型做不到，诚实声明）。

E 片（迁移）之前，运行时仍走明文 SQLite——broker 是「随时可挂钥匙的锁架」，
不改变现有数据路径；`data_protection_enabled`（config）在 E 片前恒为 False。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from .keyslots import (
    KeySlotError,
    describe_slots,
    read_local_slot,
    read_recovery_slot,
)


class KeyBrokerError(RuntimeError):
    """broker 未解锁/已锁定/参数非法。"""


class _Broker:
    """三态：inactive（未初始化密钥槽，锁功能未启用）→ unlocked（持 MK）⇄ locked（忘 MK）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._mk: bytes | None = None
        self._key_id: str = ""
        self._unlocked_at: float = 0.0
        self._locked_at: float | None = None
        self._engaged = False  # initialize/lock 任一发生即 True；重启后由槽存在性决定

    # ---- 状态查询（无密钥材料） ----
    def status(self) -> dict:
        with self._lock:
            if not self._engaged:
                state = "inactive"
            elif self._mk is not None:
                state = "unlocked"
            else:
                state = "locked"
            return {
                "state": state,
                "unlocked": state == "unlocked",
                "key_id": self._key_id if self._mk is not None else "",
                "unlocked_at": self._unlocked_at if self._mk is not None else None,
                "locked_at": self._locked_at,
            }

    def engage_if_slots(self, slot_root: Path) -> None:
        """启动钩子：keyslots 已初始化（含重启）→ 锁功能启用并处于锁定态。

        未初始化（E 片之前的现状）→ inactive，中间件不拦任何请求，
        现有部署零行为变化。
        """
        with self._lock:
            if self._engaged:
                return
            try:
                slots = describe_slots(slot_root)
            except KeySlotError:
                return
            if slots.any_slot:
                self._engaged = True
                self._locked_at = time.time()

    def _engage(self) -> None:
        self._engaged = True

    # ---- 解锁与锁定 ----
    def unlock_local(self, slot_root: Path) -> dict:
        """用本机 DPAPI 槽解锁。"""
        try:
            mk = read_local_slot(slot_root)
        except KeySlotError as exc:
            raise KeyBrokerError(str(exc)) from exc
        return self._install(mk)

    def unlock_recovery(self, slot_root: Path, passphrase: str) -> dict:
        """用恢复口令解锁（限速在 API 层做，broker 只管密码学正确性）。"""
        try:
            mk = read_recovery_slot(slot_root, passphrase)
        except KeySlotError as exc:
            raise KeyBrokerError(str(exc)) from exc
        return self._install(mk)

    def install_for_testing(self, master_key: bytes) -> dict:
        """测试/初始化路径：直接安装一把已生成的 MK（如 initialize 的返回值）。"""
        return self._install(master_key)

    def _install(self, mk: bytes) -> dict:
        from .keyslots import master_key_id

        with self._lock:
            if self._mk is not None:
                raise KeyBrokerError("已处于解锁状态（先锁定再解锁）")
            self._engage()
            self._mk = mk
            self._key_id = master_key_id(mk)
            self._unlocked_at = time.time()
            self._locked_at = None
            return {"unlocked": True, "key_id": self._key_id}

    def lock(self) -> dict:
        """应用锁：立即清零丢弃 MK。之后一切取 key 请求失败。"""
        with self._lock:
            self._engage()  # 显式锁定即启用锁功能（即使此前未初始化密钥槽）
            if self._mk is None:
                # 幂等：已锁定再锁不是错误
                return {"unlocked": False, "key_id": "", "locked_at": self._locked_at}
            mk, self._mk = self._mk, None
            self._key_id = ""
            self._locked_at = time.time()
            # 尽力缩短生命周期：删除最后引用后交给 GC（不宣称绝对清零）
            del mk
            return {"unlocked": False, "key_id": "", "locked_at": self._locked_at}

    # ---- 密钥消费（注册过的域，不外泄 MK 本体） ----
    def derive_domain_key(self, domain: str) -> bytes:
        """按 C 片语义派生分域 data key（media/attachment/...）。"""
        from ..storage.file_container import ALLOWED_DOMAINS, derive_domain_key

        if domain not in ALLOWED_DOMAINS:
            raise KeyBrokerError(f"未知数据域：{domain}")
        with self._lock:
            if self._mk is None:
                raise KeyBrokerError("已锁定或未解锁，无法派生数据密钥")
            return derive_domain_key(self._mk, domain)

    def database_key(self) -> bytes:
        """SQLCipher 用原始 key（C 片 connect_database 语义：32 字节 raw key）。"""
        with self._lock:
            if self._mk is None:
                raise KeyBrokerError("已锁定或未解锁，无法提供数据库密钥")
            return self._mk

    def key_id(self) -> str:
        with self._lock:
            if self._mk is None:
                raise KeyBrokerError("已锁定或未解锁")
            return self._key_id


_broker = _Broker()


def broker() -> _Broker:
    return _broker


def reset_for_testing() -> None:
    """测试隔离：重置单例到 inactive（清零 MK）。

    仅测试调用；生产代码没有理由重置（锁定用 lock()）。
    """
    _broker.lock()
    with _broker._lock:
        _broker._engaged = False
        _broker._locked_at = None


def slots_available(slot_root: Path) -> dict:
    """keyslots 面板信息：有哪些槽（不含密钥材料）。"""
    try:
        slots = describe_slots(slot_root)
    except KeySlotError:
        return {"initialized": False, "local_slot": False, "recovery_slot": False}
    return {
        "initialized": slots.any_slot,
        "local_slot": slots.local_slot,
        "recovery_slot": slots.recovery_slot,
    }
