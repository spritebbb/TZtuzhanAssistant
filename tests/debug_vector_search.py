# -*- coding: utf-8 -*-
"""向量检索质量调试：确认 BGE-M3 对语义相关句子的召回排序。

运行：python -m tests.debug_vector_search
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    from backend.core.memory import vector_store as vec
    from backend.core.userdb import db

    uid = "debug_vec_user"
    # 清空该用户向量
    for kind in ("lm", "facts"):
        col = vec._collection(kind)
        if col is not None:
            try:
                col.delete(where={"user_id": uid})
            except Exception:
                pass
    db.conn.execute("DELETE FROM long_memory WHERE user_id=?", (uid,))
    db.conn.commit()

    docs = [
        "用户说：你好，我喜欢下雨天，最近在养一只猫叫团团",
        "用户说：记住啦，我生日是8月8号",
        "用户说：今天工作有点累，想休息一下",
        "用户说：你还记得我养了只猫吗",
        "用户说：这周末想去爬山",
    ]
    print("写入文档 + 建向量索引...")
    for i, d in enumerate(docs):
        rid = db.add_long_memory(uid, d)
        ok = vec.add(uid, "lm", rid, d)
        print(f"  [{rid}] ok={ok} | {d[:40]}")

    print("\n查询『用户养的猫』:")
    for h in vec.search(uid, "用户养的猫", 5, "lm"):
        print(f"  dist={h.distance:.4f} | {h.text[:45]}")

    print("\n查询『用户生日』:")
    for h in vec.search(uid, "用户生日是什么时候", 5, "lm"):
        print(f"  dist={h.distance:.4f} | {h.text[:45]}")

    print("\n查询『周末打算』:")
    for h in vec.search(uid, "这个周末有什么计划", 5, "lm"):
        print(f"  dist={h.distance:.4f} | {h.text[:45]}")

    # 清理
    for kind in ("lm", "facts"):
        col = vec._collection(kind)
        if col is not None:
            try:
                col.delete(where={"user_id": uid})
            except Exception:
                pass
    db.conn.execute("DELETE FROM long_memory WHERE user_id=?", (uid,))
    db.conn.commit()
    print("\n清理完成")


if __name__ == "__main__":
    main()
