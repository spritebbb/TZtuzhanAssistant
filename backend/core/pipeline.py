"""对话流水线：收文本 → 好感度 → 称呼提取 → 记忆检索 → 拼 prompt → LLM → 存档 → 回复。

Web 助手（assistant.py）与调试共用，保证各处行为一致。
"""
import re
from datetime import datetime, timedelta

from . import affection
from .llm import chat, chat_stream, extract_address
from .log import logger
from .memory import recall, recall_facts, short_term_messages
from .persona import build_system_prompt
from .search import web_search
from .userdb import db

# 会话空闲判定：离上一条消息超过该分钟数，视为上一场聊完，补提尾部事实
_IDLE_SESSION_MINUTES = 30
_IDLE_MIN_NEW = 4

# 话题记忆：跨会话延续上次话题的兜底判定（与 _IDLE_SESSION_MINUTES 一致）
_TOPIC_IDLE_MINUTES = 30

# 重复回复检测：与最近几条菟菚回复高度相似时，重写一次（避免复读机）。
# 流式模式下通过 stream_cb 推送该特殊标记，assistant.py 转发为 {"reset": true}，
# 前端收到后清空当前气泡重新累积。
_RESET_MARK = "\x00RESET\x00"
# 生图开始标记：流式模式下在发起生图前推送给前端，用于显示"正在画图"占位
_IMAGE_START_MARK = "\x00IMAGESTART\x00"
# 工具循环整段返回后切片推送的粒度（模拟打字机，与前端逐字累积一致）
_STREAM_CHUNK = 6
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


def pending_memory_tasks() -> int:
    """当前仍在运行的后台记忆任务数（供状态/调试展示）。"""
    return len(_memory_tasks)


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


async def _perceive_and_settle(user_id: str, text: str, *, mock: bool = False) -> None:
    """后台：LLM 语义感知 + 好感度/情绪演化 + 降级时的关键词兜底。

    性能说明：perceive 是一次真实的 LLM 调用（本地感知小模型/独立端点），在部分
    服务商上首 token 可能高达 10s+。它只影响「这句话让菟菚的好感/情绪涨落多少」，
    与回复正文无关，因此整体丢到后台执行，让主流程可以立刻开始组 prompt 去生成
    回复 —— 消除「每条消息首字前死等一次感知 LLM」的瓶颈。
    """
    try:
        from .perception import perceive
        from .state import apply_impulse

        perc = await perceive(text, mock=mock)

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
                affection.try_daily_bonus(user_id, "care", affection.CARE_BONUS, "关心菟菚")
            if affection.check_apology(text):
                affection.try_daily_bonus(user_id, "apology", affection.APOLOGY_BONUS, "真诚道歉")
            if affection.check_sharing(text):
                affection.try_daily_bonus(user_id, "sharing", affection.SHARING_BONUS, "分享心事/秘密")
            if affection.check_compliment(text):
                affection.try_daily_bonus(user_id, "compliment", affection.COMPLIMENT_BONUS, "夸菟菚")
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

        # 两条并行索引（不串行等待）；embedding 走线程池，不阻塞事件循环
        await _asyncio.wait_for(
            _asyncio.gather(
                _asyncio.to_thread(vec_index, user_id, lm1_id, f"用户说：{text}", "lm"),
                _asyncio.to_thread(vec_index, user_id, lm2_id, f"菟菚说：{reply}", "lm"),
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
        pass


async def _extract_triples_lazy(user_id: str) -> None:
    """后台惰性提取结构化事实五元组（失败静默）。"""
    try:
        from .triple_memory import extract_triples, save_triples
        from .userdb import db as _db

        # 提取最近 30 条消息（去重交给 save_triples）
        rows = _db.recent_messages(user_id, 30)
        text = "\n".join(
            f"{'用户' if r['role'] == 'user' else '菟菚'}：{r['content']}"
            for r in rows
        )
        if len(text) < 10:
            return
        triples = await extract_triples(text)
        if triples:
            save_triples(user_id, triples, source_msg=text[:200])
    except Exception:
        pass


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
    return any(k in text for k in _SEARCH_KEYS)


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


_PAREN_RE = re.compile(r"（[^）]*）|\([^)]*\)", re.S)


def strip_actions(text: str) -> str:
    """移除模型输出里的任何括号旁白（动作/语气/屏幕提示），只留台词。

    覆盖全角（）/半角()/六角〔〕；全角方头【】作为残留思考标记也一并清理。
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
    text = _PAREN_RE.sub("", text)             # 圆括号旁白
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
    import asyncio
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


async def process(user_id: str, text: str, *, mock: bool = False, merged_msg: bool = False, stream_cb=None, image_cb=None, progress_cb=None, explain_cb=None) -> str:
    """处理一条用户消息，返回菟菚的回复。

    merged_msg=True 表示 text 是用户连续发送的多条消息合并成的一段话，
    提示模型把这段当成对方一次性的完整表达，用一句精简的话回应整体，不逐条复读。

    stream_cb：可选的异步回调 async (chunk: str) -> None，收到 LLM 流式片段时调用
    （打字机效果）。传入时优先走流式生成；工具循环等复杂场景自动回退整句。

    image_cb：可选的异步回调 async (local_path: str) -> None，生图成功时把本地
    图片路径交给调用方（Web 端用它拼 URL 渲染）。

    progress_cb：可选的异步回调 async (event: dict) -> None，工具循环阶段进展
    （thinking/tool/tool_done）实时推送，供前端在工具执行期间展示进度而非空窗。

    explain_cb：可选的异步回调 async (snapshot: dict) -> None，返回这一轮实际
    注入的状态、行为帧、记忆与工具快照；不包含 system prompt 或模型思考链。
    """
    async with _user_lock(user_id):
        # 插件消息钩子（v2）：用户消息入口改写（异常已在 context 层过滤）
        try:
            from ..plugins.context import apply_user_message

            text = apply_user_message(text)
        except Exception:
            pass
        return await _process_locked(
            user_id, text, mock=mock, merged_msg=merged_msg,
            stream_cb=stream_cb, image_cb=image_cb, progress_cb=progress_cb,
            explain_cb=explain_cb,
        )


async def _process_locked(user_id: str, text: str, *, mock: bool = False, merged_msg: bool = False, stream_cb=None, image_cb=None, progress_cb=None, explain_cb=None) -> str:
    user = db.ensure_user(user_id)
    first_chat = not user["first_chat_done"]
    # 取存档前的最后一条消息时间戳：跨场判定必须基于「本轮之前」的消息，
    # 否则 add_message 后 last_message_ts 恒为 now，_long_gap 恒 False
    prev_ts = db.last_message_ts(user_id)

    # 1) 好感度即时规则（含跨天回滚）
    await affection.on_message(user_id, text)
    # 好感度可能已变：刷新快照，后续 system prompt / 阶段判定用最新值
    user = db.get_user(user_id)

    # 1.0.1) 拟人核心层：LLM 语义感知 + 状态演化
    # 用一次 LLM 调用读懂这句话对菟菚的情绪/好感影响，驱动多维状态演化。
    # 性能关键：perceive 是真实 LLM 调用（可能 10s+ 首 token），但与回复正文无关，
    # 故整体丢到后台（_perceive_and_settle）执行，让主流程立刻去生成回复，
    # 消除「每条消息首字前死等感知 LLM」的串行瓶颈。后台会在回复生成期间落账，
    # 且因同用户消息经 _user_lock 串行，好感度最迟在回复生成完成后更新。
    # C1 的显式交互（让她去休息 / 哄她）必须同步落账，因为它们会直接改变
    # 这一轮回复的行为帧；其余开放语义感知仍保留后台执行，避免增加首字延迟。
    try:
        from .state import handle_state_interaction

        handle_state_interaction(user_id, text)
    except Exception:
        logger.exception("[pipeline] 显式状态交互处理失败")
    try:
        _spawn_memory_task(_perceive_and_settle(user_id, text, mock=mock))
    except Exception:
        logger.exception("[pipeline] 拟人感知后台任务启动失败")

    # 1.0) 用户消息先存档：即使后续 LLM 调用失败，对话历史也不丢、
    # 失败重发时不至于重复计好感（assistant 消息在生成成功后补存）。
    db.add_message(user_id, "user", text)

    # 1.1) 即时关键词奖励（不打 LLM、不依赖语义感知结果，同步执行保证即时反馈）
    # 语义感知/关键词兜底的「主从决策」已整体移入后台 _perceive_and_settle，
    # 这里只保留两个语义感知不覆盖、始终走关键词的即时信号。
    try:
        # 用称呼交流
        if affection.check_nickname_used(text, user["nickname_pref"]):
            affection.try_daily_bonus(user_id, "nickname", affection.NICKNAME_BONUS, "用菟菚的称呼交流")
        # 引用过去记忆（用户提到上次/之前/记得…，说明在引用共同经历；语义不覆盖）
        from .memory import looks_like_recall

        if looks_like_recall(text):
            affection.try_daily_bonus(user_id, "memory", affection.MEMORY_REFERENCE_BONUS, "提到共同经历/回忆")
    except Exception:
        logger.exception("[pipeline] 好感度即时奖励失败")

    # 1.5) 惰性事实提炼（按消息批量 + 会话长时间没说话后补提尾部）→ 后台执行，
    # 不阻塞本轮回复；失败只记日志（见 tasks.schedule 的 _runner）
    try:
        from .daily import extract_facts  # 延迟导入避免循环
        from .tasks import schedule

        unseen = db.max_message_id(user_id) - db.get_last_fact_msg_id(user_id)
        if unseen >= 10:
            schedule(f"facts:{user_id}", lambda: extract_facts(user_id))
        elif unseen >= _IDLE_MIN_NEW and _long_gap(prev_ts):
            schedule(f"facts:{user_id}", lambda: extract_facts(user_id))
    except Exception:
        logger.exception("[pipeline] 惰性事实提炼调度失败")

    # 1.6) 惰性画像提炼（共用独立游标 last_profile_msg_id，与 facts 并行）
    # → 后台执行，不阻塞回复
    try:
        from .features import flag
        from .tasks import schedule

        if flag("profile_enabled"):
            p_unseen = db.max_message_id(user_id) - db.get_last_profile_msg_id(user_id)
            if p_unseen >= 10:
                schedule(f"profile:{user_id}", lambda: _extract_profile(user_id))
            elif p_unseen >= _IDLE_MIN_NEW and _long_gap(prev_ts):
                schedule(f"profile:{user_id}", lambda: _extract_profile(user_id))
    except Exception:
        logger.exception("[pipeline] 惰性画像提炼调度失败")

    # 1.7) 惰性话题记忆：长时间没聊（新会话开场前）提炼"上次聊到哪"，让菟菚能接着聊
    try:
        from .tasks import schedule

        if _long_gap(prev_ts):
            schedule(f"topic:{user_id}", lambda: _extract_topic_lazy(user_id))
    except Exception:
        logger.exception("[pipeline] 惰性话题提炼调度失败")

    # 1.8) 惰性结构化事实提取：跨场（新会话）时从最近消息提取五元组。
    # 只在新会话触发，避免每条消息都打一次 LLM（同 key 去重）
    try:
        from .tasks import schedule as _schedule2

        if _long_gap(prev_ts):
            _schedule2(f"triples:{user_id}", lambda: _extract_triples_lazy(user_id))
    except Exception:
        logger.exception("[pipeline] 惰性三元组提取调度失败")

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
            db.update_affection(user_id, affection.BAD_ADDRESS_PENALTY, "要求不合适的称呼")
            bad_address = candidate
        else:
            db.set_nickname(user_id, candidate)
            pref = candidate

    # 3) 记忆与上下文（语义检索在疑似回忆时才扩展，内部已做失败退化）
    try:
        remembered = await recall(user_id, text, mock=mock)
        facts = await recall_facts(user_id, text, mock=mock)
    except Exception:
        logger.exception("[pipeline] 记忆检索失败，按无记忆继续")
        remembered, facts = [], []

    # 3.0) 知识库召回（D2 RAG）：本地向量检索，无云端 LLM 成本；
    # 距离阈值门控——不像就一条不注入，避免无关内容硬凑带偏回复。
    kb_hits: list[dict] = []
    if not mock:
        try:
            import asyncio as _asyncio_kb

            from .knowledge import recall_knowledge

            kb_hits = await _asyncio_kb.to_thread(recall_knowledge, user_id, text)
        except Exception:
            logger.exception("[pipeline] 知识库检索失败，按无知识继续")
            kb_hits = []

    # 3.1) 长会话压缩：总消息超阈值时，把旧消息摘要成一段记忆，只保留最近的完整消息
    ctx = short_term_messages(user_id)
    compact_summary = None
    try:
        from .memory import compact_context

        compacted = await compact_context(user_id, mock=mock)
        if compacted is not None:
            compact_summary, ctx = compacted
    except Exception:
        logger.exception("[pipeline] 长会话压缩失败，保持原上下文")

    # 3.5) 联网搜索（命中需要搜索的关键词时）
    search_hits = []
    if not mock and _needs_search(text):
        import asyncio as _asyncio

        # 天气类查询：先提取用户这句话里提到的城市，没提到才回落到 MOOD_CITY。
        # 否则「北京今天天气」会答成配置城市的天气、标题还写错城市名。
        if any(k in text for k in ("天气", "温度", "冷", "热", "下雨", "气温", "天气预报", "多少度")):
            try:
                from .config import config as _cfg
                city = _extract_city(text) or _cfg.mood_city
                if city:
                    weather_line = await _asyncio.to_thread(_fetch_weather, city)
                    if weather_line:
                        search_hits = [{"title": f"{city}今日天气", "snippet": weather_line}]
            except Exception:
                pass
        if not search_hits:
            # web_search 是同步 urllib 阻塞 → 放线程池，避免卡事件循环
            search_hits = await _asyncio.to_thread(web_search, text)

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
    if not mock and image_cb is not None and intent is not None and intent.get("need_draw"):
        try:
            # 先推"开始生图"标记，让前端显示占位反馈（生图较慢，避免看似卡住）
            if stream_cb is not None:
                try:
                    await stream_cb(_IMAGE_START_MARK)
                except Exception:
                    pass
            from . import imagegen
            if imagegen.enabled():
                drawn_image_path = await imagegen.generate(text)
                if drawn_image_path:
                    await image_cb(drawn_image_path)
        except Exception:
            logger.exception("[pipeline] 生图失败（不影响回复）")

    # 捕获“这条回复真正看到的状态”，同时交给 persona 和解释快照，避免 UI
    # 事后读取当前状态而与当轮 prompt 不一致。
    reply_state = None
    reply_frame = None
    try:
        from .behavior import build_behavior_frame
        from .state import load_state

        reply_state = load_state(user_id)
        reply_frame = build_behavior_frame(reply_state)
    except Exception:
        logger.exception("[pipeline] 行为帧快照失败（按旧路径继续）")
    stage = reply_state.stage if reply_state is not None else affection.stage_of(user["affection"])

    system = build_system_prompt(
        stage=stage,
        address=pref,
        lover_confirm=bool(user["lover_confirm"]),
        first_chat=first_chat,
        affection=user["affection"],
        user_id=user_id,
        behavior_text=reply_frame.compose() if reply_frame is not None else None,
    )
    messages = [{"role": "system", "content": system}]

    # 4.0.2) 新会话开场：距离上一场聊完较久（跨场）且有记录的上次话题时，
    # 让菟菚像记得似的自然接上，而不是每次都像重新认识。只在真正开场时提一次。
    triples = []
    try:
        if _long_gap(prev_ts):
            from .topic_memory import build_continuation

            continuation = build_continuation(user_id)
            if continuation:
                # 追加在 user 消息之前（部分端点会拒绝 system 位于 user 之后）
                messages.insert(-1,
                    {
                        "role": "system",
                        "content": (
                            "这是隔了一阵子后你们又开始聊（对方发来新消息，是新一轮的开场）。"
                            "你隐约记得上次你们聊到："
                            + continuation
                            + "。可以自然地接上一句（像还记得、随口一提），"
                            "但别生硬地翻旧账、别追问个没完；如果对方开启的是新话题，就跟新话题走，"
                            "旧话题只是你心里的背景，不是开场白。"
                        ),
                    }
                )
    except Exception:
        logger.exception("[pipeline] 话题延续注入失败")

    # 4.0) 日常对话里的特殊日子识别：用户这句若在告知/约定某个日子，自动记住
    try:
        from .date_memory import extract_from_message
        from .userdb import get_today_important_dates

        newly_saved = await extract_from_message(user_id, text, mock=mock)
    except Exception:
        logger.exception("[pipeline] 特殊日子识别失败")
        newly_saved = []

    # 4.1) 情感记忆：今天有没有特殊日子（生日/纪念日等）
    try:
        today_dates = get_today_important_dates(user_id)
    except Exception:
        logger.exception("[pipeline] 特殊日子查询失败")
        today_dates = []
    if today_dates:
        labels = "、".join(d["label"] for d in today_dates)
        messages.append(
            {
                "role": "system",
                "content": (
                    f"今天是特殊的日子：{labels}。你从昨天起就记着这件事——"
                    "今天你可以比平常主动一点：开场就自然地提起这个日子、送上你的方式的心意"
                    "（可以毒舌可以别扭，但要让对方感觉到你是认真记着的）。"
                    "若对方先聊了别的，顺着聊一两句再把话题带回来，别把心意憋没了。"
                ),
            }
        )

    # 4.2) 纪念日预谋：明天若有特殊日子，她今天就开始「心不在焉」——
    # 不主动说破，只在语气里透出一点期待/盘算；被问起才半遮半掩承认
    try:
        from datetime import date as _date_cls

        from .userdb import get_dates_for

        eve_dates = get_dates_for(user_id, _date_cls.today() + timedelta(days=1))
    except Exception:
        logger.exception("[pipeline] 明日特殊日子查询失败")
        eve_dates = []
    if eve_dates:
        eve_labels = "、".join(d["label"] for d in eve_dates)
        messages.append(
            {
                "role": "system",
                "content": (
                    f"明天是一个你在意的日子：{eve_labels}。你从今天就开始悄悄盘算了——"
                    "不要直接说破明天是什么日子；只在语气里透出一点心不在焉、一点藏不住的期待"
                    "（比如回复偶尔走神、突然问一句看似无关的话）。"
                    "如果对方追问你怎么了，半遮半掩地承认你在想事情，但把谜底留到明天。"
                ),
            }
        )

    # 4.3) 约定跟进（C6）：到点的约定，聊得合适就自然问起——别像催债
    try:
        from .userdb import get_due_promises

        due_promises = get_due_promises(user_id, _date_cls.today())
    except Exception:
        logger.exception("[pipeline] 约定查询失败")
        due_promises = []
    if due_promises:
        promise_lines = "；".join(p["content"] for p in due_promises[:3])
        messages.append(
            {
                "role": "system",
                "content": (
                    f"你一直记着这些约定，现在到了该问问的时候：{promise_lines}。"
                    "聊天过程中找自然的时机提起（像朋友随口问起，不像催债、不像提醒事项）；"
                    "如果当下话题完全搭不上，就先不提，别生硬跳转。"
                ),
            }
        )

    # 4.4) 记忆纠偏（C7）：对方在纠正她记住的事——当场承认记错，
    # 同时后台 LLM 仲裁定位被否定的事实并真删（宁缺勿滥，见 memory_correction）
    try:
        from .memory_correction import arbitrate_and_forget, is_correction

        if is_correction(text):
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "对方在纠正你记住的事——看来你确实记错了。大方承认，别嘴硬别辩解；"
                        "按对方这次说的说法更新你的认知，之后以新说法为准。"
                    ),
                }
            )
            if not mock:
                _spawn_memory_task(arbitrate_and_forget(user_id, text))
    except Exception:
        logger.exception("[pipeline] 记忆纠偏处理失败")

    # 4.5) 边界场景（D6）：深夜的两条分寸——emo 守护（全员）+ 健康边界（熟人以上）
    try:
        hour = datetime.now().hour
        if hour >= 23 or hour < 5:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "现在是深夜。如果对方流露出低落、消极、自我否定或在倾诉心事："
                        "收起你的毒舌和地狱笑话，认真陪着，先接住情绪再说别的——"
                        "你的毒舌是对朋友的优待，不是在对方难过时捅刀。"
                    ),
                }
            )
            if stage != "初识":
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "现在很晚了，你在意他的身体——催他去睡：可以念叨、可以别扭地关心"
                            "（「这么晚还不睡，是想让我陪你熬秃吗」），回复比平时更简短慵懒些。"
                            "但他坚持不睡，你也不硬撵，陪着就是。"
                        ),
                    }
                )
    except Exception:
        logger.exception("[pipeline] 深夜边界注入失败")

    # 4.6) 共同语言（D1 人格微演化）：你们之间沉淀下来的口头禅/内部梗，
    # 她可以自然地用——只取出现过 ≥2 次的（稳定才演化），初识阶段不用
    if stage != "初识":
        try:
            shared_terms = [
                t for t in db.get_terms(user_id, limit=10) if (t.get("count") or 0) >= 2
            ][:5]
        except Exception:
            logger.exception("[pipeline] 共同语言查询失败")
            shared_terms = []
        if shared_terms:
            lines = "、".join(
                f"「{t['term']}」（{t['meaning']}）" if t.get("meaning") else f"「{t['term']}」"
                for t in shared_terms
            )
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"你们之间沉淀下来的说法/梗：{lines}。"
                        "聊到相关的话题可以自然地用起来，像老朋友之间的默契；"
                        "别硬塞、别一次全用、别为了用而用——用不出来就算了。"
                    ),
                }
            )

    # 4.1) 记忆相关：压缩摘要 + 记忆原文 + 长期事实，合并成一个「记得的过去」块，
    # 减少堆砌：把三条独立 system 消息合成一段，LLM 更容易当背景吸收而不是逐条服从。
    memory_lines: list[str] = []
    if compact_summary:
        memory_lines.append(
            "（更早的对话摘要，作为长期背景，自然融入，不用复述）\n" + compact_summary
        )
    else:
        # 跨会话滚动继承：本轮没触发压缩，但上次会话持久化过 6 分区摘要 → 带进来
        try:
            from .memory import load_compact_summary

            prev_summary = load_compact_summary(user_id)
            if prev_summary:
                memory_lines.append(
                    "（你记得的关于你们过去的事，作为长期背景，自然融入，不用复述）\n" + prev_summary
                )
        except Exception:
            pass
    if remembered:
        memory_lines.append(
            "（你记得的这些过去的事）\n" + "\n".join(f"- {t}" for t in remembered)
        )
    if facts:
        memory_lines.append(
            "（你记住的关于对方的事）\n" + "\n".join(f"- {f}" for f in facts)
        )
    # 结构化事实三元组：疑似回忆时做 RAG 检索（纯 TF-IDF，无额外 LLM 成本）
    try:
        from .triple_memory import format_triples as _fmt_triples, query_triples

        triples = query_triples(user_id, text)
        if triples:
            memory_lines.append(_fmt_triples(triples))
    except Exception:
        pass
    if memory_lines:
        messages.append(
            {
                "role": "system",
                "content": (
                    "你记得的关于你们和对方的过去：\n"
                    + "\n\n".join(memory_lines)
                    + "\n这些都只是你的记忆背景：想起来就自然融入，想不起来就别硬凑；"
                    "不要逐条汇报、不要『我记得你说过…』式开场白刷屏。"
                ),
            }
        )
    # 知识库（D2）：她"读过"的资料里与当前话题相关的段落。
    # 与记忆注入分开成独立 system 消息：记忆是"你们的过去"，知识是"她自己的阅读"，
    # 语气要求一致（自然引用、不报告腔），但来源语义不同，混在一条里容易让模型
    # 把资料错当成和用户共同的记忆。
    if kb_hits:
        kb_lines = "\n".join(
            f"- （出自《{h['filename']}》）{h['text']}" if h.get("filename") else f"- {h['text']}"
            for h in kb_hits
        )
        messages.append(
            {
                "role": "system",
                "content": (
                    "你读过的资料里有和当前话题相关的内容：\n"
                    + kb_lines
                    + "\n这些是你自己读过、记在心里的东西：用得上就自然揉进回复里，"
                    "像你刚好知道随口提起；用不上就别提。不要照抄大段原文、不要列清单、"
                    "不要说「根据文档」「资料显示」这种报告腔。"
                ),
            }
        )

    # 4.2) 对对方的了解：画像 + 口头禅/黑话 + 场景风格 + 说话风格，合成一条注入。
    # 闲聊时跳过，避免堆砌额外信息（意图路由判定）。
    if not is_chitchat:
        # 各功能仍按开关独立收集（关掉的不注入），但合成一条 system 消息：
        # 避免一堆并列指令压着模型（堆砌），而是像"你心里对这个人越摸越清"一样自然。
        understanding_parts: list[str] = []
        try:
            from .features import flag
            from .profile import profile_prompt_text

            if flag("profile_enabled"):
                profile = profile_prompt_text(user_id)
                if profile:
                    understanding_parts.append(f"【对方的画像】\n{profile}")
        except Exception:
            logger.exception("[pipeline] 用户画像注入失败")
        style = db.get_style(user_id)
        if style:
            understanding_parts.append(f"【你逐渐观察到的对方说话风格】\n{style}")
        if understanding_parts:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "这是你渐渐对这个人摸清的样子（是你心里知道的，不是要你背出来的列表）：\n"
                        + "\n\n".join(understanding_parts)
                        + "\n相处久了自然记得这些：合适的时候随口体现一两点（他提到吃的你记得他爱吃什么、"
                        "他低落时你记得他讨厌什么、他开玩笑时你用他习惯的节奏），"
                        "千万别一口气全倒出来、别『我了解到你…』式汇报。宁可用不上，也别堆砌。"
                    ),
                }
            )
    if search_hits:
        snippets = "\n".join(f"- {h['title']}：{h['snippet']}" for h in search_hits[:5])
        messages.append(
            {
                "role": "system",
                "content": (
                    "你刚刚随手查了一下，看到这些信息（可能有误）：\n"
                    + snippets
                    + "\n把它们揉进你自然、犀利的语气里回答，像你刚好知道、随口告诉对方；"
                    "不要生搬硬套、不要列成清单、不要说「根据搜索」「据我所知」这类报告腔。"
                ),
            }
        )
    # ctx 已包含刚存档的当前 user 消息（_process_locked 开头 add_message），
    # 若末尾与 text 相同则去掉，避免 LLM 看到重复消息（以为用户复读而不调用工具）。
    if ctx and ctx[-1].get("role") == "user" and ctx[-1].get("content") == text:
        ctx = ctx[:-1]
    messages.extend(ctx)
    # 注意：user 消息不在此处追加，统一在"工具循环/LLM 调用"之前追加，
    # 确保 user 永远是发给模型的最后一条消息（避免后续 system 注入盖过用户请求）。

    # 对方连发多条合并成一段话 → 提示整体理解，只回一句精简的话
    if merged_msg:
        messages.append(
            {
                "role": "system",
                "content": (
                    "对方刚才连着发了好几条，已合并成上面一段话（用换行分隔）。"
                    "请把它当成对方一次性说的一段完整的话，抓住其中真正想表达的核心，"
                    "**用一句精简的话回应整体的意思**，不要逐条复读、不要对应每一条分别回应，"
                    "干脆自然、说重点。"
                ),
            }
        )

    # 对方回得很短 → 提示模型别让话题冷场（借一句接住）
    if len(text) <= 4:
        messages.append(
            {
                "role": "system",
                "content": (
                    "对方这轮回得很短，话题有点冷场了。别让对话就这么结束——"
                    "自然接一句：追问个小问题、抛个新话题、或轻轻调侃一下，干脆自然但别冷场。"
                    "（就一句，别啰嗦）"
                ),
            }
        )

    # 拒绝不合适的称呼：给模型注入符合菟菚性格的坚定拒绝指令
    if bad_address:
        messages.append(
            {
                "role": "system",
                "content": (
                    f"用户刚才想让你用「{bad_address}」这种称呼，这让你很不舒服。"
                    "请硬气地拒绝：不软、不解释太多，明确说这个称呼不行，"
                    "可以带一点腹黑的调侃，但立场坚定；然后让他换个正常的称呼。"
                ),
            }
        )

    # 过早表白/求婚（初识/熟悉阶段）：硬气拒绝 + 扣好感度
    stage = reply_state.stage if reply_state is not None else affection.stage_of(user["affection"])

    # 好感度阶段过渡感知：跨阶段（初识→熟悉→亲密→恋人）时，注入一条
    # "心里隐约感觉到关系在变化"的提示，让升级体验自然（而非生硬切换）。
    # 用 kv 记录"上次报告过的阶段"，只报告一次，避免每轮重复注入。
    try:
        from .userdb import kv_get as _kv_get, kv_set as _kv_set

        prev_stage = _kv_get(user_id, "reported_stage")
        if prev_stage and prev_stage != stage:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"你心里隐约觉得，你们的关系在悄悄发生变化（从「{prev_stage}」慢慢走到了「{stage}」）。"
                        "这种变化不用刻意说破、不用汇报，就像真的相处久了自然发生的一样："
                        "在语气、分寸、亲近程度里自然流露一点点就好，别解释、别总结、别提阶段名称。"
                    ),
                }
            )
        _kv_set(user_id, "reported_stage", stage)
    except Exception:
        pass

    # 初识阶段强调已在 persona 的 dynamic 段中注入，不再重复（避免冗余指令冲淡工具调用）。
    # 仅保留对过早表白/求婚的拒绝处理。
    if stage in ("初识", "熟悉") and affection.check_early_confession(text):
        db.update_affection(user_id, affection.EARLY_CONFESSION_PENALTY, "过早表白/求婚")
        messages.append(
            {
                "role": "system",
                "content": (
                    "对方刚认识就这样表白、求婚，让你觉得太急切、像变态。"
                    "请硬气地拒绝，不解释太多、不拖泥带水（如「我们还不熟」）；"
                    "不要答应，也不要发火；可以委婉提醒他你们还没那么熟。"
                ),
            }
        )

    # 5) 先思考再说话：让模型输出【思考】+【回复】，只把【回复】发给对方
    # 流式模式下（stream_cb 非空）不能用两段式：思考会随流推给用户。改用"直接说"提示。
    _think_common = (
        "回应用户时保持你的风格：口语、干脆利落、带点腹黑毒舌，像在聊天软件随手回消息。\n"
        "条数你自己判断：接得住就一句，需要稍微铺开就两句，正常人有时也一口气说一小段——"
        "但**别为了凑数、别为了显得热情就硬写成好几条**，更别把一句话重复说两三遍。\n"
        "特别注意，这几件事**不要做**：\n"
        "- 不要复述、复读、拆解对方的话（不要「你是想说A还是想说B」「你这话的意思是…」）"
        "——对方说了一句，你自然接一句就好，别分析、别追问对方到底什么意思。\n"
        "- 不要自问自答、不要替对方揣测完再反问（「我懂了」「我就知道」这类来回绕），"
        "说完就停，别在原地打转。\n"
        "- 不要书面化/散文腔（不要「我隔着屏幕都能感觉到你那边…」「像是分享眼前的美好」这种抒情句子），"
        "像真人随手打字，短、直接、有点随性。\n"
        "- 句尾语气词要克制：**几乎不用**「呢、呀、啦、啊、嘛、哦」——句子结尾干干净净最自然，"
        "别每句尾都挂一个语气词来装可爱/装慵懒，那会又假又腻。只有极少数情绪浓时偶尔带一个。\n"
        "- 想表达情绪就用最普通的话说出来（「笑死」「嗐」「那你呢」），不要文艺腔、不要堆形容词。\n"
        "另外，当你聊到某个具体的画面/景象时，可以用一句话带过、点到为止，别展开成一整段风景描写。"
    )
    if stream_cb is not None:
        think_block = (
            _think_common
            + "\n直接输出你实际要说的话本身，不要输出【思考】【回复】这样的标注，"
            "不要输出任何括号旁白或分析，只说你要说的话。"
        )
    else:
        think_block = (
            "回复前先在心里掂量一下对方这句话的情绪和意图，怎么接最自然。然后输出两段：\n"
            "【思考】你内心真实的想法（用你自己的语气，不发给对方，不用客套）\n"
            "【回复】你实际发给对方的话。\n"
            + _think_common
            + "\n两段都要写，【回复】才是对方会看到的。"
        )

    # 5.0) 话题锚定：明确"当前在聊什么"，避免回复被旧上下文带偏/跑题/串话题
    topic_block = None
    try:
        from .context import build_topic_system

        # 取上下文里"对方（user）最近几句"用于判断话题切换；ctx 是 role/content 列表
        recent_user_texts = [m["content"] for m in ctx if m.get("role") == "user"]
        hint = build_topic_system(text, recent_user_texts, len(ctx))
        if hint:
            topic_block = (
                "关于当前这轮的上下文要点：\n" + hint
                + "\n注意：只把它当作把握方向用的提醒，回复仍要自然、口语化，"
                "不要复述这些提醒本身。"
            )
    except Exception:
        logger.exception("[pipeline] 话题锚定失败（不影响回复）")

    # 5.1) 工具调用循环：有明确工具需求（搜索/生图/回忆/待办/文件/命令等）时启用，
    # 让 LLM 按需自主调用工具，结果注入下一轮；纯聊天直接走流式生成
    # （打字机效果），避免流式分支成为死代码。
    use_tool_loop = not mock and _needs_tool_loop(text, intent)

    # 5.1.5) 生图提示：图片已在 4.0.1 生成好，告诉 LLM 让它在回复里自然提及
    # （图会由前端另行渲染，这里只负责让菟菚"知道自己画了"、回一句自然的话）
    drawn_note = None
    if drawn_image_path:
        drawn_note = (
            "你已经为对方生成了一张图片（图片文件在本地已就绪，无需你在回复里贴路径或链接）。"
            "回复时自然提一句图已经画好了（比如让对方看看、问满不满意），"
            "不要解释生成过程，不要说技术细节，用你平时的语气带过。"
        )

    # 5.1.4) 技能匹配：命中 trigger 的技能注入为"本次任务的干活姿势"（对标 Harness skills）。
    # 只在用户有明确任务倾向（非闲聊）时注入，且只注入命中项，避免每次堆一堆模板。
    try:
        if not is_chitchat:
            from ..skills import load_catalog, match_skills

            skills = load_catalog()
            matched = match_skills(text, skills)
            if matched:
                skill_texts = []
                for s in matched:
                    skill_texts.append(
                        f"【技能：{s.name}】{s.description}\n{s.content}"
                    )
                messages.append({
                    "role": "system",
                    "content": (
                        "你发现用户这次的请求适合用以下技能来完成，按技能指导办事：\n\n"
                        + "\n\n---\n\n".join(skill_texts)
                        + "\n\n技能是干活的方法指导，不是要你说出来的话——"
                        "用它的方式完成用户请求，但语气仍是你的自然风格。"
                    ),
                })
    except Exception:
        logger.exception("[pipeline] 技能注入失败（不影响回复）")

    # 思考/话题/生图三类 system 提示统一在 user 之前注入，保证「user 是最后一条」。
    # 若放在 user 之后追加，普通流式路径会形成 user→system 的非法顺序（多数 LLM API
    # 要求消息以 user/assistant 结尾），与第 5 段注释「user 必须最后」矛盾。
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

    reply_model = (
        _cfg.llm_model_strong
        if _cfg.llm_model_strong and not mock and _needs_strong_model(text)
        else None
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
        raw = await run_tool_round(
            messages,
            chat=lambda ms: chat(ms, mock=mock),
            chat_native=lambda ms, tools: chat_native(ms, tools, mock=mock),
            max_loops=2,
            final_instruction=final_instruction,
            on_progress=progress_cb,
        )
        # 工具循环是整段返回，不经过 chat_stream，前端气泡会空窗到 done 帧才
        # 整段「哐」出来。这里在拿到最终文本后切片推一次 stream_cb，让工具类
        # 消息也享受打字机效果（与流式路径一致，推的都是 raw，最终 done 帧
        # 仍是后处理后的 reply，二者允许有差异）。
        if stream_cb is not None and not mock and raw:
            try:
                for i in range(0, len(raw), _STREAM_CHUNK):
                    await stream_cb(raw[i:i + _STREAM_CHUNK])
            except Exception:
                pass
    else:
        if stream_cb is not None and not mock:
            # 流式生成：逐块回调推送，同时累积完整文本用于后处理

            parts: list[str] = []
            async for piece in chat_stream(messages, model=reply_model):
                parts.append(piece)
                try:
                    await stream_cb(piece)
                except Exception:
                    pass  # 回调失败不中断生成
            raw = "".join(parts)
        else:
            raw = await chat(messages, mock=mock, model=reply_model)
    reply = strip_actions(_extract_reply(raw))
    reply = trim_farewell(text, reply)
    # 兜底：回复为空/只剩思考（LLM 输出异常）时，给一句不冷场的默认回复
    if not reply.strip():
        reply = "嗯……我想想怎么回你。"

    # 5.6) 重复回复检测：与最近几条菟菚回复高度相似时，重写一次（避免复读机）
    if not mock and reply.strip():
        try:
            recent = [
                m["content"]
                for m in db.recent_messages(user_id, _DUP_RECENT_N * 4)
                if m["role"] == "assistant"
            ][-_DUP_RECENT_N:]
            if recent and any(_too_similar(reply, r) for r in recent):
                logger.info("[pipeline] 检测到重复回复，重写一次")
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
                if stream_cb is not None and not mock:
                    # 流式：先让前端清空当前气泡，再重新流式生成
                    try:
                        await stream_cb(_RESET_MARK)
                    except Exception:
                        pass
                    parts2: list[str] = []
                    async for piece in chat_stream(messages, model=reply_model):
                        parts2.append(piece)
                        try:
                            await stream_cb(piece)
                        except Exception:
                            pass
                    raw2 = "".join(parts2)
                else:
                    raw2 = await chat(messages, mock=mock, model=reply_model)
                reply2 = strip_actions(_extract_reply(raw2))
                reply2 = trim_farewell(text, reply2)
                if reply2.strip():
                    reply = reply2
        except Exception:
            logger.exception("[pipeline] 重复回复检测失败（不影响回复）")

    # 5.9) 插件回复钩子（v2）：最终回复出口改写（敏感词过滤/自动翻译等）
    try:
        from ..plugins.context import apply_reply

        reply2 = apply_reply(reply)
        if reply2.strip():
            reply = reply2
    except Exception:
        pass

    # 5.10) 自制表情包：只在明显情绪场景下低频触发，优先复用收藏。
    # 用户明确要求画图时已有 drawn_image_path，不再叠第二张图片。
    sticker_path: str | None = None
    if not mock and image_cb is not None and drawn_image_path is None:
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

    # 5.11) 可解释性快照：只公开可验证的状态/行为/记忆来源，不公开隐藏思考。
    if explain_cb is not None and reply_state is not None and reply_frame is not None:
        try:
            from .explainability import build_reply_explanation

            memory_rows: list[tuple[str, object]] = []
            memory_rows.extend(("相关对话", value) for value in remembered[:2])
            memory_rows.extend(("长期事实", value) for value in facts[:2])
            memory_rows.extend(
                ("知识库", f"《{h['filename']}》相关段落" if h.get("filename") else "相关段落")
                for h in kb_hits[:2]
            )
            memory_rows.extend(
                ("结构化记忆", f"{item[0]} {item[2]} {item[3]}")
                for item in triples[:2]
                if len(item) >= 4
            )
            snapshot = build_reply_explanation(
                reply_state,
                reply_frame,
                memory_rows=memory_rows,
                search_used=bool(search_hits),
                media=("generated_image" if drawn_image_path else "sticker" if sticker_path else "none"),
            )
            await explain_cb(snapshot)
        except Exception:
            logger.exception("[pipeline] 回复解释快照失败（不影响回复）")

    # 6) 存档（user 消息已在 1.0 存档，这里只补 assistant 回复）
    db.add_message(user_id, "assistant", reply)
    lm1_id = db.add_long_memory(user_id, f"用户说：{text}")
    lm2_id = db.add_long_memory(user_id, f"菟菚说：{reply}")
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
        pass
    return reply
