# -*- coding: utf-8 -*-
"""G04 求助与欲望：门槛/周限/状态机/24h 过期/接受写虚构产物/聊天意图/导出。

验收锚点（docs/Zcode技术指导.md §14.9 G04 + 调度文档批次 6）：
- 候选门槛 intimacy ≥ 50 且 trust ≥ 50；7 天至多一次；无生活源不开口；
- 状态机 candidate/offered/accepted/declined/expired；24h 未回应 expired；
- 拒绝（含「随便/不想」）declined 且不扣关系分；
- 接受写角色虚构产物（source_type='fiction'），与现实承诺账分离；
- 聊天意图与 API 同一 respond 函数；生活源删除使未完成请求失效；
- LC-1：life 类别导出、引用规则、恢复清空回应编号。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_g04_"))

from backend.core import companion_requests as cr
from backend.core.userdb import db

NOW = datetime(2026, 9, 8, 15, 0)


_LIFE_SEQ = 0


def _life_event(uid: str, when: datetime | None = None) -> int:
    global _LIFE_SEQ
    moment = when or NOW
    _LIFE_SEQ += 1
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO character_life_events "
            "(user_id, kind, block_id, occurrence, payload_json, occurred_at, computed_at) "
            "VALUES (?, 'daily_life', 'afternoon', ?, ?, ?, ?)",
            (uid, f"{moment.date().isoformat()}#{_LIFE_SEQ}",
             json.dumps({"description": "在阳台听了一下午歌"}, ensure_ascii=False),
             moment.isoformat(timespec="seconds"), moment.isoformat(timespec="seconds")),
        )
        db.conn.commit()
    return int(cur.lastrowid)


def _set_dimensions(uid: str, trust: int, intimacy: int) -> None:
    with db._lock:
        db.conn.execute(
            "UPDATE users SET trust=?, intimacy=?, affection=? WHERE user_id=?",
            (trust, intimacy, min(trust, intimacy), uid),
        )
        db.conn.commit()


def test_gate_and_weekly_limit() -> int:
    uid = "g04-gate"
    db.ensure_user(uid)
    _set_dimensions(uid, 80, 80)
    _life_event(uid)
    qid = cr.maybe_create_candidate(uid, now=NOW)
    assert qid is not None
    row = cr.list_requests(uid)[0]
    assert row["status"] == "candidate" and row["kind"] in cr.KINDS
    # 7 天内不再出第二单
    _life_event(uid, NOW + timedelta(hours=2))
    assert cr.maybe_create_candidate(uid, now=NOW + timedelta(days=2)) is None
    # 7 天后可再出
    assert cr.maybe_create_candidate(uid, now=NOW + timedelta(days=8)) is not None

    # 门槛不足不出单
    low = "g04-low"
    db.ensure_user(low)
    _set_dimensions(low, 40, 80)
    _life_event(low)
    assert cr.maybe_create_candidate(low, now=NOW) is None, "trust 不足不应开口"
    # 没有生活源不开口
    bare = "g04-bare"
    db.ensure_user(bare)
    _set_dimensions(bare, 80, 80)
    assert cr.maybe_create_candidate(bare, now=NOW) is None, "无生活源不应开口"
    # 开关关闭
    with patch("backend.core.features.flag", return_value=False):
        assert cr.maybe_create_candidate(uid, now=NOW + timedelta(days=20)) is None
    print("[OK] 门槛：双维 ≥50 / 7 天一次 / 无生活源不开口 / 开关可关")
    return 0


def test_offer_expiry_and_reply_classification() -> int:
    uid = "g04-offer"
    db.ensure_user(uid)
    _set_dimensions(uid, 90, 90)
    _life_event(uid)
    qid = cr.maybe_create_candidate(uid, now=NOW)
    assert cr.active_offer(uid, now=NOW) is None, "候选未投递前不是 offer"
    assert cr.mark_offered(uid, qid, now=NOW)
    offer = cr.active_offer(uid, now=NOW + timedelta(hours=1))
    assert offer and offer["id"] == qid
    # 24h 后过期，不再追问
    assert cr.active_offer(uid, now=NOW + timedelta(hours=25)) is None
    assert cr.list_requests(uid)[0]["status"] == "expired"

    # 回复归类：接受 / 拒绝（含「随便」「不想」）/ 不猜
    assert cr.classify_reply("好啊") == "accept"
    assert cr.classify_reply("可以") == "accept"
    assert cr.classify_reply("听你的") == "accept"
    assert cr.classify_reply("随便") == "decline"
    assert cr.classify_reply("不想") == "decline"
    assert cr.classify_reply("今天天气不错") == "unknown"
    assert cr.classify_reply("") == "unknown"
    print("[OK] offer：24h 过期不再问；回复归类 accept/decline/unknown")
    return 0


def test_accept_writes_fiction_artifact_and_decline_no_penalty() -> int:
    uid = "g04-respond"
    db.ensure_user(uid)
    _set_dimensions(uid, 90, 90)
    _life_event(uid)
    qid = cr.maybe_create_candidate(uid, now=NOW)
    cr.mark_offered(uid, qid, now=NOW)

    before = db.get_user(uid)
    result = cr.respond(uid, qid, "accept", reply_text="就《晴天》吧", now=NOW)
    assert result["status"] == "accepted"
    artifact = result["artifact"]
    assert artifact and artifact["artifact_type"].startswith("companion_")
    assert "晴天" in artifact["content"]
    row = db.conn.execute(
        "SELECT source_type FROM artifacts WHERE id=?", (artifact["id"],)).fetchone()
    assert row["source_type"] == "fiction", "只写角色虚构产物"
    # 幂等：再回应同单不重复写产物
    again = cr.respond(uid, qid, "accept", now=NOW)
    assert again["idempotent"] and again["status"] == "accepted"

    # 拒绝不扣分
    _life_event(uid, NOW + timedelta(days=1))
    qid2 = cr.maybe_create_candidate(uid, now=NOW + timedelta(days=8))
    assert qid2 is not None
    cr.mark_offered(uid, qid2, now=NOW + timedelta(days=8))
    after_accept = db.get_user(uid)
    cr.respond(uid, qid2, "decline", now=NOW + timedelta(days=8))
    after_decline = db.get_user(uid)
    assert (after_accept["trust"], after_accept["intimacy"]) == \
           (after_decline["trust"], after_decline["intimacy"]), "拒绝不扣关系分"
    assert before["trust"] is not None
    print("[OK] 接受写 fiction 产物且幂等；拒绝不扣分")
    return 0


def test_chat_intent_same_function() -> int:
    uid = "g04-chat"
    db.ensure_user(uid)
    _set_dimensions(uid, 90, 90)
    _life_event(uid)
    qid = cr.maybe_create_candidate(uid, now=NOW)
    cr.mark_offered(uid, qid, now=NOW)
    # 没有有效 offer 时不猜（其他用户/其他状态下也不打扰）
    assert cr.respond_to_reply("g04-no-offer", "好啊", now=NOW) is None
    assert cr.respond_to_reply(uid, "今天聊点别的", now=NOW) is None
    out = cr.respond_to_reply(uid, "好呀，你说", message_id=123, now=NOW)
    assert out and out["status"] == "accepted"
    row = cr.list_requests(uid)[0]
    assert row["response_message_id"] == 123
    print("[OK] 聊天意图与 API 同一 respond：无 offer 不猜，回应落 message_id")
    return 0


def test_source_gone_and_export() -> int:
    from backend.core.relationship_export import export_bundle, restore_bundle

    uid = "g04-source"
    db.ensure_user(uid)
    _set_dimensions(uid, 90, 90)
    source = _life_event(uid)
    qid = cr.maybe_create_candidate(uid, now=NOW)
    assert cr.dismiss_for_source(uid, source) == 1
    assert cr.list_requests(uid)[0]["status"] == "dismissed"
    # 自愈：源消失后 pending_candidate / active_offer 都不再返回它
    assert cr.pending_candidate(uid, now=NOW) is None

    # LC-1：life 类别导出→恢复，引用重映射、回应编号清空
    uid2 = "g04-export"
    db.ensure_user(uid2)
    _set_dimensions(uid2, 90, 90)
    src2 = _life_event(uid2)
    q2 = cr.maybe_create_candidate(uid2, now=NOW)
    cr.mark_offered(uid2, q2, now=NOW)
    cr.respond(uid2, q2, "accept", message_id=999, now=NOW)
    bundle = export_bundle(uid2, ["life"])
    assert "companion_requests" in bundle["data"], list(bundle["data"].keys())
    target = "g04-import"
    restore_bundle(bundle, target)
    row = db.conn.execute(
        "SELECT life_event_id, status, response_message_id FROM companion_requests "
        "WHERE user_id=?", (target,)).fetchone()
    assert row["status"] == "accepted"
    assert row["response_message_id"] is None, "回应编号不跨库残留"
    linked = db.conn.execute(
        "SELECT user_id FROM character_life_events WHERE id=?", (row["life_event_id"],)
    ).fetchone()
    assert linked and linked["user_id"] == target, "life_event_id 应重映射到目标命名空间"
    assert src2  # 源事件仍在原库
    print("[OK] 生活源删除即失效；LC-1 导出恢复重映射引用、清空回应编号")
    return 0


def test_initiative_delivery_marks_offered() -> int:
    """候选不绕过 initiative：投递成功才置 offered。"""
    from backend.core import initiative

    uid = "g04-initiative"
    db.ensure_user(uid)
    _set_dimensions(uid, 90, 90)
    _life_event(uid)
    with patch("backend.core.initiative.chat", new=AsyncMock(return_value="帮我挑一首歌吧")):
        text = asyncio.run(initiative._produce_companion_request(uid))
    assert text, "应生成求助文案"
    offer = cr.active_offer(uid)
    assert offer and offer["status"] == "offered", offer
    # 仲裁额度耗尽时不再投递（不绕过闸门）
    with patch("backend.core.proactive_policy.try_claim_active", return_value=None), \
         patch("backend.core.initiative.chat", new=AsyncMock(return_value="再挑一次")):
        again = asyncio.run(initiative._produce_companion_request(uid))
    assert again is None, "额度/占位失败不得绕过仲裁投递"
    print("[OK] initiative 投递成功才置 offered；闸门拦截时不投递")
    return 0


def main() -> int:
    failed = (
        test_gate_and_weekly_limit()
        + test_offer_expiry_and_reply_classification()
        + test_accept_writes_fiction_artifact_and_decline_no_penalty()
        + test_chat_intent_same_function()
        + test_source_gone_and_export()
        + test_initiative_delivery_marks_offered()
    )
    if failed:
        print(f"\n=== G04 求助与欲望：{failed} 项失败 ===")
        return 1
    print("\n=== G04 求助与欲望：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
