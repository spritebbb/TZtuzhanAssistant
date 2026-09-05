# -*- coding: utf-8 -*-
"""MEMORY_V2 本地只读验收：索引完整性、历史自检索、语义样例与资源占用。

不会写 SQLite/Chroma，不打印任何真实记忆文本。输出仅包含聚合指标。
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
from ctypes import wintypes
import json
import math
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core.config import config  # noqa: E402
from backend.core.memory import embedding, vector_store  # noqa: E402
from backend.core.memory.long_term import (  # noqa: E402
    _tfidf_candidates,
    _tokenize,
    recall,
    recall_facts,
)

_SOURCES = {
    "long_memory": ("lm", ""),
    "facts": (
        "facts",
        " WHERE status = 'active' AND surface_policy != 'never_surface' "
        "AND (expires_at IS NULL OR expires_at > strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime'))",
    ),
    "triples": ("triples", ""),
    "user_profile": ("profile", ""),
    "important_dates": ("topic", ""),
}

_SEMANTIC_CASES = (
    ("昨天工作时偷偷放松了一会儿", "上班摸鱼", ("认真加班到深夜", "周末去公园散步")),
    ("最近想换个地方住", "准备搬家", ("买了一盆绿植", "换了新的键盘")),
    ("阴沉的天气让我更舒服", "偏爱下雨天", ("讨厌堵车", "喜欢吃甜点")),
    ("家里的小动物今天又闯祸了", "养了一只猫", ("订了一张机票", "开始学吉他")),
    ("打算规律地活动身体", "准备坚持锻炼", ("想看一部电影", "收藏了一本小说")),
    ("最近夜里总是很难入睡", "这阵子失眠", ("早餐喝了牛奶", "下午开了会议")),
)

_MAX_WARMUP_SECONDS = 60.0
_MAX_RSS_DELTA_MB = 1024.0
_MAX_VECTOR_AVG_MS = 300.0
_MIN_SEMANTIC_RATE = 0.75


def _rss_mb() -> float | None:
    """当前进程工作集；Windows 用系统 API，其他平台尽力返回峰值 RSS。"""
    if os.name == "nt":
        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(Counters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        ok = psapi.GetProcessMemoryInfo(
            kernel32.GetCurrentProcess(),
            ctypes.byref(counters),
            counters.cb,
        )
        return round(counters.WorkingSetSize / 1024 / 1024, 1) if ok else None
    try:
        import resource

        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(rss / (1024 if sys.platform != "darwin" else 1024 * 1024), 1)
    except Exception:
        return None


def _kid(user_id: str, kind: str, record_id: int) -> str:
    return f"{user_id}|{kind}|{record_id}"


def audit_index(conn: sqlite3.Connection) -> dict:
    """逐 ID 校验 SQLite 源记录是否存在于对应向量分区。"""
    result: dict[str, dict] = {}
    for table, (kind, where) in _SOURCES.items():
        rows = conn.execute(f"SELECT user_id, id FROM {table}{where}").fetchall()
        expected = {_kid(str(row[0]), kind, int(row[1])) for row in rows}
        collection = vector_store._collection(kind)  # 只读诊断，复用生产 collection
        actual = set(collection.get(include=[]).get("ids", [])) if collection else set()
        peek = collection.peek(1) if collection and collection.count() else {}
        embeddings = peek.get("embeddings") if peek else None
        dimension = len(embeddings[0]) if embeddings is not None and len(embeddings) else 0
        result[table] = {
            "kind": kind,
            "expected": len(expected),
            "present": len(expected & actual),
            "missing": len(expected - actual),
            "dimension": dimension,
        }
    return result


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    norm = math.sqrt(sum(a * a for a in left) * sum(b * b for b in right)) or 1.0
    return dot / norm


def semantic_benchmark() -> dict:
    """不接触用户数据的释义样例：比较本地语义向量与词面二元组 Top-1。"""
    vector_hits = 0
    lexical_hits = 0
    started = time.perf_counter()
    for query, target, distractors in _SEMANTIC_CASES:
        # 目标故意不放第一位，避免全零同分时把顺序误当命中。
        docs = [distractors[0], target, distractors[1]]
        qvec = embedding.embed(query)
        vectors = [embedding.embed(item) for item in docs]
        if qvec and all(vectors):
            scores = [_cosine(qvec, item) for item in vectors if item is not None]
            if docs[max(range(len(scores)), key=scores.__getitem__)] == target:
                vector_hits += 1
        ranked = _tfidf_candidates(_tokenize(query), docs, 1)
        if ranked and ranked[0][1] == target:
            lexical_hits += 1
    elapsed = time.perf_counter() - started
    total = len(_SEMANTIC_CASES)
    return {
        "cases": total,
        "vector_top1": vector_hits,
        "lexical_top1": lexical_hits,
        "vector_rate": round(vector_hits / total, 3),
        "lexical_rate": round(lexical_hits / total, 3),
        "elapsed_seconds": round(elapsed, 3),
    }


def historical_self_recall(conn: sqlite3.Connection, per_kind: int) -> dict:
    """用真实记录自身作匿名查询，验证整条检索管线；只输出聚合命中率。"""
    specs = {
        "lm": ("long_memory", "content", ""),
        "facts": (
            "facts",
            "content",
            " WHERE status = 'active' AND surface_policy != 'never_surface' "
            "AND (expires_at IS NULL OR expires_at > strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime'))",
        ),
    }
    result: dict[str, dict] = {}
    for kind, (table, column, where) in specs.items():
        rows = conn.execute(
            f"SELECT user_id, id, {column} FROM {table}{where} "
            f"AND {column} != '' ORDER BY id DESC LIMIT ?"
            if where
            else f"SELECT user_id, id, {column} FROM {table} WHERE {column} != '' ORDER BY id DESC LIMIT ?",
            (per_kind,),
        ).fetchall()
        vector_hits = 0
        fused_hits = 0
        lexical_hits = 0
        vector_ms = 0.0
        lexical_ms = 0.0
        for user_id, _record_id, content in rows:
            started = time.perf_counter()
            hits = vector_store.search(str(user_id), str(content), 3, kind)
            vector_ms += (time.perf_counter() - started) * 1000
            # 同一内容可能被多次记录；产品层关心是否想起正确内容，不要求命中
            # 某个重复行的精确 ID。
            vector_hits += int(any(hit.text == content for hit in hits))

            fused = asyncio.run(
                recall(str(user_id), str(content), mock=True)
                if kind == "lm"
                else recall_facts(str(user_id), str(content), mock=True)
            )
            fused_hits += int(any(text == content for text in fused))

            started = time.perf_counter()
            if kind == "lm":
                candidates = conn.execute(
                    "SELECT id, content FROM long_memory WHERE user_id = ? ORDER BY id DESC LIMIT 500",
                    (user_id,),
                ).fetchall()
            else:
                candidates = conn.execute(
                    "SELECT id, content FROM facts WHERE user_id = ? AND status = 'active' "
                    "AND surface_policy != 'never_surface' ORDER BY id DESC LIMIT 500",
                    (user_id,),
                ).fetchall()
            docs = [str(row[1]) for row in candidates]
            ranked = _tfidf_candidates(_tokenize(str(content)), docs, 3)
            lexical_ms += (time.perf_counter() - started) * 1000
            lexical_hits += int(any(text == content for _, text in ranked))
        total = len(rows)
        result[kind] = {
            "cases": total,
            "vector_top3": vector_hits,
            "fused_top3": fused_hits,
            "lexical_top3": lexical_hits,
            "vector_rate": round(vector_hits / total, 3) if total else None,
            "fused_rate": round(fused_hits / total, 3) if total else None,
            "lexical_rate": round(lexical_hits / total, 3) if total else None,
            "vector_avg_ms": round(vector_ms / total, 1) if total else None,
            "lexical_avg_ms": round(lexical_ms / total, 1) if total else None,
        }
    return result


def evaluate(report: dict, *, model_required: bool) -> dict:
    """把验收门槛变成机器可判定结果，避免靠肉眼挑指标。"""
    checks = {
        "index_complete": all(item["missing"] == 0 for item in report["index"].values()),
        "model_mode": True,
        "warmup": True,
        "memory": True,
        "historical_not_worse": True,
        "hot_latency": True,
        "semantic_gain": True,
    }
    if model_required:
        runtime = report.get("runtime", {})
        checks["model_mode"] = str(runtime.get("mode", "")).startswith("model:")
        checks["warmup"] = float(runtime.get("warmup_seconds") or 10**9) <= _MAX_WARMUP_SECONDS
        rss_delta = runtime.get("rss_delta_mb")
        checks["memory"] = rss_delta is not None and float(rss_delta) <= _MAX_RSS_DELTA_MB
        for item in report.get("historical_self_recall", {}).values():
            checks["historical_not_worse"] &= item["fused_rate"] >= item["lexical_rate"]
            checks["hot_latency"] &= item["vector_avg_ms"] <= _MAX_VECTOR_AVG_MS
        semantic = report.get("semantic_cases", {})
        checks["semantic_gain"] = (
            semantic.get("vector_rate", 0) >= _MIN_SEMANTIC_RATE
            and semantic.get("vector_rate", 0) > semantic.get("lexical_rate", 0)
        )
    return {"passed": all(checks.values()), "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=20, help="每类历史自检索样本数")
    parser.add_argument("--skip-model", action="store_true", help="只做索引完整性，不加载模型")
    args = parser.parse_args()

    conn = sqlite3.connect(config.data_dir / "bot.db")
    try:
        report: dict = {
            "memory_v2": config.memory_v2,
            "index": audit_index(conn),
        }
        if not args.skip_model:
            rss_before = _rss_mb()
            started = time.perf_counter()
            mode = embedding.warmup()
            warmup_seconds = time.perf_counter() - started
            rss_after = _rss_mb()
            report["runtime"] = {
                "mode": mode,
                "warmup_seconds": round(warmup_seconds, 2),
                "rss_before_mb": rss_before,
                "rss_after_mb": rss_after,
                "rss_delta_mb": round(rss_after - rss_before, 1)
                if rss_before is not None and rss_after is not None
                else None,
            }
            report["historical_self_recall"] = historical_self_recall(
                conn, max(1, min(100, args.sample))
            )
            report["semantic_cases"] = semantic_benchmark()
        report["verdict"] = evaluate(report, model_required=not args.skip_model)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["verdict"]["passed"] else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
