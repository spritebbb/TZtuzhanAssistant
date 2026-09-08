# -*- coding: utf-8 -*-
"""P1-02 语境注册表回归：provider 迁移一致性、生命周期（sticky/cooldown）、
源失效重验、幂等提交与人格隔离。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_ctxreg_"))

QUERY = "最近在听什么歌？给我推荐几首"
IDLE_QUERY = "今天好累啊，什么都不想干"


def _setup_lists(uid: str) -> int:
    from backend.core import colists
    from backend.core.userdb import db

    db.ensure_user(uid)
    item = colists.start_list(uid, "深夜歌单", "song")
    colists.add_item(uid, item["id"], "夜航星", "匿名歌手", "循环了一周")
    colists.add_item(uid, item["id"], "慢速公路", "", "")
    return item["id"]


def test_provider_matches_legacy_output() -> int:
    from backend.core import colists
    from backend.core.context_registry import collect_context

    uid = "ctxreg-parity"
    aid = _setup_lists(uid)

    # 无关聊天：新旧路径都是零注入
    legacy_idle = colists.list_context(uid, IDLE_QUERY)
    sel_idle = collect_context(uid, IDLE_QUERY, turn_id=1)
    assert legacy_idle == "" and not sel_idle.items, "无关聊天必须零注入"

    # 话题命中：注册表渲染文本与旧路径完全一致（单清单场景）
    legacy = colists.list_context(uid, QUERY)
    sel = collect_context(uid, QUERY, turn_id=2)
    assert sel.items and sel.assemble() == legacy, "开关打开前后注入文本应一致"
    assert sel.items[0].source_id == str(aid)
    assert sel.items[0].fresh and sel.fresh_entry_keys == {f"colists:{aid}"}
    assert sel.explain()[0]["reasons"] == ["topic_match"]
    print("[OK] provider 迁移与旧路径输出一致（含无关聊天零注入）")
    return 0


def test_lifecycle_sticky_and_cooldown() -> int:
    from backend.core.context_registry import collect_context, commit_context_turn
    from backend.core.userdb import db

    uid = "ctxreg-lifecycle"
    aid = _setup_lists(uid)

    # turn 1：话题命中并成功提交 → sticky_until=3, cooldown_until=7
    sel1 = collect_context(uid, QUERY, turn_id=1)
    commit_context_turn(uid, 1, sel1.fresh_entry_keys)
    row = db.conn.execute(
        "SELECT * FROM context_lifecycle WHERE user_id=? AND entry_id=?",
        (uid, f"colists:{aid}"),
    ).fetchone()
    assert row is not None and row["sticky_until_turn"] == 3 and row["cooldown_until_turn"] == 7

    # turn 2：话题没接上（普通轮）→ sticky 注入，但不刷新生命周期
    sel2 = collect_context(uid, IDLE_QUERY, turn_id=2)
    assert len(sel2.items) == 1 and not sel2.items[0].fresh, "粘性窗口内应延续注入"
    assert not sel2.fresh_entry_keys, "非命中轮不产生 fresh（不续 sticky）"
    commit_context_turn(uid, 2, sel2.fresh_entry_keys)  # 空集合 no-op
    row2 = db.conn.execute(
        "SELECT last_committed_turn FROM context_lifecycle WHERE user_id=? AND entry_id=?",
        (uid, f"colists:{aid}"),
    ).fetchone()
    assert row2["last_committed_turn"] == 1, "非命中轮不得刷新生命周期"

    # turn 3：粘性窗口内（sticky_until=3）仍延续
    sel3 = collect_context(uid, IDLE_QUERY, turn_id=3)
    assert len(sel3.items) == 1 and not sel3.items[0].fresh

    # turn 4：粘性已过、处于冷却（cooldown_until=7）→ 静默
    sel4 = collect_context(uid, IDLE_QUERY, turn_id=4)
    assert not sel4.items, "冷却期内不得冷启动注入"

    # 冷却期内 fresh 命中不受限（用户明确聊到就给）
    sel5 = collect_context(uid, QUERY, turn_id=5)
    assert sel5.items and sel5.items[0].fresh

    # turn 8：冷却结束后的普通轮也静默（无 sticky 可续）
    sel8 = collect_context(uid, IDLE_QUERY, turn_id=8)
    assert not sel8.items
    print("[OK] sticky=2 / cooldown=4 生命周期（非命中不续、fresh 不受限）")
    return 0


def test_source_invalidation_beats_sticky() -> int:
    from backend.core import colists
    from backend.core.context_registry import collect_context, commit_context_turn

    uid = "ctxreg-invalidate"
    aid = _setup_lists(uid)
    sel1 = collect_context(uid, QUERY, turn_id=1)
    commit_context_turn(uid, 1, sel1.fresh_entry_keys)

    # 源取消（收列后又取消）：sticky 轮重验失败 → 静默退场
    colists.cancel_list(uid, aid)
    sel2 = collect_context(uid, IDLE_QUERY, turn_id=2)
    assert not sel2.items, "源关闭/取消必须优先于 sticky"
    print("[OK] 源失效优先于 sticky（每轮重验）")
    return 0


def test_idempotent_commit_and_persona_isolation() -> int:
    from backend.core.context_registry import collect_context, commit_context_turn
    from backend.core.userdb import db

    uid = "ctxreg-idem"
    aid = _setup_lists(uid)
    sel1 = collect_context(uid, QUERY, turn_id=10)
    commit_context_turn(uid, 10, sel1.fresh_entry_keys)
    commit_context_turn(uid, 10, sel1.fresh_entry_keys)  # 重试同一轮
    rows = db.conn.execute(
        "SELECT COUNT(*) AS n FROM context_lifecycle WHERE user_id=?", (uid,)
    ).fetchone()
    assert rows["n"] == 1, "同一轮重复提交不多扣"

    # 人格隔离：另一 user_id（persona scope 后缀模拟）互不可见
    uid2 = "ctxreg-idem::persona::other"
    sel2 = collect_context(uid2, QUERY, turn_id=11)
    assert not sel2.items, "其它人格作用域没有这份清单"
    print("[OK] 幂等提交 + 人格作用域隔离")
    return 0


def test_flag_default_off_and_reset_table() -> int:
    from backend.core.features import flag
    from backend.core.userdb import db

    # 2026-09-08 拍板：开关接入设置页面板后默认开启（体验收口迭代）。
    assert flag("context_registry_enabled") is True, "开关默认开启（设置页可关闭）"
    db.conn.execute("DELETE FROM context_lifecycle")
    db.conn.commit()
    print("[OK] 开关默认开 + 生命周期表可清理（已入 reset 清单）")
    return 0


def main() -> int:
    failed = (
        test_provider_matches_legacy_output()
        + test_lifecycle_sticky_and_cooldown()
        + test_source_invalidation_beats_sticky()
        + test_idempotent_commit_and_persona_isolation()
        + test_flag_default_off_and_reset_table()
    )
    if failed:
        print(f"\n=== P1-02 语境注册表：{failed} 项失败 ===")
        return 1
    print("\n=== P1-02 语境注册表：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
