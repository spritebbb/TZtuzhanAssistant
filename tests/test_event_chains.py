# -*- coding: utf-8 -*-
"""P2-04 链式反应最小引擎回归：建链幂等、到期/收束、失败重试上限、
用户取消永久、过期熔断与红线（缺席不当失约）。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_chains_"))
NOW = datetime(2026, 9, 8, 10, 0, 0)


def test_create_once_and_due() -> int:
    from backend.core import event_chains as ec
    from backend.core.userdb import db

    uid = "chain-basic"
    db.ensure_user(uid)
    cid = ec.on_promise_completed(uid, 9001, now=NOW)
    assert cid is not None
    assert ec.on_promise_completed(uid, 9001, now=NOW) is None, "同源同规则幂等"

    due = ec.due_chains(uid, now=NOW + timedelta(minutes=19))
    assert due == []
    due = ec.due_chains(uid, now=NOW + timedelta(minutes=21))
    assert len(due) == 1 and due[0]["node"] == "aftermath" and due[0]["attempt"] == 0

    # 心事通道注册了 chain_aftermath（复用既有队列/仲裁，不建第二套计时）
    from backend.core.pending_thoughts import _THOUGHT_KINDS

    assert "chain_aftermath" in _THOUGHT_KINDS
    print("[OK] 建链幂等 + 20 分钟延迟到点 + 复用心事通道")
    return 0


def test_express_closes_chain() -> int:
    from backend.core import event_chains as ec
    from backend.core.userdb import db

    uid = "chain-done"
    db.ensure_user(uid)
    cid = ec.on_promise_completed(uid, 9101, now=NOW)
    ec.mark_expressed(cid, thought_id=77)
    row = ec.chain_for_source(uid, 9101)
    assert row["status"] == "done" and row["attempt"] == 1 and row["result_id"] == 77
    assert ec.due_chains(uid, now=NOW + timedelta(hours=1)) == [], "收束后不再到期"

    # 失败重试：attempt 累加但实例不复制；超上限后过期
    uid2 = "chain-retry"
    db.ensure_user(uid2)
    cid2 = ec.on_promise_completed(uid2, 9201, now=NOW)
    ec.mark_failed_retry(cid2)
    row2 = ec.chain_for_source(uid2, 9201)
    assert row2["status"] == "waiting" and row2["attempt"] == 1
    ec.mark_failed_retry(cid2)
    n = ec.expire_stale(now=NOW)
    assert n >= 1
    assert ec.chain_for_source(uid2, 9201)["status"] == "expired"
    print("[OK] 表达即收束 / 失败重试不复制 / 超限过期熔断")
    return 0


def test_user_cancel_is_permanent() -> int:
    from backend.core import event_chains as ec
    from backend.core.userdb import db

    uid = "chain-cancel"
    db.ensure_user(uid)
    cid = ec.on_promise_completed(uid, 9301, now=NOW)
    assert ec.cancel_chain(uid, 9301) is True
    assert ec.due_chains(uid, now=NOW + timedelta(hours=2)) == []
    # 取消后完成事件再来 → 尊重取消不重建
    assert ec.on_promise_completed(uid, 9301, now=NOW + timedelta(hours=3)) is None
    print("[OK] 用户取消永久（不再建链）")
    return 0


def test_absence_is_not_breach() -> int:
    """红线：对话正常缺席不当失约——链只有一种（完成后的正向回望），
    无任何「延迟回复→负向链」规则；负向规则在代码层不存在。"""
    from backend.core import event_chains as ec

    assert ec.RULE_ID == "promise_aftermath"
    assert not [name for name in dir(ec) if "negative" in name or "punish" in name]
    print("[OK] 无负向链（缺席不当失约，硬熔断在代码层）")
    return 0


def main() -> int:
    failed = (
        test_create_once_and_due()
        + test_express_closes_chain()
        + test_user_cancel_is_permanent()
        + test_absence_is_not_breach()
    )
    if failed:
        print(f"\n=== P2-04 链式引擎：{failed} 项失败 ===")
        return 1
    print("\n=== P2-04 链式引擎：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
