# -*- coding: utf-8 -*-
"""38项#32 反馈回路：reply_quality 候选（整轮锚定、零 id 链路改造）。

验收锚点：
- 整轮定位：最近的 assistant 消息 + 其前 user 消息进轮次摘要，
  source_message_id 是真实 assistant 消息 id；
- reason 可选（≤80 字，超长入口截断）、turn 缺失确定性拒绝；
- 确认 V1 落点 = 仅入档（archived）：不写 user_preferences / user_terms /
  persona_evolution，拒绝/删除也不需要撤销任何落点；
- 无 assistant 消息（新人格）反馈被拒；去重幂等（同轮重复点击）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_38_32_"))

from backend.core import learning_pipeline as lp
from backend.core.userdb import db


def _seed_turn(uid: str, question: str, answer: str) -> int:
    """落一轮对话（user 提问 + assistant 回复），返回 assistant 消息 id。"""
    with db._lock:
        db.conn.execute(
            "INSERT INTO messages (user_id, role, content, ts) "
            "VALUES (?, 'user', ?, datetime('now'))",
            (uid, question),
        )
        cur = db.conn.execute(
            "INSERT INTO messages (user_id, role, content, ts) "
            "VALUES (?, 'assistant', ?, datetime('now'))",
            (uid, answer),
        )
        db.conn.commit()
    return int(cur.lastrowid)


def test_whole_turn_anchor_and_dedup() -> int:
    uid = "3832-anchor"
    db.ensure_user(uid)
    aid = _seed_turn(uid, "帮我看看这段代码哪里有问题", "这段代码看起来没什么问题呀")

    out = lp.propose_reply_quality(uid)
    assert out["status"] == "candidate" and not out["duplicate"], out
    with db._lock:
        row = db.conn.execute(
            "SELECT * FROM learning_candidates WHERE id=?", (out["id"],)
        ).fetchone()
    assert row is not None
    assert row["type"] == "reply_quality"
    assert int(row["source_message_id"]) == aid, "source 必须锚定最近 assistant 消息"
    value = json.loads(row["value_json"])
    assert "帮我看看这段代码" in value["turn"] and "这段代码" in value["turn"]
    assert not value.get("reason"), "未填 reason 时 value 不含 reason"

    # 同轮重复点击：整轮 value 相同 → duplicate 幂等
    again = lp.propose_reply_quality(uid)
    assert again.get("duplicate") is True and again["id"] == out["id"], again

    # 带 reason（含超长截断）
    out2 = lp.propose_reply_quality(uid, reason="答非所问" + "长" * 100)
    with db._lock:
        row2 = db.conn.execute(
            "SELECT value_json FROM learning_candidates WHERE id=?", (out2["id"],)
        ).fetchone()
    value2 = json.loads(row2["value_json"])
    assert len(value2["reason"]) == 80 and value2["reason"].startswith("答非所问"), value2
    print("[OK] 整轮锚定 / reason 截断 / 同轮去重幂等")
    return 0


def test_validation_rejects() -> int:
    uid = "3832-reject"
    db.ensure_user(uid)
    # turn 缺失
    try:
        lp.propose(uid, "reply_quality", {"reason": "不好"})
        raise AssertionError("缺轮次摘要应被拒")
    except lp.LearningError:
        pass
    # reason 超限（绕过入口截断直捅 propose）
    try:
        lp.propose(uid, "reply_quality",
                   {"turn": "他：x｜她：y", "reason": "长" * 81})
        raise AssertionError("reason 超 80 字应被拒")
    except lp.LearningError:
        pass
    # 新人格没有任何 assistant 消息
    try:
        lp.propose_reply_quality(uid)
        raise AssertionError("无可反馈回复应被拒")
    except lp.LearningError:
        pass
    print("[OK] turn 缺失 / reason 超限 / 空历史确定性拒绝")
    return 0


def test_confirm_archives_only_and_revoke() -> int:
    uid = "3832-archive"
    db.ensure_user(uid)
    _seed_turn(uid, "推荐一部电影吧", "最近没什么好看的，随便找一部吧")
    out = lp.propose_reply_quality(uid, reason="太敷衍了")

    confirmed = lp.confirm(uid, out["id"])
    assert confirmed["routed_to"] == "archived", confirmed
    # V1 仅入档：确认不得写任何行为落点
    with db._lock:
        prefs = db.conn.execute(
            "SELECT COUNT(*) AS n FROM user_preferences WHERE user_id=?", (uid,)
        ).fetchone()["n"]
        terms = db.conn.execute(
            "SELECT COUNT(*) AS n FROM user_terms WHERE user_id=?", (uid,)
        ).fetchone()["n"]
    from backend.core.persona_evolution import history

    assert prefs == 0 and terms == 0 and history(uid) == [], "V1 确认不得改行为落点"

    # 删除（撤销）：无落点需要撤销，候选退场即可
    revoked = lp.revoke(uid, out["id"])
    assert revoked["status"] == "revoked"
    items = [i for i in lp.list_candidates(uid) if i["id"] == out["id"]]
    assert not items, "默认视图不显示 revoked"
    print("[OK] 确认仅入档（archived）/ 删除退场")
    return 0


def test_http_endpoint() -> int:
    """POST /api/chat/feedback HTTP 层：JSON body / 空 body / 同值去重。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.chat import router
    from backend.core.persona_profiles import active_user_id

    uid = active_user_id()
    db.ensure_user(uid)
    _seed_turn(uid, "今晚有什么安排", "没什么安排，随便吧")
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        r1 = client.post("/api/chat/feedback", json={"reason": "太敷衍"})
        assert r1.status_code == 200, r1.text
        data1 = r1.json()
        assert data1["ok"] and data1["status"] == "candidate" and not data1["duplicate"], data1
        # 同一 reason 重复点击 → 同 value 去重 duplicate
        r2 = client.post("/api/chat/feedback", json={"reason": "太敷衍"})
        assert r2.status_code == 200 and r2.json()["duplicate"] is True, r2.text
        # 空 body（不带 JSON）也要能用：reason 缺省
        r3 = client.post("/api/chat/feedback")
        assert r3.status_code == 200 and r3.json()["ok"], r3.text
    print("[OK] HTTP 层：JSON body / 空 body / 同值去重")
    return 0


def main() -> int:
    failed = (
        test_whole_turn_anchor_and_dedup()
        + test_validation_rejects()
        + test_confirm_archives_only_and_revoke()
        + test_http_endpoint()
    )
    if failed:
        print(f"\n=== 38项#32 反馈回路：{failed} 项失败 ===")
        return 1
    print("\n=== 38项#32 反馈回路：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
