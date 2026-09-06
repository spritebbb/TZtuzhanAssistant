# -*- coding: utf-8 -*-
"""M8 双视角叙事：锚点解析与校验、双栏读写、LLM 草稿不落库、人格隔离、
删除、导出恢复。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_dualview_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import dual_perspectives as dp, relationship_export as rex  # noqa: E402
from backend.core.dual_perspectives import DualPerspectiveError  # noqa: E402
from backend.core.relationship_events import record as record_event  # noqa: E402
from backend.core.userdb import db, save_promise  # noqa: E402

UID = "assistant-main"
UID2 = "persona-two"


def _seed_event(obj: str, promise_content: str) -> int:
    promise_id = save_promise(UID, promise_content)
    assert promise_id is not None, "约定被去重跳过"
    event_id = record_event(
        UID, "promise_completed", source_type="promise", source_id=promise_id,
        obj=obj, commit=True,
    )
    assert event_id is not None, "事件被幂等去重跳过"
    return event_id


def _rows(sql: str, args: tuple) -> list:
    with db._lock:
        return db.conn.execute(sql, args).fetchall()


def _test_create_with_anchors_and_validation() -> None:
    db.ensure_user(UID)
    event_id = _seed_event("一起完成早起挑战", "坚持早起三十天")

    page = dp.create_perspective(
        UID, "那个早起的秋天", source_type="event", source_id=event_id,
        user_view="我记得每天六点的闹钟和你嘟囔的脸。",
    )
    assert page["source_type"] == "event" and page["source_id"] == event_id
    assert page["source_date"] and page["source_label"], "锚点应留下日期与摘要快照"
    assert page["user_view"].startswith("我记得")
    assert page["tuzhan_view"] == "" and page["tuzhan_view_origin"] == "user"

    free = dp.create_perspective(UID, "第一次说晚安")
    assert free["source_type"] == "free" and free["source_id"] is None

    for kwargs, err in (
        ({"title": ""}, "标题"),
        ({"title": "x", "source_type": "magic", "source_id": 1}, "锚点类型"),
        ({"title": "x", "source_type": "event", "source_id": 999999}, "锚点经历不存在"),
        ({"title": "x", "source_type": "diary", "source_id": 999999}, "日记不存在"),
        ({"title": "x", "source_type": "event"}, "请选择要回忆的经历"),
        ({"title": "x", "source_type": "free", "tuzhan_view_origin": "robot"}, "视角来源标记"),
    ):
        try:
            dp.create_perspective(UID, **kwargs)
            raise AssertionError(f"应拒绝：{kwargs}")
        except DualPerspectiveError as exc:
            assert err in str(exc), f"{kwargs}: {exc}"
    print("[OK] 创建：锚点快照 + 非法锚点/类型/标题/origin 全部拒绝")


def _test_set_view_and_overwrite() -> None:
    page = dp.create_perspective(UID, "考试那周")
    updated = dp.set_view(UID, page["id"], "user", "我以为你嫌我烦。")
    assert updated["user_view"] == "我以为你嫌我烦。"
    updated = dp.set_view(UID, page["id"], "tuzhan", "我只是在担心你。", origin="user")
    assert updated["tuzhan_view"] == "我只是在担心你。" and updated["tuzhan_view_origin"] == "user"
    updated = dp.set_view(UID, page["id"], "tuzhan", "后来才懂那是我的笨拙关心。", origin="llm")
    assert updated["tuzhan_view_origin"] == "llm"
    # 改写覆盖旧版本（观点不是历史记录）
    updated = dp.set_view(UID, page["id"], "user", "其实我一直知道你在担心我。")
    assert updated["user_view"] == "其实我一直知道你在担心我。"
    for role, origin in (("shared", "user"), ("tuzhan", "robot")):
        try:
            dp.set_view(UID, page["id"], role, "x", origin=origin)
            raise AssertionError(f"应拒绝 role={role} origin={origin}")
        except DualPerspectiveError:
            pass
    print("[OK] 双栏读写：user/tuzhan 分别落位、origin 标记、可改写、非法角色拒绝")


async def _test_llm_draft_not_persisted() -> None:
    event_id = _seed_event("深夜赶稿的陪伴", "赶完这章稿子")
    page = dp.create_perspective(UID, "深夜赶稿", source_type="event", source_id=event_id)

    captured = {}

    async def fake_chat(messages, **kwargs):
        captured["messages"] = messages
        return "那段深夜的灯光我一直记得，安静里全是你的认真，我什么都没说，只是想陪你把灯亮到最后。"

    with patch("backend.core.dual_perspectives.chat", new=fake_chat):
        result = await dp.generate_tuzhan_draft(UID, page["id"])
    assert result["ok"] and result["origin"] == "llm" and result["draft"].startswith("那段深夜")
    # prompt 里必须带锚点真实记录
    material = captured["messages"][1]["content"]
    assert "深夜赶稿的陪伴" in material and "promise_completed" in material
    # 草稿绝不落库
    row = _rows("SELECT tuzhan_view, tuzhan_view_origin FROM dual_perspectives WHERE id = ?", (page["id"],))[0]
    assert row["tuzhan_view"] == "" and row["tuzhan_view_origin"] == "user"
    # 用户确认后落库并标记 llm
    saved = dp.set_view(UID, page["id"], "tuzhan", result["draft"], origin="llm")
    assert saved["tuzhan_view"].startswith("那段深夜") and saved["tuzhan_view_origin"] == "llm"

    async def failing_chat(messages, **kwargs):
        raise RuntimeError("model down")

    with patch("backend.core.dual_perspectives.chat", new=failing_chat):
        try:
            await dp.generate_tuzhan_draft(UID, page["id"])
            raise AssertionError("LLM 失败应转业务错误")
        except DualPerspectiveError as exc:
            assert "草稿生成失败" in str(exc)
    print("[OK] LLM 草稿：引用真实记录、不落库、确认后才写入、失败可优雅降级")


def _test_persona_isolation_and_delete() -> None:
    db.ensure_user(UID2)
    page = dp.create_perspective(UID, "只属于主人的回忆", user_view="我的")
    try:
        dp.set_view(UID2, page["id"], "user", "越界")
        raise AssertionError("跨人格读取应拒绝")
    except DualPerspectiveError as exc:
        assert "不存在" in str(exc)
    assert all(p["id"] != page["id"] for p in dp.list_perspectives(UID2))
    assert dp.delete_perspective(UID2, page["id"]) is False
    assert dp.delete_perspective(UID, page["id"]) is True
    assert all(p["id"] != page["id"] for p in dp.list_perspectives(UID))
    print("[OK] 人格隔离 + 真删除")


def _test_export_restore() -> None:
    page = dp.create_perspective(
        UID, "散步", source_type="free",
        user_view="A 版本的解释", tuzhan_view="B 版本的解释", tuzhan_view_origin="llm",
    )
    bundle = rex.export_bundle(UID)
    target = "dualview-restored"
    preview = rex.preview_restore(bundle, target)
    assert preview["ok"], preview["errors"]
    result = rex.restore_bundle(bundle, target)
    assert result["ok"]

    src = _rows("SELECT * FROM dual_perspectives WHERE user_id = ?", (UID,))
    dst = _rows("SELECT * FROM dual_perspectives WHERE user_id = ?", (target,))
    assert len(dst) == len(src) >= 1

    def _key(row):
        return (row["title"], row["user_view"], row["tuzhan_view"], row["tuzhan_view_origin"])

    assert {_key(r) for r in dst} == {_key(r) for r in src}, "视角页应原样迁移（锚点为展示性快照，不做重映射）"
    dp.delete_perspective(UID, page["id"])
    print("[OK] 导出恢复：life 类别携带，内容逐字段一致")


async def main() -> None:
    _test_create_with_anchors_and_validation()
    _test_set_view_and_overwrite()
    await _test_llm_draft_not_persisted()
    _test_persona_isolation_and_delete()
    _test_export_restore()
    print("\n=== M8 双视角叙事：全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
