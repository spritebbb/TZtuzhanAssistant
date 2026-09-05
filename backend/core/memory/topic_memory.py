"""话题记忆 v2：记住上次聊了什么，新会话能自然延续。

相对原 topic_memory.py 的增强：
- 提炼话题的同时写入 Chroma（topic kind），后续可语义检索话题
- 无额外 LLM 成本变化，逻辑保持简单
"""
from datetime import datetime

from ..llm import chat
from ..log import logger
from ..userdb import db

# 会话空闲判定：离上一条消息超过该分钟数，视为上一场聊完（可提炼话题）
TOPIC_IDLE_MINUTES = 30
# 提炼需要的最少消息条数
TOPIC_MIN_MESSAGES = 4

# topic 分区的 id 空间隔离：migration 会把 important_dates 表的行 id（自增小整数）
# 也写进同一 topic 分区；话题记忆若直接用消息表 id（同为自增小整数），
# 两套 id 会在「user|topic|N」键上撞车、向量互相覆盖（upsert 同 id 覆写）。
# 这里给话题向量加一个足够大的偏移基座，保证与 important_dates id 永不重叠。
_TOPIC_VEC_ID_BASE = 1_000_000_000


def _strip_parens(text: str) -> str:
    import re

    return re.sub(r"（[^）]*）|\([^)]*\)", "", text).strip()


def _kv_get(user_id: str, key: str) -> str | None:
    from ..userdb import kv_get

    return kv_get(user_id, key)


def _kv_set(user_id: str, key: str, value: str) -> None:
    from ..userdb import kv_set

    kv_set(user_id, key, value)


def last_topic(user_id: str) -> str | None:
    """读取上次提炼的话题（无则 None）。"""
    return _kv_get(user_id, "last_topic")


def last_topic_ts(user_id: str) -> str | None:
    return _kv_get(user_id, "last_topic_ts")


async def extract_topic(user_id: str, *, mock: bool = False) -> str | None:
    """把最近的对话提炼成一句话『上次聊到什么』；失败返回 None（不阻塞对话）。

    提炼成功后写入 kv_store + Chroma（topic kind）。
    """
    try:
        last_base = int(_kv_get(user_id, "last_topic_msg_id") or "0")
        rows = db.messages_after(user_id, last_base, 50)
        if len(rows) < TOPIC_MIN_MESSAGES:
            return None
        max_id = rows[-1]["id"]
        from ..persona_profiles import persona_name_for_user_id

        persona_name = persona_name_for_user_id(user_id)
        transcript = "\n".join(
            f"{'对方' if r['role'] == 'user' else persona_name}：{r['content'][:100]}"
            for r in rows[-TOPIC_MIN_MESSAGES:]
        )
        if mock:
            topic = "上次聊了一些日常"
        else:
            topic = await chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "下面是一段 AI 女友和一个朋友的聊天记录（按时间顺序）。"
                            "请用**一句话**概括『他们上次主要聊了什么/正聊到哪』，"
                            "像是给下一个会话准备的记忆便签。要求："
                            "①口语、自然（比如『他上次说想换台新电脑』『上次在聊周末要不要去看电影』）；"
                            "②突出还没聊完、可以接着聊的钩子；③不要复述对话、不要评价、不要『他们聊了』这种报告腔；"
                            "④只说最重要的一件/一个话题，不超过 40 字。"
                        ),
                    },
                    {"role": "user", "content": transcript},
                ],
                temperature=0.4,
                max_tokens=80,
            )
        topic = _strip_parens(topic).strip().strip("。")
        if not topic or len(topic) < 2:
            return None
        _kv_set(user_id, "last_topic", topic)
        _kv_set(user_id, "last_topic_ts", datetime.now().isoformat(timespec="seconds"))
        _kv_set(user_id, "last_topic_msg_id", str(max_id))
        # 写入 Chroma（topic kind）；id 加偏移基座与 important_dates 的 id 空间隔离
        try:
            import asyncio
            from . import vector_store as vec
            from .engine import _spawn

            _spawn(asyncio.to_thread(
                vec.add, user_id, "topic", _TOPIC_VEC_ID_BASE + max_id, topic
            ))
        except Exception:
            logger.warning("[话题记忆] 话题向量写入失败（不影响对话）")
        return topic
    except Exception:
        logger.exception("[话题记忆] 提炼失败（不影响对话）")
        return None


def build_continuation(user_id: str) -> str | None:
    """新会话开场时生成『接着上次』的提示文本；无话题返回 None。"""
    topic = last_topic(user_id)
    if not topic:
        return None
    ts = last_topic_ts(user_id)
    if ts:
        try:
            age_days = (datetime.now() - datetime.fromisoformat(ts)).days
            if age_days >= 3:
                return None
        except ValueError:
            pass
    return topic
