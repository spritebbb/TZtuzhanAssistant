# -*- coding: utf-8 -*-
"""38项#29 产物回访：共同产物完成数天后，她主动回访一次（走 pending_thoughts）。

验收锚点：
- 生产者：status=active 且三类产物、created_at 锚 3~30 天窗口 → 挂一条
  artifact_revisit 心事；窗口外（太新/太老）、非三类产物不挂；幂等一生一次；
- 存活检查：产物 status 非 active / 删除 → _source_alive False，不注入不表达；
- 阶段门：artifact_revisit 只在熟悉及以上表达（初识只放行 resume_reading）；
- surprise 互斥：已挂回访心事的产物不再当惊喜素材，全部挂过 → 无素材。

运行：D:/TZtuzhanAssistant/.venv/Scripts/python -m tests.test_artifact_revisit
"""
from __future__ import annotations

import datetime
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_revisit_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import pending_thoughts as pt  # noqa: E402
from backend.core import surprise  # noqa: E402
from backend.core.userdb import db  # noqa: E402

NOW = datetime.datetime.now().replace(microsecond=0)

_SEQ = [7000]


def _insert_artifact(uid: str, title: str, artifact_type: str, *, created_days_ago: float,
                     status: str = "active") -> int:
    """造一件共同产物；created_at 锚首次完成，updated_at 同刻（无编辑干扰）。"""
    _SEQ[0] += 1  # 唯一键 (user, artifact_type, source_id)：source_id 用自增序列不撞
    ts = (NOW - datetime.timedelta(days=created_days_ago)).isoformat(timespec="seconds")
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO artifacts (user_id, artifact_type, source_type, source_id, title, "
            "content, version, created_at, updated_at, status) "
            "VALUES (?, ?, 'activity', ?, ?, '一起留下的真实内容。', 1, ?, ?, ?)",
            (uid, artifact_type, _SEQ[0], title, ts, ts, status),
        )
        db.conn.commit()
    return int(cur.lastrowid)


def _revisit_thoughts(uid: str) -> list[dict]:
    return [t for t in pt.due_thoughts(uid, limit=10) if t["kind"] == "artifact_revisit"]


def test_sync_window_and_idempotency() -> int:
    uid = "revisit-window"
    db.ensure_user(uid)
    old_id = _insert_artifact(uid, "灯塔看守人的猫", "co_story", created_days_ago=4)
    _insert_artifact(uid, "深夜食堂", "book_summary", created_days_ago=2)   # 太新：未满 3 天
    _insert_artifact(uid, "年初的健身目标", "goal_review", created_days_ago=31)  # 太老：30 天外
    _insert_artifact(uid, "别的收藏", "relationship_object", created_days_ago=4)  # 非三类素材

    added = pt.sync_pending_thoughts(uid)
    thoughts = _revisit_thoughts(uid)
    assert added >= 1, "4 天前的产物应挂回访心事"
    assert len(thoughts) == 1, f"窗口/类型过滤后只应有 1 条，实际 {len(thoughts)}"
    assert thoughts[0]["source_type"] == "artifact"
    assert int(thoughts[0]["source_id"]) == old_id, "只回访 4 天前的那件"
    assert "灯塔看守人的猫" in thoughts[0]["content"], thoughts[0]["content"]
    assert "她" in thoughts[0]["content"], "文案是她的口吻（自己想起来的）"

    # 幂等：同源一生只挂一次；连 dismissed 也不再挂（INSERT OR IGNORE 撞唯一键）
    assert pt.sync_pending_thoughts(uid) == 0
    pt.dismiss_thought(uid, int(thoughts[0]["id"]))
    assert pt.sync_pending_thoughts(uid) == 0
    assert _revisit_thoughts(uid) == [], "放下过的回访不再回来"
    print("[OK] 生产者：3~30 天窗口三类产物才挂；幂等一生一次（dismissed 也不复发）")
    return 0


def test_source_alive_and_stage_gate() -> int:
    uid = "revisit-alive"
    db.ensure_user(uid)
    aid = _insert_artifact(uid, "便利店深夜清单", "co_story", created_days_ago=4)
    pt.sync_pending_thoughts(uid)
    thought = _revisit_thoughts(uid)[0]
    alive = {"source_type": "artifact", "source_id": aid}

    assert pt._source_alive(uid, alive) is True, "active 产物应存活"
    # 阶段门：初识只放行 resume_reading，回访不上桌
    assert pt.next_thought_for_stage(uid, "初识") is None, "初识不得表达产物回访"
    got = pt.next_thought_for_stage(uid, "熟悉")
    assert got is not None and got["kind"] == "artifact_revisit", "熟悉及以上可表达回访"

    # 产物归档（status 非 active）→ 来源死亡，双路径都不再提
    with db._lock:
        db.conn.execute("UPDATE artifacts SET status='archived' WHERE id=?", (aid,))
        db.conn.commit()
    assert pt._source_alive(uid, alive) is False, "非 active 产物即死亡"
    assert pt.next_thought_for_stage(uid, "熟悉") is None, "来源死亡不得表达"
    # 产物删除 → 同样死亡
    with db._lock:
        db.conn.execute("UPDATE artifacts SET status='active' WHERE id=?", (aid,))
        db.conn.execute("DELETE FROM artifacts WHERE id=?", (aid,))
        db.conn.commit()
    assert pt._source_alive(uid, alive) is False
    print("[OK] 存活检查：非 active/删除即作废；阶段门：熟悉以上才表达")
    return 0


def test_surprise_material_exclusion() -> int:
    uid = "revisit-surprise"
    db.ensure_user(uid)
    fresh_id = _insert_artifact(uid, "新的共同故事", "co_story", created_days_ago=1)
    visited_id = _insert_artifact(uid, "已回访过的共同故事", "co_story", created_days_ago=1)

    # 已挂 artifact_revisit 心事的产物（不论 pending/expressed）不再当惊喜素材
    assert pt._add(uid, "artifact_revisit", "artifact", visited_id, "已回访过的心事") is not None
    picked = surprise._pick_material(uid)
    assert picked is not None and int(picked["id"]) == fresh_id, \
        f"惊喜素材应避开已回访产物，实际选中 #{picked and picked['id']}"

    # 全部挂过 → 无素材（宁可不出牌，不撞车）
    assert pt._add(uid, "artifact_revisit", "artifact", fresh_id, "也回访过了") is not None
    assert surprise._pick_material(uid) is None, "全部产物已回访时应返回 None"
    print("[OK] surprise 互斥：已回访产物不当惊喜素材；全挂过则无素材")
    return 0


def main() -> int:
    failed = (
        test_sync_window_and_idempotency()
        + test_source_alive_and_stage_gate()
        + test_surprise_material_exclusion()
    )
    if failed:
        print(f"\n=== 产物回访（38项#29）：{failed} 项失败 ===")
        return 1
    print("\n=== 产物回访（38项#29）：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
