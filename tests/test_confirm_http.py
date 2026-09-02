# -*- coding: utf-8 -*-
"""确认接口 HTTP 层回归测试：POST /api/confirm 必须接受 form body（前端方式）。

历史 bug：端点用普通类型参数（request_id: str = ""）声明，FastAPI 对 POST
端点的普通参数只从 query 绑定；而前端 ConfirmPanel/AgentPanel 把参数放在
form body 里发送 → request_id 恒为空 → 恒 400 → 前端点了没反应，
只能等 60s 超时自动拒绝。

运行：python -m tests.test_confirm_http
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.confirm import router
from backend.models.tool import ToolSpec
from backend.tools.confirm import ConfirmService

app = FastAPI()
app.include_router(router)


def _spec() -> ToolSpec:
    return ToolSpec(name="run_command", description="t", input_schema={},
                    category="run", danger_level="high", needs_confirm=True)


async def test_form_body_resolve() -> None:
    """前端方式（form body）能 resolve 真实挂起的确认请求。"""
    events: list[dict] = []

    async def push(ev: dict) -> None:
        events.append(ev)

    task = asyncio.create_task(
        ConfirmService.request("run_command", {"command": "echo hi"}, _spec(),
                               push=push, timeout=10))
    for _ in range(100):
        if events:
            break
        await asyncio.sleep(0.01)
    assert events, "confirm request not pushed"
    rid = events[0]["request_id"]

    with TestClient(app) as client:
        r = client.post(
            "/api/confirm",
            content=f"request_id={rid}&allow=true",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True, "allow": True}, r.json()

    decision = await task
    assert decision == "allow", decision
    print("[OK] form body (frontend style) resolves pending confirm -> allow")


async def test_query_resolve() -> None:
    """query 方式仍然兼容。"""
    events: list[dict] = []

    async def push(ev: dict) -> None:
        events.append(ev)

    task = asyncio.create_task(
        ConfirmService.request("run_command", {"command": "echo hi"}, _spec(),
                               push=push, timeout=10))
    for _ in range(100):
        if events:
            break
        await asyncio.sleep(0.01)
    rid = events[0]["request_id"]

    with TestClient(app) as client:
        r = client.post(f"/api/confirm?request_id={rid}&allow=false")
        assert r.status_code == 200, r.text

    decision = await task
    assert decision == "deny", decision
    print("[OK] query params still work -> deny")


async def test_json_resolve() -> None:
    """JSON body 方式兼容。"""
    events: list[dict] = []

    async def push(ev: dict) -> None:
        events.append(ev)

    task = asyncio.create_task(
        ConfirmService.request("run_command", {"command": "echo hi"}, _spec(),
                               push=push, timeout=10))
    for _ in range(100):
        if events:
            break
        await asyncio.sleep(0.01)
    rid = events[0]["request_id"]

    with TestClient(app) as client:
        r = client.post("/api/confirm", json={"request_id": rid, "allow": True})
        assert r.status_code == 200, r.text

    decision = await task
    assert decision == "allow", decision
    print("[OK] json body resolves pending confirm -> allow")


async def test_missing_request_id() -> None:
    """缺少 request_id 返回 400。"""
    with TestClient(app) as client:
        r = client.post("/api/confirm", content="allow=true",
                        headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert r.status_code == 400, r.text
    print("[OK] missing request_id -> 400")


async def main() -> None:
    await test_form_body_resolve()
    await test_query_resolve()
    await test_json_resolve()
    await test_missing_request_id()
    print("\n=== confirm HTTP binding: 4 tests passed ===")


if __name__ == "__main__":
    asyncio.run(main())
