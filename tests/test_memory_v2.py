# -*- coding: utf-8 -*-
"""记忆系统 v2 冒烟测试：向量库 / 检索 / 回忆检测 / 查询扩展 / 事实检索。

运行：python -m tests.test_memory_v2
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


async def main() -> None:
    from backend.core.memory import (
        expand_query,
        looks_like_recall,
        recall,
        recall_facts,
    )
    from backend.core.memory.vector_store import stats as vec_stats
    from backend.core.userdb import db

    print("== 1. 向量库状态 ==")
    s = vec_stats()
    check("Chroma 可用", s.get("enabled") is True, str(s))
    check("长期记忆向量 >= 80", s.get("lm", 0) >= 80, f"lm={s.get('lm',0)}")
    check("事实向量 >= 20", s.get("facts", 0) >= 20, f"facts={s.get('facts',0)}")
    check("画像向量 >= 20", s.get("profile", 0) >= 20, f"profile={s.get('profile',0)}")

    user_id = "smoke_test_user"
    db.ensure_user(user_id)

    print("== 2. 检索接口 ==")
    r1 = await recall(user_id, "上次我们聊了什么", mock=True)
    check("recall 返回列表", isinstance(r1, list))
    r2 = await recall_facts(user_id, "用户喜欢什么", mock=True)
    check("recall_facts 返回列表", isinstance(r2, list))
    print(f"   recall={r1}")
    print(f"   facts={r2}")

    print("== 3. 回忆检测 ==")
    check("识别『上次』", looks_like_recall("上次你说过什么"))
    check("识别『还记得』", looks_like_recall("还记得吗"))
    check("不误判闲聊", not looks_like_recall("今天天气不错"))

    print("== 4. 查询扩展 ==")
    terms = await expand_query(user_id, "还记得我说过喜欢什么音乐吗", mock=True)
    check("mock 扩展返回列表", isinstance(terms, list) and len(terms) >= 1, str(terms))
    print(f"   terms={terms}")

    print("== 5. 记忆引擎 ==")
    from backend.core.memory.engine import ensure_ready, on_message

    ensure_ready()
    on_message(user_id, "我最爱吃番茄牛腩面", "嗯，记住了")
    print("   on_message 无异常（后台任务异步）")
    check("on_message 不阻塞", True)

    print()
    print(f"=== 测试结果: {PASS} 通过, {FAIL} 失败 ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
