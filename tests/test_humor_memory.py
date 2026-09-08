# -*- coding: utf-8 -*-
"""L04 幽默记忆：明确反馈语义、授权门槛、负反馈优先、冷却与退役。

验收锚点（docs/Zcode技术指导.md L04 + 调度文档批次 7）：
- positive/negative/unknown 只接受明确反馈；单条「哈哈」只算 unknown；
- 近 7 天 ≥2 次 positive 才 approved；负反馈优先于历史 positive（立即 retired）；
- 单次最多一梗、最近 3 回合不重复；初识不适用；严肃/求助/修复默认不插；
- 用户删除共同语言同步退役；退役后 select_humor 不再选中；
- 同一天重复表态不刷批准（幂等 + 计数窗口）。
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_l04_"))

from backend.core import humor_memory as hm
from backend.core.userdb import db

NOW = datetime(2026, 9, 8, 20, 0)


def _term(uid: str, word: str, count: int = 3) -> int:
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO user_terms (user_id, term, category, meaning, count, first_seen, last_seen) "
            "VALUES (?, ?, 'slang', '我们的黑话', ?, ?, ?)",
            (uid, word, count, NOW.isoformat(timespec="seconds"),
             NOW.isoformat(timespec="seconds")),
        )
        db.conn.commit()
    return int(cur.lastrowid)


def test_feedback_classification() -> int:
    assert hm.classify_feedback("这个梗好") == "positive"
    assert hm.classify_feedback("笑死") == "positive"
    assert hm.classify_feedback("别玩这个梗") == "negative"
    assert hm.classify_feedback("不好笑") == "negative"
    assert hm.classify_feedback("哈哈") == "unknown", "单条笑声不算明确认可"
    assert hm.classify_feedback("哈哈哈哈哈") == "positive", "连续大笑是明确反应"
    assert hm.classify_feedback("嗯") == "unknown"
    assert hm.classify_feedback("今天工作怎么样") == "unknown"
    # 严肃场景判定
    assert hm.is_serious_context("帮我看看这个怎么办") is True
    assert hm.is_serious_context("今天吃了火锅") is False
    assert hm.is_serious_context("", tension=10) is True
    print("[OK] 反馈语义：明确才计入；「哈哈」=unknown；严肃场景识别")
    return 0


def test_approval_threshold_and_negative_priority() -> int:
    uid = "l04-approve"
    db.ensure_user(uid)
    tid = _term(uid, "菟丝子")
    # 第一次 positive 仍只是 candidate
    hm.record_feedback(uid, tid, 1, "positive", now=NOW)
    assert hm.status_map(uid).get(tid) == "candidate"
    assert hm.select_humor(uid, stage="熟悉", now=NOW) is None, "未授权不得选中"
    # 第二次 positive（7 天内）→ approved
    hm.record_feedback(uid, tid, 2, "positive", now=NOW + timedelta(days=1))
    assert hm.status_map(uid).get(tid) == "approved"
    picked = hm.select_humor(uid, stage="熟悉", now=NOW + timedelta(days=1))
    assert picked and picked["term_id"] == tid
    # 负反馈优先：一次 negative 立即 retired，覆盖历史 positive
    hm.record_feedback(uid, tid, 3, "negative", now=NOW + timedelta(days=2))
    assert hm.status_map(uid).get(tid) == "retired"
    assert hm.select_humor(uid, stage="熟悉", now=NOW + timedelta(days=2)) is None
    # 7 天窗口外的 positive 不计入门槛
    uid2 = "l04-window"
    db.ensure_user(uid2)
    t2 = _term(uid2, "加班怪")
    hm.record_feedback(uid2, t2, 1, "positive", now=NOW - timedelta(days=10))
    hm.record_feedback(uid2, t2, 2, "positive", now=NOW)
    assert hm.status_map(uid2).get(t2) == "candidate", "过期 positive 不算数"
    print("[OK] 授权门槛：7 天 2 次 positive；负反馈立即退役且优先")
    return 0


def test_cooldown_and_context_gate() -> int:
    uid = "l04-cooldown"
    db.ensure_user(uid)
    t1 = _term(uid, "摸鱼学")
    t2 = _term(uid, "熬夜怪")
    for turn in (1, 2):
        hm.record_feedback(uid, t1, turn, "positive", now=NOW)
        hm.record_feedback(uid, t2, turn, "positive", now=NOW)
    assert hm.select_humor(uid, stage="熟悉", now=NOW) is not None
    # 最近 3 回合用过 → 换一个或返回 None（不重复）
    hm.note_usage(uid, t1, 10, now=NOW)
    hm.note_usage(uid, t1, 11, now=NOW)
    hm.note_usage(uid, t1, 12, now=NOW)
    picked = hm.select_humor(uid, stage="熟悉", now=NOW)
    assert picked is None or picked["term_id"] != t1, "冷却期内不得重复同一个梗"
    # 初识 / 严肃不插
    assert hm.select_humor(uid, stage="初识", now=NOW) is None
    assert hm.select_humor(uid, stage="熟悉", serious=True, now=NOW) is None
    print("[OK] 冷却：最近 3 回合不重复；初识与严肃场景不插")
    return 0


def test_usage_detection_and_cascade() -> int:
    uid = "l04-usage"
    db.ensure_user(uid)
    tid = _term(uid, "菟丝子")
    used = hm.note_reply_usage(uid, "又在提菟丝子这个梗了", 21, now=NOW)
    assert used == [tid], used
    # 幂等：同一轮重复检测不新增
    assert hm.note_reply_usage(uid, "菟丝子菟丝子", 21, now=NOW) == []
    # 用户明确反馈 → 记到上一轮用过的梗上
    out = hm.record_feedback_from_reply(uid, "这个梗好", turn_id=22, now=NOW)
    assert out and out[0]["term_id"] == tid and out[0]["reaction"] == "positive"
    # 「哈哈」不入账
    hm.note_usage(uid, tid, 23, now=NOW)
    assert hm.record_feedback_from_reply(uid, "哈哈", turn_id=24, now=NOW) == []

    # 删除共同语言 → 同步退役，注入层过滤掉
    assert hm.retire_term(uid, tid) >= 1
    assert hm.filter_injectable(uid, [{"id": tid, "term": "菟丝子"}]) == []
    with db._lock:
        db.conn.execute("DELETE FROM user_terms WHERE id=?", (tid,))
        db.conn.commit()
    assert hm.select_humor(uid, stage="熟悉", now=NOW) is None
    print("[OK] 使用检出与反馈归因；删共同语言同步退役且不再注入")
    return 0


def main() -> int:
    failed = (
        test_feedback_classification()
        + test_approval_threshold_and_negative_priority()
        + test_cooldown_and_context_gate()
        + test_usage_detection_and_cascade()
    )
    if failed:
        print(f"\n=== L04 幽默记忆：{failed} 项失败 ===")
        return 1
    print("\n=== L04 幽默记忆：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
