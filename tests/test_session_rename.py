# -*- coding: utf-8 -*-
"""NP-03 会话/归档重命名回归。

背景：单一会话模式下自动命名取首条用户消息前 20 字，极易撞车
（实测出现五条同名「聊聊菟丝子吧」），且此前无任何改名入口。
覆盖：归档重命名、当前会话重命名、自定义标题不被自动命名覆盖、
空标题 400、未知目标 404、超长标题截断到 60、缺 title 字段 422。

运行：python -m tests.test_session_rename（或经 pytest tests/ 由套件运行器执行）
"""
import asyncio
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 测试环境收敛（与 test_http_endpoints 同款）：不下载模型、关后台与外部能力
os.environ["MEMORY_EMBED_FORCE"] = "1"
os.environ["MEMORY_MEM0"] = "0"
os.environ["MEMORY_V2"] = "0"
os.environ["MOOD_CITY"] = ""
os.environ["SEARCH_ENABLED"] = "0"

_TEST_TMP = Path(tempfile.mkdtemp(prefix="tz_rename_test_"))
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


def _seed_archive(first_user_text: str) -> str:
    """造一条归档：写入一条用户消息（自动命名取前 20 字）后归档，返回归档 id。"""
    msgs = [{"role": "user", "content": first_user_text, "ts": 0.0}]
    assert asyncio.run(_session_store.append_messages("current", msgs))
    result = asyncio.run(_session_store.archive_current())
    assert result is not None
    return result["id"]


def test_rename_archive_updates_list() -> None:
    archive_id = _seed_archive("聊聊菟丝子吧的第一段对话")
    r = client.post(
        f"/api/sessions/{archive_id}/rename",
        json={"title": "我们的第一次长聊"},
        headers=JSON_HEADERS,
    )
    assert r.status_code == 200, r.status_code
    body = r.json()
    assert body["ok"] is True
    assert body["title"] == "我们的第一次长聊"
    titles = [a["title"] for a in client.get("/api/sessions/archives").json()["archives"]]
    assert "我们的第一次长聊" in titles
    assert "聊聊菟丝子吧的第一段对话" not in titles
    print("[OK] 归档重命名：标题更新且列表即时反映")


def test_rename_current_session_then_auto_title_does_not_overwrite() -> None:
    # GET current 触发 _ensure_session 建当前会话行
    assert client.get("/api/sessions/current").status_code == 200
    r = client.post("/api/sessions/current/rename", json={"title": "自定义标题"}, headers=JSON_HEADERS)
    assert r.status_code == 200, r.status_code
    # 再追加用户消息：自动命名只在 title == '新会话' 时回填，不得覆盖自定义标题
    assert asyncio.run(
        _session_store.append_messages("current", [{"role": "user", "content": "之后的新消息内容", "ts": 1.0}])
    )
    conn = sqlite3.connect(_session_store._DB)
    try:
        titles = [row[0] for row in conn.execute("SELECT title FROM sessions").fetchall()]
    finally:
        conn.close()
    assert "自定义标题" in titles
    assert all("之后的新消息内容" not in t for t in titles)
    print("[OK] 当前会话重命名：自定义标题不被自动命名覆盖")


def test_rename_empty_title_rejected() -> None:
    for blank in ("", "   "):
        r = client.post("/api/sessions/current/rename", json={"title": blank}, headers=JSON_HEADERS)
        assert r.status_code == 400, r.status_code
        assert r.json()["ok"] is False
    print("[OK] 空标题 400（含纯空白）")


def test_rename_unknown_target_404() -> None:
    r = client.post("/api/sessions/no-such-id/rename", json={"title": "任意"}, headers=JSON_HEADERS)
    assert r.status_code == 404, r.status_code
    assert r.json()["ok"] is False
    print("[OK] 未知会话/归档 404")


def test_rename_long_title_truncated_to_60() -> None:
    archive_id = _seed_archive("另一段对话")
    long_title = "长" * 80
    r = client.post(f"/api/sessions/{archive_id}/rename", json={"title": long_title}, headers=JSON_HEADERS)
    assert r.status_code == 200, r.status_code
    assert len(r.json()["title"]) == 60
    print("[OK] 超长标题截断到 60 字")


def test_rename_missing_body_title_422() -> None:
    # 缺 title 字段：pydantic 校验层直接 422
    r = client.post("/api/sessions/current/rename", json={}, headers=JSON_HEADERS)
    assert r.status_code == 422, r.status_code
    print("[OK] 缺 title 字段 422（pydantic 校验层）")


def main() -> None:
    test_rename_archive_updates_list()
    test_rename_current_session_then_auto_title_does_not_overwrite()
    test_rename_empty_title_rejected()
    test_rename_unknown_target_404()
    test_rename_long_title_truncated_to_60()
    test_rename_missing_body_title_422()
    print("\n=== NP-03 会话/归档重命名: 6 项全部通过 ===")


if __name__ == "__main__":
    main()
