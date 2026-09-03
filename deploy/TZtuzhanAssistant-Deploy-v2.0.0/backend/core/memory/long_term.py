"""长期记忆检索：三路融合（Chroma 向量 + TF-IDF 稀疏 + Mem0 管理记忆）。

替代原 memory.py 的 recall/recall_facts：
- 写入侧：每条 long_memory/facts 同时落 SQLite（保持既有接口）+ Chroma 向量
- 检索侧：疑似回忆时做 LLM 查询扩展，然后
  ① Chroma 向量语义召回（BGE-M3 本地，主路）
  ② TF-IDF 字符二元组稀疏召回（原逻辑保留，作兜底）
  ③ Mem0 管理记忆召回（偏好/画像/关系，交叉补充）
  三者融合去重，按相关度排序返回 top_k 文本片段。
"""
import asyncio
import json
import math
import re
from collections import Counter

from ..config import config
from ..llm import chat
from ..log import logger
from ..userdb import db

LONG_TERM_TOP_K = 3

# 疑似回忆触发词：命中才做 LLM 查询扩展（省一次 LLM 调用）
_RECALL_HINTS = (
    "上次", "之前", "以前", "还记得", "记得吗", "那天", "昨天", "刚才",
    "我说过", "你答应", "我们说好", "你不是说", "你不是答应", "老地方", "那个",
)


def looks_like_recall(text: str) -> bool:
    """判断这句是否在翻旧账/回忆以前的事（决定要不要做语义扩展）。"""
    return any(w in text for w in _RECALL_HINTS)


async def expand_query(user_id: str, query: str, *, mock: bool = False) -> list[str]:
    """把用户问题扩展成几个检索关键词/短语（语义检索的召回来源）。

    只取 2-6 个实体词（人名/物品/地点/事件/喜好），不保留疑问词和口语虚词。
    mock=True 或 LLM 失败时退化为原句，保证功能可用。
    """
    if not config.memory_semantic:
        return [query]
    if mock:
        return [query]
    prompt = (
        "你是记忆检索助手。用户问了一个问题（可能是在回忆以前聊过的事）。"
        "请提取 2-6 个最适合去聊天记录里检索的『关键词/短语』，要具体（人名、物品、地点、事件、喜好、约定等），"
        "不要疑问词、不要语气词、不要整句复述。尤其注意：把问题里的『核心实体词』抽出来"
        "（比如『你还记得我上次说喜欢什么天气吗』→『下雨天』『晴天』，而不是『什么天气』），"
        "去掉『什么/怎么/哪/记得/上次/说』这类虚词和疑问词。只输出 JSON 数组字符串，"
        "如 [\"养猫\",\"猫粮\",\"布偶\"]，不要其他文字。\n"
        f"用户的问题：{query}"
    )
    try:
        resp = await chat(
            [{"role": "system", "content": "只输出 JSON 数组，不要任何解释。"}, {"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=200,
        )
        cleaned = resp.strip().strip("`").strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
        terms = json.loads(cleaned)
        if isinstance(terms, list) and terms:
            return [str(t) for t in terms if str(t).strip()]
    except Exception:
        logger.warning("[记忆] 查询扩展失败，退化为原句检索：{}", query)
    return [query]


# ---- TF-IDF 稀疏向量检索（真向量召回，替代纯 bigram 重叠打分）----
# 中文没有空格分词，这里用「字符二元组」做特征（对中文记忆检索够用且无依赖）。


def _tokenize(text: str) -> list[str]:
    """把文本切成字符二元组特征。"""
    t = re.sub(r"\s+", "", text)
    if len(t) < 2:
        return list(t)
    return [t[i : i + 2] for i in range(len(t) - 1)]


def _tfidf_candidates(
    query_terms: list[str],
    docs: list[str],
    top_k: int,
) -> list[tuple[float, str]]:
    """对候选文档做 TF-IDF 余弦相似度排序，返回 (score, doc) 列表。"""
    if not query_terms:
        return []
    doc_tf: list[Counter] = [Counter(_tokenize(d)) for d in docs]
    df: Counter = Counter()
    for tf in doc_tf:
        for term in tf:
            df[term] += 1
    n_docs = max(1, len(docs))
    idf = {term: math.log((1 + n_docs) / (1 + df[term])) + 1 for term in df}

    q_tf = Counter(query_terms)
    q_vec = {t: (q_tf[t] * idf.get(t, 1)) for t in q_tf}
    q_norm = math.sqrt(sum(v * v for v in q_vec.values())) or 1

    scored: list[tuple[float, str]] = []
    for doc, tf in zip(docs, doc_tf):
        if not tf:
            continue
        d_norm = math.sqrt(sum((cnt * idf.get(term, 1)) ** 2 for term, cnt in tf.items())) or 1
        dot = sum(q_tf[term] * idf.get(term, 1) * tf[term] for term in q_tf if term in tf)
        cos = dot / (q_norm * d_norm)
        length_penalty = 1.0 if len(doc) >= 4 else 0.6
        scored.append((cos * length_penalty, doc))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:top_k]


async def _with_expansion(
    user_id: str,
    query: str,
    *,
    kind: str,
    mock: bool = False,
) -> list[str]:
    """三路融合检索的实现（kind: 'lm' 长记忆原文 / 'facts' 事实）。"""
    terms = await expand_query(user_id, query, mock=mock)
    # 原句始终参与检索：扩展词可能抽不准（含虚词/未命中实体），原句 bigram 是可靠兜底
    if query.strip() and query.strip() not in terms:
        terms = [query.strip()] + terms
    query_terms = _tokenize(query) + [t for t in terms if len(t) > 1]

    # ① TF-IDF 稀疏召回（候选放宽到 20 再重排）
    if kind == "lm":
        all_rows = db.search_long_memory_multi(user_id, terms, 20)
    else:
        candidates_all: list[str] = []
        for t in terms:
            for h in db.search_facts(user_id, t, 10):
                if h["content"] not in candidates_all:
                    candidates_all.append(h["content"])
        all_rows = [{"content": c} for c in candidates_all]
    candidates = [h["content"] for h in all_rows]
    scored = _tfidf_candidates(query_terms, candidates, LONG_TERM_TOP_K * 2) if candidates else []
    tfidf_result = [doc for _, doc in scored]

    # ② Chroma 向量语义召回（主路，质量优先）
    vec_docs: list[str] = []
    try:
        from . import vector_store as vec

        hits = await asyncio.to_thread(vec.search, user_id, query, LONG_TERM_TOP_K, kind)
        for h in hits:
            if h.text and h.text not in vec_docs:
                vec_docs.append(h.text)
    except Exception:
        logger.warning("[记忆] 向量检索失败，仅用 TF-IDF")

    # ③ Mem0 管理记忆召回（事实/画像层面交叉补充）
    try:
        if config.memory_mem0:
            from .memory_manager import manager

            mem0_hits = await asyncio.to_thread(manager.search, user_id, query, LONG_TERM_TOP_K)
            for m in mem0_hits:
                t = (m.get("text") or "").strip()
                if t and t not in vec_docs and t not in tfidf_result:
                    vec_docs.append(t)
    except Exception:
        pass

    # 融合：向量/管理记忆在前（语义），TF-IDF 补充（去重）
    fused: list[str] = []
    seen: set[str] = set()
    for doc in vec_docs + tfidf_result:
        if doc not in seen:
            seen.add(doc)
            fused.append(doc)
    return fused[:LONG_TERM_TOP_K]


async def recall(user_id: str, query: str, *, mock: bool = False) -> list[str]:
    """长期记忆检索（对话原文片段）。接口签名与旧版一致。"""
    if looks_like_recall(query):
        return await _with_expansion(user_id, query, kind="lm", mock=mock)
    # 非疑似回忆：先 TF-IDF 快查，再补向量
    base = [h["content"] for h in db.search_long_memory(user_id, query, LONG_TERM_TOP_K)]
    try:
        from . import vector_store as vec

        hits = await asyncio.to_thread(vec.search, user_id, query, LONG_TERM_TOP_K, "lm")
        for h in hits:
            if h.text and h.text not in base:
                base.append(h.text)
    except Exception:
        pass
    return base[:LONG_TERM_TOP_K]


async def recall_facts(user_id: str, query: str, *, mock: bool = False) -> list[str]:
    """检索 LLM 提炼的长期事实（用户喜好/约定等）。"""
    if looks_like_recall(query):
        return await _with_expansion(user_id, query, kind="facts", mock=mock)
    base = [h["content"] for h in db.search_facts(user_id, query, LONG_TERM_TOP_K)]
    try:
        from . import vector_store as vec

        hits = await asyncio.to_thread(vec.search, user_id, query, LONG_TERM_TOP_K, "facts")
        for h in hits:
            if h.text and h.text not in base:
                base.append(h.text)
    except Exception:
        pass
    return base[:LONG_TERM_TOP_K]
