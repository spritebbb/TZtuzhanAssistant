# -*- coding: utf-8 -*-
"""P1-04 行程与时间 tick 回归：确定性推进、幂等、素材不重复、压缩补算、
跨进程认领（真实双子进程竞争）、人格隔离与 reset 清理。"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
UTC = timezone.utc
AT = datetime(2026, 9, 7, 13, 0, 0, tzinfo=UTC)  # 周一 13:00 UTC = 本地 21:00


def _event_count(uid: str) -> int:
    from backend.core.userdb import db

    row = db.conn.execute(
        "SELECT COUNT(*) AS n FROM character_life_events WHERE user_id=?", (uid,)
    ).fetchone()
    return int(row["n"])


def test_advance_anchor_and_idempotent_hours() -> int:
    from backend.core.schedule import advance_schedule
    from backend.core.userdb import db

    uid = "tick-unit"
    db.ensure_user(uid)
    # 首调只登记锚点，不伪造历史生活
    first = advance_schedule(uid, AT)
    assert first["events"] == 0 and first["written"]
    assert _event_count(uid) == 0

    # 推进 3 小时（本地 21:00→24:00，跨两个本地日）：每小时记账，跨日各写一条素材
    adv = advance_schedule(uid, AT + timedelta(hours=3))
    assert adv["hours_processed"] == 3 and adv["events"] == 2, adv
    assert _event_count(uid) == 2

    # 同一小时重复推进：零增量
    again = advance_schedule(uid, AT + timedelta(hours=3))
    assert again["hours_processed"] == 0, "同一小时重复执行必须无增量"
    print("[OK] 首调锚点 / 逐时推进 / 同小时幂等")
    return 0


def test_daily_material_unique_within_7d() -> int:
    from backend.core.schedule import advance_schedule, _load_state
    from backend.core.userdb import db

    uid = "tick-materials"
    db.ensure_user(uid)
    advance_schedule(uid, AT)
    # 推进 10 天：每天一条素材；任意连续 7 天窗口内素材不重复
    advance_schedule(uid, AT + timedelta(days=10))
    from backend.core.userdb import kv_get

    state = json.loads(kv_get(uid, "state:schedule") or "{}")
    recent = state.get("recent_material_ids", [])
    assert len(recent) <= 7
    assert len(recent) == len(set(recent)), f"7 日滑窗内素材重复: {recent}"
    # 每个本地日最多一条 daily_life：事件数 = 推进天数（10 天里每个本地日 1 条）
    rows = db.conn.execute(
        "SELECT occurrence, COUNT(*) AS n FROM character_life_events "
        "WHERE user_id=? AND kind='daily_life' GROUP BY occurrence HAVING n>1",
        (uid,),
    ).fetchall()
    assert not rows, f"存在同日多条素材: {[dict(r) for r in rows]}"
    print("[OK] 每日一条素材 + 7 日滑窗不重复")
    return 0


def test_compressed_catchup_beyond_window() -> int:
    from backend.core.schedule import advance_schedule
    from backend.core.userdb import db, kv_set

    uid = "tick-compress"
    db.ensure_user(uid)
    # 手工把锚点放到 200 小时前：压缩推进只造最近 3 天的事件
    state = {
        "format_version": 1, "version": 0, "current_block_id": "", "local_day": "",
        "energy_delta_today": 0.0, "mood_delta_today": 0.0,
        "last_processed_utc": (AT - timedelta(hours=200)).isoformat(),
        "recent_material_ids": [],
    }
    kv_set(uid, "state:schedule", json.dumps(state, ensure_ascii=False))
    result = advance_schedule(uid, AT)
    assert result["compressed"] is True, result
    assert result["hours_processed"] <= 72, "压缩后不得逐小时结算久远区间"
    # 事件全部带 computed_at（补算标记），不冒充在线观察
    from backend.core.userdb import db as _db

    rows = _db.conn.execute(
        "SELECT computed_at, occurred_at FROM character_life_events WHERE user_id=?",
        (uid,),
    ).fetchall()
    assert rows and all(r["computed_at"] for r in rows)
    print("[OK] 超窗压缩推进 + computed_at 补算标记")
    return 0


def test_midnight_block_spanning() -> int:
    from datetime import datetime as dt

    from backend.core.schedule import block_at

    # 本地 23:30（工作日）→ late 块；01:00 → 前一天的 late 块；05:00 → 睡眠无块
    monday_night = dt(2026, 9, 7, 23, 30)
    assert block_at(monday_night).id == "weekday-late"
    tuesday_dawn = dt(2026, 9, 8, 1, 0)
    assert block_at(tuesday_dawn).id == "weekday-late", "跨午夜后半段归前一天 late 块"
    assert block_at(dt(2026, 9, 8, 5, 0)) is None, "睡眠时段无块"
    print("[OK] 跨午夜块归属")
    return 0


def _spawn_tick(data_root: Path, at: datetime, extra: list[str] | None = None) -> dict:
    env = os.environ.copy()
    env["FEATURE_TELEMETRY_ENABLED"] = "1"
    proc = subprocess.run(
        [str(PY), "-X", "utf8", "-m", "backend.maintenance.time_tick",
         "--data-root", str(data_root), "--at", at.isoformat(), "--json",
         *(extra or [])],
        cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    lines = (proc.stdout or "").strip().splitlines()
    if not lines:
        raise AssertionError(
            f"time_tick 子进程无输出（exit={proc.returncode}）：\n{proc.stderr[-1500:]}")
    return json.loads(lines[-1])


def test_subprocess_race_single_claim() -> int:
    import concurrent.futures

    data_root = Path(tempfile.mkdtemp(prefix="tztuzhan_tick_race_"))
    args = ["--once"]

    def worker(_: int) -> dict:
        return _spawn_tick(data_root, AT, args)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        outs = list(pool.map(worker, range(2)))
    succeeded = sum(o.get("succeeded", 0) for o in outs)
    skipped = sum(o.get("skipped", 0) for o in outs)
    assert succeeded == 1 and skipped == 1, outs

    # 第三个进程补跑同周期：也让位（succeeded 幂等）
    third = _spawn_tick(data_root, AT, args)
    assert third.get("skipped", 0) == 1 and third.get("succeeded", 0) == 0, third

    # 同一小时的稍后时刻再来（时钟回拨到周期内任意点）：仍是同一 period 键，零增量
    back = _spawn_tick(data_root, AT + timedelta(minutes=15), args)
    assert back.get("succeeded", 0) == 0, back

    telemetry = sqlite3.connect(data_root / "telemetry.db")
    rows = telemetry.execute(
        "SELECT event_name,job_run_id,outcome FROM telemetry_events "
        "WHERE event_name IN ('job_started','job_finished') ORDER BY id"
    ).fetchall()
    telemetry.close()
    expected_id = f"time_tick:{AT.isoformat().replace('+00:00', 'Z')}"
    assert rows == [
        ("job_started", expected_id, "started"),
        ("job_finished", expected_id, "success"),
    ], rows
    print("[OK] 真实双子进程竞争恰好认领一次 / 重复与回拨零增量")
    return 0


def test_dry_run_and_persona_isolation() -> int:
    data_root = Path(tempfile.mkdtemp(prefix="tztuzhan_tick_dry_"))
    dry = _spawn_tick(data_root, AT, ["--dry-run"])
    assert dry.get("dry_run") is True and dry.get("periods"), dry

    iso = _spawn_tick(data_root, AT, ["--once", "--persona-id", "other-persona"])
    assert iso.get("scope") == "persona::other-persona", iso
    assert iso.get("succeeded", 1) == 1, iso
    print("[OK] dry-run 不落库 + 人格作用域隔离")
    return 0


def test_reset_tables_registered() -> int:
    from backend.core.reset import _TABLES

    assert "character_life_events" in _TABLES
    # job_runs 没有 user_id，reset_everything 按 persona scope_key 单独清理；
    # 放进通用 _TABLES 会重新引入 "no such column: user_id"。
    import inspect
    from backend.core import reset

    source = inspect.getsource(reset.reset_everything)
    assert "DELETE FROM job_runs WHERE scope_key=?" in source
    assert "job_runs" not in _TABLES
    print("[OK] 生活事件走通用 reset，job_runs 按人格 scope 单独清理")
    return 0


def main() -> int:
    os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_tick_"))
    failed = (
        test_advance_anchor_and_idempotent_hours()
        + test_daily_material_unique_within_7d()
        + test_compressed_catchup_beyond_window()
        + test_midnight_block_spanning()
        + test_subprocess_race_single_claim()
        + test_dry_run_and_persona_isolation()
        + test_reset_tables_registered()
    )
    if failed:
        print(f"\n=== P1-04 时间 tick：{failed} 项失败 ===")
        return 1
    print("\n=== P1-04 时间 tick：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
