"""联网搜索：给菟菚接一个信息检索能力（仅作参考，结果可能不准确）。

默认用 Bing（中国大陆可访问、无需 API key）；也可用 DuckDuckGo（SEARCH_ENGINE=ddg）。
失败或关闭时返回空列表，并通过 web_search.last_error 给出原因，便于排查。

带 TTL 缓存：相同 query 在缓存窗口内直接命中（节省 API 调用与网络等待）。
"""
import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request

from .config import config
from .log import logger

web_search_last_error = ""

# TTL 缓存：时效查询 2–5 分钟，普通查询最多 24 小时；上限 200 条防内存膨胀
_SEARCH_CACHE_TTL = 10 * 60  # 兼容旧测试/调用方；实际 TTL 由 cache_ttl_for 决定
_SEARCH_CACHE_MAX = 200
_search_cache: dict[str, tuple[float, list[dict], float]] = {}


def last_error() -> str:
    """最近一次搜索失败的原因（供界面显示）。"""
    return web_search_last_error


def normalize_query(query: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", query).lower().split())


def cache_ttl_for(query: str) -> int:
    normalized = normalize_query(query)
    if any(word in normalized for word in ("价格", "多少钱", "汇率", "天气", "温度", "当前版本", "现在版本")):
        return 2 * 60
    if any(word in normalized for word in ("最新", "今天", "刚刚", "新闻", "发布", "现在")):
        return 5 * 60
    return 24 * 60 * 60


def _cache_get(query: str) -> list[dict] | None:
    hit = _search_cache.get(normalize_query(query))
    if hit is None:
        return None
    expire_ts, results, fetched_at = hit
    if time.time() > expire_ts:
        _search_cache.pop(normalize_query(query), None)
        return None
    return [{**item, "cache_hit": True, "fetched_at": fetched_at} for item in results]


def _cache_put(query: str, results: list[dict]) -> None:
    # 超上限时清掉最旧的一半（dict 保持插入序，先插入的先被弹出）
    if len(_search_cache) >= _SEARCH_CACHE_MAX:
        for old in list(_search_cache)[: _SEARCH_CACHE_MAX // 2]:
            _search_cache.pop(old, None)
    now = time.time()
    _search_cache[normalize_query(query)] = (
        now + cache_ttl_for(query),
        [{**item, "cache_hit": False, "fetched_at": now} for item in results],
        now,
    )


def web_search(query: str, max_results: int = 5, *, force_refresh: bool = False) -> list[dict]:
    """搜索并返回 [{title, snippet, url}]。失败时返回 [] 并在 last_error 记录原因。"""
    global web_search_last_error
    web_search_last_error = ""

    query = query.strip()
    if not query:
        web_search_last_error = "搜索词为空"
        return []

    if not config.search_enabled:
        web_search_last_error = "搜索已关闭（SEARCH_ENABLED=0）"
        return []

    # 命中缓存直接返回
    if not force_refresh:
        cached = _cache_get(query)
        if cached is not None:
            return cached[:max_results]

    engine = getattr(config, "search_engine", "bing")
    order = ["bing", "ddg"] if engine != "ddg" else ["ddg", "bing"]
    if getattr(config, "search_api_key", ""):
        order.insert(0, "bocha")

    errors = []
    for eng in order:
        if eng == "bocha":
            results, err = _bocha_search(query, max_results)
        elif eng == "bing":
            results, err = _bing_search(query, max_results)
        else:
            results, err = _ddg_search(query, max_results)
        if results:
            fetched_at = time.time()
            results = [
                {**item, "provider": eng, "cache_hit": False, "fetched_at": fetched_at}
                for item in results
            ]
            _cache_put(query, results)
            return results[:max_results]
        if err:
            errors.append(f"{eng}: {err}")
    web_search_last_error = "；".join(errors)
    logger.warning("[搜索] 全部引擎失败：{}（query={!r}）", web_search_last_error, query[:60])
    return []


def _bocha_search(query: str, max_results: int):
    """通过博查 AI 搜索（国内，需 API key，返回结构化结果）。返回 (results, error)。"""
    token = getattr(config, "search_api_key", "")
    if not token:
        return [], "未配置 SEARCH_API_KEY"
    url = "https://api.bochaai.com/v1/web-search"
    payload = json.dumps(
        {"query": query, "summary": False, "count": max(1, min(10, max_results))}
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return [], f"网络错误: {e}"

    if not isinstance(data, dict) or data.get("code") != 200:
        return [], f"API 返回异常: {data.get('msg') or data.get('message') or data}"

    pages = (data.get("data") or {}).get("webPages") or {}
    value = pages.get("value") or []
    results = []
    for r in value:
        results.append(
            {
                "title": r.get("name") or "",
                "snippet": r.get("snippet") or "",
                "url": r.get("url") or "",
            }
        )
    return results, ("" if results else "无结果")


def _bing_search(query: str, max_results: int, host: str = "www.bing.com"):
    """通过 Bing 网页搜索（国内可访问）。返回 (results, error)。"""
    url = f"https://{host}/search?q=" + urllib.parse.quote(query)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    try:
        html = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", "ignore")
    except Exception as e:
        # www 失败就换国内域名 cn.bing.com 再试一次
        if host == "www.bing.com":
            return _bing_search(query, max_results, host="cn.bing.com")
        return [], f"网络错误: {e}"

    results: list[dict] = []
    for block in re.findall(r'<li class="b_algo[^"]*".*?</li>', html, re.S)[:max_results]:
        # 2026-09：Bing 的 <a> 属性顺序变了（href 不再紧跟 <a>，前面有 target 等），
        # 旧正则要求 href 紧随其后导致全部解析失败、搜索静默返回空。改为顺序无关。
        m_title = re.search(
            r'<h2[^>]*>\s*<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', block, re.S
        )
        if not m_title:
            continue
        m_snippet = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        title = re.sub(r"<[^>]+>", "", m_title.group(2)).strip()
        snippet = re.sub(r"<[^>]+>", "", m_snippet.group(1)).strip() if m_snippet else ""
        results.append({"title": title, "snippet": snippet, "url": m_title.group(1)})

    return results, ("" if results else "解析到 0 条（Bing 可能返回了非结果页/验证页）")


def _ddg_search(query: str, max_results: int):
    """通过 DuckDuckGo 搜索（中国大陆可能不可用）。返回 (results, error)。"""
    text_fn = None
    proxy = getattr(config, "llm_proxy", "") or None
    try:
        from ddgs import DDGS

        text_fn = DDGS(proxy=proxy).text if proxy else DDGS().text
    except Exception:
        try:
            from duckduckgo_search import DDGS

            text_fn = DDGS().text
        except Exception:
            text_fn = None
    if text_fn is None:
        return [], "未安装 ddgs/duckduckgo_search"

    results: list[dict] = []
    try:
        for r in text_fn(query, max_results=max_results):
            results.append(
                {
                    "title": r.get("title", ""),
                    "snippet": r.get("body") or r.get("snippet") or "",
                    "url": r.get("href") or r.get("link") or "",
                }
            )
    except Exception as e:
        return [], f"网络错误: {e}"
    return results, "" if results else "解析到 0 条"
