# -*- coding: utf-8 -*-
"""DF-8 重发刷分防护回归：regenerate 60s 节流 + 情绪事件当日去重。

背景（2026-09-29 缺陷审查 N8-1/N8-2）：此前连点「重新生成」可以以每轮一条的
速度向 long_memory 冲刷重复原文、并把同一句原文反复灌进情绪记忆/长期档案/
事件级记忆/张力——代码注释声称的「每日限额与置信度合并」两个兜底并不存在
（reconcile 是零调用死代码；语义感知入账无日限）。

修复（自行补充决策）：①chat.py regenerate 端点 60 秒节流（语义零变化，从源头
挡连点）；②apply_impulse 的 emotional_hit 分支按「hit+原文」指纹当日去重
（mood delta 不在去重限内——每轮 ±15 是她对这句话当下的反应）。

运行：python -m tests.test_df8_regen_guard
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

_TEST_TMP = Path(tempfile.mkdtemp(prefix="tz_df8_test_"))
_TEST_TMP.mkdir(parents=True, exist_ok=True)
os.environ["TZTUZHAN_DATA_DIR"] = str(_TEST_TMP)

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import app  # noqa: E402
import backend.core.initiative as _initiative  # noqa: E402
import backend.session.store as _session_store  # noqa: E402
from backend.core.state import apply_impulse, recall_event_memory  # noqa: E402
from backend.core.userdb import db, kv_get, kv_set  # noqa: E402


async def _disabled_initiative_loop() -> None:
    return


_initiative.initiative_loop = _disabled_initiative_loop
_session_store._DB = _TEST_TMP / "sessions.db"

client = TestClient(app)

FORM_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "http://127.0.0.1:8801",
    "Sec-Fetch-Site": "same-origin",
}


def test_regen_throttle() -> None:
    assert asyncio.run(_session_store.append_messages(
        "current",
        [{"role": "user", "content": "帮我记一下菟丝子的习性", "ts": 1.0}],
    ))
    with client.stream("POST", "/api/chat", data="regenerate=true&mock=true", headers=FORM_HEADERS) as resp:
        assert resp.status_code == 200, resp.status_code
    # 前端「再点一次重新生成」= 先截掉 bot 回复（末条回到 user）再发 regenerate
    assert asyncio.run(_session_store.truncate_session("current", 1)) == 1
    # 60 秒内的第二次重发 → 429（连点刷分的根源被挡住）
    r = client.post("/api/chat", content="regenerate=true&mock=true", headers=FORM_HEADERS)
    assert r.status_code == 429, (r.status_code, r.text)
    assert "重新生成" in r.text
    # 节流键落账，且把时钟拨前 61 秒后放行
    uid = db.conn.execute(
        "SELECT user_id FROM kv_store WHERE key='chat:regen_last_at' LIMIT 1").fetchone()["user_id"]
    kv_set(uid, "chat:regen_last_at", str(__import__("time").time() - 61))
    with client.stream("POST", "/api/chat", data="regenerate=true&mock=true", headers=FORM_HEADERS) as resp:
        assert resp.status_code == 200, resp.status_code
    print("[OK] regenerate 60s 节流：连点 429、窗口过后放行")
    return 0


def test_emotion_hit_dedupe() -> None:
    uid = "df8-emo"
    db.ensure_user(uid)
    kv_set(uid, "chat:regen_last_at", "")  # 隔离其他用例的 kv 视野
    before = db.get_mood(uid)[0]

    apply_impulse(uid, emotion_delta=10, affection_delta=0,
                  emotional_hit="被夸了", emotional_weight=0.9, text="你今天真好看")
    first_events = recall_event_memory(uid)
    assert len(first_events) == 1 and first_events[0]["text"] == "你今天真好看"

    # 同一句原文当日重复入账（连点重发场景）→ 事件记忆不增长
    mood_after_first = db.get_mood(uid)[0]
    from backend.core.state import load_state

    tension_after_first = int(load_state(uid).tension or 0)
    apply_impulse(uid, emotion_delta=10, affection_delta=0,
                  emotional_hit="被夸了", emotional_weight=0.9, text="你今天真好看")
    assert len(recall_event_memory(uid)) == 1, "同一句原文当日不得重复记成情绪事件"
    assert db.get_mood(uid)[0] > mood_after_first, "mood delta 不在去重限内（她的即时反应仍在）"
    # M4 契约：张力不参与当日去重——「同一句话反复说，每次都更伤」
    # （此例为正向 hit 且 delta 和为正，不涨张力属正常；负向场景由
    #  test_m4_relationship 的连续冒犯封顶用例覆盖。）
    assert int(load_state(uid).tension or 0) == tension_after_first

    # 不同原文 → 正常入账第二条
    apply_impulse(uid, emotion_delta=5, affection_delta=0,
                  emotional_hit="被夸了", emotional_weight=0.9, text="你做的饭也好吃")
    events = recall_event_memory(uid)
    assert len(events) == 2, events
    assert db.get_mood(uid)[0] > before, "mood 净变化为正"
    print("[OK] 情绪事件当日去重：重复原文不再灌记忆，mood 反应保留")
    return 0


def main() -> int:
    failed = test_regen_throttle() + test_emotion_hit_dedupe()
    if failed:
        print(f"\n=== DF-8 重发刷分防护：{failed} 项失败 ===")
        return 1
    print("\n=== DF-8 重发刷分防护：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
