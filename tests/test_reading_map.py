# -*- coding: utf-8 -*-
"""F05 阅读地图与书签解锁：建图幂等、finish 唯一解锁、草稿→确认、汇总、删除。

验收锚点（docs/Zcode技术指导.md F05 + 调度文档批次 8）：
- 重开同书不重复建图；跳页不提前解锁；两客户端完成同段幂等；
- 模型观点只进 tuzhan_view，user_view 只来自用户；
- 全书汇总只含 confirmed 书签，未填段只报省略计数；
- 源文档改版 hash 不符 → 409；删除活动清空地图与书签。
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_f05_"))

from backend.core import reading_map as rm
from backend.core.userdb import db

NOW = datetime(2026, 9, 8, 16, 0)


def _doc(uid: str, chunks: list[str]) -> tuple[int, int]:
    """建文档 + 共读活动，返回 (document_id, activity_id)。"""
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO kb_documents (user_id, filename, stored_path, format, chunk_count, ts) "
            "VALUES (?, '测试书.txt', 'docs/test.txt', 'txt', ?, ?)",
            (uid, len(chunks), NOW.isoformat(timespec="seconds")),
        )
        doc_id = int(cur.lastrowid)
        for seq, text in enumerate(chunks):
            db.conn.execute(
                "INSERT INTO kb_chunks (user_id, doc_id, seq, text, ts) VALUES (?, ?, ?, ?, ?)",
                (uid, doc_id, seq, text, NOW.isoformat(timespec="seconds")),
            )
        cur = db.conn.execute(
            "INSERT INTO activities (user_id, kind, document_id, title, status, position, "
            "created_at, updated_at) VALUES (?, 'reading', ?, '共读《测试书.txt》', 'active', 0, ?, ?)",
            (uid, doc_id, NOW.isoformat(timespec="seconds"), NOW.isoformat(timespec="seconds")),
        )
        activity_id = int(cur.lastrowid)
        db.conn.commit()
    return doc_id, activity_id


def test_build_map_idempotent() -> int:
    uid = "f05-build"
    db.ensure_user(uid)
    _, aid = _doc(uid, ["第一段内容", "第二段内容", "第三段内容"])
    data = rm.ensure_map(uid, aid)
    assert data["total"] == 3
    assert [s["status"] for s in data["segments"]] == ["current", "locked", "locked"]
    assert data["segments"][0]["source_start"] == 0
    # 重开同书不重复建图
    again = rm.ensure_map(uid, aid)
    assert again["total"] == 3
    assert db.conn.execute(
        "SELECT COUNT(*) n FROM reading_segments WHERE user_id=?", (uid,)).fetchone()["n"] == 3
    print("[OK] 建图幂等；首段 current、其余 locked")
    return 0


def test_finish_is_the_only_unlock() -> int:
    uid = "f05-finish"
    db.ensure_user(uid)
    _, aid = _doc(uid, ["A", "B", "C"])
    rm.ensure_map(uid, aid)
    # 跳页不算已读：直接完成第 3 段被拒
    try:
        rm.finish_segment(uid, aid, 2)
        raise AssertionError("跳段应被拒绝")
    except rm.ReadingMapError:
        pass
    data = rm.finish_segment(uid, aid, 0)
    assert [s["status"] for s in data["segments"]] == ["read", "current", "locked"]
    # 幂等：两客户端完成同段不报错、不重复推进
    data = rm.finish_segment(uid, aid, 0)
    assert data["segments"][0]["status"] == "read"
    assert data["segments"][1]["status"] == "current"
    # 版本不符 → 409
    try:
        rm.finish_segment(uid, aid, 1, expected_version="deadbeef")
        raise AssertionError("版本不符应 409")
    except rm.ReadingMapConflict:
        pass
    print("[OK] finish 是唯一解锁；跳段拒绝；同段幂等；版本不符 409")
    return 0


def test_bookmark_draft_and_confirm() -> int:
    uid = "f05-bookmark"
    db.ensure_user(uid)
    _, aid = _doc(uid, ["关于海洋的段落", "第二段"])
    data = rm.ensure_map(uid, aid)
    seg_id = data["segments"][0]["id"]
    draft = rm.bookmark_draft(uid, seg_id)
    assert draft["status"] == "draft" and "海洋" in draft["excerpt"]
    # 未确认的草稿不进汇总
    assert "没有留下确认过的书签" in rm.compile_summary(uid, aid)
    saved = rm.save_bookmark(uid, seg_id, user_view="我想去看海",
                             tuzhan_view="她记下了这件事")
    assert saved["status"] == "confirmed" and saved["origin"] == "user"
    summary = rm.compile_summary(uid, aid)
    assert "我想去看海" in summary and "另有 1 段" in summary, summary
    # 空内容拒绝
    try:
        rm.save_bookmark(uid, seg_id)
        raise AssertionError("空书签应被拒绝")
    except rm.ReadingMapError:
        pass
    print("[OK] 草稿→确认；未确认不进汇总；汇总只含 confirmed 且报省略计数")
    return 0


def test_legacy_position_and_forget() -> int:
    uid = "f05-legacy"
    db.ensure_user(uid)
    _, aid = _doc(uid, ["一", "二", "三", "四"])
    with db._lock:
        db.conn.execute("UPDATE activities SET position=2 WHERE id=?", (aid,))
        db.conn.commit()
    data = rm.ensure_map(uid, aid)
    statuses = [s["status"] for s in data["segments"]]
    assert statuses == ["legacy_position", "legacy_position", "current", "locked"], statuses
    # 删除活动清空地图与书签
    seg_id = data["segments"][2]["id"]
    rm.save_bookmark(uid, seg_id, user_view="旧位置的书签")
    assert rm.forget_for_activity(uid, aid) == 4
    assert rm.get_map(uid, aid)["total"] == 0
    assert db.conn.execute(
        "SELECT COUNT(*) n FROM reading_bookmarks WHERE user_id=?", (uid,)).fetchone()["n"] == 0
    print("[OK] 旧活动按当前位置标 legacy_position；删除清空地图与书签")
    return 0


def main() -> int:
    failed = (
        test_build_map_idempotent()
        + test_finish_is_the_only_unlock()
        + test_bookmark_draft_and_confirm()
        + test_legacy_position_and_forget()
    )
    if failed:
        print(f"\n=== F05 阅读地图：{failed} 项失败 ===")
        return 1
    print("\n=== F05 阅读地图：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
