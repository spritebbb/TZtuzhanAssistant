# -*- coding: utf-8 -*-
"""M4 关系季节：真实信号 → 确定性季节推导、行为帧接入、解释快照可解释。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_seasons_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import seasons  # noqa: E402
from backend.core.relationship_events import (  # noqa: E402
    record_promise_completed,
    refresh_important_date,
)
from backend.core.seasons import current_season  # noqa: E402
from backend.core.userdb import db, save_promise  # noqa: E402

UID = "assistant-main"
# 事件窗口由生产代码按真实当前时间计算；测试基准也必须使用运行当天，
# 否则跨过固定日期后的第 2 天会把本应有效的特殊日子误判为过期。
TODAY = date.today()


class _State:
    def __init__(self, tension: int = 0):
        self.tension = tension


def _seed_user_messages(count: int, days_ago: int = 1) -> None:
    """造 count 条最近用户消息，让忙碌期判定可控。"""
    ts = (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")
    with db._lock:
        db.conn.execute(
            "DELETE FROM messages WHERE user_id = ?", (UID,)
        )
        db.conn.executemany(
            "INSERT INTO messages (user_id, role, content, ts) VALUES (?, 'user', ?, ?)",
            [(UID, f"第 {i} 条测试消息", ts) for i in range(count)],
        )
        db.conn.commit()


def _test_priority_and_reasons() -> None:
    # 沉淀期：无信号、消息正常
    _seed_user_messages(10)
    quiet = current_season(UID, _State(), today=TODAY)
    assert quiet["season"] == "quiet" and quiet["line"] == ""
    assert quiet["reason"]

    # 修复期优先级最高：即使同时有庆祝信号
    refresh_important_date(
        UID, {"id": 1, "label": "你的生日", "kind": "birthday"}, TODAY,
        commit=False,
    )
    with db._lock:
        db.conn.commit()
    repair = current_season(UID, _State(tension=45), today=TODAY)
    assert repair["season"] == "repair" and repair["line"] == ""  # 语气交给 tension_line
    assert "张力" in repair["reason"]

    # 庆祝期：特殊日子 2 天窗口内
    celebrate = current_season(UID, _State(), today=TODAY)
    assert celebrate["season"] == "celebrate" and "你的生日" in celebrate["reason"]
    assert "别硬嗨" in celebrate["line"]

    # 庆祝期：刚完成的约定
    with db._lock:
        db.conn.execute(
            "DELETE FROM relationship_events WHERE user_id = ?", (UID,)
        )
        db.conn.commit()
    promise_id = save_promise(UID, "周五一起看流星雨")
    record_promise_completed(UID, {"id": promise_id, "content": "周五一起看流星雨"})
    celebrate2 = current_season(UID, _State(), today=TODAY)
    assert celebrate2["season"] == "celebrate" and "流星雨" in celebrate2["reason"]

    # 创作期：事件清掉后回落
    with db._lock:
        db.conn.execute(
            "DELETE FROM relationship_events WHERE user_id = ?", (UID,)
        )
        db.conn.commit()
    with db._lock:
        now = datetime.now().isoformat(timespec="seconds")
        cur = db.conn.execute(
            "INSERT INTO activities (user_id, kind, document_id, title, status, position, created_at, updated_at) "
            "VALUES (?, 'reading', 1, '共读《测试》', 'active', 0, ?, ?)",
            (UID, now, now),
        )
        activity_id = int(cur.lastrowid)
        db.conn.commit()
    create = current_season(UID, _State(), today=TODAY)
    assert create["season"] == "create" and "共读" in create["reason"]

    # 忙碌期：活动清掉 + 最近 3 天消息少于 3 条（描述性，不是惩罚）
    with db._lock:
        db.conn.execute("DELETE FROM activities WHERE id = ?", (activity_id,))
        db.conn.commit()
    _seed_user_messages(1, days_ago=2)
    busy = current_season(UID, _State(), today=TODAY)
    assert busy["season"] == "busy"
    assert "不追问" in busy["line"] and "不抱怨" in busy["line"]
    print("[OK] 季节优先级：修复>庆祝>创作>忙碌>沉淀，理由均可追溯到真实信号")


async def _test_pipeline_and_explanation() -> None:
    from backend.core import pipeline
    from backend.core.explainability import build_reply_explanation

    # 行为帧：季节行参与 compose
    from backend.core.behavior import build_behavior_frame
    from backend.core.state import load_state

    _seed_user_messages(1, days_ago=2)  # 保持忙碌期信号
    state = load_state(UID)
    frame = build_behavior_frame(state, season_line=current_season(UID, state)["line"])
    assert "不追问" in frame.compose()

    # pipeline：user 消息在最后 + 解释快照带季节与理由
    captured: dict = {}

    async def fake_chat(messages, **kwargs):
        captured["messages"] = messages
        return "好呀，各自忙完再聊"

    original_chat = pipeline.chat
    pipeline.chat = fake_chat
    try:
        await pipeline.process(UID, "最近有点忙，先不多说了", mock=True)
    finally:
        pipeline.chat = original_chat
    assert captured["messages"][-1]["role"] == "user"

    season = current_season(UID, _State(), today=TODAY)
    snapshot = build_reply_explanation(
        state,
        frame,
        memory_rows=[("关系季节", f"{season['label']}（{season['reason']}）")],
    )
    assert any(item["kind"] == "关系季节" for item in snapshot["memories"])
    print("[OK] pipeline/解释快照：季节可解释（含理由），user 仍在最后")


async def main() -> None:
    _test_priority_and_reasons()
    await _test_pipeline_and_explanation()
    print("\n=== M4 关系季节：全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
