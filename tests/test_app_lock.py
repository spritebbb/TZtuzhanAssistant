# -*- coding: utf-8 -*-
"""P3-04 D 片：key broker 与应用锁 HTTP 回归（隔离数据目录 + broker 重置）。

覆盖：三态（inactive/unlocked/locked）、inactive 不拦任何请求（现状零变化）、
锁定全站 423 白名单例外、两种解锁、恢复口令限速、重复解锁拒绝、
锁定后派生/取 key 全部失败（忘钥匙语义）、重启（新进程语义用 engage_if_slots
模拟）后进入锁定态。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_applock_"))
os.environ.setdefault("MEMORY_V2", "0")

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import create_app  # noqa: E402
from backend.core.config import config  # noqa: E402
from backend.core.key_broker import KeyBrokerError, broker, reset_for_testing  # noqa: E402

PASS = "app-lock 测试口令"


def _reset() -> None:
    reset_for_testing()


def test_tri_state_inactive_does_not_gate() -> None:
    """未初始化密钥槽：锁功能 inactive，一切照旧（现有部署零行为变化）。"""
    _reset()
    with TestClient(create_app()) as client:
        assert client.get("/api/meta").status_code == 200
        st = client.get("/api/lock").json()
        assert st["state"] == "inactive" and st["unlocked"] is False
        assert st["data_encrypted"] is False, "E 片前必须如实声明数据未加密"
    print("[OK] inactive 态不拦任何请求")


def test_lock_then_gate_then_unlock_local() -> None:
    _reset()
    with TestClient(create_app()) as client:
        r = client.post("/api/lock/initialize",
                        json={"passphrase": PASS, "passphrase_repeat": PASS})
        assert r.status_code == 200, r.text
        assert client.get("/api/meta").status_code == 200  # 初始化后保持解锁

        assert client.post("/api/lock").json()["ok"]
        # 全站上锁：API/插件/MCP/人格资源一律 423
        for path in ("/api/meta", "/api/memory/facts", "/persona/full/happy"):
            assert client.get(path).status_code == 423, path
        # 白名单例外：锁面板自身与健康检查
        assert client.get("/api/lock").status_code == 200
        assert client.get("/api/health").status_code == 200

        # 本机槽解锁（一键，无口令）
        r = client.post("/api/lock/unlock", json={"method": "local"})
        assert r.status_code == 200, r.text
        assert client.get("/api/meta").status_code == 200
    print("[OK] 初始化→锁定→423 全站→白名单例外→本机槽解锁")


def test_recovery_unlock_wrong_password_and_ratelimit() -> None:
    _reset()
    with TestClient(create_app()) as client:
        client.post("/api/lock/initialize",
                    json={"passphrase": PASS, "passphrase_repeat": PASS})
        client.post("/api/lock")
        # 错误口令统一 401（不区分口令错/槽坏）
        r = client.post("/api/lock/unlock",
                        json={"method": "recovery", "passphrase": "不对"})
        assert r.status_code == 401
        assert "口令" in r.json()["error"]
        # 连续失败限速：窗口内第 6 次 → 429
        for _ in range(4):
            client.post("/api/lock/unlock",
                        json={"method": "recovery", "passphrase": "不对"})
        r = client.post("/api/lock/unlock",
                        json={"method": "recovery", "passphrase": PASS})
        assert r.status_code == 429, f"正确口令在限速窗口内也应被拒: {r.status_code}"
        # 窗口推进后恢复（缩短等待：直接清失败记录模拟超窗）
        from backend.api import lock as lock_api

        lock_api._fail_times.clear()
        r = client.post("/api/lock/unlock",
                        json={"method": "recovery", "passphrase": PASS})
        assert r.status_code == 200
        assert client.get("/api/meta").status_code == 200
    print("[OK] 恢复口令：统一 401 + 限速 429 + 超窗恢复")


def test_lock_forgets_key_and_rejects_consumers() -> None:
    """锁定 = 忘钥匙：broker 对一切取 key 请求失败。"""
    _reset()
    from backend.core.key_broker import broker as b

    b().install_for_testing(bytes(range(32)))
    assert b().derive_domain_key("media") and b().database_key()
    b().lock()
    for fn in (lambda: b().derive_domain_key("media"),
               lambda: b().database_key(),
               lambda: b().key_id()):
        try:
            fn()
            raise AssertionError("锁定后取 key 不应成功")
        except KeyBrokerError:
            pass
    # 已解锁态重复 install → 拒绝；锁定幂等
    b().install_for_testing(bytes(range(32)))
    try:
        b().install_for_testing(bytes(range(32)))
        raise AssertionError("重复解锁被接受")
    except KeyBrokerError:
        pass
    assert b().lock()["unlocked"] is False  # 再锁也 OK
    print("[OK] broker 忘钥匙语义与状态机")


def test_restart_with_slots_boots_locked() -> None:
    """重启语义：槽存在 → engage_if_slots 进入 locked（用重置模拟新进程）。"""
    _reset()
    with TestClient(create_app()) as client:
        client.post("/api/lock/initialize",
                    json={"passphrase": PASS, "passphrase_repeat": PASS})
    # 模拟新进程：单例重置后首个请求触发 engage_if_slots
    reset_for_testing()
    with TestClient(create_app()) as client:
        st = client.get("/api/lock").json()
        assert st["state"] == "locked", st
        assert st["slots"]["initialized"] is True
        assert client.get("/api/meta").status_code == 423
        assert client.post("/api/lock/unlock", json={"method": "local"}).status_code == 200
        assert client.get("/api/meta").status_code == 200
    print("[OK] 重启后以锁定态启动，本机槽解锁恢复")


def test_initialize_validations() -> None:
    _reset()
    # 换干净数据目录：前面用例已初始化过槽，会命中 409 而不是参数校验
    from unittest.mock import patch

    fresh = Path(tempfile.mkdtemp(prefix="applock_fresh_"))
    with patch.multiple(config, data_dir=fresh):
        with TestClient(create_app()) as client:
            # 口令不一致
            r = client.post("/api/lock/initialize",
                            json={"passphrase": "a", "passphrase_repeat": "b"})
            assert r.status_code == 400, (r.status_code, r.text)
            # 未知解锁方式 / 缺恢复口令（这两个不依赖槽状态）
            r = client.post("/api/lock/unlock", json={"method": "magic"})
            assert r.status_code == 400
            r = client.post("/api/lock/unlock", json={"method": "recovery"})
            assert r.status_code == 400
    _reset()
    print("[OK] 初始化/解锁参数校验")


def main() -> None:
    test_tri_state_inactive_does_not_gate()
    test_lock_then_gate_then_unlock_local()
    test_recovery_unlock_wrong_password_and_ratelimit()
    test_lock_forgets_key_and_rejects_consumers()
    test_restart_with_slots_boots_locked()
    test_initialize_validations()
    _reset()
    print("\n=== P3-04 D 应用锁与 key broker：全部通过 ===")


if __name__ == "__main__":
    main()
