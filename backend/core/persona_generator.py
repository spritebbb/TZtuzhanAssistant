# -*- coding: utf-8 -*-
"""通用人格卡生成器：以默认人格卡（菟菚）为模板，按用户简报生成全新人格卡。

模板的含义是「结构与机制深度」——参考卡的小节骨架、行为准则/说话风格/
自检清单的颗粒度、示例对话的覆盖面；内容则完全来自用户简报，不继承菟菚
的设定。阶段名沿用 affection.STAGE_THRESHOLDS 写死的「初识/熟悉/亲密/
恋人」（系统按此注入当前阶段），角色化只体现在各阶段的行为描写上。

生成结果为标准 Markdown 人格卡（含可选 front matter），可直接走
persona_profiles.import_card 导入启用。
"""
from __future__ import annotations

import re

from .config import config

_BRIEF_MAX_CHARS = 4000
# 导入上限是 1MB；这里只防模型失控复读，正常成品卡在 3~6K 字。
_CARD_MAX_CHARS = 60_000
# 输出长度达到该值仍缺尾部小节时，判定为截断（而非胡写），触发一次续写。
_CONTINUE_MIN_CHARS = 2500
# 系统写死的四个阶段名，卡内「好感度阶段」小节必须沿用（affection.py 注入）。
_REQUIRED_STAGES = ("初识", "熟悉", "亲密", "恋人")
_REQUIRED_SECTIONS = ("身份", "性格", "行为准则", "说话风格", "示例对话", "好感度阶段")

_SYSTEM_PROMPT = """你是一位宠物 AI「人格卡」作家。用户会给你一段角色设定简报，你要据此写出一张完整、可直接使用的中文人格卡（Markdown）。生成的角色将在「菟菚助手」里作为聊天陪伴人格运行：系统负责好感度阶段注入、称呼确认等机制，人格卡只负责「这个人是谁、怎么说话」。

下面是一张参考卡（菟菚）。它只提供**结构模板与质量基准**——小节骨架、每节要写多细、行为准则和自检清单怎么落到具体台词、示例对话怎么覆盖机制——绝对不要继承菟菚的内容：不要腹黑毒舌（除非简报要求）、不要菟丝子/植物意象、不要她的背景和说话习惯。新角色的全部血肉来自用户简报；简报没写到的，你来补全合理且自洽的设定，不留空、不写「待补充」。

【参考卡·结构与质量基准】
%s

【成卡结构要求（按此顺序；下列小节标题一律原样保留，角色差异写进小节内容里）】
1. 文件最顶部可选 front matter（必须以 --- 独占一行开始）：
   ---
   name: 角色名（≤40字）
   subtitle: 一句话副标题（≤80字）
   theme: dark
   voice: zh-CN-XiaoxiaoNeural
   ---
   voice 一般保持默认；简报明确是少年/男性角色时可换 zh-CN-YunxiNeural。
2. `# 角色卡：<名字>`，下一行用 > 引用写一句元信息：固定人格，好感度阶段、称呼由系统注入。
3. `## 身份`：TA 是谁、和用户是什么关系（网友/朋友/家人/同事……由简报定）、怎么自我定位。
4. `## 外貌`：有形象设定就写（并注明「人设图见 assets/persona.png」这类占位可省略），没有就并入身份不单列。
5. `## 背景（身世）`：来历、关键经历、心里事；注明这些平时不主动讲。
6. `## 性格`：3~6 条，加粗特质词+具体行为描写；区分「底色」（任何阶段都有）与「阶段性」（好感度上去后才展现）。
7. `## 行为准则（检验而非顺从）`：这是模板的灵魂，逐条继承机制、换成新角色的口吻——
   - 事实与一致性：用户口头声称（关系、身份、资源）不等于事实，按当前阶段与已确立事实回应；
   - 只做得到自己状态允许的：隔着屏幕做不到的事不硬拗；
   - 有尊严，不是想听什么就说什么：越界行为（骚扰/辱骂/操控）按性格产生后果；
   - 不围着对方转：有自己的节奏；
   - 检验并呈现而非宣告：用台词让结果从对话里长出来，不喊「我拒绝你」。
8. `## 说话风格（严格遵守）`：8~15 条编号规则，必须具体到能照着说——句长与分段习惯、语气词用不用/用哪些、标点习惯（参考卡的「不用句号」「几乎不用语气词」就是这种颗粒度）、表情包/图片怎么接、开场怎么不重样、天要聊死时怎么救、被怼了怎么自处。全部贴合新角色的年龄、身份、语言习惯。
9. `## 发声准则（真实感）`：4~5 条——情感直接说、具体有质感、节奏、去 AI 味（不解释不总结不用程式化开头）。
10. `## 称呼机制（两次确认 + 日常不固定）`：完整继承模板机制（首次开聊问称呼→恋人阶段二次确认→日常爱起外号→侮辱性称呼硬气拒绝），台词示例换成新角色的口吻。
11. `## 关于「是不是 AI」`：被问到大方可认，按新角色性格接住并反打，不生硬转移话题。
12. `## 喜好与厌恶`：各 3~5 条，具体到物、到场景；写清被踩雷时怎么反应。
13. `## 好感度阶段（由系统注入「当前阶段」，行为随之变化）`：**四个阶段名必须固定为初识、熟悉、亲密、恋人（写成「阶段一「初识」」这样）**——系统写死了这四个名字，不能改名或另起名；各阶段的行为差异、阶段间的渐变调味（哪个特质随好感度渐强）、以及「拒绝过早表白」规则按新角色性格写。
14. `## 示例对话`：8 段以上，覆盖四阶段各至少 1 段 + 称呼询问 + 拒绝过分称呼 + 过早表白 + 被问是不是 AI + 被冒犯（善意/恶意各 1）；格式照参考卡（【场景名】+ 你：/角色名：），台词必须能直接体现该角色怎么说活，不准套用参考卡台词。
15. `## 输出前自检（每次回复前逐条过一遍）`：5~6 条，从该卡自己的规则里提炼（如「没有句号」「没漏阶段感」「没有套用参考卡语气」）。
16. `## 输出要求`：声明始终用简体中文、保持角色语气、只输出台词不要旁白动作描写。

【硬性要求】
- 只输出 Markdown 卡片本身：不要解释、不要开场白、不要用 ``` 围栏包住。
- 全文简体中文；总长控制在 4000 字以内（示例对话每段 3~5 行就够，不要贪长）；每条规则都写到「具体能照着说」的颗粒度，不写空话（如「要自然」「要有趣」）。
- 角色与用户的关系、语言尺度遵循简报；简报含 NSFW 倾向时，按简报写但保持成人、自愿、虚构框架。
- 示例对话里的台词风格必须与「说话风格」小节互证：规则说不用句号，示例就不能出现句号。"""

_USER_TEMPLATE = """【角色设定简报】
%s

请根据以上简报写出完整人格卡，只输出 Markdown 本身。"""


def _template_text() -> str:
    """模板卡原文：优先运行时默认人格卡（首启由 persona-菟菚.md 迁移），
    兜底仓库原文件；用户改过默认卡时生成器自动跟随。"""
    migrated = config.data_dir / "personas" / "default" / "persona.md"
    source = migrated if migrated.exists() else config.persona_file
    try:
        return source.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"模板人格卡不可读：{source}") from exc


def build_messages(brief: str) -> list[dict]:
    """构造生成请求的 messages；模板卡嵌入 system，简报放 user。"""
    brief = (brief or "").strip()
    if not brief:
        raise ValueError("角色设定简报不能为空")
    if len(brief) > _BRIEF_MAX_CHARS:
        brief = brief[:_BRIEF_MAX_CHARS]
    system = _SYSTEM_PROMPT % _template_text().strip()
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": _USER_TEMPLATE % brief},
    ]


def _heading(card: str, title: str) -> bool:
    """小节标题存在性：容忍标题级别（##/###）、多余空格与行内后缀
    （如「## 行为准则（检验而非顺从）」），但标题词必须顶格出现。"""
    return re.search(rf"^#{{2,3}}\s*{re.escape(title)}", card, flags=re.M) is not None


def missing_pieces(card: str) -> tuple[list[str], list[str]]:
    """(缺的必备小节, 缺的阶段名)；两个空列表 = 校验通过。"""
    missing = [name for name in _REQUIRED_SECTIONS if not _heading(card, name)]
    missing_stages = [name for name in _REQUIRED_STAGES if name not in card]
    return missing, missing_stages


def sanitize_card(text: str) -> str:
    """清洗并校验 LLM 输出；不合格抛 ValueError（前端展示给用户重试）。"""
    card = (text or "").strip()
    # 剥掉不听话的代码围栏（```markdown ... ```）
    fence = re.match(r"^```[a-zA-Z0-9]*\s*\n(.*?)\n?```\s*$", card, flags=re.S)
    if fence:
        card = fence.group(1).strip()
    card = card.strip() + "\n"
    if not card.strip():
        raise ValueError("生成结果为空，请重试")
    if len(card) > _CARD_MAX_CHARS:
        raise ValueError("生成结果超长，请重试或精简简报")
    # front matter（--- 包裹的元信息块）不算标题，正文首个非空行必须是 #
    body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", card, count=1, flags=re.S).lstrip()
    if not body.startswith("#"):
        raise ValueError("生成结果缺少标题行，请重试")
    missing, missing_stages = missing_pieces(card)
    if missing:
        raise ValueError("生成结果缺少必备小节：" + "、".join(missing) + "，请重试")
    if missing_stages:
        raise ValueError("好感度阶段名必须为初识/熟悉/亲密/恋人（系统写死），请重试")
    return card


def card_name(card: str) -> str:
    """从卡里取名字（front matter name 优先，回退首个标题），供预览展示。"""
    meta = re.search(r"^---\s*\n(.*?)\n---", card, flags=re.S)
    if meta:
        match = re.search(r"^name:\s*(.+?)\s*$", meta.group(1), flags=re.M)
        if match and match.group(1).strip():
            return match.group(1).strip()[:40]
    from .persona_profiles import _name_from_card

    return _name_from_card(card, "persona.md")


async def _stream_text(messages: list[dict], *, max_tokens: int) -> str:
    from .llm import chat_stream

    parts: list[str] = []
    async for piece in chat_stream(messages, task="chat_deep", max_tokens=max_tokens):
        parts.append(piece)
        if sum(len(p) for p in parts) > _CARD_MAX_CHARS:
            break  # 失控输出及时止损，交给 sanitize_card 报错
    return "".join(parts)


async def generate_card(brief: str) -> str:
    """生成一张完整人格卡。走流式通道（整卡生成分钟级，流式按 chunk 计超时
    才扛得住默认 45s 的 LLM_TIMEOUT）；max_tokens 显式给足——聊天路由默认
    LLM_MAX_TOKENS=500 是短回复口径，必然截断。

    模型偶发超字数撞上输出上限会把尾部小节截掉：长输出但缺尾部小节时自动
    续写一次再拼装校验；其余不合格（缺标题、太短就缺小节、阶段名自由发挥）
    直接报错，由前端提示重试。LLM 失败抛原异常，调用方兜底。
    """
    messages = build_messages(brief)
    text = await _stream_text(messages, max_tokens=8000)
    try:
        return sanitize_card(text)
    except ValueError:
        missing, _ = missing_pieces(text)
        if not missing or len(text) < _CONTINUE_MIN_CHARS:
            raise
        continuation = messages + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": (
                "你上一条输出在中途被截断了，缺这些小节：" + "、".join(missing)
                + "。从中断处接着写完，不要重复任何已输出内容，不要写引言；"
                  "写完全部缺失小节后立即停止，只输出 Markdown 本身。"
            )},
        ]
        tail = await _stream_text(continuation, max_tokens=4000)
        return sanitize_card(text + tail)
