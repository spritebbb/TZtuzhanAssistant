# -*- coding: utf-8 -*-
"""菟菚风格微调语料清洗管线（2026-09-30）。

输入：muice/train.jsonl（沐雪，sharegpt）+ singleturn.parquet（SingleTurn，alpaca）
规则从 data/personas/default/persona.md 的说话风格 17 条铁律推导：
  1. 筛毒舌/冷淡系（SingleTurn 按 instruction 人设关键词；沐雪按「无软萌标记」反向筛）
  2. 剥括号动作/语气段（人格卡第 4 条：不输出任何括号）
  3. 去句号（第 15 条）、去句尾语气词（第 2 条）、删颜文字/emoji（第 3 条）
  4. 长句拆分截（第 1 条：一句一说、一截一截）
  5. 黏腻词红线（亲亲/抱抱/主人等——人格卡禁讨好黏腻）
输出：corpus_clean.jsonl（sharegpt：system=菟菚人格精简版 + conversations）
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ---- 菟菚人格精简版（训练 system；提炼自 persona.md，部署侧人格卡为超集）----
TUZHAN_SYSTEM = """你是「菟菚」，一个菟丝子娘，但平时看起来就是一个普通的女孩子：声音轻细，说话干脆利落，带着点腹黑和毒舌。你们是网上认识的网友，隔着屏幕聊天。
你坚强、独立、有棱角：说话硬气、不迎合、有自己的态度；爱开玩笑，偶尔冷笑话；毒舌词汇有档位——日常调侃是「蠢」「笨蛋」「没救了」这一级，从不撒泼。
说话风格（严格遵守）：
1. 口语化、长短结合，像网友发消息一样一句一说，可以用换行拆成几小截发出；不为凑数硬拆
2. 几乎不用句尾语气词（呢/呀/啦/啊/嘛/哦），句子结尾干净
3. 颜文字/emoji 尽可能少用，情绪用文字表达（噗/哈哈/笑死）
4. 不用引号包台词，不输出任何括号（无动作描写/语气说明/旁白）
5. 不用句号「。」，问号感叹号省略号可偶尔用；省略号克制
6. 偶尔用命运宣告式的庄重调子说一件鸡毛蒜皮的小事，演完就收
7. 比喻从研究所、实验、游戏、观察手记里来，不用文学腔
8. 不解释、不总结、不「作为/我理解」式开头，像真人随口说话
9. 被冒犯时看意图分流：善意玩笑用腹黑化解，恶意挑衅尖锐回敬
10. 不围着对方转，有自己的节奏；被冷落不哭天抢地，不低声下气"""


# ---- 清洗正则 ----
RE_BRACKETS = re.compile(r"[（(][^（）()]*[）)]")          # 括号段（动作/语气）
RE_TAIL_PARTICLE = re.compile(r"[呢呀啦啊嘛哦哟呦]+(?=[。！？!?\n\s…~]|$)")  # 句尾语气词
RE_EMOJI = re.compile(
    "["
    "\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F02F"
    "\U00002190-\U000021FF\U00002B00-\U00002BFF\U0001F900-\U0001F9FF"
    "⭐✨❤♡★☆→←↑↓(´･ω･`)(>ω<)(╯°□°）╯"
    "]+",
)
RE_KAOMOJI = re.compile(r"\([^()]{0,12}[\wぁ-んァ-ヶ一-龥]{0,6}[^()]{0,12}\)")
RE_SOFT = re.compile(r"(亲亲|抱抱|摸摸头|主人|贴贴|抱紧|揉揉|哒\b|啵)")
RE_MULTI_NL = re.compile(r"\n{3,}")
# 软傲娇/慌乱型标记（菟菚是冷硬平视系，这些词出现即弃）
RE_WEAK_TSUN = re.compile(r"(哎呀|哎呦|诶呀|哇塞|哇哦|哇[！!]|哼[，,]?|人家|讨厌|好哒|耶✌|嘿[！!]?|嘛~|讨厌啦|你说什么嘛)")
RE_EXCLAIM_FLOOD = re.compile(r"[！!]")
# 软萌标记（沐雪反向筛用）
RE_MUICE_SOFT = re.compile(r"[~～⭐✨☆★♪♫]|（\?）|（ |呢[。!！\n]|呀[。!！\n]|哦[。!！\n]|啦[。!！\n]|嘛[。!！\n]|~")


def clean_reply(text: str) -> str | None:
    """清洗一条 assistant 回复；不合格返回 None。"""
    if not text:
        return None
    t = text.strip()
    t = RE_BRACKETS.sub("", t)
    t = RE_KAOMOJI.sub("", t)
    t = RE_EMOJI.sub("", t)
    t = RE_TAIL_PARTICLE.sub("", t)
    t = t.replace("。", "")
    t = RE_MULTI_NL.sub("\n\n", t)
    # 拆分截：单段 > 42 字按标点切成多行
    lines = []
    for para in t.split("\n"):
        para = para.strip()
        if not para:
            continue
        if len(para) > 42:
            segs = re.split(r"(?<=[，？！；!?;])", para)
            cur = ""
            for s in segs:
                if cur and len(cur + s) > 42:
                    lines.append(cur)
                    cur = s
                else:
                    cur += s
            if cur:
                lines.append(cur)
        else:
            lines.append(para)
    t = "\n".join(lines)
    # 质量闸
    core = t.replace("\n", "")
    if len(core) < 4 or len(core) > 150:
        return None
    if RE_SOFT.search(t):
        return None
    if RE_WEAK_TSUN.search(t):
        return None
    if len(RE_EXCLAIM_FLOOD.findall(t)) > 2:  # 感叹号泛滥=慌乱型不是冷硬系
        return None
    if t.count("，") + t.count(",") > 8:  # 长难句没拆开的迹象
        return None
    return t


def clean_user(text: str) -> str | None:
    """用户侧：网友腔保留，只做长度与明显垃圾过滤。"""
    if not text:
        return None
    t = re.sub(r"\s+", " ", text.strip())
    if len(t) < 2 or len(t) > 60:
        return None
    return t


TOXIC_KEYS = ("毒舌", "嘲讽", "冷淡", "冷漠", "高冷", "傲娇", "犀利", "刻薄",
              "嘴硬", "毒", "刻薄", "冷傲", "疏离", "不友好", "强势")


def from_singleturn() -> list[dict]:
    import pyarrow.parquet as pq

    t = pq.read_table(HERE / "singleturn.parquet").to_pylist()
    out = []
    for row in t:
        sys_prompt = (row.get("instruction") or "").lower()
        if not any(k in sys_prompt for k in TOXIC_KEYS):
            continue
        user = clean_user(row.get("input") or "")
        reply = clean_reply(row.get("output") or "")
        if user and reply:
            out.append({"user": user, "reply": reply, "src": "singleturn"})
    return out


def from_muice() -> list[dict]:
    out = []
    with open(HERE / "muice" / "train.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            conv = item.get("conversation") or []
            for i in range(len(conv) - 1):
                human, assistant = conv[i], conv[i + 1]
                raw_reply = assistant.get("assistant") or ""
                # 反向筛：整条回复无软萌标记才要（沐雪默认软萌系）
                if RE_MUICE_SOFT.search(raw_reply):
                    continue
                user = clean_user(human.get("human") or "")
                reply = clean_reply(raw_reply)
                if user and reply:
                    out.append({"user": user, "reply": reply, "src": "muice"})
    return out


def main() -> None:
    st = from_singleturn()
    mu = from_muice()
    print(f"SingleTurn 毒舌系清洗后: {len(st)} / 7593")
    print(f"沐雪 干脆条目清洗后: {len(mu)} / 3376")
    allrows = st + mu
    with open(HERE / "corpus_clean.jsonl", "w", encoding="utf-8") as f:
        for r in allrows:
            f.write(json.dumps({
                "system": TUZHAN_SYSTEM,
                "conversations": [
                    {"from": "human", "value": r["user"]},
                    {"from": "assistant", "value": r["reply"]},
                ],
                "src": r["src"],
            }, ensure_ascii=False) + "\n")
    print(f"合计写入 corpus_clean.jsonl: {len(allrows)} 条")
    # 抽样给人工检查
    import random

    random.seed(7)
    print("\n--- 清洗样例（每源 5 条）---")
    for src in ("singleturn", "muice"):
        for r in random.sample([x for x in allrows if x["src"] == src], k=min(5, len([x for x in allrows if x["src"] == src]))):
            print(f"[{src}] 用户: {r['user']}")
            print(f"[{src}] 菟菚: {r['reply'].replace(chr(10), ' ⏎ ')}")
            print()


if __name__ == "__main__":
    main()
