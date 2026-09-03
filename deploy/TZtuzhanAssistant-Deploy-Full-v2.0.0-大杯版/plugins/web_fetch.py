# -*- coding: utf-8 -*-
"""工具插件：网页抓取。"""
from __future__ import annotations

PLUGIN_META = {
    "name": "网页抓取",
    "version": "1.0.0",
    "description": "web_fetch 工具：抓取网页正文（SSRF 防护 + 大小上限）",
    "author": "tuzhan",
}

import asyncio
import re
import urllib.parse
import urllib.request

from backend.tools.base import ToolRegistry
from backend.tools.safety import check_url

_MAX_BYTES = 2 * 1024 * 1024  # 抓取大小上限（2MB），防异常大页面撑爆内存
_MAX_REDIRECTS = 5


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不自动跟随重定向：每一跳都显式复检目标 URL（防 302 → 内网 SSRF）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _fetch_sync(url: str) -> str:
    """同步抓取（由调用方放线程池，避免阻塞事件循环）。"""
    import urllib.error

    opener = urllib.request.build_opener(_NoRedirect)
    for _ in range(_MAX_REDIRECTS + 1):
        ok, err = check_url(url)
        if not ok:
            return err
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with opener.open(req, timeout=15) as resp:
                html = resp.read(_MAX_BYTES + 1)
        except urllib.error.HTTPError as e:
            # 不自动跟随的重定向会以 HTTPError(3xx) 抛给我们：取出 Location 复检后手动跳转
            if e.code in (301, 302, 303, 307, 308):
                loc = e.headers.get("Location")
                if not loc:
                    return "（重定向缺少 Location，已停止）"
                url = urllib.parse.urljoin(url, loc)
                continue
            return f"（抓取失败：HTTP {e.code}）"
        except Exception as e:
            return f"（抓取失败：{e}）"
        if len(html) > _MAX_BYTES:
            return "（页面超过 2MB，已拒绝抓取）"
        html = html.decode("utf-8", errors="replace")

        # 提取正文：有 <body> 取 body 内文本；没有则取整页文本
        body_start = html.lower().find("<body")
        body_end = html.lower().find("</body>")
        if body_start >= 0:
            if body_end > body_start:
                body = html[body_start:body_end]
            else:
                body = html[body_start:]
        else:
            body = html
        text = re.sub(r"<[^>]+>", " ", body)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 3000:
            text = text[:3000] + "...（已截断）"
        return text or "（页面内容为空）"
    return "（重定向次数过多，已停止）"


async def _web_fetch(url: str = "") -> str:
    """抓取网页正文内容。"""
    if not url:
        return "（缺少 URL）"
    try:
        return await asyncio.to_thread(_fetch_sync, url)
    except Exception as e:
        return f"（抓取失败：{e}）"


def register(ctx=None) -> None:
    ToolRegistry.register_func(
        name="web_fetch",
        description="抓取指定 URL 的网页内容（只读）",
        func=_web_fetch,
        owner="web_fetch",
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "目标网页 URL"}
            },
            "required": ["url"],
        },
    )
