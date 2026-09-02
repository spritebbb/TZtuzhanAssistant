# -*- coding: utf-8 -*-
"""P6 端到端 smoke test：启动 FastAPI 应用，验证关键路由真实可响应。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from backend.app import app


def main() -> None:
    client = TestClient(app)
    # 触发 startup（register_all + confirm hook 注册）
    with client:
        # 1) health
        r = client.get("/api/health")
        assert r.status_code == 200, f"health: {r.status_code}"
        print(f"[OK] /api/health: {r.json()}")

        # 2) meta 含完整工具清单
        r = client.get("/api/meta")
        d = r.json()
        assert d["ok"] and "tool_list" in d, "meta 应含 tool_list"
        n = len(d["tool_list"])
        assert n >= 34, f"工具数应≥34: {n}"
        print(f"[OK] /api/meta: {n} 个工具元数据")

        # 3) MCP /mcp/tools 含安全元数据
        r = client.get("/mcp/tools")
        d = r.json()
        tools = d["result"]["tools"]
        codex = next((t for t in tools if t["name"] == "codex_run"), None)
        assert codex and codex["needsConfirm"] is True and codex["dangerLevel"] == "high"
        print(f"[OK] /mcp/tools: {len(tools)} 个工具，含完整安全元数据")

        # 4) MCP 调用只读工具（system_info）
        r = client.post("/mcp/call", json={"name": "system_info", "arguments": {}})
        d = r.json()
        assert not d["result"]["isError"], f"system_info 应成功: {d}"
        print(f"[OK] /mcp/call system_info: {d['result']['content'][0]['text'][:40]!r}")

        # 5) 会话路由
        r = client.get("/api/sessions")
        assert r.status_code == 200
        print(f"[OK] /api/sessions: {r.status_code}")

        # 6) 确认接口（无 request_id 应 400）
        r = client.post("/api/confirm", data={})
        assert r.status_code == 400
        r2 = client.get("/api/confirm/pending")
        assert r2.json()["ok"]
        print("[OK] /api/confirm 路由 + pending 计数")

        # 7) 远程任务路由（空 task 应 400）
        r = client.post("/api/remote/task", data={})
        assert r.status_code == 400
        print("[OK] /api/remote/task 路由")

    print("\n=== P6 端到端 smoke: 7 项全部通过 ===")


main()
