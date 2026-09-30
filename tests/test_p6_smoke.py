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

        # 3) MCP /mcp/tools 含安全元数据（external 桥已删，代表换 agent_run）
        r = client.get("/mcp/tools")
        d = r.json()
        tools = d["result"]["tools"]
        agent = next((t for t in tools if t["name"] == "agent_run"), None)
        assert agent and agent["needsConfirm"] is True
        assert not any(t["name"] in ("codex_run", "dsh_run") for t in tools)
        print(f"[OK] /mcp/tools: {len(tools)} 个工具，含完整安全元数据")

        # 4) MCP 调用只读工具（system_info）
        r = client.post("/mcp/call", json={"name": "system_info", "arguments": {}})
        d = r.json()
        assert not d["result"]["isError"], f"system_info 应成功: {d}"
        print(f"[OK] /mcp/call system_info: {d['result']['content'][0]['text'][:40]!r}")

        # 5) 会话路由（单一会话模式：读取固定会话 'current'）
        r = client.get("/api/sessions/current")
        assert r.status_code == 200, f"/api/sessions/current: {r.status_code}"
        print(f"[OK] /api/sessions/current: {r.status_code}")

        # 6) 确认接口（无 request_id 应 400）
        r = client.post("/api/confirm", data={})
        assert r.status_code == 400
        r2 = client.get("/api/confirm/pending")
        assert r2.json()["ok"]
        print("[OK] /api/confirm 路由 + pending 计数")

        # 7) 远程任务路由按需注册：未配 AGENT_REMOTE_TOKEN → 404（不挂载）；
        #    配了 token → 空 task 应 400（参数校验）
        from backend.core.config import config as _cfg
        r = client.post("/api/remote/task", data={})
        if _cfg.agent_remote_token:
            assert r.status_code == 400
            print("[OK] /api/remote/task 路由（token 已配置）")
        else:
            # 未挂载时 POST 落到前端静态 catch-all → 405；两者都算「端点不存在」
            assert r.status_code in (404, 405), \
                f"未配 token 时 /api/remote/* 应按需不注册(404/405)，实得 {r.status_code}"
            print("[OK] /api/remote/task 未挂载（按需注册，未配 token）")

    print("\n=== P6 端到端 smoke: 7 项全部通过 ===")


main()
