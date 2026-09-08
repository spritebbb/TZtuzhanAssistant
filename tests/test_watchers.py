# -*- coding: utf-8 -*-
"""监控 Agent：网页变化监视（添加/列出/删除、到期检查、变化识别、SSRF 拒绝）。

验收锚点：
- 只接受公网地址（本机/内网拒绝）；
- 首次检查只记基线，不算变化；内容变了才报变化；
- 到期才检查，未到期不重复抓取；
- 抓取失败不写坏状态（更新检查时间但保留旧哈希）。
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_watch_"))

from backend.core import watchers as w
from backend.core.userdb import db

UID = "watch-user"


def test_add_list_remove_and_ssrf() -> int:
    db.ensure_user(UID)
    # 本机/内网地址必须拒绝
    for bad in ("http://127.0.0.1:8801/", "http://localhost/x", "http://192.168.1.1/"):
        try:
            w.add_watch(UID, bad)
            raise AssertionError(f"应拒绝内网地址：{bad}")
        except w.WatchError:
            pass
    item = w.add_watch(UID, "https://example.com/notice", label="公告", interval_minutes=10)
    assert item["interval_minutes"] == 30, "间隔下限应为 30 分钟"
    again = w.add_watch(UID, "https://example.com/notice", label="公告2", interval_minutes=60)
    assert again["id"] == item["id"] and again["label"] == "公告2", "同 URL 幂等更新"
    listed = w.list_watches(UID)
    assert len([x for x in listed if x["id"] == item["id"]]) == 1
    assert w.remove_watch(UID, item["id"]) is True
    assert w.list_watches(UID) == []
    print("[OK] 添加/幂等更新/列出/删除 + 内网地址拒绝")
    return 0


def test_change_detection_and_interval() -> int:
    db.ensure_user(UID)
    item = w.add_watch(UID, "https://example.com/a", label="A", interval_minutes=60)
    calls: list[str] = []
    content = {"v": "第一版"}

    def fake_fetch(url: str) -> str:
        calls.append(url)
        return content["v"]

    old = w._fetch_text
    w._fetch_text = fake_fetch
    try:
        # 首次：只记基线，不报变化
        first = w.check_due_watches(UID, force=True)
        assert first == [] and len(calls) == 1, (first, calls)
        # 未到期：不重复抓
        assert w.check_due_watches(UID) == []
        assert len(calls) == 1, calls
        # 内容变化 + 强制检查 → 报一次
        content["v"] = "第二版"
        changes = w.check_due_watches(UID, force=True)
        assert len(changes) == 1 and changes[0]["watch_id"] == item["id"], changes
        assert changes[0]["url"].endswith("/a")
        # 同一内容再查 → 不再报
        assert w.check_due_watches(UID, force=True) == []
        # 到期判定：把上次检查时间往前推
        with db._lock:
            db.conn.execute(
                "UPDATE watches SET last_checked_at=? WHERE id=?",
                ((datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds"), item["id"]),
            )
            db.conn.commit()
        content["v"] = "第三版"
        assert len(w.check_due_watches(UID)) == 1
    finally:
        w._fetch_text = old
        w.remove_watch(UID, item["id"])
    print("[OK] 基线/变化识别/未到期不抓/到期自动抓")
    return 0


def test_fetch_failure_keeps_state() -> int:
    db.ensure_user(UID)
    item = w.add_watch(UID, "https://example.com/b", label="B")
    def boom(url: str) -> str:
        raise w.WatchError("抓取失败：HTTP 503")
    old = w._fetch_text
    w._fetch_text = boom
    try:
        assert w.check_due_watches(UID, force=True) == []
        row = db.conn.execute("SELECT last_hash, last_checked_at FROM watches WHERE id=?",
                              (item["id"],)).fetchone()
        assert row["last_hash"] == "" and row["last_checked_at"], dict(row)
    finally:
        w._fetch_text = old
        w.remove_watch(UID, item["id"])
    print("[OK] 抓取失败：保留旧哈希、只更新检查时间")
    return 0


def test_proactive_message_on_change() -> int:
    """监控 Agent 接到主动链：有变化才出声，没变化保持沉默。"""
    import asyncio

    from backend.core import initiative

    sent: list[str] = []

    def fake_check(uid, *a, **k):
        return [{"watch_id": 1, "user_id": uid, "label": "官网公告",
                 "url": "https://example.com/n"}]

    old = initiative.__dict__.get("check_due_watches")
    import backend.core.watchers as wmod
    old_check = wmod.check_due_watches
    wmod.check_due_watches = fake_check
    try:
        # 直接跑次级链：命中监视变化时应出牌
        async def fake_produce(*a, **k):
            return None

        msg = asyncio.run(initiative._arbitrate_secondary("watch-proactive-user"))
        # 该用户没有任何其它到点来源，只有监视变化 → 应返回 True（出了一张牌）
        assert msg is True, msg
    finally:
        wmod.check_due_watches = old_check
        if old is not None:
            initiative.__dict__["check_due_watches"] = old
    # 没变化时整条次级链保持沉默（该用户没有其它到点来源）
    def none_check(uid, *a, **k):
        return []
    wmod.check_due_watches = none_check
    try:
        assert asyncio.run(initiative._arbitrate_secondary("watch-quiet-user")) is False
    finally:
        wmod.check_due_watches = old_check
    print("[OK] 监视变化接入主动链（有变化出牌 / 无变化沉默）")
    return 0


def main() -> int:
    failed = (
        test_add_list_remove_and_ssrf()
        + test_change_detection_and_interval()
        + test_fetch_failure_keeps_state()
        + test_proactive_message_on_change()
    )
    if failed:
        print(f"\n=== 网页监视：{failed} 项失败 ===")
        return 1
    print("\n=== 网页监视：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
