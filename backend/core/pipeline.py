"""对话流水线：收文本 → 好感度 → 称呼提取 → 记忆检索 → 拼 prompt → LLM → 存档 → 回复。

Web 助手（assistant.py）与调试共用，保证各处行为一致。

本模块只保留「编排」职责：定序、决定分支、把结果交给 LLM。具体工作分派给：

- :mod:`effect_ledger`   —— 旁路副作用的失败登记与进程级统计；
- :mod:`turn_effects`    —— 本轮的确定性写副作用（关系入账/教学/惰性提炼/派活）；
- :mod:`turn_context_gather` —— 本轮的只读上下文检索（记忆/知识/活动/事件/搜索）；
- :mod:`turn_prompt`     —— 把「她这一轮该知道什么」组装成 system 消息。

这样每个新增能力都落在独立可测的函数里，而不是再往 ``_process_locked`` 里追加
第 N 个内联 ``try/except``。
"""
import asyncio
import re
from datetime import datetime, timedelta

from . import affection
from .effect_ledger import EffectLedger
from .llm import chat, chat_stream, extract_address
from .log import logger
from .memory import recall, recall_facts, short_term_messages
from .persona import build_system_prompt
from .turn_context_gather import gather_all
from .turn_effects import run_turn_effects
from .turn_prompt import (
    build_drawn_note,
    build_think_block,
    build_topic_block,
    inject_activity_contexts,
    inject_anniversary_eve,
    inject_bad_address,
    inject_continuation,
    inject_conversation_context,
    inject_due_promises,
    inject_early_confession,
    inject_knowledge,
    inject_memory_block,
    inject_memory_correction,
    inject_night_boundary,
    inject_pending_unlock,
    inject_preference_constraints,
    inject_presence,
    inject_search_context,
    inject_shared_terms,
    inject_skills,
    inject_stage_transition,
    inject_today_dates,
    inject_understanding,
    inject_wake_prompt,
)
from .userdb import db

# 会话空闲判定：离上一条消息超过该分钟数，视为上一场聊完，补提尾部事实
_IDLE_SESSION_MINUTES = 30
_IDLE_MIN_NEW = 4

# 重复回复检测：与最近几条菟菚回复高度相似时，重写一次（避免复读机）。
# 流式模式下通过 stream_cb 推送该特殊标记，assistant.py 转发为 {"reset": true}，
# 前端收到后清空当前气泡重新累积。
_RESET_MARK = "\x00RESET\x00"
_STREAM_CHUNK = 6
# 生图开始标记：流式模式下在发起生图前推送给前端，用于显示"正在画图"占位
_IMAGE_START_MARK = "\x00IMAGESTART\x00"
# 工具循环整段返回后切片推送的粒度（模拟打字机，与前端逐字累积一致）
_DUP_MIN_LEN = 8      # 短于该长度的回复不判重复（避免"嗯""好"误伤）
_DUP_RATIO = 0.75     # 字符级相似度阈值
_DUP_RECENT_N = 3     # 与最近几条菟菚回复比对
# 长期记忆表每用户上限：超过后删除最旧记录（本轮起对长期记忆做容量约束，
# 避免"用户说/菟菚说"双写导致表与向量库无限增长）
_LM_MAX_ROWS = 800

# 记忆向量化等后台任务：强引用句柄集合（可查可弃），避免任务被 GC 静默丢弃
_memory_tasks: set = set()  # asyncio.Task
# 单次记忆向量化/清理的超时（秒）：超时只停止等待，to_thread 线程无法强杀，
# 但绝不阻塞回复返回
_MEMORY_INDEX_TIMEOUT = 90


# D5 强模型路由：写作/代码/长文类请求走强模型（LLM_MODEL_STRONG 已配置时）
_STRONG_MODEL_RE = re.compile(
    r"帮我写|写一篇|写个|写一段|写首|写封|作文|论文|报告|方案|脚本|代码|翻译|总结|分析|"
    r"小说|诗歌|诗词|debug|code|python|java|javascript|sql|excel|ppt|markdown",
    re.IGNORECASE,
)
_STRONG_MODEL_MIN_LEN = 80  # 超过该长度的消息大概率是复杂任务


def _needs_strong_model(text: str) -> bool:
    """是否走强模型：长消息或命中写作/代码/分析类关键词（误伤代价仅是多花点 tokens）。"""
    t = (text or "").strip()
    return len(t) >= _STRONG_MODEL_MIN_LEN or bool(_STRONG_MODEL_RE.search(t))


def _spawn_memory_task(coro) -> None:
    """创建后台记忆任务并持有强引用。"""
    import asyncio

    task = asyncio.ensure_future(coro)
    _memory_tasks.add(task)
    task.add_done_callback(_memory_tasks.discard)


async def _veto_life_templates(user_id: str) -> None:
    """后台：用户意见取消当日生活模板（L06 拍板 #10，同步 kv 无网络调用）。"""
    import asyncio as _aio

    from .life_templates import mark_vetoed

    await _aio.to_thread(mark_vetoed, user_id)


async def _perceive_and_settle(user_id: str, text: str, *, mock: bool = False) -> None:
    """后台：LLM 语义感知 + 好感度/情绪演化 + 降级时的关键词兜底。

    性能说明：perceive 是一次真实的 LLM 调用（本地感知小模型/独立端点），在部分
    服务商上首 token 可能高达 10s+。它只影响「这句话让菟菚的好感/情绪涨落多少」，
    与回复正文无关，因此整体丢到后台执行，让主流程可以立刻开始组 prompt 去生成
    回复 —— 消除「每条消息首字前死等一次感知 LLM」的瓶颈。
    """
    try:
        from .perception import perceive
        from .persona_profiles import persona_name_for_user_id
        from .state import apply_impulse

        persona_name = persona_name_for_user_id(user_id)

        perc = await perceive(text, mock=mock, persona_name=persona_name)

        # 语义成功 = 拿到结果且未降级
        semantic_ok = perc is not None and not perc.get("degraded", False)

        # 语义感知驱动状态演化（该条消息的情绪/好感影响）
        if perc:
            apply_impulse(
                user_id,
                emotion_delta=perc.get("emotion_delta", 0),
                affection_delta=perc.get("affection_delta", 0),
                affection_reason="语义感知",
                emotional_hit=perc.get("emotional_hit") or None,
                emotional_weight=perc.get("hit_weight", 0.0),
                text=text,
            )
        # else perc 恒非 None（失败内部已降级返回 dict）

        # 降级/失败时才启用关键词兜底（避免「语义 delta + 关键词」双重计分）
        if not semantic_ok:
            affection.apply_abuse_penalty(user_id, text)
            if affection.check_care(text):
                affection.try_daily_bonus(user_id, "care", affection.CARE_BONUS, f"关心{persona_name}")
            if affection.check_apology(text):
                affection.try_daily_bonus(user_id, "apology", affection.APOLOGY_BONUS, "真诚道歉")
            if affection.check_sharing(text):
                affection.try_daily_bonus(user_id, "sharing", affection.SHARING_BONUS, "分享心事/秘密")
            if affection.check_compliment(text):
                affection.try_daily_bonus(user_id, "compliment", affection.COMPLIMENT_BONUS, f"夸{persona_name}")
    except Exception:
        logger.exception("[pipeline] 后台拟人状态感知失败（不影响回复）")


async def _vectorize_memory_async(
    user_id: str, text: str, reply: str,
    lm1_id: int, lm2_id: int, removed_ids: list[int],
) -> None:
    """后台：给两条新长期记忆建 Chroma 向量索引，并清理超限旧向量。

    副作用说明（配合超时/失败日志，保证"哪些已完成、哪些未完成"可查）：
    - SQLite（assistant 消息 + 长期记忆）已在此函数调用前同步落库；
    - 本函数只负责 Chroma 侧；超时/失败不会影响已返回的回复与已落库记忆，
      语义检索会退化为 TF-IDF（SQLite 仍在）。
    """
    import asyncio as _asyncio

    try:
        from .vector_store import index as vec_index
        from .persona_profiles import persona_name_for_user_id

        persona_name = persona_name_for_user_id(user_id)

        # 两条并行索引（不串行等待）；embedding 走线程池，不阻塞事件循环
        await _asyncio.wait_for(
            _asyncio.gather(
                _asyncio.to_thread(vec_index, user_id, lm1_id, f"用户说：{text}", "lm"),
                _asyncio.to_thread(vec_index, user_id, lm2_id, f"{persona_name}说：{reply}", "lm"),
            ),
            timeout=_MEMORY_INDEX_TIMEOUT,
        )
    except _asyncio.TimeoutError:
        logger.warning(
            "[pipeline] 记忆向量化超时（{}s）：SQLite 已落库，但 {} 的两条新记忆"
            "未完成 Chroma 索引（后台线程仍在运行，无法强杀）",
            _MEMORY_INDEX_TIMEOUT, user_id,
        )
    except Exception:
        logger.exception(
            "[pipeline] 记忆向量化失败（已落库记忆不受影响，语义检索退化为 TF-IDF）：{}",
            user_id,
        )

    if removed_ids:
        try:
            from .vector_store import delete as vec_delete

            await _asyncio.wait_for(
                _asyncio.gather(
                    *[
                        _asyncio.to_thread(vec_delete, user_id, "lm", rid)
                        for rid in removed_ids
                    ]
                ),
                timeout=_MEMORY_INDEX_TIMEOUT,
            )
        except _asyncio.TimeoutError:
            logger.warning(
                "[pipeline] 旧向量清理超时（{}s）：{} 的 {} 条超限记录未完成删除",
                _MEMORY_INDEX_TIMEOUT, user_id, len(removed_ids),
            )
        except Exception:
            logger.exception("[pipeline] 旧向量清理失败：{}", user_id)


def _too_similar(a: str, b: str, ratio: float = _DUP_RATIO) -> bool:
    """判断两条文本是否高度相似（去空白后字符级比较）。

    短句（< _DUP_MIN_LEN）一律不判：即使完全相同（"嗯"vs"嗯"）也不算复读，
    避免把自然的简短回应误判成重复。
    """
    import difflib

    a2 = re.sub(r"\s+", "", a or "")
    b2 = re.sub(r"\s+", "", b or "")
    if not a2 or not b2:
        return False
    if len(a2) < _DUP_MIN_LEN or len(b2) < _DUP_MIN_LEN:
        return False  # 太短不判，避免"嗯""好呀"之类误伤
    if a2 == b2:
        return True
    return difflib.SequenceMatcher(None, a2, b2).ratio() >= ratio


_INTERNAL_EXPLANATION_RE = re.compile(
    r"(?:解释|介绍|分析|展示|举例|示例|代码|正则|文档).{0,16}"
    r"(?:系统提示词|系统指令|开发者指令|tool.?call|reasoning|think标签)|"
    r"(?:系统提示词|系统指令|开发者指令|tool.?call|reasoning|think标签).{0,16}"
    r"(?:是什么|怎么|格式|结构|写法|代码|规则)",
    re.IGNORECASE,
)


def _requested_internal_explanation(text: str) -> bool:
    """用户是否明确要求讨论内部协议术语；只用于避免普通技术解释误伤。"""
    return bool(_INTERNAL_EXPLANATION_RE.search(text or ""))


def _postprocess_reply(user_text: str, raw: str) -> str:
    reply = strip_actions(_extract_reply(raw))
    reply = trim_farewell(user_text, reply)
    return reply if reply.strip() else "嗯……我想想怎么回你。"


def _apply_reply_plugins(reply: str, *, ephemeral: bool) -> str:
    if ephemeral:
        return reply
    try:
        from ..plugins.context import apply_reply

        candidate = apply_reply(reply)
        return candidate if candidate.strip() else reply
    except Exception:
        return reply


async def _emit_final_reply(stream_cb, reply: str, *, mock: bool) -> None:
    """以稳定的小块形状发送已定稿正文，供非增量出口复用。"""
    if stream_cb is None or mock or not reply:
        return
    try:
        for i in range(0, len(reply), _STREAM_CHUNK):
            await stream_cb(reply[i:i + _STREAM_CHUNK])
    except Exception:
        logger.debug("[pipeline] 流式回调失败（客户端可能已断开），本轮正文不受影响")


def _long_gap(ts: str | None) -> bool:
    """判断某时间戳是否距现在超过空闲阈值。"""
    if not ts:
        return False
    try:
        t = datetime.fromisoformat(ts)
    except ValueError:
        return False
    return (datetime.now() - t).total_seconds() >= _IDLE_SESSION_MINUTES * 60


async def _extract_topic_lazy(user_id: str) -> None:
    """后台惰性提炼话题记忆（失败静默，不阻塞对话）。"""
    try:
        from .topic_memory import extract_topic

        await extract_topic(user_id)
    except Exception:
        logger.debug("[pipeline] 后台话题记忆提炼失败（不影响本轮回复）")


async def _extract_triples_lazy(user_id: str) -> None:
    """后台惰性提取结构化事实五元组（失败静默）。"""
    try:
        from .triple_memory import extract_triples, save_triples
        from .userdb import db as _db
        from .persona_profiles import persona_name_for_user_id

        persona_name = persona_name_for_user_id(user_id)

        # 提取最近 30 条消息（去重交给 save_triples）
        rows = _db.recent_messages(user_id, 30)
        text = "\n".join(
            f"{'用户' if r['role'] == 'user' else persona_name}：{r['content']}"
            for r in rows
        )
        if len(text) < 10:
            return
        triples = await extract_triples(text, persona_name=persona_name)
        if triples:
            save_triples(user_id, triples, source_msg=text[:200])
    except Exception:
        logger.debug("[pipeline] 后台三元组提取失败（不影响本轮回复）")


_ADDRESS_ASK_WORDS = ("称呼你", "怎么称", "怎么叫", "叫你什么", "想让你怎么称呼", "叫法")


def _asked_address(last_assistant: str | None) -> bool:
    """判断菟菚上一句是否在问称呼（用于捕捉用户直接报名字的情况）。"""
    return bool(last_assistant) and any(w in last_assistant for w in _ADDRESS_ASK_WORDS)


_SEARCH_KEYS = ("搜索", "搜一下", "查一下", "帮我查", "查查", "新闻", "天气", "多少钱", "价格", "汇率", "现在几点", "最新", "今天有", "今天有没有",
                 # 天气类：与下方天气分支（MOOD_CITY 真实天气查询）的关键词保持一致，
                 # 否则"冷/热/下雨/温度/气温/多少度"这些常见问法会因 _needs_search 总开关未命中
                 # 而永远不触发真实天气查询（历史死代码 bug）
                 "冷", "热", "下雨", "温度", "气温", "天气预报", "多少度")


def _needs_search(text: str) -> bool:
    """是否命中需要联网搜索的内容。"""
    from .intent import requires_search

    return requires_search(text) or any(k in text for k in _SEARCH_KEYS)


# 工具循环触发词：命中表示消息有明确的工具诉求，应走工具循环而非纯流式
# （匹配前统一 lower，英文词如 Codex/DSH 大小写不敏感）
_TOOL_LOOP_KEYS = (
    "待办", "记一下", "记住", "记忆", "搜索", "查一下", "帮我查", "查查",
    "写文件", "读文件", "打开", "执行", "运行", "删除", "创建", "整理",
    "保存", "画", "生成", "截图", "进程", "窗口", "文件", "命令", "代码",
    "汇率", "转换", "换算", "搜索一下",
    # 外部 Agent 桥 / 通用工具诉求（曾因漏词导致模型只能嘴上说"我去调"却无法真调）
    "codex", "dsh", "harness", "桥接", "插件", "工具", "调动", "调用",
    "脚本", "接口", "能力", "确认一下", "测试一下", "验证一下",
)


def _needs_tool_loop(text: str, intent: dict | None) -> bool:
    """判断这条消息是否需要走工具调用循环。"""
    if intent and (intent.get("need_search") or intent.get("need_draw") or intent.get("need_recall")):
        return True
    t = text.lower()
    return any(k in t for k in _TOOL_LOOP_KEYS)


def _skills_need_tools(skills: list | None) -> bool:
    """命中的技能正文点名了某个已注册工具 → 该轮必须给模型工具通道。

    否则「用 agent_fanout 并行派发」这类技能指令会落在没有工具的纯流式轮次里，
    模型只能照着技能的方法论嘴上说、却调不动工具（子代理工具零调用的根因）。
    """
    if not skills:
        return False
    try:
        from ..skills import skills_reference_tools
        from ..tools.base import ToolRegistry

        return bool(skills_reference_tools(skills, [t.name for t in ToolRegistry.list()]))
    except Exception:
        logger.exception("[pipeline] 技能工具识别失败（按无工具处理）")
        return False


def _tool_loop_enabled(
    text: str,
    intent: dict | None,
    skills: list | None,
    *,
    ephemeral: bool,
    mock: bool,
) -> bool:
    """这一轮是否走工具调用循环：关键词命中，或命中的技能点名了工具。"""
    if ephemeral or mock:
        return False
    return _needs_tool_loop(text, intent) or _skills_need_tools(skills)


# 常见城市名 → wttr.in 查询名（中文城市直接用中文名查询即可，wttr.in 支持中文；
# 这里主要处理英文/拼音别名和易歧义名，其余城市名原样透传）
_CITY_ALIASES: dict[str, str] = {
    "北京": "Beijing", "上海": "Shanghai", "广州": "Guangzhou", "深圳": "Shenzhen",
    "武汉": "Wuhan", "襄阳": "Xiangyang", "杭州": "Hangzhou", "成都": "Chengdu",
    "重庆": "Chongqing", "西安": "Xi'an", "南京": "Nanjing", "天津": "Tianjin",
    "苏州": "Suzhou", "长沙": "Changsha", "郑州": "Zhengzhou", "青岛": "Qingdao",
    "大连": "Dalian", "厦门": "Xiamen", "昆明": "Kunming", "贵阳": "Guiyang",
    "兰州": "Lanzhou", "哈尔滨": "Harbin", "沈阳": "Shenyang", "合肥": "Hefei",
    "福州": "Fuzhou", "南昌": "Nanchang", "济南": "Jinan", "石家庄": "Shijiazhuang",
    "太原": "Taiyuan", "呼和浩特": "Hohhot", "南宁": "Nanning", "海口": "Haikou",
    "银川": "Yinchuan", "西宁": "Xining", "乌鲁木齐": "Urumqi", "拉萨": "Lhasa",
    "香港": "Hong Kong", "澳门": "Macau", "台北": "Taipei", "高雄": "Kaohsiung",
}


def _extract_city(text: str) -> str | None:
    """从用户消息里提取城市名（用于天气查询）。

    规则：优先用已知城市别名表匹配（覆盖国内主要城市，可靠且不会误抓动词），
    命中直接返回；未命中时再用「城市名紧贴天气词」的正则兜底（覆盖港澳台/国外等
    不在表里的城市）。返回 None 表示没提到城市，调用方应回落到 mood_city。
    """
    import re as _re

    # 1) 已知城市别名表优先（含中文名，避免「查一下上海」把动词一起吸进去）
    for name in _CITY_ALIASES:
        if name in text:
            return name
    # 2) 正则兜底：中文城市 2~6 字 + 天气词（覆盖表外城市）
    m = _re.search(r"([\u4e00-\u9fa5]{2,6}?)的?(?:今天|明天|现在)?(?:天气|气温|温度|多少度|下雨|下雪|晴|阴)", text)
    if m:
        city = m.group(1).strip()
        # 剥离常见动词/虚词前缀，避免「我想知道巴黎天气」误抓成「我想知道巴黎」
        for prefix in ("我想知道", "我想查", "请问", "帮我查", "查一下", "查", "一下", "帮我", "请", "今天", "明天", "现在", "这", "那"):
            if city.startswith(prefix):
                city = city[len(prefix):]
        city = city.strip()
        if city and len(city) >= 2:
            return city
    return None


# L03 关系气质 → 行为帧轻倾向（不显示分数/等级，表达带宽微调）
_STYLE_HINT_TEXT = {
    "companion": "长期相处让你们的氛围偏向安稳的陪伴，她可以更放松地闲聊",
    "playful": "互相玩梗的默契已经形成，她可以偶尔先开一个无伤大雅的玩笑",
    "confidant": "你愿意跟她聊心事，她可以更自然地接住认真话题",
    "growth": "一起做事的经历不少，她可以更主动地拉你聊共同目标或正在进行的事",
    "romantic": "浪漫氛围已经稳定，她可以自然地表达在意，不刻意掩饰",
}


def _mcp_tool_filter(user_text: str, skill_texts: list[str]):
    """MCP 外部工具按需注入：未命中触发条件时隐藏（本地工具不受影响）。"""
    try:
        from ..tools.mcp_server import mcp_tool_filter

        return mcp_tool_filter(user_text, skill_texts)
    except Exception:
        logger.exception("[pipeline] MCP 工具可见性判定失败，按全部可见处理")
        return None


def _evolution_line(user_id: str) -> str:
    """P3-05 表达层演化 + L05 领域调制 + L03 气质倾向 → 行为帧 evolution_line。

    三路都只给「轻倾向」的自然语言，不写分数；任一路失败即跳过，不阻塞回复。
    """
    fragments: list[str] = []
    try:
        from .persona_evolution import behavior_hints

        hints = behavior_hints(user_id)
        verbosity = float(hints.get("verbosity_preference", 0.5))
        humor = float(hints.get("humor_usage_rate", 0.5))
        if verbosity >= 0.7:
            fragments.append("你最近更愿意多说一点，可以把想法铺开讲")
        elif verbosity <= 0.3:
            fragments.append("你最近说话偏简短，别铺陈，一两句说到就好")
        if humor >= 0.7:
            fragments.append("你最近玩笑开得比平时多一点")
        elif humor <= 0.3:
            fragments.append("你最近收着玩笑，少玩梗")
    except Exception:
        logger.debug("[pipeline] 表达层演化片段读取失败（跳过该片段）")
    # L05 领域调制：某域偏低 → 对应强度收敛（不碰阶段边界与隐私开关）
    try:
        from .domain_trust import behavior_hint as _domain_hint

        domain = _domain_hint(user_id)
        if domain.get("probing", 0.0) < 0:
            fragments.append("最近你在情绪话题上更收敛，不急着往深里问")
        if domain.get("humor", 0.0) < 0:
            fragments.append("玩笑的分寸上你收着点")
        if domain.get("initiative", 0.0) < 0:
            fragments.append("最近别太主动张罗事情，顺着对方来")
    except Exception:
        logger.debug("[pipeline] 领域调制片段读取失败（跳过该片段）")
    # L03 气质倾向：单轴 ±0.1 的轻修正（数值→语气词，不暴露轴名）
    try:
        from .relationship_style import behavior_hint as _style_hint, derive_style

        style = derive_style(user_id)
        axes = _style_hint(list(style.get("style_ids") or []))
        if axes.get("humor", 0.0) > 0:
            fragments.append("你们之间玩笑的默契已经在了，可以自然玩一下")
        if axes.get("probing", 0.0) > 0:
            fragments.append("你更敢接住对方的认真话题了")
    except Exception:
        logger.debug("[pipeline] 关系气质片段读取失败（跳过该片段）")
    return "；".join(fragments) + "。" if fragments else ""


def _style_line(user_id: str) -> str:
    """derive_style → 行为帧 style_line；关闭/未形成时为空串。"""
    try:
        from .relationship_style import derive_style

        result = derive_style(user_id)
        if result.get("forming") or not result.get("style_ids"):
            return ""
        fragments: list[str] = []
        for style in result["style_ids"][:2]:
            text = _STYLE_HINT_TEXT.get(style)
            if text:
                fragments.append(text)
        return "；".join(fragments) + "。" if fragments else ""
    except Exception:
        return ""


# 天气查询专用：用城市查真实天气（wttr.in，含温度/风速），
# 避免"问天气不带城市"时搜索返回全国杂乱结果、LLM 只能瞎猜。
def _fetch_weather(city: str) -> str | None:
    """返回如「襄阳：晴 30°C 微风」的天气描述；失败返回 None。"""
    try:
        import urllib.parse
        import urllib.request

        query = _CITY_ALIASES.get(city, city)
        url = f"https://wttr.in/{urllib.parse.quote(query)}?format=4&lang=zh"
        # 与 web_fetch 同一 SSRF 防线：出网前统一过 check_url（公网 http(s) 校验）
        from ..tools.safety import check_url

        ok_url, _ = check_url(url)
        if not ok_url:
            return None
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68"})
        # 说明：这里不像 web_fetch 那样做逐跳重定向复检——wttr.in 为固定公共
        # 域名、city 参数已 urlencode、返回仅用于展示天气；未知城市返回的是
        # 文本 "Unknown location" 而非重定向，无内网可达面，风险可接受。
        with urllib.request.urlopen(req, timeout=8) as resp:
            line = resp.read().decode("utf-8", "ignore")
        line = line.strip()
        if not line or line.startswith("Unknown"):
            return None
        return line
    except Exception:
        return None

# 称呼意图检测：判断「这句是否在设置称呼」（正则精确匹配，避免无关句误触）
# 注意：只用完整意图短语，不用裸「叫我」「你叫我」「喊我」——它们会误配「叫我去吃饭」等无关句。
ADDRESS_RE = re.compile(
    r"(?:你可以叫我|可以叫我|以后叫我|以后就叫我|以后都叫我|叫我一声|称呼我)[:：]?\s*"
    r"[「『\"'“”《〈]*([^吧呀嘛啊呢哦啦呗哈咯～~。，,、!！?？…\s]{1,8})"
)
# 称呼候选词黑名单：含这些词的不是真正要设置的称呼
_ADDRESS_BLACKLIST = ("帮", "给", "去", "来", "拿", "做", "让", "是", "有", "要", "走", "放", "买", "吃", "喝")
_TRAIL_CHARS = "吧呀嘛啊呢哦啦呗哈咯～~。，,、!！?？…"


def clean_address(name: str) -> str:
    """清理称呼：去掉引号包裹与尾部语气词，如「以实玛利吧」→「以实玛利」。"""
    name = name.strip(" \t「」『』\"'“”《〈》〉")
    return name.rstrip(_TRAIL_CHARS)


def _extract_reply(text: str) -> str:
    """从「先思考后发言」的输出里提取回复正文；无标记则裁剪掉思考段。

    LLM 输出可能用不同的括号/标注来分隔思考与实际发言：
      【思考】…【回复】…      〔思考〕…〔回复〕…      思考:…回复:…
    规则：
    - 显式括号回复标记（【回复】/〔回复〕/[回复]）：取**最后一个**标记后的内容
      （兼容多段【回复】输出，前面的回复块不重复保留）
    - 裸「回复：」只认**行首**（避免命中正文里的「回复：」字样）
    - 找不到回复段则把「思考」段裁掉，只留最终要发的部分；
      裸「思考：」同样只认行首，正文中间的「思考：」不处理。
    """
    # ① 显式括号回复标记：取最后一个标记后的全部内容
    for pat in (r"【回复】", r"〔回复〕", r"\[回复\]"):
        ends = [m.end() for m in re.finditer(pat, text)]
        if ends:
            return text[ends[-1]:].strip()
    # ② 裸「回复：/回复:」锚定行首：取最后一个匹配行之后的内容（正文可能跨行）
    lines = text.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        m = re.match(r"^\s*(?:回复|reply)[：:]\s*(.*)$", lines[i], re.I)
        if m:
            body = m.group(1).strip()
            rest = "\n".join(lines[i + 1:]).strip()
            return (body + "\n" + rest).strip() if rest else body
    # ③ 思考段：括号标记任意位置；裸「思考：」仅行首，取其后正文
    thought_pat = re.compile(
        r"(?:【思考】|〔思考〕|^[ \t]*思考[：:])\s*[^\n]*(?:\n(?P<body>[\s\S]*))?",
        re.M,
    )
    m = thought_pat.search(text)
    if m:
        body = (m.group("body") or "").strip()
        if body:
            return body
        # 思考段后无正文（整句都是思考）→ 保守返回空，由调用方兜底
        return ""
    # 无思考标注 → 整段当回复
    return text.strip()


# 只匹配「整行都是括号旁白」的行（行内仅含圆括号/空白），正文内的合法括号保留。
# 避免误删正文中正常出现的（），例如「我昨天去了（公园）」不应变成「我昨天去了」。
_PARA_LINE_RE = re.compile(r"^\s*(?:（[^）]*）|\([^)]*\))\s*$", re.M)


def strip_actions(text: str) -> str:
    """移除模型输出里的括号旁白（动作/语气/屏幕提示），只留台词。

    覆盖全角（）/半角()/六角〔〕；全角方头【】作为残留思考标记也一并清理。
    圆括号旁白只删「独立成行」的（整行仅括号），正文中的合法（）保留。
    裸「思考：/回复：」只认行首，正文中间的措辞不受影响。
    """
    # ① 有显式回复标记（括号或行首「回复：」）：丢弃思考，保留最后一段回复
    for pat in (r"【回复】", r"〔回复〕", r"\[回复\]"):
        ends = [m.end() for m in re.finditer(pat, text)]
        if ends:
            text = text[ends[-1]:]
            break
    else:
        lines = text.splitlines()
        for i in range(len(lines) - 1, -1, -1):
            m = re.match(r"^\s*(?:回复|reply)[：:]\s*(.*)$", lines[i], re.I)
            if m:
                body = m.group(1).strip()
                rest = "\n".join(lines[i + 1:]).strip()
                text = body + ("\n" + rest if rest else "")
                break
    # ② 剥思考段：括号标记任意位置；裸「思考：」仅行首
    text = re.sub(r"(?m)^[ \t]*(?:【思考】|〔思考〕|思考[：:])[^\n]*\n?", "", text)
    text = re.sub(r"【[^】]*】", "", text)     # 全角方头（思考/标注残留）
    text = re.sub(r"〔[^〕]*〕", "", text)     # 六角旁白/思考残留
    # 圆括号旁白：只删独立成行的，正文内的合法括号保留
    text = _PARA_LINE_RE.sub("", text)
    return text.strip()


# 告别场景：用户说了这些，菟菚只需一句简短道别，不复读、不刷屏
# 注意：不用裸「睡了」（会误伤「睡不着/睡了吗/还没睡」），只用明确的道别短语
_FAREWELL_RE = re.compile(r"(晚安|再见|拜拜|明天见|睡啦|先睡了|我睡了|我去睡了|睡了睡了|睡觉了|该睡了|告辞|886)")
_FAREWELL_REPLY = {
    "晚安": "晚安🌙",
    "再见": "再见呀",
    "拜拜": "拜拜",
    "明天见": "明天见",
}


def trim_farewell(user_text: str, reply: str) -> str:
    """告别语境兜底：若用户消息是道别词，把回复精简成一句道别，避免刷屏/复读。"""
    m = _FAREWELL_RE.search(user_text)
    if not m:
        return reply
    word = m.group(1)
    # 若回复已经是一句简明道别（不长、无追问），保留
    lines = [l for l in reply.splitlines() if l.strip() and not l.startswith("【")]
    compact = " ".join(lines).strip()
    # 道别答复：来自词表，或回复很短含道别词
    if compact in _FAREWELL_REPLY.values():
        return compact
    if compact and len(compact) <= 8 and any(k in compact for k in ("晚安", "再见", "拜拜", "明天见", "睡")):
        # 已经是简短道别，保留原样
        return compact
    # 否则收敛成一句道别（避免复读对方的词 + 多条刷屏）
    return _FAREWELL_REPLY.get(word, f"{word}")


async def _extract_profile(user_id: str) -> None:
    """画像提炼（共用游标，一次取消息、LLM 调用、一次推进游标）。"""
    from .features import flag
    from .profile import extract_profile

    if not flag("profile_enabled"):
        return
    last_id = db.get_last_profile_msg_id(user_id)
    rows = db.messages_after(user_id, last_id, 60)
    if len(rows) < 8:
        return
    done = rows[-1]["id"]
    await extract_profile(user_id, rows=rows, done=done)


# 同用户串行锁：pipeline 会写好感度/记忆/消息表，若两条消息并发处理会竞态
# （好感度计数错乱、消息顺序颠倒）。按 user_id 加锁，天然串行。
_user_locks: dict[str, "asyncio.Lock"] = {}
# 锁上次使用时间：空闲超时后清理，避免长跑积累无界内存
_LOCK_IDLE_TIMEOUT = 3600.0
_lock_last_used: dict[str, float] = {}


def _user_lock(user_id: str) -> "asyncio.Lock":
    import time

    now = time.monotonic()
    # 顺带清理长期不用的锁（每次取锁时惰性清扫，避免额外定时任务）
    if len(_user_locks) > 64:
        for uid in [u for u, t in _lock_last_used.items() if now - t > _LOCK_IDLE_TIMEOUT]:
            _user_locks.pop(uid, None)
            _lock_last_used.pop(uid, None)
    lock = _user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[user_id] = lock
    _lock_last_used[user_id] = now
    return lock


async def process(user_id: str, text: str, *, mock: bool = False, merged_msg: bool = False, ephemeral: bool = False, stream_cb=None, image_cb=None, progress_cb=None, explain_cb=None, draft_cb=None) -> str:
    """处理一条用户消息，返回菟菚的回复。

    merged_msg=True 表示 text 是用户连续发送的多条消息合并成的一段话，
    提示模型把这段当成对方一次性的完整表达，用一句精简的话回应整体，不逐条复读。

    stream_cb：可选的异步回调 async (chunk: str) -> None，收到 LLM 流式片段时调用
    （打字机效果）。传入时优先走流式生成；工具循环等复杂场景自动回退整句。

    image_cb：可选的异步回调 async (local_path: str) -> None，生图成功时把本地
    图片路径交给调用方（Web 端用它拼 URL 渲染）。

    progress_cb：可选的异步回调 async (event: dict) -> None，工具循环阶段进展
    （thinking/tool/tool_done）实时推送，供前端在工具执行期间展示进度而非空窗。

    ephemeral=True 表示这一轮只在当前界面暂时展示，不写会话、记忆、画像、关系
    状态或主动回访素材；仍可读取既有背景，以正常完成陪伴回复。

    explain_cb：可选的异步回调 async (snapshot: dict) -> None，返回这一轮实际
    注入的状态、行为帧、记忆与工具快照；不包含 system prompt 或模型思考链。
    """
    from .privacy import is_ephemeral_request

    # 在任何可扩展钩子之前识别临时语义，避免插件把本轮内容另行持久化。
    ephemeral = is_ephemeral_request(text, explicit=ephemeral)
    async with _user_lock(user_id):
        # 插件消息钩子（v2）：用户消息入口改写（异常已在 context 层过滤）
        if not ephemeral:
            try:
                from ..plugins.context import apply_user_message

                text = apply_user_message(text)
            except Exception:
                logger.debug("[pipeline] 插件用户消息钩子失败（按原文继续）")
        return await _process_locked(
            user_id, text, mock=mock, merged_msg=merged_msg, ephemeral=ephemeral,
            stream_cb=stream_cb, image_cb=image_cb, progress_cb=progress_cb,
            explain_cb=explain_cb,
            draft_cb=draft_cb,
        )


async def _process_locked(user_id: str, text: str, *, mock: bool = False, merged_msg: bool = False, ephemeral: bool = False, stream_cb=None, image_cb=None, progress_cb=None, explain_cb=None, draft_cb=None) -> str:
    from .persona_profiles import persona_name_for_user_id
    from .privacy import ephemeral_prompt, is_ephemeral_request

    ephemeral = is_ephemeral_request(text, explicit=ephemeral)

    persona_name = persona_name_for_user_id(user_id)
    user = db.get_user(user_id) if ephemeral else db.ensure_user(user_id)
    if user is None:
        # 尚未建档的临时会话也能回复，初始关系值只存在于本轮内存。
        user = {
            "first_chat_done": 0,
            "nickname_pref": None,
            "lover_confirm": 0,
            "affection": 0,
        }
    first_chat = not user["first_chat_done"]
    # 取存档前的最后一条消息时间戳：跨场判定必须基于「本轮之前」的消息，
    # 否则 add_message 后 last_message_ts 恒为 now，_long_gap 恒 False
    prev_ts = db.last_message_ts(user_id)

    # 休息状态下保持输入可用，但前两条消息不调用模型；十分钟内第三条消息
    # 才把她吵醒。沉默轮只保存用户消息，不制造空助手消息或其它关系副作用。
    sleep_gate_state = "active"
    try:
        from .sleep_gate import before_user_message

        sleep_gate_state = before_user_message(
            user_id,
            now=datetime.now().astimezone(),
            persist=not ephemeral,
        )
    except Exception:
        logger.exception("[pipeline] 休息唤醒门控失败（回退为正常回复）")
    if sleep_gate_state == "silent":
        if not ephemeral:
            db.add_message(user_id, "user", text)
        return ""

    # 1) 好感度即时规则（含跨天回滚）
    if not ephemeral:
        await affection.on_message(user_id, text)
        # 好感度可能已变：刷新快照，后续 system prompt / 阶段判定用最新值
        user = db.get_user(user_id)

    # 本轮副作用台账：所有「不影响回复的旁路动作」统一登记，失败不再静默。
    ledger = EffectLedger("turn")

    # 1.0.0) C4 解锁时刻检测 + 1.0.1) 拟人核心层：语义感知丢后台执行，消除
    # 「每条消息首字前死等感知 LLM」的串行瓶颈；显式状态交互必须同步落账。
    if not ephemeral:
        from . import unlock as _unlock_mod

        ledger.run("解锁检测", _unlock_mod.check_and_enqueue, user_id)

        from .state import handle_state_interaction

        ledger.run("显式状态交互", handle_state_interaction, user_id, text)
        ledger.run(
            "拟人感知后台任务",
            _spawn_memory_task,
            _perceive_and_settle(user_id, text, mock=mock),
        )

        # L06 用户意见取消（拍板 #10）：「别出门」类关键词命中 → 当日候选作废
        from .life_templates import veto_keywords_hit

        if ledger.run("生活模板取消检查", veto_keywords_hit, text):
            ledger.run(
                "生活模板取消", _spawn_memory_task, _veto_life_templates(user_id)
            )

    # 1.0) 用户消息先存档：即使后续 LLM 调用失败，对话历史也不丢、
    # 失败重发时不至于重复计好感（assistant 消息在生成成功后补存）。
    # message id 即本轮 conversation turn id（P1-02 语境生命周期按它计数）。
    turn_id = 0
    if not ephemeral:
        turn_id = db.add_message(user_id, "user", text)

    # 1.0b)~1.8) 本轮确定性旁路副作用（关系入账 / 用户教学 / 即时奖励 / 惰性提炼 /
    # 聊天派活）。messages 先建空表，派活的系统提示才能落在本轮 prompt 里。
    messages: list[dict] = []
    await run_turn_effects(
        user_id,
        text,
        turn_id=turn_id,
        ephemeral=ephemeral,
        nickname_pref=user["nickname_pref"],
        persona_name=persona_name,
        prev_ts=prev_ts,
        draft_cb=draft_cb,
        messages=messages,
    )

    # 2) 称呼与过分称呼处理（无论是否已设称呼，过分称呼都要检测并扣分）
    pref = user["nickname_pref"]
    bad_address = None
    address_intent = ADDRESS_RE.search(text) is not None
    candidate = None
    if not pref:
        if mock:
            m = ADDRESS_RE.search(text)
            candidate = clean_address(m.group(1)) if m else None
        elif address_intent or _asked_address(db.last_assistant_message(user_id)):
            try:
                candidate = await extract_address(text)
            except Exception:
                logger.exception("[pipeline] 称呼提取失败")
                candidate = None
    elif address_intent:
        # 已设称呼：仅在用户主动设置/更改称呼时检测（过分称呼同样扣分）
        if mock:
            m = ADDRESS_RE.search(text)
            candidate = clean_address(m.group(1)) if m else None
        else:
            try:
                candidate = await extract_address(text)
            except Exception:
                logger.exception("[pipeline] 称呼提取失败")
                candidate = None
    if candidate:
        # 黑名单过滤：含动词/功能词的候选不是真正要设置的称呼
        if any(b in candidate for b in _ADDRESS_BLACKLIST):
            candidate = None
    if candidate:
        if affection.check_bad_address(candidate):
            if not ephemeral:
                db.update_affection(user_id, affection.BAD_ADDRESS_PENALTY, "要求不合适的称呼")
            bad_address = candidate
        elif not ephemeral:
            db.set_nickname(user_id, candidate)
            pref = candidate

    # 3) 记忆与上下文：全部只读检索，任一路失败都降级成「这一轮少一点背景」。
    gathered, gather_failures = await gather_all(
        user_id,
        text,
        mock=mock,
        ephemeral=ephemeral,
        turn_id=turn_id,
        needs_search=_needs_search(text),
    )
    for effect_name, effect_exc in gather_failures:
        ledger.fail(effect_name, effect_exc)

    remembered = gathered["remembered"]
    facts = gathered["facts"]
    fact_id_map = gathered["fact_id_map"]
    kb_hits = gathered["kb_hits"]
    reading_context = gathered["reading_context"]
    focus_ctx = gathered["focus_ctx"]
    goal_ctx = gathered["goal_ctx"]
    writing_ctx = gathered["writing_ctx"]
    tavern_mem_ctx = gathered["tavern_mem_ctx"]
    list_ctx = gathered["list_ctx"]
    context_selection = gathered["context_selection"]
    event_recall_result = gathered["event_recall_result"]
    search_hits = gathered["search_hits"]
    search_report = gathered["search_report"]

    # 3.1) 长会话压缩：总消息超阈值时，把旧消息摘要成一段记忆，只保留最近的完整消息
    ctx = short_term_messages(user_id)
    compact_summary = None
    if not ephemeral:
        try:
            from .memory import compact_context

            compacted = await compact_context(user_id, mock=mock)
            if compacted is not None:
                compact_summary, ctx = compacted
        except Exception:
            logger.exception("[pipeline] 长会话压缩失败，保持原上下文")

    # 4) 组装 prompt
    # 4.0) 意图路由：判断这条消息是闲聊还是需要工具/回忆/情感注入。
    # 闲聊时跳过最大的堆砌源（热梗 + 对对方的了解），只保留 persona + 短上下文，
    # 让回复更自然轻快；需要工具/回忆/情感时仍全量注入（安全优先）。
    intent = None
    try:
        from .intent import classify as _classify_intent

        intent = _classify_intent(text)
    except Exception:
        logger.exception("[pipeline] 意图路由失败，按全量注入")
    is_chitchat = bool(intent and intent.get("chitchat"))

    # 4.0.1) 生图：意图判定要画图、且调用方给了 image_cb 时，先生成图片。
    # 生成结果通过 image_cb 交出去（Web 端用它拼 URL 渲染）；失败不阻塞对话，
    # 靠 LLM 自然回应。注意：只有 user 显式触发"画"才生成，避免无关句误触。
    drawn_image_path: str | None = None
    if (
        not ephemeral
        and not mock
        and image_cb is not None
        and intent is not None
        and intent.get("need_draw")
    ):
        try:
            # 先推"开始生图"标记，让前端显示占位反馈（生图较慢，避免看似卡住）
            if stream_cb is not None:
                try:
                    await stream_cb(_IMAGE_START_MARK)
                except Exception:
                    logger.debug("[pipeline] 生图开始标记推送失败（前端少一个占位，不影响生成）")
            from . import imagegen

            if imagegen.enabled():
                from .aesthetic_preferences import image_prompt

                drawn_image_path = await imagegen.generate(image_prompt(user_id, text))
                if drawn_image_path:
                    await image_cb(drawn_image_path)
        except Exception:
            logger.exception("[pipeline] 生图失败（不影响回复）")

    # 捕获“这条回复真正看到的状态”，同时交给 persona 和解释快照，避免 UI
    # 事后读取当前状态而与当轮 prompt 不一致。
    reply_state = None
    reply_frame = None
    reply_season = None
    try:
        from .behavior import build_behavior_frame
        from .calendar_modulation import compose_line, effective_modulation
        from .seasons import current_season
        from .state import load_state

        reply_state = load_state(user_id, create_if_missing=not ephemeral)
        reply_season = current_season(user_id, reply_state)
        cal_mod = effective_modulation(
            user_id,
            datetime.now().date(),
            energy=reply_state.energy,
            tension=reply_state.tension,
        )
        reply_frame = build_behavior_frame(
            reply_state,
            season_line=reply_season["line"],
            calendar_line=compose_line(cal_mod),
            style_line=_style_line(user_id),
            evolution_line=_evolution_line(user_id),
        )
    except Exception:
        logger.exception("[pipeline] 行为帧快照失败（按旧路径继续）")
    stage = (
        reply_state.stage if reply_state is not None else affection.stage_of(user["affection"])
    )

    system = build_system_prompt(
        stage=stage,
        address=pref,
        lover_confirm=bool(user["lover_confirm"]),
        first_chat=first_chat,
        affection=user["affection"],
        user_id=user_id,
        behavior_text=reply_frame.compose() if reply_frame is not None else None,
        include_plugins=not ephemeral,
    )
    # 人格与临时声明插到最前；派活的系统提示（若已追加）保持在其后。
    messages.insert(0, {"role": "system", "content": system})
    if ephemeral:
        messages.insert(1, {"role": "system", "content": ephemeral_prompt()})

    # 4.0.2 / 4.1 / 4.2 / 4.3 / 4.4 / 4.5 / 4.6：时间与关系类注入
    inject_continuation(messages, user_id, prev_ts)
    await inject_today_dates(messages, user_id, text, mock=mock, ephemeral=ephemeral)
    inject_anniversary_eve(messages, user_id)
    inject_due_promises(messages, user_id)
    inject_memory_correction(messages, user_id, text, mock=mock, ephemeral=ephemeral)
    # 当前时刻属于本模块的依赖（测试通过 patch pipeline.datetime 替换时钟），
    # 注入函数只消费 hour，不自己读时钟。
    inject_night_boundary(messages, stage, hour=datetime.now().hour)
    inject_shared_terms(messages, user_id, text, stage=stage, reply_state=reply_state)

    # 4.1 / D2 / 活动与事件类注入
    triples = await inject_memory_block(
        messages,
        user_id,
        text,
        compact_summary=compact_summary,
        remembered=remembered,
        facts=facts,
    )
    inject_knowledge(messages, kb_hits)
    inject_activity_contexts(
        messages,
        reading_context=reading_context,
        focus_ctx=focus_ctx,
        goal_ctx=goal_ctx,
        writing_ctx=writing_ctx,
        tavern_mem_ctx=tavern_mem_ctx,
        list_ctx=list_ctx,
        event_recall_result=event_recall_result,
    )
    pending_unlock = inject_pending_unlock(messages, user_id, ephemeral=ephemeral)

    # 4.2 / 4.2b / 杂项：对对方的了解、硬约束、行程、唤醒、搜索求证
    inject_understanding(messages, user_id, is_chitchat=is_chitchat)
    inject_preference_constraints(messages, user_id)
    inject_presence(messages, user_id)
    inject_wake_prompt(messages, sleep_gate_state)
    inject_search_context(messages, search_report)
    ctx = inject_conversation_context(messages, ctx, text, merged_msg=merged_msg)
    inject_bad_address(messages, bad_address)
    inject_stage_transition(messages, user_id, stage, ephemeral=ephemeral)
    inject_early_confession(messages, user_id, text, stage, ephemeral=ephemeral)

    # 5) 先思考再说话：流式模式下不能用两段式（思考会随流推给用户）。
    think_block = build_think_block(streaming=stream_cb is not None)

    # 5.0) 话题锚定：明确"当前在聊什么"，避免回复被旧上下文带偏/跑题/串话题
    topic_block = build_topic_block(text, ctx)

    # 5.1) 技能匹配：必须排在工具循环判定之前——技能正文点名了工具时，
    # 这一轮就得给模型工具通道，否则指令落进纯流式轮次只能嘴上照做。
    matched_skills, skill_texts = inject_skills(messages, text, is_chitchat=is_chitchat)

    # 5.2) 工具调用循环：有明确工具需求时启用；临时对话禁用，避免旁路写入。
    use_tool_loop = _tool_loop_enabled(
        text, intent, matched_skills, ephemeral=ephemeral, mock=mock
    )

    # 5.3) 生图提示：图已在 4.0.1 生成好，让菟菟知道自己画了。
    drawn_note = build_drawn_note(drawn_image_path)

    # 思考/话题/生图三类 system 提示统一在 user 之前注入，保证「user 是最后一条」。
    if think_block:
        messages.append({"role": "system", "content": think_block})
    if topic_block:
        messages.append({"role": "system", "content": topic_block})
    if drawn_note:
        messages.append({"role": "system", "content": drawn_note})

    # 用户消息统一在最后追加（所有 system 注入之后），确保 user 是发给模型的最后一条。
    messages.append({"role": "user", "content": text})

    # D5 模型路由：写作/代码/长文类请求走强模型（已配置 LLM_MODEL_STRONG 时）
    from .config import config as _cfg

    deep_request = not mock and _needs_strong_model(text)
    reply_model = _cfg.llm_model_strong if _cfg.llm_model_strong and deep_request else None
    reply_task = "chat_deep" if deep_request else "chat_routine"
    try:
        from .features import flag as _feature_flag

        hygiene_enabled = _feature_flag("output_hygiene_enabled")
    except Exception:
        hygiene_enabled = False

    hygiene_ctx = None
    hygiene_stream = None
    if hygiene_enabled:
        from .output_hygiene import HygieneContext
        from .stream_hygiene import IncrementalHygieneStream

        hygiene_ctx = HygieneContext(
            kind="chat",
            user_requested_explanation=_requested_internal_explanation(text),
            persona_id=user_id,
        )
        hygiene_stream = IncrementalHygieneStream(
            stream_cb, context=hygiene_ctx, enabled=not mock
        )

    if use_tool_loop:
        from ..tools.service import run_tool_round
        from .llm import chat_native

        # 工具循环把 system 指令拆到 final_instruction 单独传递，主 messages 里
        # 已注入的 think/topic/drawn 与这里保持一致即可，无需重复。
        final_instruction = [{"role": "system", "content": think_block}]
        if topic_block:
            final_instruction.append({"role": "system", "content": topic_block})
        if drawn_note:
            final_instruction.append({"role": "system", "content": drawn_note})

        async def _tool_final_stream(ms):
            async for piece in chat_stream(ms, model=reply_model, task=reply_task):
                if hygiene_stream is not None:
                    await hygiene_stream.feed(piece)
                elif stream_cb is not None and not mock:
                    try:
                        await stream_cb(piece)
                    except Exception:
                        logger.debug("[pipeline] 工具循环流式回调失败（客户端可能已断开）")
                yield piece

        raw = await run_tool_round(
            messages,
            chat=lambda ms: chat(ms, mock=mock),
            chat_native=lambda ms, tools: chat_native(ms, tools, mock=mock),
            chat_final_stream=_tool_final_stream if stream_cb is not None and not mock else None,
            max_loops=2,
            final_instruction=final_instruction,
            on_progress=progress_cb,
            tool_filter=_mcp_tool_filter(text, skill_texts),
        )
    else:
        if stream_cb is not None and not mock:
            # 卫生开启时按完整安全句段增量放行；关闭时保持 provider 原始逐块回调。
            parts: list[str] = []
            async for piece in chat_stream(messages, model=reply_model, task=reply_task):
                parts.append(piece)
                if hygiene_stream is not None:
                    await hygiene_stream.feed(piece)
                else:
                    try:
                        await stream_cb(piece)
                    except Exception:
                        pass  # 回调失败不中断生成
            raw = "".join(parts)
        else:
            raw = await chat(messages, mock=mock, model=reply_model, task=reply_task)
    reply = _postprocess_reply(text, raw)
    rewrite_used = False

    # 5.6) 重复回复检测：与最近几条菟菟回复高度相似时，重写一次（避免复读机）
    if not mock and reply.strip():
        try:
            recent = [
                m["content"]
                for m in db.recent_messages(user_id, _DUP_RECENT_N * 4)
                if m["role"] == "assistant"
            ][-_DUP_RECENT_N:]
            if recent and any(_too_similar(reply, r) for r in recent):
                logger.info("[pipeline] 检测到重复回复，重写一次")
                rewrite_used = True
                # P3-05B 统计：重复率（只记命中窗口大小，不记正文）
                try:
                    from .experience_metrics import record as _metric

                    _metric(user_id, "repetition", f"recent{len(recent)}")
                except Exception:
                    logger.debug("[pipeline] 重复率统计写入失败（不影响回复）")
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "你刚才那句回复和你之前说过的某句话太像了（几乎在复读）。"
                            "请换一种全新的说法、换个角度重新回应对方这一句，"
                            "内容不要重复刚才那句，语气照旧。"
                        ),
                    }
                )
                if stream_cb is not None and not mock and not hygiene_enabled:
                    # 流式：先让前端清空当前气泡，再重新流式生成
                    try:
                        await stream_cb(_RESET_MARK)
                    except Exception:
                        logger.debug("[pipeline] 重置标记推送失败（前端可能未清空气泡）")
                    parts2: list[str] = []
                    async for piece in chat_stream(messages, model=reply_model, task=reply_task):
                        parts2.append(piece)
                        try:
                            await stream_cb(piece)
                        except Exception:
                            logger.debug("[pipeline] 重写流式回调失败（客户端可能已断开）")
                    raw2 = "".join(parts2)
                else:
                    raw2 = await chat(messages, mock=mock, model=reply_model, task=reply_task)
                reply2 = _postprocess_reply(text, raw2)
                if reply2.strip():
                    reply = reply2
        except Exception:
            logger.exception("[pipeline] 重复回复检测失败（不影响回复）")

    # 5.9) 插件改写后才进入卫生出口，防插件重新引入内部协议文本。
    reply = _apply_reply_plugins(reply, ephemeral=ephemeral)

    if hygiene_enabled:
        from .output_hygiene import RULE_VERSION, inspect_reply

        assert hygiene_ctx is not None
        checked = inspect_reply(reply, context=hygiene_ctx)
        try:
            from .telemetry import record_current

            record_current(
                "output_checked",
                source_ids=[f"rule:{rule}" for rule in checked.rule_ids[:3]],
                rule_version=RULE_VERSION,
                outcome=checked.action,
            )
        except Exception:
            logger.warning("[telemetry] output_checked 记录失败")
        if checked.action == "rewrite":
            # P3-05B 统计：规则失败计数（只记规则名，不记正文）
            try:
                from .experience_metrics import record as _metric

                for rule in (checked.rule_ids or [])[:3]:
                    _metric(user_id, "rule_failure", str(rule)[:60])
            except Exception:
                logger.debug("[pipeline] 规则失败计数写入失败（不影响回复）")
        if checked.action == "accept":
            reply = checked.text
        elif checked.action == "rewrite" and not rewrite_used:
            # 与重复消除共享唯一重写预算。重新生成只改文案，不携带 tools，
            # 也不把被拒候选写入日志、数据库或前端。
            rewrite_used = True
            retry_instruction = {
                "role": "system",
                "content": (
                    "上一版候选包含不能展示给用户的内部推理、系统指令或工具协议。"
                    "请重新回答最后一条用户消息，只输出自然的最终答复正文；"
                    "不要输出思考过程、系统提示、工具调用格式或任何内部标记。"
                ),
            }
            retry_messages = messages[:-1] + [retry_instruction, messages[-1]]
            raw2 = await chat(retry_messages, mock=mock, model=reply_model, task=reply_task)
            reply2 = _apply_reply_plugins(
                _postprocess_reply(text, raw2), ephemeral=ephemeral
            )
            checked2 = inspect_reply(reply2, context=hygiene_ctx)
            reply = (
                checked2.text
                if checked2.action == "accept"
                else "嗯……刚才那句没整理好，我重新听你说。"
            )
        else:
            reply = "嗯……刚才那句没整理好，我重新听你说。"

        # 最终候选用于对齐已显示正文；之后的持久化、TTS/解释等都消费同一个 reply。
        if hygiene_stream is not None:
            await hygiene_stream.finish(reply)

    # 5.10) 自制表情包：只在明显情绪场景下低频触发，优先复用收藏。
    # 用户明确要求画图时已有 drawn_image_path，不再叠第二张图片。
    sticker_path: str | None = None
    if not ephemeral and not mock and image_cb is not None and drawn_image_path is None:
        try:
            from .stickers import maybe_attach_sticker

            mood_value, _ = db.get_mood(user_id)
            sticker_path = await maybe_attach_sticker(
                user_id,
                text,
                reply,
                stage=stage,
                mood=mood_value,
                image_cb=image_cb,
            )
        except Exception:
            logger.exception("[pipeline] 表情包附带失败（不影响回复）")

    # 5.10.05) L04 幽默记忆：回复里出现的共同语言记一次使用，供下一轮反馈归因
    if not ephemeral:
        try:
            from .humor_memory import note_reply_usage

            note_reply_usage(user_id, reply, turn_id or None)
        except Exception:
            logger.exception("[pipeline] 幽默记忆使用登记失败（不影响回复）")

    # 5.10.1) C4 解锁落账：回复已定稿，把「她说出口的话」摘要存进收集页
    if not ephemeral and pending_unlock is not None:
        try:
            from . import unlock as _unlock_mod

            _unlock_mod.mark_delivered(user_id, pending_unlock["key"], reply[:300])
        except Exception:
            logger.exception("[pipeline] 解锁落账失败（不影响回复）")

    # 5.11) 可解释性快照：只公开可验证的状态/行为/记忆来源，不公开隐藏思考。
    if explain_cb is not None and reply_state is not None and reply_frame is not None:
        try:
            from .explainability import build_reply_explanation

            memory_rows: list[tuple] = []
            memory_rows.extend(("相关对话", value) for value in remembered[:2])
            fact_ids_used: list[int] = []
            for value in facts[:2]:
                fact_id = fact_id_map.get(str(value).strip())
                memory_rows.append(("长期事实", value, fact_id))
                if fact_id:
                    fact_ids_used.append(fact_id)
            memory_rows.extend(
                ("知识库", f"《{h['filename']}》相关段落" if h.get("filename") else "相关段落")
                for h in kb_hits[:2]
            )
            if pending_unlock is not None:
                memory_rows.append(("解锁时刻", f"她说出了心里话：{pending_unlock['title']}"))
            memory_rows.extend(
                ("结构化记忆", f"{item[0]} {item[2]} {item[3]}")
                for item in triples[:2]
                if len(item) >= 4
            )
            memory_rows.extend(
                ("事件来源", source) for source in event_recall_result.get("sources", [])[:2]
            )
            if reply_season:
                memory_rows.append(
                    ("关系季节", f"{reply_season['label']}（{reply_season['reason']}）")
                )
            snapshot = build_reply_explanation(
                reply_state,
                reply_frame,
                memory_rows=memory_rows,
                search_used=bool(search_hits),
                media=(
                    "generated_image"
                    if drawn_image_path
                    else "sticker" if sticker_path else "none"
                ),
                contexts=(
                    context_selection.explain() if context_selection is not None else ()
                ),
                fact_ids=fact_ids_used,
                user_id=user_id,
            )
            await explain_cb(snapshot)
        except Exception:
            logger.exception("[pipeline] 回复解释快照失败（不影响回复）")

    # 6) 临时对话只把回复交给当前客户端，任何会话/记忆/关系状态都不落盘。
    if ephemeral:
        return reply

    # 普通对话存档（user 消息已在 1.0 存档，这里只补 assistant 回复）
    db.add_message(user_id, "assistant", reply)
    # P1-02 语境生命周期：回复成功提交后才刷新 sticky/cooldown（幂等，同一轮
    # 不多扣；只续本轮话题真正命中的条目）。注册表关闭时 selection 为 None。
    if context_selection is not None and context_selection.fresh_entry_keys:
        try:
            from .context_registry import commit_context_turn

            commit_context_turn(user_id, turn_id, context_selection.fresh_entry_keys)
        except Exception:
            logger.exception("[pipeline] 语境生命周期提交失败（不影响回复）")
    lm1_id = db.add_long_memory(user_id, f"用户说：{text}")
    lm2_id = db.add_long_memory(user_id, f"{persona_name}说：{reply}")
    db.set_first_chat_done(user_id)

    # 6.1) 容量上限：超过 _LM_MAX_ROWS 时清理最旧 SQLite 记录（本地毫秒级）。
    # 对应 Chroma 旧向量的删除放到 6.1.1 的后台任务里，不阻塞回复。
    removed_lm: list[int] = []
    try:
        removed_lm = db.prune_long_memory(user_id, keep=_LM_MAX_ROWS)
    except Exception:
        logger.exception("[pipeline] 长期记忆容量清理失败（忽略）")

    # 6.1.1) Chroma 向量索引 + 旧向量删除 → 后台 fire-and-forget：
    # 回复先返回，embedding 下载/编码不再阻塞 done 帧与下一轮；任务带独立
    # 超时与失败日志，句柄存于 _memory_tasks（P1/P4/P7：首轮不卡 300s、
    # 每轮不叠加不可预测延迟、副作用发生情况可查）
    if not mock:
        try:
            _spawn_memory_task(
                _vectorize_memory_async(user_id, text, reply, lm1_id, lm2_id, removed_lm)
            )
        except Exception:
            logger.exception("[pipeline] 记忆后台任务启动失败")

    # 6.2) 记忆引擎 v2：后台提炼画像 / Mem0 记忆管理（失败静默，不阻塞回复）
    try:
        from .memory.engine import on_message

        on_message(user_id, text, reply, mock=mock)
    except Exception:
        logger.debug("[pipeline] 记忆引擎 on_message 失败（后台提炼，不影响回复）")
    # 本轮吞掉的旁路错误计数（进程级，供 /api/meta 观测）；不暴露用户内容。
    ledger.run("本轮台账记账", _record_ledger, ledger)
    return reply


def _record_ledger(ledger: EffectLedger) -> None:
    """把本轮台账写进进程级统计（只记名称与错误类型，不记用户内容）。"""
    if not ledger.failed:
        return
    logger.debug("[pipeline] 本轮旁路失败 {} 项：{}", len(ledger.failures), ledger.applied)
