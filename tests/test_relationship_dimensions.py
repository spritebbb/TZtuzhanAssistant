# -*- coding: utf-8 -*-
"""P2-01 信任 × 亲密二维关系回归：迁移、事件入账幂等与日限、撤销重放、
双门槛派生、旧路径降级与升档解锁。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_dims_"))


def test_migration_null_first_time_only() -> int:
    from backend.core.userdb import db

    uid = "dims-migrate"
    db.ensure_user(uid)
    db.conn.execute(
        "UPDATE users SET affection=90, trust=NULL, intimacy=NULL WHERE user_id=?", (uid,)
    )
    db.conn.commit()

    # 模拟重启迁移：NULL → 两侧回填 90
    db.conn.execute(
        "UPDATE users SET trust = affection, intimacy = affection WHERE trust IS NULL"
    )
    db.conn.commit()
    t, i = db.get_user(uid)["trust"], db.get_user(uid)["intimacy"]
    assert (t, i) == (90, 90)

    # 分化后再「迁移」：不覆盖已分化值（90/20 保持）
    db.conn.execute("UPDATE users SET trust=20 WHERE user_id=?", (uid,))
    db.conn.commit()
    db.conn.execute(
        "UPDATE users SET trust = affection, intimacy = affection WHERE trust IS NULL"
    )
    db.conn.commit()
    u = db.get_user(uid)
    assert (u["trust"], u["intimacy"]) == (20, 90), "二次迁移不得重置分化值"
    print("[OK] 迁移按 NULL 判首次，二次不覆盖（90/20 保留）")
    return 0


def test_apply_event_rules_idempotent_and_daily_caps() -> int:
    from backend.core import affection as aff
    from backend.core.userdb import db

    uid = "dims-events"
    db.ensure_user(uid)
    db.set_affection_absolute(uid, 50)

    # 规则映射：约定 trust+2；角色披露被接纳 intimacy+2
    aff.apply_relationship_event(uid, 101, "promise_confirmed")
    t, i = aff.dimensions_of(uid)
    assert (t, i) == (52, 50), (t, i)
    aff.apply_relationship_event(uid, 102, "persona_disclosure_accepted")
    t, i = aff.dimensions_of(uid)
    assert (t, i) == (52, 52), (t, i)

    # 幂等：同一 (event, rule) 二次入账不加
    aff.apply_relationship_event(uid, 101, "promise_confirmed")
    t, i = aff.dimensions_of(uid)
    assert (t, i) == (52, 52), "重复事件不得二次加分"

    # 日限：正向每维 ≤ +4（已 +2，再发 3 个约定只 +2）
    for ev in (201, 202, 203):
        aff.apply_relationship_event(uid, ev, "promise_confirmed")
    t, _ = aff.dimensions_of(uid)
    assert t == 54, f"正向日限应截断到 +4，实际 trust={t}"

    # 负向下限：已确认冒犯连发 → trust ≥ 54-6=48
    for ev in (301, 302, 303, 304):
        aff.apply_relationship_event(uid, ev, "confirmed_offense")
    t, _ = aff.dimensions_of(uid)
    assert t == 48, f"负向日限应截断到 -6，实际 trust={t}"

    # 未登记规则拒绝
    try:
        aff.apply_relationship_event(uid, 401, "no_such_rule")
        raise AssertionError("未登记规则应抛错")
    except ValueError:
        pass
    print("[OK] 规则映射 / 幂等 / 正负日限 / 未登记规则拒绝")
    return 0


def test_revert_reverses_only_that_event() -> int:
    from backend.core import affection as aff
    from backend.core.userdb import db

    uid = "dims-revert"
    db.ensure_user(uid)
    db.set_affection_absolute(uid, 60)
    aff.apply_relationship_event(uid, 501, "promise_confirmed")     # +2
    aff.apply_relationship_event(uid, 502, "boundary_respected")    # +1
    t, _ = aff.dimensions_of(uid)
    assert t == 63

    aff.revert_relationship_event(uid, 501, "promise_confirmed")    # -2，仅该笔
    t, _ = aff.dimensions_of(uid)
    assert t == 61, f"撤销只回退该笔（应 61），实际 {t}"
    # 幂等：撤销已撤销的不再扣
    aff.revert_relationship_event(uid, 501, "promise_confirmed")
    t, _ = aff.dimensions_of(uid)
    assert t == 61
    print("[OK] 撤销按笔反向扣除且幂等")
    return 0


def test_dual_thresholds_and_display() -> int:
    from backend.core import affection as aff
    from backend.core.userdb import db

    uid = "dims-thresholds"
    db.ensure_user(uid)
    # 90/20 = 初识；90/55 = 亲密；80/76 = 恋人（双门槛取低维）
    db.conn.execute(
        "UPDATE users SET trust=90, intimacy=20, affection=20 WHERE user_id=?", (uid,)
    )
    db.conn.commit()
    assert aff.dimensions_stage(90, 20) == "初识"
    assert aff.bond_level_dimensions(90, 20) is None
    assert aff.dimensions_stage(90, 55) == "亲密"
    assert aff.dimensions_stage(80, 76) == "恋人"

    d = aff.display(uid)
    assert d["trust"] == 90 and d["intimacy"] == 20
    assert d["value"] == 20 and d["stage"] == "初识" and d["substage"] == "晚"
    print("[OK] 双门槛派生（90/20 初识、90/55 亲密、80/76 恋人）+ 展示负载")
    return 0


def test_legacy_path_demoted_and_debug_entry() -> int:
    from backend.core import affection as aff
    from backend.core.userdb import db

    uid = "dims-legacy"
    db.ensure_user(uid)
    db.set_affection_absolute(uid, 30)
    # 旧频次奖励：只留 legacy 痕迹，两维与 affection 均不变
    db.update_affection(uid, 5, "每日陪伴")
    u = db.get_user(uid)
    assert (u["trust"], u["intimacy"], u["affection"]) == (30, 30, 30), "频次奖励不得刷两维"
    row = db.conn.execute(
        "SELECT reason FROM affection_log WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (uid,),
    ).fetchone()
    assert "[legacy]" in row["reason"]

    # 调试入口：两维同值 + affection 同步 min
    db.set_affection_absolute(uid, 82)
    u = db.get_user(uid)
    assert (u["trust"], u["intimacy"], u["affection"]) == (82, 82, 82)
    print("[OK] 旧路径降级为统计留痕 / 调试入口两维同值")
    return 0


def test_state_derives_dimensions_and_slices_use_real_values() -> int:
    from backend.core.persona_slices import build_state_view
    from backend.core.state import load_state
    from backend.core.userdb import db

    uid = "dims-state"
    db.ensure_user(uid)
    db.conn.execute(
        "UPDATE users SET trust=95, intimacy=96, affection=95 WHERE user_id=?", (uid,)
    )
    db.conn.commit()
    state = load_state(uid)
    assert (state.trust, state.intimacy) == (95, 96)
    assert state.affection == 95 and state.stage == "恋人"

    # P1-01 视图接真源：95/96 传入后深水区白头门槛按真 trust 判定
    view = build_state_view(stage="恋人", affection=95, trust=95, intimacy=96,
                            now=__import__("datetime").datetime(2026, 9, 7, 21, 0),
                            emotions={"tenderness": 0.6})
    assert view["trust"] == 95 and view["intimacy"] == 96
    print("[OK] AgentState 派生两维 + 切片视图真源")
    return 0


def test_substage_unlock_moments() -> int:
    from backend.core import unlock
    from backend.core.userdb import db

    uid = "dims-unlock"
    db.ensure_user(uid)
    db.set_affection_absolute(uid, 7)
    keys = unlock.check_and_enqueue(uid)  # 首次登记现状
    assert keys == []

    db.set_affection_absolute(uid, 9)     # 跨过初识 8 切点
    keys = unlock.check_and_enqueue(uid)
    assert "substage_chuyi_mid" in keys, keys

    # 连跳多档：一次跨 17/33/42 → 依次入队（每轮最多说一条由 next_pending 控制）
    db.set_affection_absolute(uid, 45)
    keys = unlock.check_and_enqueue(uid)
    assert {"substage_chuyi_late", "substage_shuxi_mid", "substage_shuxi_late"} <= set(keys), keys
    print("[OK] 小档升档时刻（首过登记现状、跨切点入队、连跳逐条）")
    return 0


def main() -> int:
    failed = (
        test_migration_null_first_time_only()
        + test_apply_event_rules_idempotent_and_daily_caps()
        + test_revert_reverses_only_that_event()
        + test_dual_thresholds_and_display()
        + test_legacy_path_demoted_and_debug_entry()
        + test_state_derives_dimensions_and_slices_use_real_values()
        + test_substage_unlock_moments()
    )
    if failed:
        print(f"\n=== P2-01 二维关系：{failed} 项失败 ===")
        return 1
    print("\n=== P2-01 二维关系：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
