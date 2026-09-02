# -*- coding: utf-8 -*-
"""真实数据检索验证：用存量记忆库做端到端检索（非 mock）。

运行：python -m tests.test_recall_real
"""
import asyncio
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main() -> None:
    conn = sqlite3.connect(ROOT / "data" / "bot.db")
    conn.row_factory = sqlite3.Row
    users = conn.execute("SELECT DISTINCT user_id FROM long_memory LIMIT 3").fetchall()
    conn.close()
    if not users:
        print("无存量记忆数据")
        return

    from backend.core.memory import recall, recall_facts
    from backend.core.memory.vector_store import stats as vec_stats

    print("== 向量库状态 ==")
    s = vec_stats()
    print("   ", s)

    for u in users:
        uid = u["user_id"]
        print(f"\n== 用户 {uid} ==")
        for q in ("上次聊了什么", "用户喜欢什么", "有什么约定"):
            r = await recall(uid, q)
            print(f"  recall[{q}]: {len(r)} 条")
            for t in r[:2]:
                print(f"    - {t[:70]}")
            f = await recall_facts(uid, q)
            print(f"  facts[{q}]: {len(f)} 条")
            for t in f[:2]:
                print(f"    - {t[:70]}")
        break  # 只验证第一个用户

    print("\n真实检索验证完成")


if __name__ == "__main__":
    asyncio.run(main())
