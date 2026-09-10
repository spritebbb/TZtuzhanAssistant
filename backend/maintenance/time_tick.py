# -*- coding: utf-8 -*-
"""P1-04B 跨进程时间 tick：每小时推进菟菚的虚构生活状态。

契约（docs/Zcode技术指导.md §5 P1-04 / §13.4 JOB-1）：
- 入口显式数据根与人格范围（--data-root 必填；人格默认取该数据根
  active.json，不依赖「当前窗口人格」）；
- ``job_runs`` 持久认领（scope/job/period 唯一，bot.db）：单条 UPDATE 的
  CAS 抢占（先例 agent/session.py:_claim_running），租约 120 秒、崩溃后
  过期可恢复；同一小时重复执行零增量（advance_schedule 幂等 + 唯一键）；
- 确定性状态与事件写库不持锁等模型（本 tick 无网络调用；日记/备份仍由
  既有维护循环驱动，避免双跑——自行裁剪，见回信）；
- 补跑限定窗口（默认 72 小时）与次数，更久远区间由 advance_schedule
  压缩推进，保留 computed_at 不冒充在线观察；
- tick 只推进状态、产生结构化素材，不推送任何消息。

用法（Windows 计划任务每小时执行）：
    python -m backend.maintenance.time_tick --data-root D:\\path\\to\\data [--json]
选项：--until ISO（补跑截止）、--once（只处理当前小时）、--dry-run、
      --at ISO（注入当前时刻，测试用）、--persona-id（显式人格）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

JOB_KEY = "time_tick"
DEFAULT_MAX_HOURS = 72
LEASE_SECONDS = 120
MAX_ATTEMPTS = 3
_RETRY_BACKOFF_MIN = (1, 5, 30)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="time_tick", description="每小时行程状态推进")
    parser.add_argument("--data-root", required=True, help="显式数据根目录（TZTUZHAN_DATA_DIR）")
    parser.add_argument("--persona-id", default=None, help="人格 id；缺省读数据根 active.json")
    parser.add_argument("--until", default=None, help="补跑截止时刻 ISO（默认当前时间）")
    parser.add_argument("--at", default=None, help="注入「当前时刻」ISO（测试用）")
    parser.add_argument("--once", action="store_true", help="只处理当前小时，不补跑缺失周期")
    parser.add_argument("--max-hours", type=int, default=DEFAULT_MAX_HOURS, help="补跑窗口")
    parser.add_argument("--dry-run", action="store_true", help="只列出将处理的周期")
    parser.add_argument("--json", action="store_true", help="输出 JSON 摘要")
    return parser.parse_args(argv)


def _active_persona(data_root: Path) -> str:
    try:
        return str(json.loads((data_root / "personas" / "active.json").read_text(
            encoding="utf-8")).get("active_id", "default"))
    except (OSError, json.JSONDecodeError, AttributeError, ValueError):
        return "default"


def _utc_hour_floor(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _job_run_id(period: str) -> str:
    """生成符合遥测标识符白名单的稳定周期 id。"""
    return f"{JOB_KEY}:{period.replace('+00:00', 'Z')}"


def _claim(conn, scope_key: str, period: str, owner: str, now: datetime) -> bool:
    """CAS 认领：pending/到期重试/租约过期的 running 可抢，否则让位。"""
    conn.execute(
        "INSERT OR IGNORE INTO job_runs (scope_key, job_key, period_start, status, "
        "attempt, created_at, updated_at) VALUES (?, ?, ?, 'pending', 0, ?, ?)",
        (scope_key, JOB_KEY, period, _iso(now), _iso(now)),
    )
    cur = conn.execute(
        "UPDATE job_runs SET status='running', lease_owner=?, lease_until=?, "
        "attempt=attempt+1, updated_at=? "
        "WHERE scope_key=? AND job_key=? AND period_start=? "
        "AND ((status IN ('pending','failed') "
        "      AND (next_retry IS NULL OR next_retry<=? OR next_retry='')) "
        "   OR (status='running' AND lease_until IS NOT NULL AND lease_until<?))",
        (owner, _iso(now + timedelta(seconds=LEASE_SECONDS)), _iso(now),
         scope_key, JOB_KEY, period, _iso(now), _iso(now)),
    )
    conn.commit()
    return cur.rowcount == 1


def _finish(conn, scope_key: str, period: str, owner: str, ok: bool,
            now: datetime, attempt: int) -> bool:
    """仅当前租约 owner 可完成；迟到 worker 或 reset 后的完成必须失败。"""
    if ok:
        cur = conn.execute(
            "UPDATE job_runs SET status='succeeded', lease_owner=NULL, lease_until=NULL, "
            "next_retry=NULL, finished_at=?, updated_at=? "
            "WHERE scope_key=? AND job_key=? AND period_start=? "
            "AND status='running' AND lease_owner=?",
            (_iso(now), _iso(now), scope_key, JOB_KEY, period, owner),
        )
    else:
        backoff = _RETRY_BACKOFF_MIN[min(attempt, len(_RETRY_BACKOFF_MIN)) - 1] \
            if attempt <= MAX_ATTEMPTS else _RETRY_BACKOFF_MIN[-1]
        status = "failed" if attempt < MAX_ATTEMPTS else "cancelled"
        cur = conn.execute(
            "UPDATE job_runs SET status=?, lease_owner=NULL, lease_until=NULL, "
            "next_retry=?, finished_at=?, updated_at=? "
            "WHERE scope_key=? AND job_key=? AND period_start=? "
            "AND status='running' AND lease_owner=?",
            (status, _iso(now + timedelta(minutes=backoff)), _iso(now), _iso(now),
             scope_key, JOB_KEY, period, owner),
        )
    conn.commit()
    return cur.rowcount == 1


def _pending_periods(conn, scope_key: str, start: datetime, end: datetime,
                     limit: int) -> list[str]:
    """窗口内未成功的周期键（升序），供补跑。"""
    rows = conn.execute(
        "SELECT period_start FROM job_runs WHERE scope_key=? AND job_key=? "
        "AND status IN ('succeeded','cancelled') "
        "AND period_start>=? AND period_start<=?",
        (scope_key, JOB_KEY, _iso(start), _iso(end)),
    ).fetchall()
    done = {r["period_start"] for r in rows}
    periods, cursor = [], start
    while cursor <= end and len(periods) < limit:
        key = _iso(cursor)
        if key not in done:
            periods.append(key)
        cursor = cursor + timedelta(hours=1)
    return periods


def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    data_root = Path(args.data_root).resolve()
    os.environ["TZTUZHAN_DATA_DIR"] = str(data_root)
    # 数据根先于 backend import 设置：config 在 import 时固化 data_dir
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    from backend.core.persona_profiles import DEFAULT_USER_ID, scoped_user_id
    from backend.core.schedule import advance_schedule
    from backend.core.userdb import db

    now = (
        datetime.fromisoformat(args.at) if args.at else datetime.now(timezone.utc)
    ).astimezone(timezone.utc)
    until = (
        datetime.fromisoformat(args.until).astimezone(timezone.utc)
        if args.until else now
    )

    # reset 进行中直接让位（跨进程的写竞争由 schedule 的 version CAS 兜底）
    from backend.core.reset import reset_in_progress

    if reset_in_progress():
        if args.json:
            print(json.dumps({"skipped": "reset_in_progress"}, ensure_ascii=False))
        else:
            print("[time_tick] reset 进行中，本次跳过")
        return 0

    persona_id = args.persona_id or _active_persona(data_root)
    scope_key = f"persona::{persona_id}"
    user_id = scoped_user_id(DEFAULT_USER_ID, persona_id)

    # 应用内启动补跑运行在线程池中，但仍与 API 共用全局连接。该连接不允许
    # 两个线程交叉开启事务，因此从读取待办周期到完成认领都持有同一把可重入锁。
    # 独立计划任务进程之间仍由 job_runs 的租约和 SQLite 写锁协调。
    with db._lock:
        # 窗口：--once 只处理当前小时；默认补跑缺失周期（限窗限次）
        current_hour = _utc_hour_floor(now)
        if args.once:
            periods = [_iso(current_hour)]
        else:
            window_start = current_hour - timedelta(hours=max(1, args.max_hours))
            periods = _pending_periods(db.conn, scope_key, window_start,
                                       min(current_hour, until), limit=args.max_hours)
            if until > current_hour:
                periods.append(_iso(current_hour))
            periods = sorted(set(periods))

        if args.dry_run:
            print(json.dumps({"dry_run": True, "scope": scope_key, "periods": periods},
                             ensure_ascii=False))
            return 0

        summary = {"scope": scope_key, "user": user_id, "attempted": 0,
                   "succeeded": 0, "skipped": 0, "failed": 0, "events": 0}
        owner = f"tick-{os.getpid()}-{int(now.timestamp())}"

        def _observe(event_name: str, period: str, *, outcome: str,
                     error_code: str = "") -> None:
            try:
                from backend.core.telemetry import record_event

                record_event(user_id, {
                    "event_name": event_name,
                    "persona_id": persona_id,
                    "source_ids": ["job:time_tick"],
                    "job_run_id": _job_run_id(period),
                    "outcome": outcome,
                    "error_code": error_code,
                })
            except Exception:
                pass

        for period in periods:
            period_dt = datetime.fromisoformat(period)
            if not _claim(db.conn, scope_key, period, owner, now):
                summary["skipped"] += 1
                continue
            summary["attempted"] += 1
            _observe("job_started", period, outcome="started")
            try:
                result = advance_schedule(user_id, min(period_dt + timedelta(hours=1), until))
                summary["events"] += int(result.get("events", 0))
                row = db.conn.execute(
                    "SELECT attempt FROM job_runs WHERE scope_key=? AND job_key=? AND period_start=?",
                    (scope_key, JOB_KEY, period),
                ).fetchone()
                attempt = int(row["attempt"]) if row else 1
                if _finish(db.conn, scope_key, period, owner, True, now, attempt):
                    summary["succeeded"] += 1
                    _observe("job_finished", period, outcome="success")
                else:
                    summary["failed"] += 1
                    _observe("job_finished", period, outcome="failure", error_code="permission")
            except Exception:
                # advance_schedule 失败时不得让未提交事务泄漏给失败状态更新。
                db.conn.rollback()
                row = db.conn.execute(
                    "SELECT attempt FROM job_runs WHERE scope_key=? AND job_key=? AND period_start=?",
                    (scope_key, JOB_KEY, period),
                ).fetchone()
                attempt = int(row["attempt"]) if row else 1
                _finish(db.conn, scope_key, period, owner, False, now, attempt)
                _observe("job_finished", period, outcome="failure", error_code="pipeline_exception")
                summary["failed"] += 1

    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(f"[time_tick] {summary}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run())
