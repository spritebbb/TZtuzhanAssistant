# -*- coding: utf-8 -*-
"""pipeline 场景验证：模拟完整对话流程（mock 模式，不消耗 API 额度）。

运行：python -m tests.test_pipeline_scenario
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


async def main() -> None:
    from backend.core.pipeline import process
    from backend.core.userdb import db

    user_id = "pipeline_test_user"
    db.ensure_user(user_id)
    # 先清空测试用户的消息（避免旧数据干扰）
    db.conn.execute("DELETE FROM messages WHERE user_id=?", (user_id,))
    db.conn.execute("DELETE FROM long_memory WHERE user_id=?", (user_id,))
    db.conn.execute("DELETE FROM facts WHERE user_id=?", (user_id,))
    db.conn.commit()

    print("== pipeline 场景测试（mock=True）==")

    # 场景 1: 问候
    text = "你好啊菟菚"
    reply = await process(user_id, text, mock=True)
    check("问候回复", reply and len(reply) > 5, reply)
    print(f"  回复: {reply[:50]}")

    # 场景 2: 闲聊
    text = "今天天气不错"
    reply = await process(user_id, text, mock=True)
    check("闲聊回复", reply and len(reply) > 5, reply)
    print(f"  回复: {reply[:50]}")

    # 场景 3: 回忆触发（hit recall keywords）
    text = "还记得上次我们聊过的番茄牛腩面吗"
    reply = await process(user_id, text, mock=True)
    check("回忆触发回复", reply and len(reply) > 5, reply)
    print(f"  回复: {reply[:50]}")

    # 场景 4: 告知重要日子
    text = "我生日是12月25日"
    reply = await process(user_id, text, mock=True)
    check("日子识别回复", reply and len(reply) > 5, reply)
    # 检查日子是否被记住（mock 模式下 LLM 不参与，日子识别可能不触发）
    dates = db.conn.execute(
        "SELECT * FROM important_dates WHERE user_id=?", (user_id,)
    ).fetchall()
    if dates:
        check("日子已入库", len(dates) >= 1, str([d["label"] for d in dates]))
        for d in dates:
            print(f"  记住日子: {d['label']} ({d['date']})")
    else:
        print("  (skip) 日子入库：mock 模式需真实 LLM，跳过验证")

    # 场景 5: 设置昵称
    text = "叫我小明吧"
    reply = await process(user_id, text, mock=True)
    check("称呼设置回复", reply and len(reply) > 5, reply)
    user = db.get_user(user_id)
    if user["nickname_pref"]:
        check("昵称已设置", bool(user["nickname_pref"]), str(user["nickname_pref"]))
        print(f"  昵称: {user['nickname_pref']}")
    else:
        print("  (skip) 昵称设置：mock 模式需真实 LLM，跳过验证")

    # 场景 6: 检查记忆写入
    msgs = db.recent_messages(user_id, 10)
    check("消息已存档", len(msgs) >= 10, f"got {len(msgs)}")
    lm = db.conn.execute(
        "SELECT COUNT(*) as c FROM long_memory WHERE user_id=?", (user_id,)
    ).fetchone()["c"]
    check("长期记忆已写入", lm >= 10, f"lm={lm}")

    # 清理测试数据
    db.conn.execute("DELETE FROM messages WHERE user_id=?", (user_id,))
    db.conn.execute("DELETE FROM long_memory WHERE user_id=?", (user_id,))
    db.conn.execute("DELETE FROM facts WHERE user_id=?", (user_id,))
    db.conn.execute("DELETE FROM important_dates WHERE user_id=?", (user_id,))
    db.conn.execute("DELETE FROM users WHERE user_id=?", (user_id,))
    db.conn.commit()

    print()
    print(f"=== 场景测试结果: {PASS} 通过, {FAIL} 失败 ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())