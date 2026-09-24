# -*- coding: utf-8 -*-
"""§24.3-3 权限中间态（限时免确认）回归。

覆盖六件事：
1. grace 生命周期：授予→命中→到期失效→撤销；重复授予刷新不叠加；
2. 高危永不豁免：grant 拒绝 high/critical，granted 判定处双保险再拦；
3. 上限封顶：超过 120 分钟按 120 计；
4. confirm hook 集成：grace 命中时不进 ConfirmService.request 直接放行；
5. resolve 带 grace_minutes：allow+grace 注册授权、deny 不注册、高危不注册；
6. API：POST /api/confirm 带 grace_minutes（form）；GET/DELETE /confirm/grace。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_grace_"))
os.environ.setdefault("MEMORY_V2", "0")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.api import confirm as confirm_api  # noqa: E402
from backend.tools import grace  # noqa: E402
from backend.tools.confirm import ConfirmService, default_confirm_hook  # noqa: E402


class _Spec:
    def __init__(self, danger="normal", category="read"):
        self.danger_level = danger
        self.category = category


def test_grace_lifecycle() -> None:
    grace.reset_for_testing()
    assert not grace.granted("web_search", "info")
    r = grace.grant("web_search", 30, danger_level="info")
    assert r["ok"] and r["minutes"] == 30
    assert grace.granted("web_search", "info")
    assert len(grace.list_grants()) == 1
    # 重复授予刷新（不叠加成 60）
    grace.grant("web_search", 30, danger_level="info")
    grants = grace.list_grants()
    assert len(grants) == 1 and grants[0]["remaining_min"] <= 30.01
    # 撤销
    assert grace.revoke_all() == 1
    assert not grace.granted("web_search", "info")
    print("[OK] 生命周期：授予/命中/刷新不叠加/撤销")


def test_high_danger_never_graceable() -> None:
    grace.reset_for_testing()
    for danger in ("high", "critical"):
        r = grace.grant("run_command", 30, danger_level=danger)
        assert r["ok"] is False and "不可" in r["error"], f"{danger} 应拒绝授予"
        assert not grace.granted("run_command", danger), "判定处双保险"
    # 注入残留授权后判定仍拦（双保险语义）
    grace._grants["run_command"] = __import__("time").time() + 999
    assert not grace.granted("run_command", "high")
    grace.reset_for_testing()
    print("[OK] 高危永不豁免：授予拒绝 + 判定双保险")


def test_cap_and_expiry() -> None:
    grace.reset_for_testing()
    r = grace.grant("read_file", 9999, danger_level="info")
    assert r["minutes"] == grace.GRACE_MAX_MINUTES, "超上限按 120 封顶"
    # 到期失效（注入过去的到期时间）
    grace._grants["read_file"] = __import__("time").time() - 1
    assert not grace.granted("read_file", "info")
    assert grace.list_grants() == [], "过期项被惰性清理"
    print("[OK] 上限封顶与到期失效")


def test_hook_skips_confirm_on_grace() -> None:
    grace.reset_for_testing()

    async def scenario():
        # 本机 .env 可能开着演示模式（hook 首位自动放行）：测试显式关闭，
        # 才能验证「无 grace 走确认 / 有 grace 免确认」的真实分支
        from backend.core.config import config

        with patch.object(config, "agent_demo_mode", False):
            # 未授权：有 push 时应挂起等确认（此处 push 会收到请求，我们直接 deny）
            async def push_deny(ev):
                await ConfirmService.resolve(ev["request_id"], False)

            from backend.tools.confirm import current_sse_push

            token = current_sse_push.set(push_deny)
            try:
                decision = await default_confirm_hook(
                    "web_search", {}, _Spec("info", "read"), {})
                assert decision == "deny", "无 grace 时应走确认流程（被 deny）"
            finally:
                current_sse_push.reset(token)

            # 授予 grace 后：不再进确认流程，直接 allow
            grace.grant("web_search", 30, danger_level="info")
            decision = await default_confirm_hook(
                "web_search", {}, _Spec("info", "read"), {})
            assert decision == "allow", "grace 命中应自动放行"

    asyncio.run(scenario())
    grace.reset_for_testing()
    print("[OK] hook 集成：无 grace 走确认、命中 grace 免确认放行")


def test_resolve_with_grace() -> None:
    grace.reset_for_testing()

    async def scenario():
        from backend.core.config import config

        with patch.object(config, "agent_demo_mode", False):
            # 允许 + grace_minutes → 注册授权
            async def fake_push(ev):
                await ConfirmService.resolve(ev["request_id"], True, grace_minutes=30)

            from backend.tools.confirm import current_sse_push

            token = current_sse_push.set(fake_push)
            try:
                decision = await default_confirm_hook(
                    "web_search", {}, _Spec("info", "read"), {})
                assert decision == "allow"
            finally:
                current_sse_push.reset(token)
            assert grace.granted("web_search", "info"), "resolve(allow, grace) 应注册授权"

            # 拒绝 + grace_minutes → 不注册
            async def push_deny(ev):
                await ConfirmService.resolve(ev["request_id"], False, grace_minutes=30)

            token = current_sse_push.set(push_deny)
            try:
                await default_confirm_hook("todo_create", {}, _Spec("info", "write"), {})
            finally:
                current_sse_push.reset(token)
            assert not grace.granted("todo_create", "info"), "deny 不得注册 grace"

            # 高危工具即便带 grace_minutes 也不注册
            async def push_allow_grace(ev):
                await ConfirmService.resolve(ev["request_id"], True, grace_minutes=30)

            token = current_sse_push.set(push_allow_grace)
            try:
                await default_confirm_hook("run_command", {}, _Spec("high", "run"), {})
            finally:
                current_sse_push.reset(token)
            assert not grace.granted("run_command", "high"), "高危不得注册 grace"

    asyncio.run(scenario())
    grace.reset_for_testing()
    print("[OK] resolve 带 grace：allow 注册、deny 不注册、高危不注册")


def test_api_endpoints() -> None:
    grace.reset_for_testing()
    app = FastAPI()
    app.include_router(confirm_api.router)
    client = TestClient(app)

    # HTTP 解析层：form 里的 grace_minutes 被正确提取并传给 resolve
    #（跨 loop 的真实挂起流程不可在 TestClient 里稳定复现，resolve 集成语义
    #  已由 test_resolve_with_grace 覆盖）
    captured: dict = {}

    async def fake_resolve(request_id, allow, grace_minutes=0):
        captured["args"] = (request_id, allow, grace_minutes)
        return True

    with patch.object(confirm_api.ConfirmService, "resolve", staticmethod(fake_resolve)):
        resp = client.post("/api/confirm", data={
            "request_id": "r1", "allow": "true", "grace_minutes": "30",
        })
    assert resp.json()["ok"] is True
    assert captured["args"] == ("r1", True, 30)

    with patch.object(confirm_api.ConfirmService, "resolve", staticmethod(fake_resolve)):
        client.post("/api/confirm", data={
            "request_id": "r2", "allow": "true", "grace_minutes": "abc",
        })
    assert captured["args"] == ("r2", True, 0), "非法 grace_minutes 安全默认 0"

    # 管理端点走真实 grace 注册表
    grace.grant("web_search", 30, danger_level="info")
    grants = client.get("/api/confirm/grace").json()
    assert grants["ok"] and any(g["tool"] == "web_search" for g in grants["grants"])
    revoked = client.delete("/api/confirm/grace").json()
    assert revoked["ok"] and revoked["revoked"] == 1
    assert client.get("/api/confirm/grace").json()["grants"] == []
    grace.reset_for_testing()
    print("[OK] API：form 带 grace_minutes / 列表 / 撤销 / 非法值安全默认")


def main() -> None:
    test_grace_lifecycle()
    test_high_danger_never_graceable()
    test_cap_and_expiry()
    test_hook_skips_confirm_on_grace()
    test_resolve_with_grace()
    test_api_endpoints()
    print("\n=== §24.3-3 权限中间态：6 组全部通过 ===")


if __name__ == "__main__":
    main()
