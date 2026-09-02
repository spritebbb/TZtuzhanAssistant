# -*- coding: utf-8 -*-
"""真实 LLM 端到端对话验证（非 mock，会消耗少量 API 额度）。

验证：真实对话 → 记忆写入 → 画像提炼 → 事实检索 → 回忆命中。
运行：python -m tests.test_live_chat
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main() -> None:
    from backend.core.pipeline import process
    from backend.core.userdb import db
    from backend.core.config import config

    if not config.llm_api_key:
        print("未配置 LLM_API_KEY，无法做真实对话验证")
        return

    user_id = "live_test_user"
    db.ensure_user(user_id)
    # 清空测试用户数据
    for t in ("messages", "long_memory", "facts", "triples", "user_profile"):
        db.conn.execute(f"DELETE FROM {t} WHERE user_id=?", (user_id,))
    db.conn.execute("DELETE FROM users WHERE user_id=?", (user_id,))
    db.conn.execute("DELETE FROM user_meta WHERE user_id=?", (user_id,))
    db.conn.commit()
    db.ensure_user(user_id)

    print("== 真实对话链路测试（LLM: {}）==".format(config.llm_model))

    # 1) 第一句：自我介绍+偏好（让菟菚记住）
    r1 = await process(user_id, "你好，我喜欢下雨天，最近在养一只猫叫团团")
    print(f"[1] 回复: {r1[:60]}")

    # 2) 等待后台画像提炼完成
    await asyncio.sleep(3)

    # 3) 设置重要日子
    r2 = await process(user_id, "记住啦，我生日是8月8号")
    print(f"[2] 回复: {r2[:60]}")
    dates = db.conn.execute(
        "SELECT label, date FROM important_dates WHERE user_id=?", (user_id,)
    ).fetchall()
    print(f"    记住的日子: {[dict(d) for d in dates]}")

    # 4) 换一个话题聊两句，然后回忆
    r3 = await process(user_id, "今天工作有点累")
    print(f"[3] 回复: {r3[:60]}")
    await asyncio.sleep(2)

    # 5) 关键验证：回忆（应命中之前的"下雨天/猫/团团"）
    r4 = await process(user_id, "你还记得我养了只猫吗")
    print(f"[4] 回忆回复: {r4[:80]}")

    # 6) 检索侧验证
    from backend.core.memory import recall, recall_facts

    lm = await recall(user_id, "用户养的猫")
    facts = await recall_facts(user_id, "用户喜欢什么天气")
    print(f"    recall(猫): {[t[:50] for t in lm[:2]]}")
    print(f"    facts(天气): {[t[:50] for t in facts[:2]]}")

    # 7) 画像检查
    profile = db.get_profile(user_id)
    print(f"    画像条目: {len(profile)} 条")
    for p in profile[:3]:
        print(f"      - [{p['category']}] {p['content'][:40]}")

    # 8) 向量库状态
    from backend.core.memory.vector_store import stats as vec_stats
    print(f"    向量库: {vec_stats()}")

    # 清理
    for t in ("messages", "long_memory", "facts", "triples", "user_profile"):
        db.conn.execute(f"DELETE FROM {t} WHERE user_id=?", (user_id,))
    db.conn.execute("DELETE FROM users WHERE user_id=?", (user_id,))
    db.conn.execute("DELETE FROM user_meta WHERE user_id=?", (user_id,))
    db.conn.commit()
    print("\n真实对话链路验证完成")


if __name__ == "__main__":
    asyncio.run(main())
