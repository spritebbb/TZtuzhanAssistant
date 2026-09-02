"""结构化事实记忆（五元组：主体-谓词-客体-类型）v2。

相对原 triple_memory.py 的增强：
- 提取走更强 fact_extractor（含置信度/类别）
- 检索走 Chroma 向量（语义）优先 + TF-IDF 兜底
- 存库时同时建向量索引（triples kind）
"""
import asyncio
import math
import re
from collections import Counter
from datetime import datetime

from ..log import logger
from ..userdb import db

_RETRIEVE_TOP_K = 6


def _tokenize(text: str) -> list[str]:
    """字符二元组特征。"""
    t = re.sub(r"\s+", "", text)
    if len(t) < 2:
        return list(t)
    return [t[i : i + 2] for i in range(len(t) - 1)]


def _tfidf_score(query_terms: list[str], docs: list[tuple[int, str]]) -> list[tuple[float, int]]:
    """对候选文档做 TF-IDF 余弦相似度排序，返回 (score, doc_id) 列表。"""
    if not query_terms or not docs:
        return []
    doc_texts = [d[1] for d in docs]
    doc_tf = [Counter(_tokenize(d)) for d in doc_texts]
    df = Counter()
    for tf in doc_tf:
        for term in tf:
            df[term] += 1
    n_docs = max(1, len(docs))
    idf = {term: math.log((1 + n_docs) / (1 + df[term])) + 1 for term in df}

    q_tf = Counter(query_terms)
    q_vec = {t: (q_tf[t] * idf.get(t, 1)) for t in q_tf}
    q_norm = math.sqrt(sum(v * v for v in q_vec.values())) or 1

    scored: list[tuple[float, int]] = []
    for (doc_id, text), tf in zip(docs, doc_tf):
        if not tf:
            continue
        d_norm = math.sqrt(sum((cnt * idf.get(term, 1)) ** 2 for term, cnt in tf.items())) or 1
        dot = sum(q_tf[term] * idf.get(term, 1) * tf[term] for term in q_tf if term in tf)
        cos = dot / (q_norm * d_norm)
        scored.append((cos, doc_id))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _parse_triples(text: str) -> list[list[str]]:
    """解析 LLM 返回的 JSON 五元组数组（兼容 5 字段旧格式）。"""
    import json

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    result = None
    try:
        result = json.loads(cleaned)
    except Exception:
        m = re.search(r"\[.*\]", cleaned, re.DOTALL)
        if m:
            try:
                result = json.loads(m.group())
            except Exception:
                return []
    if not isinstance(result, list):
        return []
    valid = []
    for item in result:
        if isinstance(item, list) and len(item) >= 5 and all(isinstance(x, str) for x in item[:5]):
            valid.append([x.strip() for x in item[:5]])
    return valid[:32]


async def extract_triples(text: str, *, mock: bool = False) -> list[list[str]]:
    """从文本中提取结构化五元组（走更强 fact_extractor，向下兼容 5 字段返回）。"""
    from .fact_extractor import extract_triples as _extract

    triples = await _extract(text, mock=mock)
    # 兼容：转成 5 字段 [sub, st, pred, obj, ot]
    out = []
    for t in triples:
        if len(t) >= 5:
            out.append(t[:5])
    return out


def save_triples(user_id: str, triples: list[list[str]], source_msg: str = "") -> int:
    """存五元组到数据库（去重），并同步建向量索引。返回新插入数。"""
    now = datetime.now().isoformat(timespec="seconds")
    count = 0
    inserted_ids: list[int] = []
    for t in triples:
        if len(t) < 5:
            continue
        sub, st, pred, obj, ot = t[:5]
        dup = db.conn.execute(
            "SELECT id FROM triples WHERE user_id=? AND subject=? AND predicate=? AND object=?",
            (user_id, sub, pred, obj),
        ).fetchone()
        if dup:
            continue
        cur = db.conn.execute(
            "INSERT INTO triples (user_id, subject, subject_type, predicate, object, object_type, source_msg, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (user_id, sub, st, pred, obj, ot, source_msg[:200], now),
        )
        inserted_ids.append(cur.lastrowid)
        count += 1
    db.conn.commit()
    # 异步建向量索引（triples kind）
    if inserted_ids:
        import asyncio as _asyncio
        from . import vector_store as vec

        def _idx():
            for rid in inserted_ids:
                row = db.conn.execute(
                    "SELECT subject, predicate, object FROM triples WHERE id=?", (rid,)
                ).fetchone()
                if row:
                    vec.add(user_id, "triples", rid, f"{row['subject']} {row['predicate']} {row['object']}")
        try:
            _asyncio.ensure_future(_asyncio.to_thread(_idx))
        except Exception:
            pass
    return count


def query_triples(user_id: str, query: str, top_k: int = _RETRIEVE_TOP_K) -> list[tuple[str, str, str, str, str]]:
    """检索与 query 相关的五元组。向量语义优先，TF-IDF 兜底。"""
    rows = db.conn.execute(
        "SELECT id, subject, subject_type, predicate, object, object_type FROM triples WHERE user_id=?",
        (user_id,),
    ).fetchall()
    if not rows:
        return []

    id_to_row = {r["id"]: r for r in rows}
    scored_ids: list[int] = []
    # ① Chroma 向量语义召回
    try:
        from . import vector_store as vec

        hits = vec.search(user_id, query, top_k, "triples")
        for h in hits:
            if h.record_id in id_to_row and h.record_id not in scored_ids:
                scored_ids.append(h.record_id)
    except Exception:
        pass
    # ② TF-IDF 补充
    docs = [(r["id"], f"{r['subject']} {r['predicate']} {r['object']}") for r in rows]
    tokens = list(set(_tokenize(query) + [t for t in re.split(r"[\s,，。！？、]", query) if len(t) > 1]))
    for _, doc_id in _tfidf_score(tokens, docs):
        if doc_id not in scored_ids:
            scored_ids.append(doc_id)
        if len(scored_ids) >= top_k * 2:
            break

    results = []
    seen = set()
    for rid in scored_ids[: top_k * 2]:
        r = id_to_row.get(rid)
        if not r:
            continue
        key = (r["subject"], r["predicate"], r["object"])
        if key not in seen:
            seen.add(key)
            results.append((r["subject"], r["subject_type"], r["predicate"], r["object"], r["object_type"]))
        if len(results) >= top_k:
            break
    return results


def format_triples(triples: list[tuple[str, str, str, str, str]]) -> str:
    """格式化为注入文本。"""
    if not triples:
        return ""
    lines = []
    for s, st, p, o, ot in triples:
        lines.append(f"{s}({st}) —[{p}]→ {o}({ot})")
    return "你记得的这些关于对方的事实：\n" + "\n".join(lines)