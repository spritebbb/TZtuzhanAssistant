# -*- coding: utf-8 -*-
"""Mem0 集成验证：测试 add / search / get_all 完整链路。

运行：python -m tests.test_mem0
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    from backend.core.memory.memory_manager import manager

    print("== Mem0 初始化 ==")
    ok = manager._ensure_ready()
    print(f"初始化结果: provider={manager.stats()}")
    if manager._mem0 is None:
        print("Mem0 未启用，回退到自研管理（不影响功能）")
        print("自研管理 add/search 测试：")
        uid = "mem0_test_user"
        manager.add(uid, "用户爱吃番茄牛腩面")
        res = manager.search(uid, "喜欢吃什么")
        print(f"  检索结果: {res}")
        return

    print("Mem0 已启用，执行 add/search/get_all")
    uid = "mem0_live_test"
    # add
    added = manager.add(uid, "用户喜欢在雨天听轻音乐")
    print(f"add: {added}")
    added2 = manager.add(uid, "用户养了一只三花猫叫团团")
    print(f"add2: {added2}")
    # search
    res = manager.search(uid, "用户喜欢什么音乐", limit=3)
    print(f"search(音乐): {res}")
    res2 = manager.search(uid, "用户有宠物吗", limit=3)
    print(f"search(宠物): {res2}")
    # get_all
    all_mem = manager.get_all(uid)
    print(f"get_all: {len(all_mem)} 条")
    for m in all_mem[:5]:
        print(f"  - {m}")


if __name__ == "__main__":
    main()
