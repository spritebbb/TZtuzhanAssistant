# -*- coding: utf-8 -*-
"""彻底重置的清除覆盖回归：Mem0 独立向量库、用户图片、归档/备份/缓存与物理回收。

背景：reset 只清 bot.db + 主 Chroma + 当前会话时，Mem0 的独立库
（data/chroma_mem0）与用户图片会留下来，重置后长期记忆召回仍会命中旧记忆。
本文件把这些漏点锁成回归。

隔离：main() 里先把 TZTUZHAN_DATA_DIR 指到临时目录，再导入 backend，
所有落盘都发生在临时目录里，不碰真实 data/。
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _write(path: Path, data: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_mem0_manager_clear_user_delegates_to_delete_all() -> int:
    """Mem0 可用时 clear_user 必须调 delete_all(user_id=...)，并按前后差值报数。"""
    from backend.core.memory.memory_manager import Mem0Manager

    class _FakeMem0:
        def __init__(self, items: list[str]):
            self.items = list(items)
            self.deleted_for: list[str | None] = []

        def get_all(self, filters=None, **kwargs):
            return {"results": [{"id": i, "memory": i} for i in self.items]}

        def delete_all(self, user_id=None, agent_id=None, run_id=None):
            self.deleted_for.append(user_id)
            self.items = []
            return {"message": "Mem0 deleted"}

    mgr = Mem0Manager()
    fake = _FakeMem0(["a", "b", "c"])
    mgr._mem0 = fake
    mgr._ready = True  # 跳过真实 Mem0 初始化

    removed = mgr.clear_user("u1")
    assert fake.deleted_for == ["u1"], f"delete_all 必须带 user_id（实际={fake.deleted_for}）"
    assert removed == 3, f"应报删除 3 条（实际={removed}）"
    assert mgr.get_all("u1") == [], "清除后该用户不应还有记忆"
    print("[OK] Mem0 clear_user 委托 delete_all(user_id) 并正确报数")
    return 0


def test_media_sweep_keeps_still_referenced_files() -> int:
    """图片清扫只删「已无人引用」的文件，其余用户/行仍在用的必须保留。"""
    from backend.core import reset as reset_mod
    from backend.core.config import config
    from backend.core.userdb import db

    imgs = config.data_dir / "imgs"
    for name in ("a.png", "b.png", "c.png"):
        _write(imgs / name)

    # uid 的行引用 a.png；别人引用 b.png；c.png 谁都不引用
    with db._lock:
        for user, name in (("u1", "a.png"), ("u2", "b.png")):
            db.conn.execute(
                "INSERT INTO messages (user_id, role, content, ts)"
                " VALUES (?,?,?,datetime('now'))",
                (user, "user", f"看 ![](/api/images/{name})"),
            )
        db.conn.commit()

    candidates = reset_mod._media_candidates()
    assert {"a.png", "b.png", "c.png"} <= candidates

    names = reset_mod._collect_user_media("u1", "current", "default", False, candidates)
    assert names == {"a.png"}, f"只应收集 u1 引用的 a.png（实际={names}）"

    # 删掉 u1 的行后，a.png 已无人引用；b.png 仍被 u2 引用
    with db._lock:
        db.conn.execute("DELETE FROM messages WHERE user_id='u1'")
        db.conn.commit()
    still = reset_mod._referenced_media_names(names, [db.conn])
    assert still == set(), f"a.png 已无人引用（实际={still}）"

    removed = reset_mod._sweep_media(names - still)
    assert removed == 1
    assert not (imgs / "a.png").exists(), "无人引用的图片应被删除"
    assert (imgs / "b.png").exists(), "仍被他人引用的图片必须保留"
    assert (imgs / "c.png").exists(), "没进候选的图片不该被删"
    print("[OK] 图片清扫只删无人引用的文件")
    return 0


def test_deep_reset_clears_mem0_media_archives_backups() -> int:
    """端到端：deep 重置后 Mem0 被清、图片/归档/备份/缓存消失，用户行零残留。"""
    from backend.core import reset as reset_mod
    from backend.core.config import config
    from backend.core.persona_profiles import active_id, active_user_id
    from backend.core.userdb import db
    from backend.session import store

    uid = active_user_id()
    persona = active_id()

    # 造数据：bot.db 消息 + 图片文件 + 会话气泡 + 归档 + 备份 + 语音缓存
    db.ensure_user(uid)
    img_name = "regress_media.png"
    _write(config.data_dir / "imgs" / img_name)
    with db._lock:
        db.conn.execute(
            "INSERT INTO messages (user_id, role, content, ts)"
            " VALUES (?,?,?,datetime('now'))",
            (uid, "user", f"看看这张图 ![](/api/images/{img_name})"),
        )
        db.conn.commit()

    asyncio.run(store.append_messages("current", [
        {"role": "user", "content": "这段对话会被归档", "image": f"/api/images/{img_name}"},
    ]))
    archived = asyncio.run(store.archive_current())
    assert archived, "测试前置：当前会话应能归档"

    backup_dir = config.data_dir / "backups" / "periodic-test"
    _write(backup_dir / "bot.db", b"old")
    tts_dir = config.data_dir / "tts_cache"
    _write(tts_dir / "cache.mp3", b"old")

    # Mem0 用假对象替身：真实初始化代价高，委托逻辑已由单测覆盖
    from backend.core.memory import memory_manager as mm

    cleared: list[str] = []
    mm.manager.clear_user = lambda user_id: cleared.append(user_id) or 7  # type: ignore[assignment]

    stats = asyncio.run(reset_mod.reset_everything(deep=True))

    assert stats["ok"], f"重置应整体成功：{stats['failures']}"
    assert stats["mem0"] == 7 and cleared == [uid], f"Mem0 清除未接入（{cleared}）"
    assert stats["media"] == 1, f"用户图片应被删除（实际={stats['media']}）"
    assert not (config.data_dir / "imgs" / img_name).exists(), "图片文件仍在"
    assert stats["archives"] == 1, f"归档应被删除（实际={stats['archives']}）"
    assert asyncio.run(store.list_archives()) == [], "归档仍有残留"
    assert not backup_dir.exists(), "备份目录仍在"
    assert not (tts_dir / "cache.mp3").exists(), "语音缓存仍在"
    assert stats["compact"].endswith("=ok"), f"物理回收未执行：{stats['compact']}"

    left = db.conn.execute("SELECT COUNT(*) AS n FROM messages WHERE user_id=?", (uid,)).fetchone()
    assert int(left["n"]) == 0, "重置后 bot.db 消息仍有残留"
    print("[OK] deep 重置清空 Mem0/图片/归档/备份/缓存并做物理回收")
    return 0


def test_shallow_reset_keeps_archives_and_referenced_media() -> int:
    """默认重置不删归档，因此归档里引用的图片也必须保留（清扫不能过界）。"""
    from backend.core import reset as reset_mod
    from backend.core.config import config
    from backend.core.persona_profiles import active_user_id
    from backend.core.userdb import db
    from backend.session import store

    uid = active_user_id()
    img_name = "archived_media.png"
    _write(config.data_dir / "imgs" / img_name)

    asyncio.run(store.append_messages("current", [
        {"role": "user", "content": "这段会被归档", "image": f"/api/images/{img_name}"},
    ]))
    assert asyncio.run(store.archive_current()), "测试前置：当前会话应能归档"
    # bot.db 里也留一条引用，模拟「当前会话气泡」那一侧
    with db._lock:
        db.conn.execute(
            "INSERT INTO messages (user_id, role, content, ts)"
            " VALUES (?,?,?,datetime('now'))",
            (uid, "user", f"当前会话的图 ![](/api/images/{img_name})"),
        )
        db.conn.commit()

    stats = asyncio.run(reset_mod.reset_everything(deep=False))

    assert stats["ok"], f"重置应整体成功：{stats['failures']}"
    assert stats["archives"] == 0 and len(asyncio.run(store.list_archives())) == 1, (
        "默认重置不应删除归档"
    )
    assert stats["media"] == 0, f"归档仍在引用，图片不该被删（实际={stats['media']}）"
    assert (config.data_dir / "imgs" / img_name).exists(), "归档仍在引用，图片必须保留"
    print("[OK] 默认重置保留归档与其引用的图片")
    return 0


def main() -> int:
    os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_reset_"))
    failed = (
        test_mem0_manager_clear_user_delegates_to_delete_all()
        + test_media_sweep_keeps_still_referenced_files()
        + test_deep_reset_clears_mem0_media_archives_backups()
        + test_shallow_reset_keeps_archives_and_referenced_media()
    )
    if failed:
        print(f"\n=== 重置清除覆盖：{failed} 项失败 ===")
        return 1
    print("\n=== 重置清除覆盖：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
