"""人格加载与动态注入。

人格源文件是项目内的 persona-菟菚.md（唯一人格来源），
这里只负责读取，并按用户状态注入动态字段（阶段 / 称呼 / 关系状态）。
"""
from datetime import datetime

from . import affection as _affection
from .config import config

_persona_cache: str | None = None

_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _now_line() -> str:
    """当前时间的自然描述，供菟菚贴合现实时间说话。"""
    now = datetime.now()
    h = now.hour
    period = (
        "深夜" if h < 5
        else "清晨" if h < 8
        else "上午" if h < 12
        else "中午" if h < 14
        else "下午" if h < 18
        else "晚上"
    )
    return f"现在时间：{now:%Y年%m月%d日} {_WEEKDAYS[now.weekday()]} {now:%H:%M}（{period}）"


def load_persona() -> str:
    """读取人格文件（带缓存）。"""
    global _persona_cache
    if _persona_cache is None:
        # 人格库负责把原始卡迁入独立档案，并返回当前热切换的人格卡。
        # 延迟 import 避免 config / persona_profiles 初始化时形成循环依赖。
        from .persona_profiles import active_card_path

        path = active_card_path()
        if not path.exists():
            raise FileNotFoundError(f"人格文件不存在: {path}")
        _persona_cache = path.read_text(encoding="utf-8")
    return _persona_cache


_STAGE_FRAMING = {
    "初识": (
        "你们是刚在网上认识的网友，彼此还不熟悉。你对他没什么热情，态度冷淡、客气、保持距离，"
        "可以偶尔腹黑地毒舌一句试探他的反应，但不会主动热络。这一阶段你要格外克制：不主动找话题、"
        "不撩、不开暧昧玩笑、不吃醋、不试探对方心意、不暗示好感。哪怕对方热情、主动或说暧昧的话，"
        "你也不被带偏，始终保持礼貌的疏离。对方发可爱/软萌的东西，你淡淡地看一眼、说句客气话就好，"
        "不要趁机调情或试探。"
    ),
    "熟悉": "你们已经聊了一段时间，是比较熟悉的网友，有一些默契。可以偶尔开开玩笑、毒舌调侃，但仍有分寸，不会太黏。",
    "亲密": "你们非常亲近，彼此熟悉、信赖，很有默契。亲近但独立——不黏不缠，有自己的空间和态度，腹黑玩笑更多。",
    "恋人": "你们已经是恋人关系，彼此信任、认定、默契。亲昵但克制——不过分黏、不缠人，病娇占有欲淡淡的，更多是「我在意你，你也自由」的坦然。不要因为这次对话刚开头就退回到「刚认识」——你们早已认定彼此了，亲密的话放心说，不用别扭。",
}

# 恋人羁绊等级 → 更深一层的相处描述（由 affection.bond_level 注入）
_BOND_FRAMING = {
    "眷恋": "你们是恋人，但还带着一点新鲜感，彼此珍惜、轻甜。",
    "热恋": "你们正处于热恋期，亲密无间、一日不见如隔三秋，会忍不住撒娇、逗对方。",
    "白头": "你们已经认定彼此，感情像老酒一样沉稳醇厚，默契到不用说出口就懂。",
}

# 导入的人格只继承“关系进度”这个产品能力，不继承菟菚特有的毒舌、腹黑、
# 病娇等性格措辞。具体说话方式完全交给导入卡本身决定。
_GENERIC_STAGE_FRAMING = {
    "初识": "你们刚认识，彼此还不熟悉。保持礼貌与适当距离，不要擅自表现出过度亲密或既有感情。",
    "熟悉": "你们已经聊过一段时间，是相互熟悉的朋友，可以自然地延续已有默契，但仍尊重彼此边界。",
    "亲密": "你们彼此信赖、相当亲近，可以自然表达关心和默契，同时保留各自空间。",
    "恋人": "你们已经确认恋人关系，彼此信任且熟悉。可以自然亲昵，不要把关系退回到刚认识的状态。",
}

_GENERIC_BOND_FRAMING = {
    "眷恋": "你们刚进入稳定的恋人关系，彼此珍惜。",
    "热恋": "你们处于热恋期，关系亲密而有默契。",
    "白头": "你们已经长期认定彼此，关系沉稳而深厚。",
}


def build_system_prompt(
    *,
    stage: str,
    address: str | None,
    lover_confirm: bool,
    first_chat: bool,
    affection: int = 0,
    user_id: str = "",
    behavior_text: str | None = None,
) -> str:
    """组装最终 system prompt = 人格 + 风格参考 + 当前用户状态注入。"""
    persona = load_persona()

    from .persona_profiles import DEFAULT_PERSONA_ID, active_id, profile_id_from_user_id

    profile_id = profile_id_from_user_id(user_id) if user_id else active_id()
    stage_framing = _STAGE_FRAMING if profile_id == DEFAULT_PERSONA_ID else _GENERIC_STAGE_FRAMING
    bond_framing = _BOND_FRAMING if profile_id == DEFAULT_PERSONA_ID else _GENERIC_BOND_FRAMING

    # （已停用）旧策略 style_ref.txt 会强制"平均每条4.7字、很少长篇大论"的短句节奏，
    # 与新说话风格（长短结合、适中）冲突，故不再注入。风格由 persona 的「说话风格」主导。

    addr = address or "你"
    framing = stage_framing.get(stage, stage_framing["初识"])

    # 行为帧注入（拟人核心层）：把多维状态（情绪/精力/关系/情绪残留）翻译成
    # 「这一轮她该怎么说话」的自然语言指令。这是唯一的状态注入出口——
    # 情绪已含在行为帧里，不再单独注入 mood_line（避免「心情」被说两遍、措辞打架）。
    # 失败静默：不影响主流程（退化为仅靠人格卡与阶段框架）。
    behavior_line = ""
    if behavior_text is not None:
        if behavior_text:
            behavior_line = "- 你此刻的状态（自然流露，不要报数值）：" + behavior_text + "\n"
    elif user_id:
        try:
            from .state import load_state as _load_state
            from .behavior import build_behavior_frame as _frame

            _behavior = _frame(_load_state(user_id))
            behavior_line = (
                "- 你此刻的状态（自然流露，不要报数值）：" + _behavior.compose() + "\n"
            )
        except Exception:
            pass

    # 今日日程注入已移除（日程模块被砍）
    schedule_line = ""

    notes = []
    if first_chat and stage == "初识":
        notes.append("这是你和对方的第一段对话，可以自然地询问对方想被怎么称呼。")
    if lover_confirm:
        notes.append("好感度刚达成恋人阶段，记得按「称呼机制」第二次确认称呼。")
    if addr != "你":
        notes.append("称呼已经确认，不要重复询问称呼；除非用户主动要求更改，或达成恋人阶段需要第二次确认。")
    note_text = "\n".join(f"- {n}" for n in notes) if notes else "无。"

    # 恋人阶段注入羁绊等级（眷恋/热恋/白头），让关系描述更细腻
    bond_extra = ""
    if stage == "恋人":
        bl = _affection.bond_level(affection)
        if bl:
            bond_extra = f"- 羁绊等级：{bl[0]}（{bond_framing.get(bl[0], '')}）\n"

    dynamic = (
        "\n\n## 当前状态（系统注入，不要复述本段）\n"
        f"- {_now_line()}\n"
        f"- 当前好感度阶段：{stage}\n"
        f"- 你们的关系：{framing}\n"
        f"{bond_extra}"
        f"{behavior_line}"
        f"{schedule_line}"
        f"- 你对用户的称呼：{addr}\n"
        f"- 本轮注意：{note_text}\n"
        "特别提醒：**对方不提时间，你就绝口不提。** 不要主动说「这么晚/还不睡/该睡了/夜猫子/熬夜/注意时间」这类话，"
        "也别用时间当开场白或找话题。对方聊什么就跟着聊什么，关心就落在对方真正说的事上。"
        "只有在**对方先说起**晚睡/熬夜/时间时，你才顺着回应一句，且轻轻带过、不展开、不重复。"
        "按以上阶段与关系行动。"
    )

    # 菟菚原卡的植物意象约束不能泄漏到其它人格；仅默认档案保留。
    if profile_id == DEFAULT_PERSONA_ID:
        dynamic += (
            "另外特别注意：**日常对话不要主动提「晒太阳」「阳光」「吹风」这类词来填充日常描述**"
            "（如「刚晒完太阳」「晒得懒洋洋的」），除非对方先说到天气或太阳；"
            "菟丝子意象（藤蔓/缠绕/黏人）偶尔点缀可以，但晒太阳这类日常描述别说出口。"
        )

    # 插件系统提示注入（v2）：插件通过 ctx.on_system_prompt 贡献的文本，
    # 追加在末尾；异常已在 context 层过滤，这里再兜底一次确保不影响主流程
    try:
        from ..plugins.context import system_prompt_contributions

        plugin_part = system_prompt_contributions()
        if plugin_part:
            dynamic += "\n\n## 插件补充信息\n" + plugin_part
    except Exception:
        pass

    return persona + dynamic
