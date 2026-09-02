# -*- coding: utf-8 -*-
"""MCP 外部服务器持久化回归测试：注册落盘、恢复重连、卸载同步。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio

from backend.tools import mcp_server as m
from backend.tools.mcp_server import (
    _PERSIST_PATH,
    _EXTERNAL_SERVERS,
    _load_persisted,
    list_external_servers,
    register_external_server,
    restore_persisted_servers,
    unregister_external_server,
)


def test_persist_roundtrip():
    """注册落盘 → 清内存 → 从盘恢复登记。"""
    # 伪造一个外部服务器登记（不真正连网），直接写入内存表并落盘
    _EXTERNAL_SERVERS["fake-server"] = {
        "name": "fake-server", "url": "http://127.0.0.1:9999", "tools": 2
    }
    m._persist_servers()
    assert _PERSIST_PATH.exists(), "持久化文件应已创建"

    # 清空内存（模拟重启）
    _EXTERNAL_SERVERS.clear()
    saved = _load_persisted()
    assert any(s["name"] == "fake-server" for s in saved), f"应能从盘恢复: {saved}"
    print(f"[OK] 注册落盘 + 重启后可读回登记: {saved}")

    # 卸载时同步清理持久化
    _EXTERNAL_SERVERS["fake-server"] = {"name": "fake-server", "url": "http://127.0.0.1:9999", "tools": 2}
    ok = unregister_external_server("fake-server")
    assert ok, "卸载应成功"
    assert "fake-server" not in _EXTERNAL_SERVERS
    saved = _load_persisted()
    assert not any(s["name"] == "fake-server" for s in saved), "卸载后盘上不应再有记录"
    print("[OK] 卸载同步移除持久化记录")


async def test_restore_skips_dead_server():
    """恢复：连不上的服务器保留登记但不崩溃，返回 0。"""
    # 伪造一个无法连接的登记
    _EXTERNAL_SERVERS["dead-server"] = {
        "name": "dead-server", "url": "http://127.0.0.1:1", "tools": 0
    }
    m._persist_servers()
    _EXTERNAL_SERVERS.clear()
    n = await restore_persisted_servers()
    assert n == 0, f"死服务器恢复数应为 0，实际 {n}"
    # 恢复失败不应崩溃，登记仍保留（等待下次启动）
    saved = _load_persisted()
    assert any(s["name"] == "dead-server" for s in saved), "死服务器登记应保留"
    _EXTERNAL_SERVERS.clear()
    m._persist_servers()
    print("[OK] 死服务器恢复不崩溃、登记保留")


def main():
    test_persist_roundtrip()
    asyncio.run(test_restore_skips_dead_server())
    print("\n=== MCP 持久化回归: 全部通过 ===")


main()
