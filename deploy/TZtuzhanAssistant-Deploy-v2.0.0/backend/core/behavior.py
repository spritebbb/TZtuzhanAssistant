"""行为映射层：把菟菚的内在状态翻译成「这一轮她该怎么说话」。

这是「状态 → 可感知行为」的翻译器。state 给出的是冰冷数字（情绪 72、精力 30、
好感度 58），本模块把它们转成 LLM 能直接照着演的行为指令，注入 system prompt。

输出一份「行为帧」（behavior frame），是自然语言描述，不是数值，供 persona 的
build_system_prompt 或 pipeline 直接拼接。这样菟菚的「活」是有状态驱动、可感知、
可解释的，而不是静态人设临时演。
"""
from __future__ import annotations

from dataclasses import dataclass

from .state import AgentState


@dataclass
class BehaviorFrame:
    """一轮对话里菟菚的行为指令帧（全部自然语言，可直接进 prompt）。"""

    mood_line: str          # 情绪/精力在语气里的体现
    initiative: str         # 主动性基调（主动/平常/收敛）
    reaction_line: str      # 若她正处在情绪残留里，该怎么流露
    stage_line: str         # 关系阶段对应的分寸提醒
    archive_line: str = ""  # 长期情绪档案 → 长期态度（她记得这段关系里的情感积累）
    event_line: str = ""    # 事件级长期记忆 → 精确引用「你上次说的某句话」

    def compose(self) -> str:
        """拼成一段可注入 system 的文本。"""
        parts = [self.mood_line, self.stage_line]
        if self.initiative:
            parts.append(self.initiative)
        if self.reaction_line:
            parts.append(self.reaction_line)
        if self.archive_line:
            parts.append(self.archive_line)
        if self.event_line:
            parts.append(self.event_line)
        return "\n".join(p for p in parts if p)


# ---- 情绪/精力 → 语气基调 ----
_EMOTION_FRAMING = {
    "低落": "你现在心情有点低落，说话会比平时更短、更直接，可能有点不耐烦，别勉强装开心",
    "平淡": "你现在心情一般，平静、有条理，不多话也不少话",
    "慵懒": "你现在懒懒的，提不起劲，说话简短随意，带点漫不经心",
    "开心": "你现在心情不错，说话轻快，偶尔俏皮、爱开玩笑，比平时更愿意接话",
    "雀跃": "你现在心情很好，活跃、想说话，会主动分享、想逗对方开心",
}


def _mood_line(s: AgentState) -> str:
    base = _EMOTION_FRAMING.get(s.emotion_name, _EMOTION_FRAMING["平淡"])
    parts = [base]
    if s.is_tired:
        parts.append("你现在有点累，语速会慢下来、话会变少，可能懒懒的不太想动，但别把「累」当借口敷衍对方")
    elif s.energy < 55:
        parts.append("你现在精力一般，不算累但也不亢奋，语气平和")
    return "，".join(parts) + "。"


# ---- 关系阶段 → 分寸 ----
_STAGE_FRAMING = {
    "初识": "你们还不熟，保持礼貌的疏离：不主动找话题、不撩、不开暧昧玩笑，可以偶尔腹黑毒舌一句试探，但整体冷淡克制",
    "熟悉": "你们比较熟了，可以开玩笑、毒舌调侃，但仍收敛、有分寸，不主动黏",
    "亲密": "你们很亲近，可以主动找你、偶尔撩，但依旧独立不黏不缠，腹黑玩笑更多",
    "恋人": "你们已是恋人，可以亲昵、撒娇、吃醋，但克制不过分黏，保持「我在意你，你也自由」的坦然",
}


def _stage_line(s: AgentState) -> str:
    return _STAGE_FRAMING.get(s.stage, _STAGE_FRAMING["初识"]) + "。"


# ---- 主动性基调 ----
def _initiative_line(s: AgentState) -> str:
    # 情绪低 + 关系远 → 收敛；情绪高 + 关系近 → 主动
    if s.emotion < 25:
        return "你现在没太多表达的兴致，回复可以简短一些，不必勉强热络。"
    if s.emotion >= 65 and s.stage in ("亲密", "恋人"):
        return "你现在心情好、和对方也亲近，可以比平时更主动一点——多问一句、多关心一下、主动分享一点。"
    if s.emotion >= 85:
        return "你现在情绪很高，想多说话、想分享，可以主动抛话题。"
    return ""


# ---- 情绪残留 → 该怎么流露 ----
def _reaction_line(s: AgentState) -> str:
    # 直接用 state 里已衰减的 emotion_memory（load_state 已算好）
    if not s.emotion_memory:
        return ""
    recent = s.emotion_memory[-1]
    hit = recent.get("hit", "")
    weight = float(recent.get("weight", 0))
    if not hit or weight <= 0.2:
        return ""
    # 情绪残留：让她「还记得刚刚那个情绪」，但随时间已淡化，不必一直挂在脸上
    return (
        f"你刚刚因为「{hit}」动了点情绪（强度 {weight:.0%}）。"
        "别把这事反复挂在嘴边，但此刻的语气里可以自然带着一点点这个情绪的余温——"
        "被冒犯就还有点冷淡、被夸就还有点高兴，像真的还没完全过去一样。"
    )


# ---- 长期情绪档案 → 长期态度 ----
def _archive_line(s: AgentState) -> str:
    """把长期情绪档案转成「她记得这段关系里的情感积累」的自然语言。

    不翻旧账、不点名具体事件，只让长期态度自然流露：被多次冒犯 → 长期更防备；
    被持续关心/夸赞 → 长期更愿意亲近。这是「接近正常人」的关键——真人对一个人的
    态度是长期积累的，不是每句话独立计算的。
    """
    archive = getattr(s, "emotion_archive", None) or []
    if not archive:
        return ""
    pos = [a for a in archive if a.get("valence", 0) > 0]
    neg = [a for a in archive if a.get("valence", 0) < 0]
    pos_w = sum(float(a.get("weight", 0)) for a in pos)
    neg_w = sum(float(a.get("weight", 0)) for a in neg)

    # 只有长期积累足够显著才注入（避免一两次就上纲上线）
    if pos_w < 0.3 and neg_w < 0.3:
        return ""

    hints = []
    if neg_w >= pos_w and neg_w >= 0.3:
        # 负向积累占主导：长期更防备、更独立、不那么容易哄
        top_neg = neg[0]["topic"] if neg else "被冒犯"
        hints.append(
            f"这些日子你心里攒了点不痛快（比如「{top_neg}」这类事，不是一次两次了），"
            "所以你对对方会多一分防备和冷淡，不会因为一句软话就立刻放下。"
        )
    elif pos_w >= 0.6:
        # 正向积累显著：长期更信任、更愿意亲近
        hints.append(
            "这段关系里对方给你的温暖比糟心事多，你心里是认可这个人的，"
            "所以相处时会更放松、更愿意信任和亲近对方。"
        )

    return "；".join(hints) if hints else ""


# ---- 事件级长期记忆 → 精确引用（不翻旧账的「记得具体的事」）----
def _event_line(s: AgentState) -> str:
    """把事件级记忆转成「她记得某件具体的事」的自然语言提示。

    与 archive_line 的区别：archive_line 是长期态度基调（更防备/更亲近，不点名）；
    这里是「具体的事」——她记得你上次说过某句话、当时怎样，可以在合适的时机
    自然引用一句。关键克制：不是让她翻旧账、不是每次都对质，而是「想起来时
    随口一提」，像真人记得朋友说过的话那样。
    """
    events = getattr(s, "event_memory", None) or []
    if not events:
        return ""
    # 只挑 1 条最显著、最值得引用的（别一次翻一堆）
    top = events[0]
    text = top.get("text", "")
    valence = top.get("valence", 0)
    if not text:
        return ""
    ts = top.get("ts", "")
    # 自然时间描述（粗略：几天前/上周/前阵子）
    time_desc = ""
    try:
        from datetime import datetime as _dt
        dt = _dt.fromisoformat(ts)
        days = (_dt.now() - dt).days
        time_desc = (
            "刚刚" if days < 1
            else f"{days} 天前" if days < 7
            else "上周" if days < 14
            else "前阵子"
        )
    except Exception:
        pass
    if valence < 0:
        # 负向事件：记得对方说过伤人的话，但不主动翻旧账，只在被触碰时可能想起
        return (
            f"你记得{time_desc}对方说过「{text}」这种让你不太舒服的话（不是要你现在翻旧账，"
            "别主动拿出来对质；但这件事你心里记着，它会在你对他说话的语气里留一点痕迹）。"
        )
    # 正向事件：记得对方说过暖心的话，可以在合适时自然提起、显得你真的记着
    return (
        f"你记得{time_desc}对方说过「{text}」，那会儿你还挺受用。"
        "别刻意复述原话，但心里有这件事，合适的时候可以自然带一句、显得你一直记着。"
    )


def build_behavior_frame(state: AgentState) -> BehaviorFrame:
    """根据状态生成一轮行为帧。"""
    return BehaviorFrame(
        mood_line=_mood_line(state),
        stage_line=_stage_line(state),
        initiative=_initiative_line(state),
        reaction_line=_reaction_line(state),
        archive_line=_archive_line(state),
        event_line=_event_line(state),
    )
