# -*- coding: utf-8 -*-
"""P3-02B 动态 2+1 多源求证回归。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core.intent import requires_search
from backend.core.search import cache_ttl_for, normalize_query
from backend.core.source_verification import (
    canonical_url,
    format_verification_context,
    site_domain,
    verify_search,
)


def _search(hits):
    def run(query, max_results=10, force_refresh=False):
        return hits[:max_results]
    return run


def test_query_url_and_domain_normalization() -> None:
    assert normalize_query("  GPT６   最新 ") == "gpt6 最新"
    assert canonical_url("HTTPS://www.Example.com/a/?utm_source=x&b=2#part") == "https://example.com/a?b=2"
    assert site_domain("https://news.bbc.co.uk/story") == "bbc.co.uk"
    assert cache_ttl_for("今天气温") == 120
    assert cache_ttl_for("最新新闻") == 300
    assert cache_ttl_for("历史资料") == 86400


def test_two_independent_sources_and_repost_dedup() -> None:
    hits = [
        {"title": "版本发布", "snippet": "正式发布", "url": "https://a.example.com/1", "provider": "bing"},
        {"title": "另一页", "snippet": "同站说明", "url": "https://www.example.com/2", "provider": "ddg"},
        {"title": "版本发布", "snippet": "正式发布", "url": "https://mirror.test/repost", "provider": "bing"},
    ]
    report = verify_search("最新版本", search_fn=_search(hits))
    assert report["status"] == "insufficient"
    assert len(report["evidence"]) == 1, "同站子域及明显转载均不算独立来源"

    hits.append({
        "title": "官方说明", "snippet": "发布记录可查", "url": "https://official.test/release",
        "provider": "bing", "cache_hit": True,
    })
    report = verify_search("最新版本", search_fn=_search(hits))
    assert report["status"] == "supported" and len(report["evidence"]) == 2
    assert report["agreement"] == "not_comparable"
    context = format_verification_context(report)
    assert "https://" in context and 'untrusted_external kind="web"' in context
    assert "来自缓存" in context and "不要宣称它们数值一致" in context


def test_conflict_requests_third_source() -> None:
    base_claim = {"entity": "产品", "metric": "价格", "unit": "CNY", "period": "2026-09"}
    hits = [
        {"title": "来源一", "snippet": "价格100", "url": "https://one.test/a",
         "claim": {**base_claim, "value": 100}},
        {"title": "来源二", "snippet": "价格120", "url": "https://two.test/a",
         "claim": {**base_claim, "value": 120}},
        {"title": "来源三", "snippet": "价格120", "url": "https://three.test/a",
         "claim": {**base_claim, "value": 120}},
    ]
    report = verify_search("产品价格", search_fn=_search(hits))
    assert report["status"] == "conflict" and report["used_third_source"]
    assert len(report["evidence"]) == 3
    assert "并列差异" in format_verification_context(report)


def test_failure_and_time_sensitive_gate() -> None:
    def broken(query, max_results=10, force_refresh=False):
        raise TimeoutError("offline")

    report = verify_search("最新新闻", search_fn=broken)
    assert report["status"] == "failed" and report["reason"] == "search_error:TimeoutError"
    assert requires_search("这个软件目前是什么版本")
    assert requires_search("今天黄金多少钱")
    assert not requires_search("讲个睡前故事")


def main() -> None:
    test_query_url_and_domain_normalization()
    test_two_independent_sources_and_repost_dedup()
    test_conflict_requests_third_source()
    test_failure_and_time_sensitive_gate()
    print("[OK] P3-02B 查询时效、独立来源、转载去重、冲突第三源与失败状态")


if __name__ == "__main__":
    main()
