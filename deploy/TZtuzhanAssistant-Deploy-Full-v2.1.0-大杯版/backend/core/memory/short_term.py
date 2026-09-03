"""短期上下文：最近 N 轮对话，作为 chat 的 messages 上下文。"""
from ..userdb import db

_SHORT_TERM_LIMIT = 30


def short_term_messages(user_id: str) -> list[dict]:
    """最近 N 轮对话，作为 chat 的 messages 上下文。"""
    rows = db.recent_messages(user_id, _SHORT_TERM_LIMIT)
    return [{"role": r["role"], "content": r["content"]} for r in rows]
