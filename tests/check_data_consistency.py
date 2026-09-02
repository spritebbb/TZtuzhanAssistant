# -*- coding: utf-8 -*-
"""检查向量库与 SQLite 的数据一致性。"""
import sqlite3, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

conn = sqlite3.connect(ROOT / "data" / "bot.db")
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT user_id, COUNT(*) c FROM long_memory GROUP BY user_id").fetchall()
print("SQLite long_memory 各用户:")
for r in rows:
    print(f"  {r['user_id']}: {r['c']}")
conn.close()

from backend.core.memory import vector_store as vec
col = vec._collection("lm")
data = col.get()
from collections import Counter
c = Counter()
for uid in data["metadatas"]:
    if uid and "user_id" in uid:
        c[uid["user_id"]] += 1
print("\nChroma lm 各用户:")
for k, v in sorted(c.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}")
print(f"\nSQLite 总数: {sum(r['c'] for r in rows)}")
print(f"Chroma 总数: {len(data['ids'])}")
# 一致性检查
sqlite_ids = {r['user_id'] for r in rows}
chroma_ids = set(c.keys())
missing = sqlite_ids - chroma_ids
extra = chroma_ids - sqlite_ids
print(f"SQLite 有而 Chroma 缺的用户: {missing or '无'}")
print(f"Chroma 有而 SQLite 无的用户: {extra or '无'}")