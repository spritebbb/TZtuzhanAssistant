# -*- coding: utf-8 -*-
"""G03 悬念/开放问题：触发门控、状态机、复查预算、证据哈希、JOB-1、导出。

验收锚点（docs/Zcode技术指导.md §14.9 + 调度文档批次 5）：
- 只有用户明确要求追踪才建单；普通提问/「我不知道」不追踪；
- 7 天到期、最多 2 次复查、最小间隔 24h；状态 open/researching/resolved/dismissed/expired；
- evidence_hash = 排序后证据条目（canonical_url+title+摘录）规范化哈希，不含模型叙述；
- 证据散列未变/无证据 → 不发假进展（不挂候选）；有新证据 → resolved + 候选；
- JOB-1 认领防跨进程双跑；源消息消失立即 dismissed；LC-1 导出 tasks 类别。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_g03_"))

from backend.core import open_questions as oq
from backend.core.userdb import db

NOW = datetime(2026, 9, 8, 12, 0)


def _evidence(*titles: str) -> list[dict]:
    return [
        {"id": f"E{i + 1}", "title": title, "snippet": f"{title} 的摘录",
         "url": f"https://example{i + 1}.com/a", "domain": f"example{i + 1}.com"}
        for i, title in enumerate(titles)
    ]


def test_detect_and_track() -> int:
    assert oq.detect_tracking_request("帮我查一下这家公司什么时候上市，有结果告诉我")
    assert oq.detect_tracking_request("以后有结果跟我说一声")
    assert oq.detect_tracking_request("继续帮我查那个论文的结论")
    assert oq.detect_tracking_request("我不知道耶") is None, "普通提问不追踪"
    assert oq.detect_tracking_request("今天天气怎么样") is None
    assert oq.detect_tracking_request("") is None

    uid = "g03-track"
    db.ensure_user(uid)
    qid = oq.track_question(uid, "帮我查一下这家公司什么时候上市", source_message_id=None, now=NOW)
    assert qid is not None
    row = oq.get_question(uid, qid)
    assert row["status"] == "open" and row["attempts"] == 0
    assert row["expires_at"] == (NOW + timedelta(days=oq.EXPIRE_DAYS)).isoformat(timespec="seconds")
    assert row["next_check_at"] == (NOW + timedelta(hours=24)).isoformat(timespec="seconds")
    # 同主题未结单幂等
    assert oq.track_question(uid, "  帮我查一下这家公司什么时候上市  ", now=NOW) is None
    print("[OK] 触发门控：仅明确要求追踪建单；同主题幂等")
    return 0


def test_evidence_hash_normalization() -> int:
    a = _evidence("标题甲", "标题乙")
    b = list(reversed(a))  # 顺序无关
    assert oq.evidence_hash(a) == oq.evidence_hash(b)
    assert oq.evidence_hash(a) != oq.evidence_hash(_evidence("标题甲", "标题丙"))
    assert oq.evidence_hash([]) == ""
    # 追踪参数（utm/ref）不影响规范化结果
    noisy = _evidence("标题甲")
    noisy[0]["url"] = "https://example1.com/a?utm_source=x&ref=y"
    assert oq.evidence_hash(noisy) == oq.evidence_hash(_evidence("标题甲"))
    print("[OK] evidence_hash：顺序无关 / 追踪参数无关 / 空证据为空串")
    return 0


def test_research_state_machine() -> int:
    uid = "g03-research"
    db.ensure_user(uid)
    qid = oq.track_question(uid, "帮我查一下量子计算的进展", now=NOW)
    assert oq.due_questions(uid, now=NOW) == [], "24h 内不到点"
    due = oq.due_questions(uid, now=NOW + timedelta(hours=25))
    assert [int(q["id"]) for q in due] == [qid]

    # 第一次复查：有新证据 → resolved + 候选（来源摘要，无模型叙述）
    result = oq.research_question(
        uid, qid, now=NOW + timedelta(hours=25),
        search_fn=lambda q, **kw: [
            {"title": "量子计算新进展", "snippet": "摘要一", "url": "https://a.com/x"},
            {"title": "另一篇报道", "snippet": "摘要二", "url": "https://b.com/y"},
        ],
    )
    assert result["status"] == "resolved", result
    row = oq.get_question(uid, qid)
    assert row["status"] == "resolved" and row["last_evidence_hash"]
    thoughts = db.conn.execute(
        "SELECT kind, source_id, content FROM pending_thoughts "
        "WHERE user_id=? AND kind='open_question_result'", (uid,)).fetchall()
    assert len(thoughts) == 1 and int(thoughts[0]["source_id"]) == qid
    assert "量子计算新进展" in thoughts[0]["content"]

    # 第二次复查同一批证据：哈希未变 → 不再发进展
    qid2 = oq.track_question(uid, "帮我查一下另一个问题", now=NOW)
    first = oq.research_question(
        uid, qid2, now=NOW + timedelta(hours=25),
        search_fn=lambda q, **kw: [
            {"title": "同一篇", "snippet": "同一段", "url": "https://a.com/x"},
            {"title": "第二篇", "snippet": "第二段", "url": "https://b.com/y"},
        ],
    )
    assert first["status"] == "resolved"
    qid3 = oq.track_question(uid, "帮我查一下第三个问题", now=NOW)
    same = oq.research_question(
        uid, qid3, now=NOW + timedelta(hours=25),
        search_fn=lambda q, **kw: [
            {"title": "无新意一", "snippet": "s", "url": "https://c.com/1"},
            {"title": "无新意二", "snippet": "s", "url": "https://d.com/2"},
        ],
    )
    assert same["status"] == "resolved"
    # 同一单再复查：哈希未变 → no_new_evidence，不新增候选
    with db._lock:
        db.conn.execute(
            "UPDATE open_questions SET status='open', next_check_at=? WHERE user_id=? AND id=?",
            (NOW.isoformat(timespec="seconds"), uid, qid3))
        db.conn.commit()
    before = db.conn.execute(
        "SELECT COUNT(*) n FROM pending_thoughts WHERE user_id=? AND kind='open_question_result'",
        (uid,)).fetchone()["n"]
    again = oq.research_question(
        uid, qid3, now=NOW + timedelta(hours=50),
        search_fn=lambda q, **kw: [
            {"title": "无新意一", "snippet": "s", "url": "https://c.com/1"},
            {"title": "无新意二", "snippet": "s", "url": "https://d.com/2"},
        ],
    )
    assert again["status"] == "no_new_evidence", again
    after = db.conn.execute(
        "SELECT COUNT(*) n FROM pending_thoughts WHERE user_id=? AND kind='open_question_result'",
        (uid,)).fetchone()["n"]
    assert after == before, f"无新证据不得再挂候选：{before} -> {after}"

    # 无证据（搜索失败）→ 不 resolved、不发进展
    qid4 = oq.track_question(uid, "帮我查一下第四个问题", now=NOW)
    empty = oq.research_question(
        uid, qid4, now=NOW + timedelta(hours=25), search_fn=lambda q, **kw: [])
    assert empty["status"] == "no_new_evidence", empty
    assert oq.get_question(uid, qid4)["status"] == "open"
    print("[OK] 复查状态机：resolved / 无新证据不发假进展 / 失败不误报")
    return 0


def test_budget_expiry_and_job_claim() -> int:
    uid = "g03-budget"
    db.ensure_user(uid)
    qid = oq.track_question(uid, "帮我查一下预算问题", now=NOW)
    # 两次复查用尽 → expired
    for i in range(oq.MAX_ATTEMPTS):
        oq.research_question(
            uid, qid, now=NOW + timedelta(hours=25 * (i + 1)),
            search_fn=lambda q, **kw: [])
    row = oq.get_question(uid, qid)
    assert row["attempts"] == oq.MAX_ATTEMPTS and row["status"] == "expired", row
    # 到期清理：超过 7 天
    qid2 = oq.track_question(uid, "帮我查一下到期问题", now=NOW)
    assert oq.expire_stale(uid, now=NOW + timedelta(days=8)) >= 1
    assert oq.get_question(uid, qid2)["status"] == "expired"
    # JOB-1：同一单同一轮次不能被第二次认领
    qid3 = oq.track_question(uid, "帮我查一下并发问题", now=NOW)
    scope = oq._scope_key()
    with db._lock:
        assert oq._claim(db.conn, scope, f"q{qid3}:a1", "owner-1", NOW) is True
        assert oq._claim(db.conn, scope, f"q{qid3}:a1", "owner-2", NOW) is False
        oq._finish(db.conn, scope, f"q{qid3}:a1", True, NOW)
    print("[OK] 预算与到期：最多 2 次 / 7 天过期 / JOB-1 认领互斥")
    return 0


def test_source_gone_and_dismiss() -> int:
    uid = "g03-source"
    db.ensure_user(uid)
    mid = db.add_message(uid, "user", "帮我查一下那篇论文，有结果告诉我")
    qid = oq.track_question(uid, "帮我查一下那篇论文", source_message_id=mid, now=NOW)
    assert oq.due_questions(uid, now=NOW + timedelta(hours=25))
    # 源消息被删除 → 立即 dismissed，不再出现在待复查列表
    with db._lock:
        db.conn.execute("DELETE FROM messages WHERE id=?", (mid,))
        db.conn.commit()
    assert oq.due_questions(uid, now=NOW + timedelta(hours=25)) == []
    assert oq.get_question(uid, qid)["status"] == "dismissed"
    # 用户主权：主动作废
    qid2 = oq.track_question(uid, "帮我查一下另一件事", now=NOW)
    assert oq.dismiss_question(uid, qid2, reason="user") is True
    assert oq.get_question(uid, qid2)["status"] == "dismissed"
    print("[OK] 源消息消失即作废；用户可主动放下")
    return 0


def test_export_restore() -> int:
    from backend.core.relationship_export import export_bundle, restore_bundle

    uid, target = "g03-export", "g03-import"
    db.ensure_user(uid)
    mid = db.add_message(uid, "user", "帮我查一下导出问题，有结果告诉我")
    oq.track_question(uid, "帮我查一下导出问题", source_message_id=mid, now=NOW)
    bundle = export_bundle(uid, ["tasks"])
    assert "open_questions" in bundle["data"], list(bundle["data"].keys())
    restore_bundle(bundle, target)
    row = db.conn.execute(
        "SELECT topic, source_message_id, status FROM open_questions WHERE user_id=?",
        (target,)).fetchone()
    assert row is not None and row["status"] == "open"
    assert row["source_message_id"] is None, "源消息不在包内，恢复时清空编号"
    print("[OK] LC-1：tasks 类别导出→恢复；源消息编号不跨库残留")
    return 0


def test_daily_batch_hook() -> int:
    """每日批次复查入口：到点单被复查且不阻塞其余总结。"""
    from backend.core import daily

    uid = "g03-daily"
    db.ensure_user(uid)
    qid = oq.track_question(uid, "帮我查一下每日批次问题", now=NOW)
    with patch("backend.core.open_questions._default_search",
               return_value=[{"title": "批次结果", "snippet": "s", "url": "https://e.com/1"}]), \
         patch("backend.core.open_questions.research_due_questions",
               wraps=oq.research_due_questions) as spy:
        out = asyncio.run(daily.run_daily_batch(uid, NOW.date()))
    assert out is None
    assert spy.called, "每日批次应触发开放问题复查"
    assert oq.get_question(uid, qid) is not None
    print("[OK] 每日批处理触发复查（既有槽位，无新增定时任务）")
    return 0


def main() -> int:
    failed = (
        test_detect_and_track()
        + test_evidence_hash_normalization()
        + test_research_state_machine()
        + test_budget_expiry_and_job_claim()
        + test_source_gone_and_dismiss()
        + test_export_restore()
        + test_daily_batch_hook()
    )
    if failed:
        print(f"\n=== G03 开放问题：{failed} 项失败 ===")
        return 1
    print("\n=== G03 开放问题：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
