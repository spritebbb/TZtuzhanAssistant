# -*- coding: utf-8 -*-
"""搜索链路回归：Bing 新版页面解析（属性顺序无关）、失败可见性、缓存 TTL。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_search_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core.search import (  # noqa: E402
    _SEARCH_CACHE_TTL,
    _bing_search,
    web_search_last_error,
)

# 2026-09 真实 Bing 页面结构：<a> 的 href 前有 target 等属性，旧正则失配导致静默空结果
_BING_HTML = """
<li class="b_algo" data-id iid=SERP.5235><link rel="stylesheet" href="/rp/a.css"/>
<h2 class=""><a target="_blank" target="_blank" href="https://example.com/gpt6" h="ID=SERP,5127.2"><strong>ChatGPT</strong> 6 发布</a></h2>
<div class="b_caption"><p class="b_lineclamp2">2026 年 9 月 · OpenAI 正式发布 ChatGPT-6</p></div></li>
"""


def test_bing_parse_new_attribute_order():
    """新版属性顺序（href 前有 target）必须能解析出标题/链接/摘要。"""
    import unittest.mock as mock

    def fake_urlopen(req, timeout=10):
        import io

        return io.BytesIO(_BING_HTML.encode("utf-8"))

    with mock.patch("urllib.request.urlopen", new=fake_urlopen):
        results, err = _bing_search("ChatGPT-6 发布", 5)

    assert err == "" or results, f"解析失败: {err}"
    assert len(results) == 1
    assert results[0]["url"] == "https://example.com/gpt6"
    assert "6 发布" in results[0]["title"].replace("ChatGPT", "").strip() or "发布" in results[0]["title"]
    assert "ChatGPT-6" in results[0]["snippet"] or "发布" in results[0]["snippet"]
    print("[OK] Bing 解析：新版属性顺序可解析")


def test_cache_ttl_reasonable():
    """新闻类时效话题的缓存不能太长（曾 30 分钟，导致刚发布的消息被旧缓存盖住）。"""
    assert _SEARCH_CACHE_TTL <= 10 * 60, f"缓存 TTL 过长: {_SEARCH_CACHE_TTL}s"
    print(f"[OK] 缓存 TTL = {_SEARCH_CACHE_TTL}s（≤10 分钟）")


async def test_failure_message_visible():
    """全部引擎失败时 last_error 必须透传给模型（诚实降级，不伪装成「没有结果」）。"""
    from unittest.mock import patch

    import backend.core.search as s

    s.web_search_last_error = "bocha: HTTP 403"
    with patch("plugins.web_search.web_search", new=lambda q, max_results=5: []):
        from plugins.web_search import _web_search

        out = await _web_search("随便搜点什么")
    assert "搜索服务暂时不可用" in out, f"故障应如实透传给模型，实际: {out!r}"
    print("[OK] 失败可见性：引擎故障不再伪装成「没有结果」")


async def main() -> None:
    test_bing_parse_new_attribute_order()
    test_cache_ttl_reasonable()
    await test_failure_message_visible()
    print("搜索链路回归通过")


if __name__ == "__main__":
    asyncio.run(main())
