# -*- coding: utf-8 -*-
"""P2-03 对称约定回归：owner 对称、promise_hash 幂等、到期状态机、
完成入账一次、用户取消不扣分、迁移兼容。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_promises_"))


def test_owner_symmetry_and_hash_idempotent() -> int:
    from backend.core.userdb import (get_open_promises, normalize_promise_hash,
                                     save_promise)

    uid = "sym-promise"
    from backend.core.userdb import db

    db.ensure_user(uid)

    # 她许下的约定：owner=assistant + 叙事 action_kind
    # 期限必须相对今天：get_open_promises 会先推进到期状态机，写死的过去日期
    # 会让这条 open 约定被标记 expired，下面的行断言随之找不到。
    due = (date.today() + timedelta(days=3)).isoformat()
    pid = save_promise(uid, "周末把那本书的读后感写给你", due,
                       owner="assistant", due_at=due)
    assert pid is not None
    rows = get_open_promises(uid)
    row = [r for r in rows if r["id"] == pid][0]
    assert row["owner"] == "assistant" and row["action_kind"] is None
    assert row["namespace"] == "user_real"

    # hash 幂等：同义重复（仅标点差异）不重复建行
    again = save_promise(uid, "周末把那本书的读后感写给你。", owner="assistant")
    assert again is None, "规范化后同 hash 的 open 约定不得重复"
    # hash 只看内容：owner 不同也不重复（同一件事两人都记 → 一行一 owner）
    third = save_promise(uid, "周末，把那本书的读后感写给你", owner="user")
    assert third is None

    # 规范化顺序可复算（13.2：NFKC→lower→去标点→压空白）
    assert normalize_promise_hash("Ａｂc， 你好！") == normalize_promise_hash("abc 你好")
    print("[OK] owner 对称 / hash 规范化幂等 / 叙事约定标记")
    return 0


def test_expiry_state_machine_and_no_penalty() -> int:
    from backend.core.affection import dimensions_of
    from backend.core.userdb import (db, expire_due_promises, get_open_promises,
                                     save_promise)

    uid = "sym-expire"
    db.ensure_user(uid)
    db.set_affection_absolute(uid, 50)
    today = date.today()

    # 有期限且已过 → expired；无期限 → 永不自动失约
    save_promise(uid, "周五前整理歌单", owner="user",
                 due_at=(today - timedelta(days=1)).isoformat())
    save_promise(uid, "改天一起看那部片", owner="user", due_at=None)
    save_promise(uid, "下周给你画张图", owner="assistant",
                 due_at=(today - timedelta(days=2)).isoformat())
    n = expire_due_promises(uid, today.isoformat())
    assert n == 2, f"应推进 2 条到期，实际 {n}"

    remaining = get_open_promises(uid)
    contents = [r["content"] for r in remaining]
    assert contents == ["改天一起看那部片"], contents

    # 到期推进不扣分（用户未做到不当失约；她自己的到期走坦白）
    t, i = dimensions_of(uid)
    assert (t, i) == (50, 50), "到期状态机不得产生数值惩罚"
    print("[OK] 到期状态机 / 无期限不自动失约 / 到期不扣分")
    return 0


def test_completion_accounts_once() -> int:
    from backend.core.affection import dimensions_of
    from backend.core.userdb import db, mark_promise_done, save_promise

    uid = "sym-done"
    db.ensure_user(uid)
    db.set_affection_absolute(uid, 50)
    pid = save_promise(uid, "明天把演示发过去", "2026-09-08", owner="user")

    mark_promise_done(pid)
    t, i = dimensions_of(uid)
    assert t == 52, f"用户约定完成应 trust+2，实际 {t}"
    # 幂等：重复标记不二次加分
    mark_promise_done(pid)
    t2, _ = dimensions_of(uid)
    assert t2 == 52, "重复完成不得二次入账"

    # assistant owner 的完成不动用户信任（她做完自己的事不是用户的功劳）
    pid2 = save_promise(uid, "把读后感写给你", owner="assistant")
    mark_promise_done(pid2)
    t3, _ = dimensions_of(uid)
    assert t3 == 52
    print("[OK] 完成按 owner 入账且幂等")
    return 0


def test_legacy_rows_migrated() -> int:
    from backend.core.userdb import db

    uid = "sym-legacy"
    db.ensure_user(uid)
    # 模拟旧行：无 owner/hash，状态 pending
    with db._lock:
        db.conn.execute(
            "INSERT INTO promises (user_id, content, follow_up, status, source, created_at) "
            "VALUES (?, '旧版约定', '', 'pending', '', '2026-09-01')",
            (uid,),
        )
        db.conn.execute(
            "UPDATE promises SET status='open' WHERE status='pending'"
        )
        db.conn.commit()
    row = db.conn.execute(
        "SELECT owner, namespace FROM promises WHERE user_id=?", (uid,)
    ).fetchone()
    assert row["owner"] == "user" and row["namespace"] == "user_real", "旧行默认 user 域"
    print("[OK] 旧约定行按 user 域兼容")
    return 0


def main() -> int:
    failed = (
        test_owner_symmetry_and_hash_idempotent()
        + test_expiry_state_machine_and_no_penalty()
        + test_completion_accounts_once()
        + test_legacy_rows_migrated()
    )
    if failed:
        print(f"\n=== P2-03 对称约定：{failed} 项失败 ===")
        return 1
    print("\n=== P2-03 对称约定：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
