# -*- coding: utf-8 -*-
"""MCP 工具按需注入：默认隐藏，命中触发条件才暴露；本地工具永远可见。

验收锚点：
- 未注册 MCP 服务器 / AGENT_MCP_ALWAYS_ON=1 → 返回 None（不过滤，零开销）；
- 用户消息或技能正文命中「服务器名/关键词/工具名」→ 该服务器工具可见；
- 未命中 → MCP 工具被过滤掉，本地工具不受影响；
- 隐藏的工具即使被模型猜名字调用，也返回结构化拒绝而不是执行。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_lazy_"))

from backend.core.config import config
from backend.tools import mcp_server
from backend.tools.base import ToolRegistry
from backend.tools.tool_loop import _execute_calls


def _fake_server() -> None:
    mcp_server._EXTERNAL_SERVERS["pw"] = {
        "name": "pw", "url": "http://127.0.0.1:1/mcp", "tools": 2,
        "keywords": ["浏览器", "browser"],
    }
    for short in ("browser_navigate", "browser_snapshot"):
        ToolRegistry.register_func(
            name=f"pw::{short}", description="远程工具",
            func=_noop, category="external", needs_confirm=True, owner="mcp:pw",
        )


async def _noop(**kwargs):
    return "executed"


def _teardown() -> None:
    for name in list(ToolRegistry.tool_names()):
        if name.startswith("pw::"):
            ToolRegistry.unregister(name)
    mcp_server._EXTERNAL_SERVERS.pop("pw", None)


def test_no_server_or_always_on() -> int:
    old = dict(mcp_server._EXTERNAL_SERVERS)
    mcp_server._EXTERNAL_SERVERS.clear()
    assert mcp_server.mcp_tool_filter("随便说说", []) is None
    mcp_server._EXTERNAL_SERVERS.update(old)
    _fake_server()
    try:
        config.agent_mcp_always_on = True
        assert mcp_server.mcp_tool_filter("随便说说", []) is None, "always-on 不过滤"
        config.agent_mcp_always_on = False
    finally:
        config.agent_mcp_always_on = False
        _teardown()
    print("[OK] 无服务器 / always-on → 不过滤")
    return 0


def test_hit_and_miss() -> int:
    _fake_server()
    try:
        # 未命中：MCP 工具被隐藏，本地工具仍在
        miss = mcp_server.mcp_tool_filter("今天晚饭吃什么", [])
        assert miss is not None
        assert miss(ToolRegistry.get("pw::browser_navigate")) is False
        assert miss(ToolRegistry.get("memory_add")) is True
        # 关键词命中
        hit_kw = mcp_server.mcp_tool_filter("用浏览器帮我打开那个页面", [])
        assert hit_kw(ToolRegistry.get("pw::browser_navigate")) is True
        # 工具名命中（ASCII 词边界，中文紧邻也命中）
        hit_tool = mcp_server.mcp_tool_filter("调用browser_snapshot看看", [])
        assert hit_tool(ToolRegistry.get("pw::browser_snapshot")) is True
        # 技能正文命中
        hit_skill = mcp_server.mcp_tool_filter("帮我看看", ["【技能：网页分析】用 browser 抓取页面"])
        assert hit_skill(ToolRegistry.get("pw::browser_navigate")) is True
        # 服务器名命中
        hit_name = mcp_server.mcp_tool_filter("让 pw 去处理", [])
        assert hit_name(ToolRegistry.get("pw::browser_navigate")) is True
    finally:
        _teardown()
    print("[OK] 关键词/工具名/技能正文/服务器名四种命中 + 未命中隐藏")
    return 0


def test_hidden_tool_not_executable() -> int:
    _fake_server()
    try:
        miss = mcp_server.mcp_tool_filter("今天晚饭吃什么", [])
        block, executed = asyncio.run(_execute_calls(
            [{"name": "pw::browser_navigate", "arguments": {"url": "x"}}],
            tool_filter=miss,
        ))
        assert executed == 0, "隐藏工具不应被执行"
        assert "[工具错误 permission]" in block, block
        # 命中时正常执行
        hit = mcp_server.mcp_tool_filter("用浏览器打开", [])
        block2, executed2 = asyncio.run(_execute_calls(
            [{"name": "pw::browser_navigate", "arguments": {"url": "x"}}],
            tool_filter=hit,
        ))
        assert executed2 == 1 and "executed" in block2, block2
    finally:
        _teardown()
    print("[OK] 隐藏工具拒绝执行 / 命中后正常执行")
    return 0


def test_pinned_https_handler_no_attribute_error() -> int:
    """回归：Python 3.12 的 HTTPSHandler 不设 _check_hostname。

    修复前 build_pinned_opener 的 HTTPS 分支直接引用 self._check_hostname，
    导致所有 HTTPS 出网（web_fetch / 公网 MCP）AttributeError。这里用必然
    连接失败的地址验证：抛的是 URLError（连不上），而不是 AttributeError。
    """
    import urllib.error
    import urllib.request

    from backend.tools.safety import build_pinned_opener

    opener = build_pinned_opener("127.0.0.1")
    req = urllib.request.Request("https://example.com/", method="GET")
    try:
        opener.open(req, timeout=3)
        raise AssertionError("本机 443 不应可达（测试前提被破坏）")
    except urllib.error.URLError:
        pass   # 期望：网络层失败，说明 handler 本身工作正常
    except AttributeError as exc:
        raise AssertionError(f"HTTPS pinning 仍然 AttributeError：{exc}") from exc
    print("[OK] HTTPS pinning 不再 AttributeError")
    return 0


def test_keywords_persist_roundtrip() -> int:
    """关键词要随登记表持久化（重启后按需注入仍生效）。"""
    import json

    from backend.tools import mcp_server as ms

    ms._EXTERNAL_SERVERS.clear()
    ms._EXTERNAL_SERVERS["kwsvc"] = {
        "name": "kwsvc", "url": "http://127.0.0.1:9/mcp", "tools": 0,
        "keywords": ["关键词A"],
    }
    ms._persist_servers()
    raw = json.loads(ms._PERSIST_PATH.read_text(encoding="utf-8"))
    assert raw and raw[0]["keywords"] == ["关键词A"], raw
    loaded = ms._load_persisted()
    assert loaded[0]["keywords"] == ["关键词A"], loaded
    ms._EXTERNAL_SERVERS.clear()
    print("[OK] keywords 持久化往返")
    return 0


def main() -> int:
    failed = (
        test_no_server_or_always_on()
        + test_hit_and_miss()
        + test_hidden_tool_not_executable()
        + test_pinned_https_handler_no_attribute_error()
        + test_keywords_persist_roundtrip()
    )
    if failed:
        print(f"\n=== MCP 按需注入：{failed} 项失败 ===")
        return 1
    print("\n=== MCP 按需注入：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
