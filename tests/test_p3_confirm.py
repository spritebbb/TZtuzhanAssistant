# -*- coding: utf-8 -*-
"""P3 确认机制单测：挂起/恢复/超时/拒绝。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.tools.base import ToolRegistry, CATEGORY_RUN, DANGER_HIGH
from backend.tools.confirm import ConfirmService, current_sse_push
from backend.models.tool import ToolSpec


def _spec() -> ToolSpec:
    return ToolSpec(name="run_command", description="t", input_schema={},
                    category=CATEGORY_RUN, danger_level=DANGER_HIGH, needs_confirm=True)


async def test_allow_flow() -> None:
    """用户允许：hook 返回 allow。"""
    events: list[dict] = []

    # 直接测 ConfirmService.request
    async def run() -> None:
        rid_box: dict = {}

        async def push(ev: dict) -> None:
            events.append(ev)
            rid_box["rid"] = ev["request_id"]

        task = asyncio.create_task(
            ConfirmService.request("run_command", {"command": "echo hi"}, _spec(), push=push, timeout=10)
        )
        # 等确认请求推出来
        for _ in range(50):
            if events:
                break
            await asyncio.sleep(0.01)
        assert events, "确认请求未推送"
        assert events[0]["type"] == "confirm_request"
        # 模拟用户允许
        ok = await ConfirmService.resolve(rid_box["rid"], True)
        assert ok
        decision = await task
        assert decision == "allow", decision

    await run()
    print("[OK] allow 流程：请求推送 → 用户允许 → 放行")


async def test_deny_flow() -> None:
    """用户拒绝：hook 返回 deny。"""
    events: list[dict] = []
    rid_box: dict = {}

    async def push(ev: dict) -> None:
        events.append(ev)
        rid_box["rid"] = ev["request_id"]

    task = asyncio.create_task(
        ConfirmService.request("run_command", {"command": "echo hi"}, _spec(), push=push, timeout=10)
    )
    for _ in range(50):
        if events:
            break
        await asyncio.sleep(0.01)
    ok = await ConfirmService.resolve(rid_box["rid"], False)
    assert ok
    decision = await task
    assert decision == "deny", decision
    print("[OK] deny 流程：用户拒绝 → 拒绝执行")


async def test_timeout_flow() -> None:
    """超时：自动按拒绝处理。"""
    events: list[dict] = []

    async def push(ev: dict) -> None:
        events.append(ev)

    task = asyncio.create_task(
        ConfirmService.request("run_command", {"command": "echo hi"}, _spec(), push=push, timeout=1)
    )
    decision = await task
    assert decision == "deny", decision
    print("[OK] 超时流程：超时自动拒绝")


async def test_default_hook_no_channel() -> None:
    """无 SSE 通道：写/命令/外部工具默认拒绝，只读放行。"""
    from backend.tools.confirm import default_confirm_hook

    current_sse_push.set(None)
    decision = await default_confirm_hook("run_command", {"command": "echo hi"}, _spec(), {})
    assert decision == "deny", decision
    # 只读工具（read 类）无通道时仍放行
    read_spec = ToolSpec(name="web_search", description="t", input_schema={},
                         category="read", danger_level="info", needs_confirm=False)
    decision_read = await default_confirm_hook("web_search", {}, read_spec, {})
    assert decision_read == "allow", decision_read
    print("[OK] 无通道：写/命令类拒绝、只读放行")


async def test_toolregistry_integration() -> None:
    """ToolRegistry.execute 集成：确认钩子挂到全局后，run_command 被拦截。"""
    from tests._helpers import load_all_tools

    load_all_tools()

    async def deny_hook(name, args, spec, ctx):
        return "deny"

    ToolRegistry.set_confirm_hook(deny_hook)
    r = await ToolRegistry.execute("run_command", {"command": "echo hi"})
    assert r.ok is False
    assert r.confirmed == "deny"
    print("[OK] 注册表集成：needs_confirm 工具被确认钩子拦截，confirmed=deny")
    ToolRegistry.set_confirm_hook(None)


async def main() -> None:
    await test_allow_flow()
    await test_deny_flow()
    await test_timeout_flow()
    await test_default_hook_no_channel()
    await test_toolregistry_integration()
    print("\n=== P3 确认机制: 5 项全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
