# -*- coding: utf-8 -*-
"""F06 活动草稿：意图识别、签名短期引用、幂等确认、互斥告知。

验收锚点（docs/Zcode技术指导.md F06 + 调度文档批次 8）：
- 明确邀请才产草稿；普通提及不产；置信不足只问所缺主题；
- 草稿是签名短期引用（20 分钟、进程重启失效），服务端无草稿表；
- 同草稿二次确认返回同一活动（幂等回执）；跨人格/过期/伪造一律拒绝；
- 确认走既有权威 start 函数；活动互斥先告知；
- 开关关闭不产草稿，手动入口不受影响。
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_f06_"))

from backend.core import activity_drafts as ad
from backend.core.userdb import db

NOW = datetime(2026, 9, 8, 11, 0)


def test_detect_intent() -> int:
    assert ad.detect_draft_intent("我想一起做点什么，比如一起定个目标")[0] == "goal"
    assert ad.detect_draft_intent("要不要一起读一本书，弄个书单")[0] == "book_list"
    assert ad.detect_draft_intent("陪我一起写故事吧")[0] == "writing"
    assert ad.detect_draft_intent("咱们一起搞个歌单")[0] == "song_list"
    assert ad.detect_draft_intent("昨天读了一本书") is None, "普通提及不产草稿"
    assert ad.detect_draft_intent("今天天气不错") is None
    assert ad.detect_draft_intent("") is None
    print("[OK] 意图识别：明确邀请才产；普通提及不产")
    return 0


def test_signed_short_lived_draft() -> int:
    uid = "f06-draft"
    db.ensure_user(uid)
    draft = ad.create_draft(uid, "goal", "一起坚持晨跑",
                            payload={"motivation": "想早起", "next_step": "先定闹钟"},
                            source_turn_id=11, now=NOW)
    assert draft and draft["draft_id"] and draft["kind"] == "goal"
    assert draft["payload"]["next_step"] == "先定闹钟"
    # 伪造签名拒绝
    try:
        ad.confirm_draft(uid, draft["draft_id"] + "x", now=NOW)
        raise AssertionError("伪造签名应被拒绝")
    except ad.ActivityDraftError:
        pass
    # 过期拒绝
    try:
        ad.confirm_draft(uid, draft["draft_id"], now=NOW + timedelta(minutes=21))
        raise AssertionError("过期草稿应被拒绝")
    except ad.ActivityDraftError:
        pass
    # 跨人格拒绝
    db.ensure_user("f06-other")
    try:
        ad.confirm_draft("f06-other", draft["draft_id"], now=NOW)
        raise AssertionError("跨人格应被拒绝")
    except ad.ActivityDraftError:
        pass
    # 未知 kind 不产
    assert ad.create_draft(uid, "hack", "x", now=NOW) is None
    with patch("backend.core.features.flag", return_value=False):
        assert ad.create_draft(uid, "goal", "x", now=NOW) is None
    print("[OK] 签名短期引用：伪造/过期/跨人格/未知类型一律拒绝；开关可关")
    return 0


def test_confirm_idempotent_and_authoritative() -> int:
    uid = "f06-confirm"
    db.ensure_user(uid)
    draft = ad.create_draft(uid, "goal", "一起背单词",
                            payload={"motivation": "考试", "next_step": "每天 20 个"},
                            now=NOW)
    result = ad.confirm_draft(uid, draft["draft_id"], now=NOW)
    assert result["ok"] and result["activity_id"] > 0
    assert result["activity"]["title"] == "一起背单词"
    # 幂等：二次确认返回同一活动，不重复创建
    again = ad.confirm_draft(uid, draft["draft_id"], now=NOW)
    assert again["idempotent"] and again["activity_id"] == result["activity_id"]
    assert db.conn.execute(
        "SELECT COUNT(*) n FROM activities WHERE user_id=? AND kind='goal'",
        (uid,)).fetchone()["n"] == 1
    # 前端可改 title/payload
    draft2 = ad.create_draft(uid, "song_list", "深夜歌单", now=NOW)
    out = ad.confirm_draft(uid, draft2["draft_id"], title="通勤歌单", now=NOW)
    assert out["activity"]["title"] == "通勤歌单"
    print("[OK] 确认走权威 start 函数；同草稿二次确认幂等；可改标题")
    return 0


def test_mutex_notice() -> int:
    uid = "f06-mutex"
    db.ensure_user(uid)
    with db._lock:
        db.conn.execute(
            "INSERT INTO activities (user_id, kind, document_id, title, status, created_at, updated_at) "
            "VALUES (?, 'goal', 0, '旧目标', 'active', ?, ?)",
            (uid, NOW.isoformat(timespec="seconds"), NOW.isoformat(timespec="seconds")),
        )
        db.conn.commit()
    notice = ad.active_mutex_notice(uid, "goal")
    assert "旧目标" in notice and "暂停" in notice
    assert ad.active_mutex_notice(uid, "writing") == ""
    print("[OK] 互斥告知：同壳有进行中活动时先告知会暂停")
    return 0


def main() -> int:
    failed = (
        test_detect_intent()
        + test_signed_short_lived_draft()
        + test_confirm_idempotent_and_authoritative()
        + test_mutex_notice()
    )
    if failed:
        print(f"\n=== F06 活动草稿：{failed} 项失败 ===")
        return 1
    print("\n=== F06 活动草稿：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
