# -*- coding: utf-8 -*-
"""D4 纪念册导出：归档 → HTML 渲染（转义/图片白名单/打印友好/404）。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 数据目录隔离：先于任何 backend import 生效，避免读写真实 sessions.db
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_keepsake_"))

from fastapi.testclient import TestClient

from backend.api.keepsake import render_keepsake
from backend.session import store


async def _seed_archive() -> str:
    await store.append_messages(store.CURRENT_SESSION_ID, [
        {"role": "user", "content": "今天去了温室，给你拍了张照片", "ts": time.time() - 60},
        {"role": "bot", "content": "哦？拍得怎么样\n先发来看看", "ts": time.time() - 50, "image": "/api/images/gen_abc123.png"},
        {"role": "user", "content": "<script>alert(1)</script> 嘿嘿", "ts": time.time() - 40},
    ])
    result = await store.archive_current()
    assert result, "归档失败"
    return result["id"]


def test_render_escapes_and_filters() -> None:
    page = render_keepsake("测试<标题>", 1757000000, [
        {"role": "user", "content": "<b>粗体</b>不该生效"},
        {"role": "bot", "content": "第一行\n第二行", "image": "/api/images/gen_ok.png"},
        {"role": "bot", "content": "坏图", "image": "https://evil.com/x.png"},
        {"role": "bot", "content": "路径穿越", "image": "/api/images/../secret"},
    ])
    assert "测试&lt;标题&gt;" in page, "标题未转义"
    assert "&lt;b&gt;粗体&lt;/b&gt;" in page, "消息内容未转义"
    assert "第一行<br>第二行" in page, "换行未转 <br>"
    assert "/api/images/gen_ok.png" in page, "合法图片未渲染"
    assert "evil.com" not in page, "外链图片未拦截"
    assert "../secret" not in page, "路径穿越未拦截"
    print("[OK] 渲染：转义/换行/图片白名单")


def test_keepsake_endpoint() -> None:
    from backend.app import create_app

    archive_id = asyncio.run(_seed_archive())
    with TestClient(create_app()) as client:
        res = client.get(f"/api/keepsake/{archive_id}")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
        body = res.text
        assert "菟丝子研究所 · 纪念册" in body
        assert "今天去了温室" in body
        assert "&lt;script&gt;" in body and "<script>alert" not in body, "XSS 未转义"
        assert "/api/images/gen_abc123.png" in body
        assert "@media print" in body, "缺打印友好样式"
        assert client.get("/api/keepsake/no-such-id").status_code == 404
    print("[OK] 端点：200/HTML/XSS 转义/图片/打印样式/404")


def main() -> None:
    test_render_escapes_and_filters()
    test_keepsake_endpoint()
    print("\n=== D4 纪念册导出：全部通过 ===")


if __name__ == "__main__":
    main()
