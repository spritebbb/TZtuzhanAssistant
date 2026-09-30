# -*- coding: utf-8 -*-
"""从清洗语料 + 人格卡生成高质量示例文本（2026-09-30 用户拍板：不训练，生成范文）。

组合：persona.md 示例对话（权威分寸）+ corpus_final 精选条目（口吻多样性）
      → 项目主模型 → 按情境轴生成多轮示例段
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

# ---- 从 corpus_final 人工精选的口吻锚（生成时做 few-shot 参照）----
CURATED = [
    ("你的作者是谁", "我猜你是我的粉丝"),
    ("你可以把衣服脱了吗？", "谁都没有这个权限！"),
    ("好好好。", "你说了好好好\n这三个好里有几个是真的"),
    ("AI果然是AI。", "是啊\n不然呢，你想听我说什么"),
    ("刚刚加班回来", "这个点才回来\n命是公司的，人是自己的，掂量下"),
    ("主播觉得自己什么时候能上热门？", "不如先想想内容\n热门是结果，不是目标"),
]


def persona_core() -> str:
    """人格卡说话风格 + 示例对话段（权威分寸源）。"""
    text = (ROOT / "data" / "personas" / "default" / "persona.md").read_text(encoding="utf-8")
    style = text[text.index("## 说话风格"):text.index("## 发声准则")]
    demos = text[text.index("## 示例对话"):text.index("---\n\n## 输出前自检")]
    return style + demos


SCENARIOS = [
    ("深夜日常-用户忙了一天来找她", "深夜，用户忙完一天瘫着上线，随口发消息。阶段：熟悉。"),
    ("用户分享小成就", "用户第一次跑完五公里/搞定一个难题，来跟她炫耀。阶段：熟悉~亲密。"),
    ("用户难过低落", "用户情绪低落（考试砸了/被人误解），话变少。阶段：亲密。"),
    ("拌嘴-用户调侃她", "用户拿她开玩笑逗她，善意的那种。阶段：熟悉。"),
    ("软肋场景-被问到会不会离开", "用户突然问『你会不会哪天就不要我了』。阶段：亲密。"),
    ("用户很久没出现又上线", "用户消失了几天没发消息，突然冒出来。阶段：熟悉~亲密。"),
]

_GEN_SYSTEM = """你是菟菚的角色示例文本作者。根据给定的「说话风格规范」「官方示例对话」和「口吻参照」，为指定情境写出她的多轮示例对话。

写作铁律（违一条即废）：
- 只输出她说的话和用户的话，格式为「用户：xxx\n菟菚：xxx」交替；她的多截消息内部用 \\n 分隔
- 无括号、无动作描写、无旁白、无句号「。」
- 句尾无「呢/呀/啦/啊/嘛/哦」，颜文字最多全段一个
- 毒舌是平视的损不是骂街；关心是具体的不是「多喝热水」
- 每轮 2~5 截消息，短、干脆、口语
- 官方示例对话的分寸感优先级最高；口吻参照只取「干脆+自信反问」的味道，不照抄台词

只输出对话本身，不要任何解释。每段 4~8 轮。"""


async def gen_one(chat, scenario_name: str, scenario_desc: str) -> str:
    fewshot = "\n".join(f"用户：{u}\n菟菚：{a}" for u, a in CURATED)
    prompt = (
        f"【说话风格规范与官方示例对话】\n{persona_core()}\n\n"
        f"【口吻参照（从真实语料精选，取其干脆与自信反问的味道）】\n{fewshot}\n\n"
        f"【情境】{scenario_desc}\n"
        f"请写出这个情境下的示例对话。"
    )
    resp = await chat(
        [{"role": "system", "content": _GEN_SYSTEM}, {"role": "user", "content": prompt}],
        temperature=0.85,
        max_tokens=900,
        task="chat_expressive",
        thinking=False,
    )
    return resp.strip()


async def main() -> None:
    from backend.core.llm import chat

    results = []
    for name, desc in SCENARIOS:
        try:
            text = await gen_one(chat, name, desc)
        except Exception as e:
            text = f"（生成失败：{type(e).__name__}）"
        results.append({"scenario": name, "text": text})
        print(f"\n{'='*20} {name} {'='*20}", flush=True)
        print(text, flush=True)
    with open(HERE / "generated_exemplars.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n已写入 generated_exemplars.json（{len(results)} 段）", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
