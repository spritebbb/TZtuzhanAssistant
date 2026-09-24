# -*- coding: utf-8 -*-
"""P0-1 回归：fallback 管理记忆以 manager_memories 表为权威副本。

Mem0 降级期的记忆此前只写 Chroma mem 分区，任何 rebuild 都会整库删除且
migrate 不重灌 → 永久丢失。修复后 SQLite 是权威源：add 先落库、检索按表
过滤孤儿向量、migrate 把 mem 纳入重灌源。

运行：python -m tests.test_manager_memories
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# 独立临时数据目录（避免污染真实数据）
os.environ.setdefault(
    "TZTUZHAN_DATA_DIR", str(Path(tempfile.mkdtemp(prefix="tzt-mgr-mem-")) / "data")
)


def main() -> None:
    from backend.core.memory import migration, vector_store as vs
    from backend.core.memory.memory_manager import _FallbackManager
    from backend.core.userdb import db

    uid = "assistant-main"
    fb = _FallbackManager()

    # 1) add：权威表落行（向量写入尽力而为，不作为判定依据）
    assert fb.add(uid, "用户爱吃番茄牛腩面"), "add 应成功（权威表落库）"
    rows = db.list_manager_memory_rows(uid)
    assert len(rows) == 1 and rows[0]["content"] == "用户爱吃番茄牛腩面"

    # 2) 去重：同文本（同 rid）再写仍是 1 行
    assert fb.add(uid, "用户爱吃番茄牛腩面")
    assert len(db.list_manager_memory_rows(uid)) == 1

    # 3) update：旧 rid 删、新内容写入（mem 分区之外的 id 拒绝）
    old_rid = rows[0]["rid"]
    assert fb.update(uid, f"{uid}|mem|{old_rid}", "用户最爱吃的是番茄牛腩面")
    contents = [r["content"] for r in db.list_manager_memory_rows(uid)]
    assert "用户最爱吃的是番茄牛腩面" in contents
    assert not fb.update(uid, f"{uid}|lm|123", "不该写入的分区")

    # 4) delete：权威表删除；重复删返回 False
    rid_now = [r["rid"] for r in db.list_manager_memory_rows(uid) if r["content"] == "用户最爱吃的是番茄牛腩面"]
    assert rid_now, "更新后的内容应在表中"
    assert fb.delete(uid, f"{uid}|mem|{rid_now[0]}")
    assert not fb.delete(uid, f"{uid}|mem|{rid_now[0]}")

    # 5) 检索权威闸门：向量孤儿（表里没有的 rid）不返回
    fb.add(uid, "用户在学吉他")
    good_rid = [r["rid"] for r in db.list_manager_memory_rows(uid) if r["content"] == "用户在学吉他"][0]

    class _FakeHit:
        def __init__(self, rid: int):
            self.record_id = rid
            self.distance = 0.1
            self.text = "命中"
            self.meta = {}

    orig_search = vs.search
    vs.search = lambda *a, **k: [_FakeHit(good_rid), _FakeHit(999999)]  # 999999=孤儿
    try:
        out = fb.search(uid, "吉他")
    finally:
        vs.search = orig_search
    assert len(out) == 1 and out[0]["id"] == f"{uid}|mem|{good_rid}", f"孤儿向量应被过滤：{out}"

    # 6) forget_old：超龄清理（直接落库旧记录，绕开 add 自带的写入后淘汰）
    old_ts = (datetime.now() - timedelta(days=120)).isoformat(timespec="seconds")
    assert db.save_manager_memory(uid, 424242, "很老的记忆", 0.5, old_ts)
    removed = fb.forget_old(uid, max_age_days=90)
    assert removed >= 1, "超龄记忆应被清理"
    contents = [r["content"] for r in db.list_manager_memory_rows(uid)]
    assert "很老的记忆" not in contents

    # 7) clear_user：权威表全清（reset 的失忆重开走这条路径）
    assert fb.clear_user(uid) >= 1
    assert db.list_manager_memory_rows(uid) == []

    # 8) migrate 增量判定：有权威行、mem 向量计数为 0 → 需要迁移。
    #    不依赖真实 chroma/embedding：把 enabled/count 打桩后验证计数逻辑。
    db.save_manager_memory(uid, 12345, "待迁移记忆")
    orig_enabled, orig_count = vs.enabled, vs.count
    vs.enabled = lambda: True
    vs.count = lambda kind=None: 0
    try:
        assert migration._needs_migration(), "mem 权威行存在而向量缺失时，应判定需要迁移"
    finally:
        vs.enabled, vs.count = orig_enabled, orig_count
    db.clear_manager_memories(uid)

    # 9) migrate 的 mem 源重灌使用内容哈希 rid（不是自增 id）
    import inspect

    src = inspect.getsource(migration.migrate)
    assert "manager_memories" in src and 'rid_fn=lambda r: r["rid"]' in src

    print("test_manager_memories: 全部通过")


if __name__ == "__main__":
    main()
