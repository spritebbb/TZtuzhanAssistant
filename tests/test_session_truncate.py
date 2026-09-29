# -*- coding: utf-8 -*-
"""NP-08 消息重发最小切片回归：截断端点 + regenerate 语义。

语义（拍板）：「重新生成 = 你把那句话又说了一遍」——sessions 侧只截掉旧 bot
回复，pipeline 走完全正常路径；被截断消息已提取的记忆不回滚。

覆盖：truncate 边界（keep=0/全部/越界/负数）、regenerate 末条非 user 400、
regenerate 不重复落 user 消息、regenerate 与 ephemeral/image 互斥。

运行：python -m tests.test_session_truncate（或经 pytest tests/ 由套件运行器执行）
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["MEMORY_EMBED_FORCE"] = "1"
os.environ["MEMORY_MEM0"] = "0"
os.environ["MEMORY_V2"] = "0"
os.environ["MOOD_CITY"] = ""
os.environ["SEARCH_ENABLED"] = "0"

_TEST_TMP = Path(tempfile.mkdtemp(prefix="tz_truncate_test_"))
_TEST_TMP.mkdir(parents=True, exist_ok=True)
os.environ["TZTUZHAN_DATA_DIR"] = str(_TEST_TMP)

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import app  # noqa: E402
import backend.core.initiative as _initiative  # noqa: E402
import backend.session.store as _session_store  # noqa: E402


async def _disabled_initiative_loop() -> None:
    return


_initiative.initiative_loop = _disabled_initiative_loop
_session_store._DB = _TEST_TMP / "sessions.db"

client = TestClient(app)

JSON_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "http://127.0.0.1:8801",
    "Sec-Fetch-Site": "same-origin",
}

FORM_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "http://127.0.0.1:8801",
    "Sec-Fetch-Site": "same-origin",
}


def _seed(user_text: str, bot_text: str) -> None:
    assert asyncio.run(_session_store.append_messages(
        "current",
        [
            {"role": "user", "content": user_text, "ts": 1.0},
            {"role": "bot", "content": bot_text, "ts": 2.0},
        ],
    ))


def test_truncate_boundaries() -> None:
    # 清空当前会话起点：keep=0 删光
    removed = asyncio.run(_session_store.truncate_session("current", 0))
    assert removed == 0
    _seed("第一问", "第一答")
    _seed("第二问", "第二答")
    assert asyncio.run(_session_store.message_count("current")) == 4
    # keep=3：保留 user1/bot1/user2，删 bot2
    removed = asyncio.run(_session_store.truncate_session("current", 3))
    assert removed == 1
    msgs = asyncio.run(_session_store.get_messages("current"))
    assert [m["role"] for m in msgs] == ["user", "bot", "user"]
    assert msgs[-1]["content"] == "第二问"
    # 越界与负数
    for bad in (4, -1):
        try:
            asyncio.run(_session_store.truncate_session("current", bad))
            raise AssertionError(f"keep_count={bad} 应抛 ValueError")
        except ValueError:
            pass
    print("[OK] truncate 边界：正常截断 / 越界与负数拒绝 / keep=0 清空")


def test_truncate_endpoint_contract() -> None:
    r = client.post("/api/sessions/current/truncate", json={"keep_count": 10}, headers=JSON_HEADERS)
    assert r.status_code == 400, r.status_code
    r = client.post("/api/sessions/current/truncate", json={"keep_count": 1}, headers=JSON_HEADERS)
    assert r.status_code == 200 and r.json()["removed"] == 2
    print("[OK] truncate 端点契约：越界 400、正常返回删除数")


def test_regenerate_requires_last_user() -> None:
    # 当前末条是 bot（keep=1 后剩 user+bot）→ 400
    _seed("第三问", "第三答")
    r = client.post(
        "/api/chat",
        content="regenerate=true",
        headers=FORM_HEADERS,
    )
    assert r.status_code == 400, r.status_code
    assert "最后一条不是你的消息" in r.text
    print("[OK] regenerate：末条非 user 拒绝 400")


def test_regenerate_mutex_and_no_repersist() -> None:
    # 与 ephemeral / image 互斥
    r = client.post(
        "/api/chat",
        content="regenerate=true&ephemeral=true",
        headers=FORM_HEADERS,
    )
    assert r.status_code == 400
    r = client.post(
        "/api/chat",
        content="regenerate=true&image=/api/images/x.png",
        headers=FORM_HEADERS,
    )
    assert r.status_code == 400

    # 末条是 user 时 regenerate 不重复落 user 消息
    # （当前会话剩 [user1, user3, bot3]，截断 keep=1 → 删 user3+bot3 两条，剩 [user1]）
    removed = asyncio.run(_session_store.truncate_session("current", 1))
    assert removed == 2
    before = asyncio.run(_session_store.get_messages("current"))
    assert len(before) == 1 and before[0]["role"] == "user"
    # mock 轮走 pipeline mock 分支（不依赖真实 LLM）；regenerate=true
    with client.stream("POST", "/api/chat", data="regenerate=true&mock=true", headers=FORM_HEADERS) as resp:
        assert resp.status_code == 200, resp.status_code
        body = "".join(chunk for chunk in resp.iter_text())
    assert "done" in body
    after = asyncio.run(_session_store.get_messages("current"))
    user_count = sum(1 for m in after if m["role"] == "user")
    assert user_count == 1, f"regenerate 不应重复落 user 消息：{[m['role'] for m in after]}"
    assert after[-1]["role"] == "bot", "regenerate 的回复应正常落库"
    print("[OK] regenerate：不重复落 user 消息，回复正常落库（mock 轮）")


def test_truncate_waits_for_inflight_generation() -> None:
    """DF-10：truncate 必须等在途生成（持 pipeline._user_lock）结束才执行，
    否则截断先落、旧回复后落，已删气泡「复活」。"""
    import asyncio as _aio

    from backend.api.sessions import TruncatePayload, api_sessions_truncate
    from backend.core.pipeline import _user_lock
    from backend.core.persona_profiles import active_user_id

    uid = active_user_id()

    async def scenario():
        async with _user_lock(uid):
            task = _aio.create_task(api_sessions_truncate(TruncatePayload(keep_count=1)))
            await _aio.sleep(0.05)
            assert not task.done(), "持锁期间 truncate 必须阻塞等待"
        # 直接调用端点返回原生 dict（未经 FastAPI 响应层）
        body = await task
        assert body.get("ok") is True, body
        return body["removed"]

    _seed("互斥前问", "互斥前答")
    removed = asyncio.run(scenario())
    # 前序用例留下 [user1, bot]，再 seed 两条 = 4 条；keep=1 → 删 3 条
    assert removed == 3, removed
    after = asyncio.run(_session_store.get_messages("current"))
    assert [m["role"] for m in after] == ["user"]
    print("[OK] DF-10 truncate：与在途生成互斥，锁释放后才截断")


def main() -> None:
    test_truncate_boundaries()
    test_truncate_endpoint_contract()
    test_regenerate_requires_last_user()
    test_regenerate_mutex_and_no_repersist()
    test_truncate_waits_for_inflight_generation()
    print("\n=== NP-08 消息重发: 5 项全部通过 ===")


if __name__ == "__main__":
    main()
