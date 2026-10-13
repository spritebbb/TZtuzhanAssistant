# -*- coding: utf-8 -*-
"""P1-02 语境注册表回归：生命周期（sticky/cooldown）、源失效重验、
幂等提交与人格隔离。用测试 provider 驱动，不依赖具体业务 provider。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_ctxreg_"))

from backend.core import context_registry as cr

QUERY = "最近在听什么歌？给我推荐几首"
IDLE_QUERY = "今天好累啊，什么都不想干"

# 已登记用户（模拟「这份语境只属于某个人格作用域」）
_KNOWN: set[str] = set()


class _FakeProvider(cr.ContextProvider):
    """测试 provider：QUERY 命中、alive、且属于已登记用户时产一条候选。"""

    def __init__(self):
        self.entry = cr.ContextEntry(id="fake", namespace="test/fake", source_type="fake", priority=50.0)
        self.alive = True

    def _candidate(self):
        text = "测试语境：一份真实攒下的清单"
        return cr.ContextCandidate(
            entry_id=self.entry.id, source_id="1", source_version="v1",
            text=text, token_count=cr._estimate_tokens(text),
            source_namespace=self.entry.namespace, priority=self.entry.priority, relevance=1.0,
        )

    def collect(self, user_id, query, state, turn_id):
        if not self.alive or user_id not in _KNOWN or QUERY not in query:
            return []
        return [self._candidate()]

    def render(self, candidates):
        return "\n".join(c.text for c in candidates)

    def refresh(self, user_id, source_id):
        if not self.alive or user_id not in _KNOWN or str(source_id) != "1":
            return None
        return self._candidate()


def _setup(uid: str) -> _FakeProvider:
    from backend.core.userdb import db

    db.ensure_user(uid)
    _KNOWN.add(uid)
    p = _FakeProvider()
    cr._PROVIDERS.clear()
    cr.register_provider(p)
    return p


def test_provider_hit_and_idle() -> int:
    from backend.core.context_registry import collect_context

    uid = "ctxreg-parity"
    _setup(uid)
    # 无关聊天：零注入
    sel_idle = collect_context(uid, IDLE_QUERY, turn_id=1)
    assert not sel_idle.items, "无关聊天必须零注入"
    # 话题命中：产候选且 fresh
    sel = collect_context(uid, QUERY, turn_id=2)
    assert sel.items and sel.items[0].source_id == "1"
    assert sel.items[0].fresh and sel.fresh_entry_keys == {"fake:1"}
    print("[OK] provider：无关聊天零注入、命中产候选且 fresh")
    return 0


def test_lifecycle_sticky_and_cooldown() -> int:
    from backend.core.context_registry import collect_context, commit_context_turn
    from backend.core.userdb import db

    uid = "ctxreg-lifecycle"
    _setup(uid)

    # turn 1：话题命中并成功提交 → sticky_until=3, cooldown_until=7
    sel1 = collect_context(uid, QUERY, turn_id=1)
    commit_context_turn(uid, 1, sel1.fresh_entry_keys)
    row = db.conn.execute(
        "SELECT * FROM context_lifecycle WHERE user_id=? AND entry_id=?",
        (uid, "fake:1"),
    ).fetchone()
    assert row is not None and row["sticky_until_turn"] == 3 and row["cooldown_until_turn"] == 7

    # turn 2：话题没接上（普通轮）→ sticky 注入，但不刷新生命周期
    sel2 = collect_context(uid, IDLE_QUERY, turn_id=2)
    assert len(sel2.items) == 1 and not sel2.items[0].fresh, "粘性窗口内应延续注入"
    assert not sel2.fresh_entry_keys, "非命中轮不产生 fresh（不续 sticky）"
    commit_context_turn(uid, 2, sel2.fresh_entry_keys)  # 空集合 no-op
    row2 = db.conn.execute(
        "SELECT last_committed_turn FROM context_lifecycle WHERE user_id=? AND entry_id=?",
        (uid, "fake:1"),
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
    from backend.core.context_registry import collect_context, commit_context_turn

    uid = "ctxreg-invalidate"
    p = _setup(uid)
    sel1 = collect_context(uid, QUERY, turn_id=1)
    commit_context_turn(uid, 1, sel1.fresh_entry_keys)

    # 源关闭：sticky 轮重验失败 → 静默退场
    p.alive = False
    sel2 = collect_context(uid, IDLE_QUERY, turn_id=2)
    assert not sel2.items, "源关闭/取消必须优先于 sticky"
    print("[OK] 源失效优先于 sticky（每轮重验）")
    return 0


def test_idempotent_commit_and_persona_isolation() -> int:
    from backend.core.context_registry import collect_context, commit_context_turn
    from backend.core.userdb import db

    uid = "ctxreg-idem"
    _setup(uid)
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
    assert not sel2.items, "其它人格作用域没有这份语境"
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
        test_provider_hit_and_idle()
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
