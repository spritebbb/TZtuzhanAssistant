# -*- coding: utf-8 -*-
"""LLM 复审清洗语料：逐条判断是否符合菟菚说话风格，筛掉规则洗不净的杂质。

复用 backend.core.llm.chat（judge task 通路，与 consistency_judge 同模式）。
输入 corpus_clean.jsonl → 输出 corpus_final.jsonl（只保留 keep=true）
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

_REVIEW_SYSTEM = """你是语料评审。判断这条 AI 角色回复是否符合「菟菚」的说话风格。只评风格，不管内容对错。

菟菚风格标准（不合格任一条即 keep=false）：
1. 干脆利落、冷硬平视：无「哎呀/哼/人家/哇塞/本小姐/天才少女」式慌乱傲娇或自夸腔
2. 无文艺腔书面抒情（如「旋律在心中回荡」「夜晚让人难以入眠」这类小说句=不合格）
3. 无颜文字/emoji/掀桌符号残留
4. 句尾无「呢/呀/啦/啊/嘛/哦」（句中语气自然的不算）
5. 毒舌但不撒泼：平视的损、克制的怼；骂街、咆哮、连续感叹号轰炸=不合格
6. 像网友发消息：短、直接、口语；客服腔/助手腔/百科腔=不合格

只输出一个 JSON 对象：{"keep": true或false, "reason": "不合格原因，≤15字"}"""


async def review_one(chat, item: dict) -> dict:
    prompt = f"用户说：{item['conversations'][0]['value']}\n\n角色回复：\n{item['conversations'][1]['value']}"
    try:
        resp = await chat(
            [
                {"role": "system", "content": _REVIEW_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=120,
            task="judge",
            thinking=False,
        )
        import re as _re

        m = _re.search(r"\{[\s\S]*\}", resp)
        verdict = json.loads(m.group(0)) if m else {"keep": True, "reason": "解析失败默认留"}
        item["review"] = verdict
    except Exception as e:
        item["review"] = {"keep": True, "reason": f"评审异常默认留:{type(e).__name__}"}
    return item


async def main() -> None:
    from backend.core.llm import chat

    rows = [json.loads(l) for l in open(HERE / "corpus_clean.jsonl", encoding="utf-8") if l.strip()]
    print(f"待复审: {len(rows)} 条", flush=True)

    sem = asyncio.Semaphore(8)

    async def bounded(item):
        async with sem:
            return await review_one(chat, item)

    reviewed = await asyncio.gather(*(bounded(r) for r in rows))
    kept = [r for r in reviewed if r.get("review", {}).get("keep")]
    dropped = [r for r in reviewed if not r.get("review", {}).get("keep")]

    with open(HERE / "corpus_final.jsonl", "w", encoding="utf-8") as f:
        for r in kept:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"保留 {len(kept)} / {len(rows)}（筛除 {len(dropped)}）→ corpus_final.jsonl", flush=True)
    # 淘汰原因分布
    from collections import Counter

    reasons = Counter(r["review"].get("reason", "?") for r in dropped)
    for reason, n in reasons.most_common(10):
        print(f"  淘汰 {n:4d} × {reason}")
    # 保留样例
    import random

    random.seed(3)
    print("\n--- 保留样例 8 条 ---")
    for r in random.sample(kept, k=min(8, len(kept))):
        print(f"[{r['src']}] 用户: {r['conversations'][0]['value']}")
        print(f"[{r['src']}] 菟菚: {r['conversations'][1]['value'].replace(chr(10), ' ⏎ ')}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
