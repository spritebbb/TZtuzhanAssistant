# -*- coding: utf-8 -*-
"""HTTP 层端点测试：按前端真实调用方式打端点，消除"服务层绿灯、端点实际坏掉"的盲区。

背景（P1-0 教训）：/api/confirm 用普通类型参数声明（只从 query 绑定），
而前端 ConfirmPanel 用 form body 发送 → 恒 400，确认卡片永远点不动。
服务层测试（直接调 ConfirmService）全绿，HTTP 层零覆盖导致漏网。

本文件用 TestClient 复刻三个前端组件的真实请求形态：
- ConfirmPanel.vue：POST /api/confirm，form body（request_id/allow）
- ChatView.vue：POST /api/chat，form body（text/session_id），SSE 帧契约
- AgentPanel.vue：POST /api/agent/tasks 用 form body（objective/user_id）、
  confirm-step/confirm-all 用 query 参数、run/cancel 无 body、GET stream 收 SSE

覆盖面：SSE 帧契约（session_id/piece/confirm_request/reset/image_url/done/error）、
消息持久化、Origin 守卫矩阵（跨站/null Origin 拦截，可信来源放行）、
Agent 步骤确认门禁（pending 不执行 → confirm-all → done）。

运行：python -m tests.test_http_endpoints（或经 pytest tests/ 由套件运行器执行）
"""
import asyncio
import json
import os
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 测试环境收敛：embedding 走哈希（不下载模型）、关 Mem0/画像后台、关搜索与天气
# （必须在首次导入 backend.core.config 之前设置）
os.environ.setdefault("MEMORY_EMBED_FORCE", "1")
os.environ.setdefault("MEMORY_MEM0", "0")
os.environ.setdefault("MEMORY_V2", "0")
os.environ.setdefault("MOOD_CITY", "")
os.environ.setdefault("SEARCH_ENABLED", "0")

from fastapi.testclient import TestClient

from backend.app import app
from backend.core.config import config

# 双保险：config 单例若已被更早的导入创建，直接压属性保证测试确定性
config.mood_city = ""
config.search_enabled = False
config.memory_v2 = False
config.memory_mem0 = False
config.memory_embed_force = True

AGENT_USER = "test-http-agent"

# Electron 生产前端（同源 http://127.0.0.1:8801）的真实请求头
FRONTEND_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "http://127.0.0.1:8801",
    "Sec-Fetch-Site": "same-origin",
}


def parse_sse(text: str) -> list[dict]:
    """SSE 响应体 → 帧列表（data: {json}）。"""
    frames = []
    for chunk in text.split("\n\n"):
        chunk = chunk.strip()
        if chunk.startswith("data: "):
            frames.append(json.loads(chunk[6:]))
    return frames


def form_body(**fields: str) -> str:
    """按浏览器 URLSearchParams 的方式构造 form body（非 ASCII 百分号编码）。"""
    return urllib.parse.urlencode(fields)


# ---- /api/chat SSE ----

def test_chat_sse_frame_contract() -> None:
    """复刻前端 streamChat 的完整帧契约：session_id → confirm_request →
    piece → reset → image_url → piece → done；并验证确认推送器已注入
    SSE 上下文（current_sse_push 接线）。"""
    import backend.api.chat as chat_mod

    orig_process = chat_mod.process

    async def fake_process(user_id, text, *, mock=False, merged_msg=False,
                           stream_cb=None, image_cb=None):
        from backend.tools.confirm import current_sse_push

        push = current_sse_push.get()
        assert push is not None, "确认推送器未注入 SSE 上下文（current_sse_push 断线）"
        # 工具确认请求：走真实推送链路（q → SSE confirm_request 帧）
        await push({"type": "confirm_request", "request_id": "req-x",
                    "tool": "dsh_run", "args": {"task": "x"}, "danger": "high",
                    "message": "要派发外部任务", "timeout": 60})
        await stream_cb("你")
        await stream_cb("\x00RESET\x00")   # 重复回复重写：前端清空气泡
        await image_cb("data/imgs/x.png")  # 生图完成：前端渲染 <img>
        await stream_cb("好")
        return "最终回复"

    chat_mod.process = fake_process
    try:
        with TestClient(app) as client:
            r = client.post("/api/chat", content=form_body(text="你好"),
                            headers=FRONTEND_HEADERS)
            assert r.status_code == 200, r.text
            frames = parse_sse(r.text)
            keys = []
            for f in frames:
                keys.extend(f.keys())
            assert keys == ["session_id", "confirm_request", "piece", "reset",
                            "image_url", "piece", "done"], keys
            sid = frames[0]["session_id"]
            assert frames[1]["confirm_request"]["request_id"] == "req-x"
            assert frames[2]["piece"] == "你"
            assert frames[3] == {"reset": True}
            assert frames[4]["image_url"] == "/api/images/x.png"
            assert frames[5]["piece"] == "好"
            assert frames[6]["done"] == "最终回复"

            # done 之后消息已持久化（user + bot）
            msgs = client.get(f"/api/sessions/{sid}").json()
            roles = [m["role"] for m in msgs]
            assert roles == ["user", "bot"], msgs
            assert msgs[0]["content"] == "你好"
            assert msgs[1]["content"] == "最终回复"
            # 清理测试会话（顺带验证 DELETE 端点）
            assert client.delete(f"/api/sessions/{sid}").json()["ok"] is True
            assert client.get(f"/api/sessions/{sid}").status_code == 404
        print("[OK] chat SSE：帧契约 + current_sse_push 接线 + 消息持久化 + 会话删除")
    finally:
        chat_mod.process = orig_process


def test_chat_sse_error_frame() -> None:
    """process 抛异常 → SSE error 帧（前端 onError 显示）。"""
    import backend.api.chat as chat_mod

    orig_process = chat_mod.process

    async def fake_process(user_id, text, **kw):
        raise RuntimeError("LLM 炸了")

    chat_mod.process = fake_process
    try:
        with TestClient(app) as client:
            r = client.post("/api/chat", content="text=触发异常",
                            headers=FRONTEND_HEADERS)
            frames = parse_sse(r.text)
            assert frames[-1].get("error") is not None, frames
            assert "RuntimeError" in frames[-1]["error"]
        print("[OK] chat SSE：process 异常 → error 帧")
    finally:
        chat_mod.process = orig_process


def test_chat_validation() -> None:
    """空文本 400；不存在会话 404（JSON 而非 SSE）。"""
    with TestClient(app) as client:
        r = client.post("/api/chat", content=form_body(text=""),
                        headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert r.status_code == 400, r.text
        r2 = client.post("/api/chat", content=form_body(text="你好", session_id="no-such-session"),
                         headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert r2.status_code == 404, r2.text
        assert r2.json()["ok"] is False
    print("[OK] chat 校验：空文本 400 / 未知会话 404")


# ---- /api/agent/*（AgentPanel.vue 真实调用形态）----

def _cleanup_agent() -> None:
    from backend.agent import session as agent_session

    agent_session._connect().execute(
        "DELETE FROM agent_tasks WHERE user_id=?", (AGENT_USER,)
    ).connection.commit()


def _mock_plan() -> None:
    from backend.agent import session as agent_session

    async def fake_chat(messages, **kw):
        return ('[{"title": "打开记事本", "detail": "用 open_app 打开 notepad"},'
                ' {"title": "截图", "detail": "用 screenshot 截图"}]')

    agent_session.chat = fake_chat


def test_agent_create_form_body() -> None:
    """前端 AgentPanel.createTask 的真实方式：form body 发 objective/user_id。
    历史 bug：后端用普通类型参数（只从 query 绑定）→ form 传参被忽略 → 恒 400。"""
    _mock_plan()
    try:
        with TestClient(app) as client:
            r = client.post("/api/agent/tasks",
                            content=form_body(objective="帮我查系统信息", user_id=AGENT_USER),
                            headers=FRONTEND_HEADERS)
            assert r.status_code == 200, r.text
            d = r.json()
            assert d["ok"] is True, d
            assert len(d["task"]["plan"]) == 2, d["task"]["plan"]
            assert d["task"]["status"] == "planned"
        print("[OK] agent create：form body（前端方式）创建成功，计划 2 步")
    finally:
        _cleanup_agent()


def test_agent_create_json_and_query_compat() -> None:
    """JSON body 与 query 传参也兼容；缺 objective → 400。"""
    _mock_plan()
    try:
        with TestClient(app) as client:
            r = client.post("/api/agent/tasks", json={"objective": "查天气",
                                                      "user_id": AGENT_USER})
            assert r.status_code == 200 and r.json()["ok"], r.text
            r2 = client.post(f"/api/agent/tasks?objective=整理文件&user_id={AGENT_USER}")
            assert r2.status_code == 200 and r2.json()["ok"], r2.text
            r3 = client.post("/api/agent/tasks", content=form_body(user_id="x"),
                             headers=FRONTEND_HEADERS)
            assert r3.status_code == 400, r3.text
        print("[OK] agent create：JSON/query 兼容，缺 objective 400")
    finally:
        _cleanup_agent()


def test_agent_full_flow_gate_to_done() -> None:
    """完整链路：pending 门禁 → confirm-step（query）→ confirm-all → run →
    done → SSE stream 收 task_done 帧。"""
    from backend.agent import session as agent_session
    from backend.tools import tool_loop

    _mock_plan()
    orig_loop = tool_loop.run_tool_loop

    async def fake_loop(messages, call_llm, *, max_loops=2, mock=False,
                        final_instruction=None, call_native=None):
        return "任务完成：已查完。"

    tool_loop.run_tool_loop = fake_loop
    try:
        with TestClient(app) as client:
            # 创建（form body，前端方式）
            r = client.post("/api/agent/tasks",
                            content=form_body(objective="查系统信息", user_id=AGENT_USER),
                            headers=FRONTEND_HEADERS)
            tid = r.json()["task"]["id"]

            # 1) 全 pending：run 触发但门禁拦下，状态回 planned
            r2 = client.post(f"/api/agent/tasks/{tid}/run")
            assert r2.status_code == 200 and r2.json()["status"] == "running", r2.text
            for _ in range(50):
                t = client.get(f"/api/agent/tasks/{tid}").json()["task"]
                if t["status"] != "running":
                    break
                time.sleep(0.1)
            assert t["status"] == "planned", t
            assert "等待步骤确认" in (t["result"] or ""), t["result"]

            # 2) confirm-step（query 参数，前端方式）：允许第 0 步
            r3 = client.post(f"/api/agent/tasks/{tid}/confirm-step?step_index=0&allow=true")
            assert r3.status_code == 200, r3.text
            t = client.get(f"/api/agent/tasks/{tid}").json()["task"]
            assert t["step_confirmations"]["0"] == "allowed", t["step_confirmations"]

            # 3) 越界步骤 → 400
            r4 = client.post(f"/api/agent/tasks/{tid}/confirm-step?step_index=99")
            assert r4.status_code == 400, r4.text

            # 4) confirm-all（query）→ run → done
            r5 = client.post(f"/api/agent/tasks/{tid}/confirm-all?allow=true")
            assert r5.status_code == 200, r5.text
            r6 = client.post(f"/api/agent/tasks/{tid}/run")
            assert r6.status_code == 200, r6.text
            for _ in range(100):
                t = client.get(f"/api/agent/tasks/{tid}").json()["task"]
                if t["status"] == "done":
                    break
                time.sleep(0.1)
            assert t["status"] == "done", t
            assert t["result"] == "任务完成：已查完。", t["result"]

            # 5) SSE stream：任务结束后迟到连接补看 task_done 帧
            r7 = client.get(f"/api/agent/tasks/{tid}/stream")
            frames = parse_sse(r7.text)
            assert any(f.get("type") == "task_done" for f in frames), frames

            # 6) 任务列表包含该任务
            lst = client.get(f"/api/agent/tasks?user_id={AGENT_USER}").json()
            assert any(x["id"] == tid for x in lst["tasks"]), lst
        print("[OK] agent 全链路：门禁 → confirm-step/all → run → done → stream 补看")
    finally:
        tool_loop.run_tool_loop = orig_loop
        _cleanup_agent()


def test_agent_cancel_and_404() -> None:
    """cancel planned 任务返回 ok；不存在任务 404。"""
    _mock_plan()
    try:
        with TestClient(app) as client:
            r = client.post("/api/agent/tasks", json={"objective": "x", "user_id": AGENT_USER})
            tid = r.json()["task"]["id"]
            rc = client.post(f"/api/agent/tasks/{tid}/cancel")
            assert rc.status_code == 200 and rc.json()["ok"], rc.text
            r404 = client.get("/api/agent/tasks/no-such-task")
            assert r404.status_code == 404, r404.text
            r404b = client.post("/api/agent/tasks/no-such-task/run")
            assert r404b.status_code == 404, r404b.text
            r404c = client.post("/api/agent/tasks/no-such-task/cancel")
            assert r404c.status_code == 404, r404c.text
        print("[OK] agent cancel / 404 语义")
    finally:
        _cleanup_agent()


# ---- /api/confirm（ConfirmPanel.vue 真实方式，含 Origin 守卫交互）----

async def _pend_and_resolve(client, allow: str) -> tuple[int, str]:
    """造一个真实挂起的确认请求，再用前端方式（form body）resolve。"""
    from backend.models.tool import ToolSpec
    from backend.tools.confirm import ConfirmService

    events: list[dict] = []

    async def push(ev: dict) -> None:
        events.append(ev)

    spec = ToolSpec(name="run_command", description="t", input_schema={},
                    category="run", danger_level="high", needs_confirm=True)
    task = asyncio.create_task(
        ConfirmService.request("run_command", {"command": "echo hi"}, spec,
                               push=push, timeout=10))
    for _ in range(100):
        if events:
            break
        await asyncio.sleep(0.01)
    rid = events[0]["request_id"]
    r = client.post("/api/confirm", content=f"request_id={rid}&allow={allow}",
                    headers=FRONTEND_HEADERS)
    decision = await task
    return r.status_code, decision


def test_confirm_frontend_flow() -> None:
    async def run() -> None:
        with TestClient(app) as client:
            status, decision = await _pend_and_resolve(client, "true")
            assert status == 200, status
            assert decision == "allow", decision
            status, decision = await _pend_and_resolve(client, "false")
            assert status == 200, status
            assert decision == "deny", decision
        print("[OK] confirm：前端 form body 允许/拒绝全链路（含同源 Origin 头）")
    asyncio.run(run())


# ---- Origin 守卫矩阵（三族端点共用的安全中间件）----

def test_origin_guard_matrix() -> None:
    with TestClient(app) as client:
        form = "application/x-www-form-urlencoded"

        def post_confirm(headers: dict) -> int:
            return client.post("/api/confirm", content="allow=true",
                               headers={"Content-Type": form, **headers}).status_code

        # 恶意网页（任意站点）跨站写 → 403
        assert post_confirm({"Origin": "https://evil.example",
                             "Sec-Fetch-Site": "cross-site"}) == 403
        # 本地恶意 HTML（file:// → Origin: null）→ 403
        assert post_confirm({"Origin": "null", "Sec-Fetch-Site": "none"}) == 403
        # 沙箱 iframe（null + cross-site）→ 403
        assert post_confirm({"Origin": "null", "Sec-Fetch-Site": "cross-site"}) == 403
        # 可信 dev 来源（Vite 5173 → 后端 8801 在浏览器眼里是 cross-site）→ 进业务逻辑
        r = client.post("/api/confirm", content="allow=true",
                        headers={"Content-Type": form,
                                 "Origin": "http://localhost:5173",
                                 "Sec-Fetch-Site": "cross-site"})
        assert r.status_code == 400, r.status_code  # 400 = 已过守卫（缺 request_id）
        # 无 Origin 的非浏览器客户端（curl/DSH/测试）→ 进业务逻辑
        r = client.post("/api/confirm", content="allow=true",
                        headers={"Content-Type": form})
        assert r.status_code == 400, r.status_code
        # GET 不受守卫影响（即使带恶意 Origin）
        assert client.get("/api/health",
                          headers={"Origin": "https://evil.example",
                                   "Sec-Fetch-Site": "cross-site"}).status_code == 200
        # AgentPanel 创建任务同样受守卫保护
        r = client.post("/api/agent/tasks", content="objective=x",
                        headers={"Content-Type": form,
                                 "Origin": "https://evil.example",
                                 "Sec-Fetch-Site": "cross-site"})
        assert r.status_code == 403, r.status_code
    print("[OK] Origin 守卫矩阵：恶意跨站/null 拦截，可信/非浏览器/GET 放行")


def main() -> None:
    test_chat_sse_frame_contract()
    test_chat_sse_error_frame()
    test_chat_validation()
    test_agent_create_form_body()
    test_agent_create_json_and_query_compat()
    test_agent_full_flow_gate_to_done()
    test_agent_cancel_and_404()
    test_confirm_frontend_flow()
    test_origin_guard_matrix()
    print("\n=== HTTP 层端点测试: 9 项全部通过 ===")


if __name__ == "__main__":
    main()
