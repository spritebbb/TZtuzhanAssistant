# -*- coding: utf-8 -*-
"""P0-04 C2 双轨接线回归：中英文分流路由、Tavily 解析与空结果回退。

路由依据双轨实测（各 10 条，2026-09-12）：博查中文查询返回中文源且平均
0.27s；Tavily 英文查询返回英文源但平均 2.39s，且对中文查询会返回英文厂商
文档、博查对英文查询会退化成中文站；两者域名重合度约 0.02。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_search_route_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import search as s  # noqa: E402


def _with_keys(bocha: bool, tavily: bool):
    """临时设置两把 key（配置对象是单例，改 attributes）。"""
    from backend.core.config import config

    return patch.multiple(
        config,
        search_api_key=("sk-test" if bocha else ""),
        tavily_api_key=("tvly-test" if tavily else ""),
    )


def test_provider_order_language_split() -> None:
    with _with_keys(bocha=True, tavily=True):
        # 中文查询：博查优先，Tavily 第二，免 key 引擎兜底
        assert s._provider_order("什么是向量数据库", "bing") == [
            "bocha", "tavily", "bing", "ddg"
        ]
        # 非中文查询：Tavily 优先，博查第二
        assert s._provider_order("What is a vector database", "bing") == [
            "tavily", "bocha", "bing", "ddg"
        ]
        # SEARCH_ENGINE 的既有语义保留：显式 ddg 时免 key 兜底顺序反转
        assert s._provider_order("中文查询", "ddg")[2:] == ["ddg", "bing"]
        # 日文/韩文同属 CJK，走中文轨
        assert s._provider_order("ベクトルデータベースとは", "bing")[0] == "bocha"

    # 只配一把：不引入另一轨，默认行为与接线前一致
    with _with_keys(bocha=True, tavily=False):
        assert s._provider_order("What is RAG", "bing") == ["bocha", "bing", "ddg"]
    with _with_keys(bocha=False, tavily=True):
        assert s._provider_order("什么是 RAG", "bing") == ["tavily", "bing", "ddg"]
    with _with_keys(bocha=False, tavily=False):
        assert s._provider_order("what is rag", "bing") == ["bing", "ddg"]
        assert s._provider_order("什么是 rag", "ddg") == ["ddg", "bing"]
    print("[OK] 路由：中文走博查、非中文走 Tavily、单 key 与无 key 行为不变")


def test_tavily_parse_and_empty_is_failure() -> None:
    class _Resp:
        def __init__(self, payload: dict):
            self._body = json.dumps(payload).encode("utf-8")

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    payload = {
        "results": [
            {"title": "RAG explained", "content": "retrieval augmented generation",
             "url": "https://example.com/rag"},
            {"title": "", "content": "", "url": "https://example.com/empty-title"},
        ]
    }
    with _with_keys(bocha=False, tavily=True), \
            patch("urllib.request.urlopen", new=lambda req, timeout=25: _Resp(payload)):
        results, err = s._tavily_search("what is rag", 5)
    assert err == "", err
    assert results[0] == {
        "title": "RAG explained",
        "snippet": "retrieval augmented generation",
        "url": "https://example.com/rag",
    }, results[0]
    assert len(results) == 2, results

    # 实测见过 HTTP 200 但 results 为空：必须当失败，好让路由继续回退下一引擎
    with _with_keys(bocha=False, tavily=True), \
            patch("urllib.request.urlopen", new=lambda req, timeout=25: _Resp({"results": []})):
        results, err = s._tavily_search("what is rag", 5)
    assert results == [] and err == "无结果", (results, err)

    # 未配 key 时不发请求，报缺失
    with _with_keys(bocha=False, tavily=False):
        results, err = s._tavily_search("what is rag", 5)
    assert results == [] and "未配置" in err, (results, err)
    print("[OK] Tavily 解析：字段映射、空结果当失败、缺 key 不联网")


def test_empty_track_falls_back_to_next_engine() -> None:
    """Tavily 返回空必须继续回退到下一引擎，而不是把空当成「没有结果」。"""
    calls: list[str] = []

    def fake_tavily(query, max_results):
        calls.append("tavily")
        return [], "无结果"

    def fake_bing(query, max_results):
        calls.append("bing")
        return [{"title": "兜底结果", "snippet": "s", "url": "https://example.com/x"}], ""

    with _with_keys(bocha=False, tavily=True), \
            patch.object(s, "_tavily_search", new=fake_tavily), \
            patch.object(s, "_bing_search", new=fake_bing):
        results = s.web_search("what is rag", 5, force_refresh=True)
    assert calls == ["tavily", "bing"], calls
    assert results and results[0]["provider"] == "bing", results
    print("[OK] 回退：Tavily 空结果后继续用 bing，并如实标记 provider")


def test_cjk_detection_edges() -> None:
    assert s._has_cjk("你好") is True
    assert s._has_cjk("hello 世界") is True
    assert s._has_cjk("hello world") is False
    assert s._has_cjk("12345") is False
    print("[OK] CJK 判定边界")


def main() -> None:
    test_provider_order_language_split()
    test_tavily_parse_and_empty_is_failure()
    test_empty_track_falls_back_to_next_engine()
    test_cjk_detection_edges()
    print("\n=== P0-04 C2 双轨路由：全部通过 ===")


if __name__ == "__main__":
    main()
