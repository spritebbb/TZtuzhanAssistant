"""长会话压缩：超出消息数阈值时，把旧消息摘要成 6 分区结构化记忆。

保留原 memory.py 的压缩逻辑（6 分区、跨会话滚动继承、游标），
增强：
- 更强 LLM 提炼 prompt（更结构化、更多分区金属性）
- 压缩后同时写入 Chroma 向量库（summary kind），跨会话检索更准
- 与记忆引擎的 Mem0 协同
"""
import re
from datetime import datetime

from ..config import config
from ..llm import chat
from ..log import logger
from ..userdb import db

_SHORT_TERM_LIMIT = 30
COMPACT_TOTAL_TRIGGER = 60
COMPACT_KEEP_RECENT = 14
COMPACT_OLDER_LIMIT = 200

# 6 分区压缩：摘要按结构化分区输出，跨会话滚动继承时信息不丢失
COMPACT_SECTIONS = [
    "关键事实",
    "用户偏好",
    "重要决定",
    "待办事项",
    "背景信息",
    "最近状态",
]

_COMPACT_KV_KEY = "compact_summary"
_COMPACT_CURSOR_KEY = "compact_cursor"

# 压缩失败冷却：LLM 摘要失败后 cursor 不推进（下次重试语义），但若每条消息
# 都重试一次 LLM 压缩（关键路径同步 await，首字延迟+数秒），持续失败时开销
# 不可接受。冷却期内直接跳过，到点后允许重试。
_COMPACT_FAIL_COOLDOWN = 600.0  # 秒
_compact_fail_ts: dict[str, float] = {}  # user_id -> 上次失败时间（monotonic）

_PAREN_RE = re.compile(r"（[^）]*）|\([^)]*\)")


def _in_cooldown(user_id: str) -> bool:
    import time as _time

    last = _compact_fail_ts.get(user_id)
    return last is not None and (_time.monotonic() - last) < _COMPACT_FAIL_COOLDOWN


def _strip_parens(text: str) -> str:
    return _PAREN_RE.sub("", text).strip()


def message_count(user_id: str) -> int:
    """该用户总共的聊天消息数。"""
    row = db.conn.execute(
        "SELECT COUNT(*) AS c FROM messages WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row["c"] or 0


def save_compact_summary(user_id: str, summary: str) -> bool:
    """持久化 6 分区摘要到 kv_store。"""
    try:
        from ..userdb import kv_set

        kv_set(user_id, _COMPACT_KV_KEY, summary)
        return True
    except Exception:
        logger.warning("[记忆] 摘要持久化失败（不影响本次压缩）")
        return False


def load_compact_summary(user_id: str) -> str | None:
    """读取上次会话持久化的 6 分区摘要。"""
    try:
        from ..userdb import kv_get

        val = kv_get(user_id, _COMPACT_KV_KEY)
        return val or None
    except Exception:
        return None


def _format_section_summary(data: dict) -> str:
    """把 LLM 返回的 6 分区 JSON 整理成注入文本。"""
    lines = []
    for sec in COMPACT_SECTIONS:
        val = (data.get(sec) or "").strip()
        if val:
            lines.append(f"【{sec}】{val}")
    return "\n".join(lines)


def _parse_compact_json(text: str) -> dict | None:
    """解析 6 分区摘要 JSON。"""
    import json as _json

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        obj = _json.loads(cleaned)
    except Exception:
        try:
            obj = _json.loads(cleaned + "}")
        except Exception:
            return None
    return obj if isinstance(obj, dict) else None


async def compact_context(user_id: str, *, mock: bool = False) -> tuple[str, list[dict]] | None:
    """长会话压缩：总消息很多时，把旧消息摘要成 6 分区结构化记忆。

    返回 (摘要, 最近完整消息)；返回 None 表示会话还不够长、无需压缩。
    成功后把摘要持久化到 kv_store + Chroma 向量库（跨会话滚动继承）。
    """
    try:
        total = message_count(user_id)
        if total < COMPACT_TOTAL_TRIGGER or _in_cooldown(user_id):
            return None
        rows = db.recent_messages_with_ids(user_id, _SHORT_TERM_LIMIT)
        recent_count = min(COMPACT_KEEP_RECENT, len(rows))
        try:
            from ..userdb import kv_get as _kv_get

            cursor = int(_kv_get(user_id, _COMPACT_CURSOR_KEY) or "0")
        except Exception:
            cursor = 0
        oldest_batch = db.messages_after(user_id, cursor, COMPACT_OLDER_LIMIT)
        keep_ids = {r["id"] for r in rows[-recent_count:]} if recent_count else set()
        old_rows = [r for r in oldest_batch if r["id"] not in keep_ids]
        if not old_rows:
            return None
        transcript = "\n".join(
            f"{'对方' if r['role'] == 'user' else '菟菚'}：{r['content'][:120]}"
            for r in old_rows
        )
        if not transcript.strip():
            return None
        if mock:
            data = {
                "关键事实": f"共 {len(old_rows)} 条旧对话被压缩",
                "用户偏好": "（示例）",
                "重要决定": "",
                "待办事项": "",
                "背景信息": "",
                "最近状态": "",
            }
            summary = _format_section_summary(data)
        else:
            prev = load_compact_summary(user_id)
            prev_block = (
                f"\n\n（更早一次会话继承下来的摘要，请把仍有效的内容合并进新摘要）\n{prev}"
                if prev
                else ""
            )
            summary = await chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是记忆整理助手。下面是一段 AI 女友和一个朋友的旧聊天记录（按时间顺序）。"
                            "请压缩成结构化摘要，只输出一个 JSON 对象，字段必须为以下 6 个（没有内容的字段给空字符串，"
                            "不要省略字段）：\n"
                            + "、".join(f"{s}" for s in COMPACT_SECTIONS)
                            + "\n\n要求：只保留有价值的信息（对方透露的喜好/习惯/经历/约定/家人朋友/情绪状态、"
                            "两人的关系进展与默契、对方说过要做或答应过的事）；丢掉闲聊废话、重复信息、比喻情绪。"
                            "每个字段用 1-2 句客观、紧凑的中文，第三人称。只输出 JSON，不要其他内容。"
                            "\n额外要求：『用户偏好』要具体（如『喜欢喝冰美式』而不是『有饮食偏好』）；"
                            "『关键事实』要可验证（如『养了一只三花猫叫团团』而不是『有宠物』）。"
                        ),
                    },
                    {"role": "user", "content": transcript + prev_block},
                ],
                temperature=0.3,
                max_tokens=400,
            )
            data = _parse_compact_json(summary)
            summary = _format_section_summary(data) if data else _strip_parens(summary).strip()
            if not summary:
                # LLM 返回了但解析不出有效摘要：视为失败，记冷却期（不推进 cursor）
                import time as _time

                _compact_fail_ts[user_id] = _time.monotonic()
                logger.warning("[记忆] 压缩摘要解析失败，{}s 内不再重试", int(_COMPACT_FAIL_COOLDOWN))
        if summary:
            saved = save_compact_summary(user_id, summary)
        else:
            saved = False
        if saved and old_rows:
            try:
                from ..userdb import kv_set as _kv_set

                _kv_set(user_id, _COMPACT_CURSOR_KEY, str(old_rows[-1]["id"]))
            except Exception:
                pass
        # 把摘要也写入 Chroma 向量库（summary kind），跨会话检索可用
        if summary:
            try:
                import asyncio as _asyncio
                from . import vector_store as vec

                _asyncio.ensure_future(
                    _asyncio.to_thread(vec.add, user_id, "summary", 0, summary, {"ts": datetime.now().isoformat()})
                )
            except Exception:
                pass
        keep = [{"role": r["role"], "content": r["content"]} for r in rows[-recent_count:]]
        return summary, keep
    except Exception:
        # LLM 调用/解析等失败：记冷却期，避免后续每条消息都同步重试一次压缩
        import time as _time

        _compact_fail_ts[user_id] = _time.monotonic()
        logger.exception("[记忆] 长会话压缩失败（{}s 内不重试），退化为原上下文",
                         int(_COMPACT_FAIL_COOLDOWN))
        return None