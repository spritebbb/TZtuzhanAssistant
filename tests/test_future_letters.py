# -*- coding: utf-8 -*-
"""M8 未来信件：三类解锁边界、锁定正文不泄露、显式拆信幂等、删除级联、
多人格隔离、reset 与导出恢复引用一致。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_letters_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import future_letters, goals, relationship_export as rex  # noqa: E402
from backend.core.future_letters import EVENT_UNLOCK_TYPES, FutureLetterError  # noqa: E402
from backend.core.relationship_events import record as record_event  # noqa: E402
from backend.core.userdb import db, save_promise  # noqa: E402

UID = "assistant-main"
UID2 = "persona-two"
BODY = "亲爱的未来的我们：记得这一天的晚风和没说完的话。"


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _rows(sql: str, params: tuple) -> list:
    with db._lock:
        return db.conn.execute(sql, params).fetchall()


def _letter_status(letter_id: int, uid: str = UID) -> str:
    rows = _rows("SELECT status, unlocked_at FROM future_letters WHERE id = ? AND user_id = ?", (letter_id, uid))
    if not rows:
        return "missing"
    return "opened" if rows[0]["status"] == "opened" else ("ready" if rows[0]["unlocked_at"] else "sealed")


def _future_artifacts(uid: str, letter_id: int | None = None) -> list:
    sql = "SELECT id, source_id, title, content FROM artifacts WHERE user_id = ? AND artifact_type = 'future_letter'"
    params: tuple = (uid,)
    if letter_id is not None:
        sql += " AND source_id = ?"
        params = (uid, letter_id)
    return _rows(sql, params)


def _expect_error(fn, needle: str) -> None:
    try:
        fn()
        raise AssertionError(f"应当报错：{needle}")
    except FutureLetterError as exc:
        assert needle in str(exc), f"错误信息不符：{exc}"


def _test_date_letter_boundaries() -> None:
    now = datetime.now()
    _expect_error(
        lambda: future_letters.create_letter(UID, BODY, "date", unlock_at=_iso(now - timedelta(hours=1))),
        "晚于现在",
    )
    _expect_error(
        lambda: future_letters.create_letter(UID, BODY, "date"),
        "选择解锁时间",
    )
    _expect_error(
        lambda: future_letters.create_letter(UID, BODY, "date", unlock_at="不是时间"),
        "格式不正确",
    )
    _expect_error(
        lambda: future_letters.create_letter(
            UID,
            BODY,
            "date",
            unlock_at=(datetime.now().astimezone() + timedelta(days=1)).isoformat(),
        ),
        "本地时间",
    )
    _expect_error(
        lambda: future_letters.create_letter(UID, "", "date", unlock_at=_iso(now + timedelta(days=1))),
        "正文",
    )

    letter = future_letters.create_letter(
        UID, BODY, "date", title="一年后拆", unlock_at=_iso(now + timedelta(days=365))
    )
    assert letter["status"] == "sealed" and "body" not in letter
    listed = future_letters.list_letters(UID)
    assert len(listed) == 1 and "body" not in listed[0]
    # 时间未到：显式拆信被拒绝
    _expect_error(lambda: future_letters.open_letter(UID, letter["id"]), "还打不开")

    # 惰性落账：到点后（直接改库模拟时间流逝）列表即判 ready，且幂等
    with db._lock:
        db.conn.execute(
            "UPDATE future_letters SET unlock_at = ? WHERE id = ?",
            (_iso(now - timedelta(minutes=1)), letter["id"]),
        )
        db.conn.commit()
    first = future_letters.list_letters(UID)[0]
    assert first["status"] == "ready" and first["unlocked_at"]
    again = future_letters.list_letters(UID)[0]
    assert again["unlocked_at"] == first["unlocked_at"], "重复查询不应改写落账时间"
    print("[OK] 日期信：过去时间拒绝、到点才 ready、落账幂等、锁定态无 body 字段")
    return letter["id"]


def _test_goal_letter_lifecycle() -> int:
    goal = goals.start_goal(UID, "一起整理完相册", "留住今年", "先挑出 30 张")
    # 他人命名空间的目标不能选
    other_goal = goals.start_goal(UID2, "别人的目标", "动机", "下一步")
    _expect_error(
        lambda: future_letters.create_letter(UID, BODY, "goal", goal_id=other_goal["id"]),
        "不存在",
    )
    # 非 goal 类活动不能选
    now = _iso(datetime.now())
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO activities (user_id, kind, document_id, title, status, position, created_at, updated_at) "
            "VALUES (?, 'reading', 0, '一本书', 'active', 0, ?, ?)",
            (UID, now, now),
        )
        reading_id = int(cur.lastrowid)
        db.conn.commit()
    _expect_error(
        lambda: future_letters.create_letter(UID, BODY, "goal", goal_id=reading_id),
        "不存在",
    )

    letter = future_letters.create_letter(UID, BODY, "goal", goal_id=goal["id"])
    assert letter["status"] == "sealed" and letter["goal_id"] == goal["id"]
    assert _letter_status(letter["id"]) == "sealed"
    _expect_error(lambda: future_letters.open_letter(UID, letter["id"]), "还打不开")

    goals.complete_goal(UID, goal["id"])
    listed = future_letters.list_letters(UID)
    assert next(item for item in listed if item["id"] == letter["id"])["status"] == "ready"

    # 已完成的目标不能再作为解锁条件写信（否则解锁瞬间就发生）
    _expect_error(
        lambda: future_letters.create_letter(UID, BODY, "goal", goal_id=goal["id"]),
        "已经结束",
    )

    opened = future_letters.open_letter(UID, letter["id"])
    assert opened["status"] == "opened" and opened["body"] == BODY and opened["opened_at"]
    assert len(_future_artifacts(UID, letter["id"])) == 1, "拆信应幂等创建唯一 artifact"
    reopened = future_letters.open_letter(UID, letter["id"])
    assert reopened["body"] == BODY and reopened["opened_at"] == opened["opened_at"]
    assert len(_future_artifacts(UID, letter["id"])) == 1
    print("[OK] 目标信：归属校验、完成后才 ready、显式拆信幂等且落 artifact")

    # 已取消的目标：条件永远无法达成，信保持锁定
    cancelled = goals.start_goal(UID2, "会取消的目标", "动机", "下一步")
    letter2 = future_letters.create_letter(UID2, BODY, "goal", goal_id=cancelled["id"])
    goals.cancel_goal(UID2, cancelled["id"])
    listed = future_letters.list_letters(UID2)
    assert next(item for item in listed if item["id"] == letter2["id"])["status"] == "sealed"
    print("[OK] 目标信：已取消的目标永远不可拆")
    return letter["id"]


def _test_event_letter_lifecycle() -> int:
    assert "important_date" not in EVENT_UNLOCK_TYPES, "日期提醒不是完成/修复类，不应进白名单"
    _expect_error(
        lambda: future_letters.create_letter(UID, BODY, "event", event_type="important_date"),
        "不在可选范围",
    )
    _expect_error(
        lambda: future_letters.create_letter(UID, BODY, "event", event_type="随便什么"),
        "不在可选范围",
    )

    before = datetime.now()
    # 写信之前完成的约定：不应解锁
    old_promise = save_promise(UID, "写信前就完成的约定")
    record_event(
        UID, "promise_completed", "promise", old_promise,
        obj="旧约定", occurred_at=_iso(before - timedelta(seconds=10)),
    )

    letter = future_letters.create_letter(UID, BODY, "event", event_type="promise_completed")
    assert letter["status"] == "sealed" and "body" not in letter
    assert _letter_status(letter["id"]) == "sealed", "写信前的事件不应解锁"

    # 写信之后完成的约定：首次匹配落账 unlocked_by_event_id
    new_promise = save_promise(UID, "写信后才完成的约定")
    event_id = record_event(
        UID, "promise_completed", "promise", new_promise,
        obj="新约定", occurred_at=_iso(before + timedelta(seconds=10)),
    )
    assert event_id is not None
    ready = future_letters.list_letters(UID)[0]
    assert ready["status"] == "ready" and ready["unlocked_by_event_id"] == event_id
    opened = future_letters.open_letter(UID, letter["id"])
    assert opened["body"] == BODY
    print("[OK] 事件信：白名单外拒绝、写信前事件不算、首次匹配落账事件 id")
    return letter["id"]


def _test_multi_user_isolation(date_letter_id: int) -> None:
    letters = future_letters.list_letters(UID)
    target = next(item for item in letters if item["id"] == date_letter_id)
    # UID2 看不到 UID1 的信
    assert all(item["id"] != date_letter_id for item in future_letters.list_letters(UID2))
    _expect_error(lambda: future_letters.open_letter(UID2, date_letter_id), "不存在")
    assert future_letters.delete_letter(UID2, date_letter_id) is False
    # UID1 自己仍可拆
    opened = future_letters.open_letter(UID, target["id"])
    assert opened["body"] == BODY
    print("[OK] 隔离：跨命名空间不可见、不可拆、不可删")


def _test_delete_cascade() -> None:
    # 自备一封信走「开→删」全流程，不消耗事件信（它要留给导出测试做引用映射）
    letter = future_letters.create_letter(
        UID, "这封信马上会被删掉", "date", unlock_at=_iso(datetime.now() + timedelta(hours=1))
    )
    with db._lock:
        db.conn.execute(
            "UPDATE future_letters SET unlock_at = ? WHERE id = ?",
            (_iso(datetime.now()), letter["id"]),
        )
        db.conn.commit()
    opened = future_letters.open_letter(UID, letter["id"])
    assert opened["body"] == "这封信马上会被删掉"
    assert _future_artifacts(UID, letter["id"])
    assert future_letters.delete_letter(UID, letter["id"]) is True
    assert future_letters.delete_letter(UID, letter["id"]) is False
    assert all(item["id"] != letter["id"] for item in future_letters.list_letters(UID))
    assert not _future_artifacts(UID, letter["id"]), "删除信必须连带清理拆信 artifact"
    assert not _rows(
        "SELECT id FROM relationship_events WHERE user_id = ? AND source_type = 'future_letter' AND source_id = ?",
        (UID, letter["id"]),
    ), "不得留下指向已删信的事件幽灵"
    print("[OK] 删除：信与 artifact 级联清除，无幽灵事件")


def _test_export_restore_references() -> None:
    goal = goals.start_goal(UID, "写完邀请函", "给朋友", "列名单")
    future_letters.create_letter(UID, "给完成后的我们", "goal", goal_id=goal["id"])
    bundle = rex.export_bundle(UID)
    target = "letters-restored"
    preview = rex.preview_restore(bundle, target)
    assert preview["ok"], preview["errors"]
    result = rex.restore_bundle(bundle, target)
    assert result["ok"]

    src = _rows("SELECT * FROM future_letters WHERE user_id = ?", (UID,))
    dst = _rows("SELECT * FROM future_letters WHERE user_id = ?", (target,))
    assert len(dst) == len(src) >= 3, "信件计数应一致"

    def _key(row) -> tuple:
        return (row["unlock_type"], row["title"], row["body"])

    by_key = {_key(row): row for row in src}
    assert {_key(row) for row in dst} == set(by_key), "恢复后应有同一批（类型/标题/正文）的信"
    for d in dst:
        s = by_key[_key(d)]
        for col in ("unlock_at", "event_type", "status", "unlocked_at", "opened_at"):
            assert s[col] == d[col], f"{col} 恢复不一致：{s[col]!r} != {d[col]!r}"
        # 引用列按新命名空间主键重建，但指向的「内容」一致
        if s["goal_id"] is not None:
            s_title = _rows("SELECT title FROM activities WHERE id = ?", (s["goal_id"],))[0]["title"]
            d_title = _rows("SELECT title FROM activities WHERE id = ?", (d["goal_id"],))[0]["title"]
            assert s_title == d_title, "goal 引用应映射到同内容的新活动"
        else:
            assert d["goal_id"] is None
        if s["unlocked_by_event_id"] is not None:
            s_ev = _rows("SELECT event_type, payload_json FROM relationship_events WHERE id = ?", (s["unlocked_by_event_id"],))[0]
            d_ev = _rows("SELECT event_type, payload_json FROM relationship_events WHERE id = ?", (d["unlocked_by_event_id"],))[0]
            assert tuple(s_ev) == tuple(d_ev), "事件引用应映射到同内容的新事件"
        else:
            assert d["unlocked_by_event_id"] is None

    def _artifacts(uid: str) -> list:
        return _rows(
            "SELECT title, content FROM artifacts WHERE user_id = ? "
            "AND artifact_type = 'future_letter' ORDER BY title", (uid,),
        )

    assert [tuple(r) for r in _artifacts(UID)] == [tuple(r) for r in _artifacts(target)]
    dst_letter_ids = {int(r["id"]) for r in dst}
    for row in _rows(
        "SELECT source_id FROM artifacts WHERE user_id = ? AND artifact_type = 'future_letter'", (target,),
    ):
        assert int(row["source_id"]) in dst_letter_ids, "恢复后 artifact 应指向新命名空间的信"
    print("[OK] 导出恢复：未来信件计数、正文与跨表引用（目标/事件）在新命名空间一致")


def _test_http_api() -> None:
    from fastapi.testclient import TestClient

    from backend.app import create_app
    from backend.core.config import config

    secret = "这行字在锁定时绝不能出现在任何接口响应里"
    future_letters.create_letter(
        UID, secret, "date", title="HTTP 锁定信",
        unlock_at=_iso(datetime.now() + timedelta(days=30)),
    )
    # 准备一封已到点可拆的信（自备，不依赖其它测试遗留状态）
    ready_letter = future_letters.create_letter(
        UID, secret, "date", title="HTTP 可拆信",
        unlock_at=_iso(datetime.now() + timedelta(hours=1)),
    )
    with db._lock:
        db.conn.execute(
            "UPDATE future_letters SET unlock_at = ? WHERE id = ?",
            (_iso(datetime.now()), ready_letter["id"]),
        )
        db.conn.commit()
    with TestClient(create_app()) as client:
        listed = client.get("/api/future-letters")
        assert listed.status_code == 200
        payload = listed.json()
        assert payload["ok"] and payload["letters"]
        assert secret not in listed.text, "列表响应泄漏了锁定正文"
        assert payload["event_types"] and payload["goal_options"] is not None

        created = client.post("/api/future-letters", json={
            "body": "给达成那天", "unlock_type": "date",
            "unlock_at": _iso(datetime.now() + timedelta(days=1)),
        })
        assert created.status_code == 200 and created.json()["letter"]["status"] == "sealed"
        bad = client.post("/api/future-letters", json={
            "body": "x", "unlock_type": "date", "unlock_at": "2020-01-01T00:00:00",
        })
        assert bad.status_code == 400

        ready_api_letter = next(
            item for item in payload["letters"] if item["status"] == "ready"
        )
        opened = client.post(f"/api/future-letters/{ready_api_letter['id']}/open")
        assert opened.status_code == 200
        assert secret in opened.text or opened.json()["letter"]["body"], "拆开后应能看到正文"
        assert client.delete(f"/api/future-letters/{ready_api_letter['id']}").status_code == 200
        assert client.delete(f"/api/future-letters/{ready_api_letter['id']}").status_code == 404

        config.future_letters_enabled = False
        try:
            assert client.get("/api/future-letters").status_code == 403
            assert client.post("/api/future-letters", json={
                "body": "x", "unlock_type": "event", "event_type": "goal_completed",
            }).status_code == 403
        finally:
            config.future_letters_enabled = True
    print("[OK] HTTP：锁定不泄正文、创建/拆信/删除可用、feature flag 关闭时 403")


def _test_reset_clears_letters() -> None:
    assert _rows("SELECT id FROM future_letters WHERE user_id = ?", (UID,))
    db.reset()
    assert _rows("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'future_letters'", ())
    assert not _rows("SELECT id FROM future_letters", ())
    print("[OK] 重置：future_letters 全部清空且表结构保留")


async def main() -> None:
    date_letter_id = _test_date_letter_boundaries()
    _test_goal_letter_lifecycle()
    _test_event_letter_lifecycle()
    _test_multi_user_isolation(date_letter_id)
    _test_delete_cascade()
    _test_export_restore_references()
    _test_http_api()
    _test_reset_clears_letters()
    print("未来信件 M8 测试通过")


if __name__ == "__main__":
    asyncio.run(main())
