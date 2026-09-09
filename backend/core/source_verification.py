# -*- coding: utf-8 -*-
"""P3-02B 动态 2+1 多源求证：来源规范化、独立性与冲突状态。"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .search import normalize_query, web_search

_TRACKING_KEYS = {"fbclid", "gclid", "ref", "ref_src", "source", "spm"}
_MULTIPART_SUFFIXES = {"co.uk", "com.cn", "com.au", "co.jp", "com.hk"}


def canonical_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return ""
        host = parts.hostname.lower().rstrip(".")
        if host.startswith("www."):
            host = host[4:]
        port = f":{parts.port}" if parts.port and parts.port not in {80, 443} else ""
        query = urlencode(sorted(
            (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_KEYS
        ))
        path = re.sub(r"/{2,}", "/", parts.path or "/").rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower(), host + port, path, query, ""))
    except (TypeError, ValueError):
        return ""


def site_domain(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    for prefix in ("www.", "m.", "mobile."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    suffix2 = ".".join(labels[-2:])
    return ".".join(labels[-3:]) if suffix2 in _MULTIPART_SUFFIXES else suffix2


def _text_fingerprint(title: str, snippet: str) -> str:
    text = normalize_query(re.sub(r"[^\w\u4e00-\u9fff]+", " ", f"{title} {snippet}"))
    return hashlib.sha256(text[:500].encode("utf-8")).hexdigest()


def _claim_key(hit: dict) -> tuple[str, str, str, str] | None:
    claim = hit.get("claim")
    if not isinstance(claim, dict):
        return None
    entity = normalize_query(str(claim.get("entity", "")))
    metric = normalize_query(str(claim.get("metric", "")))
    unit = normalize_query(str(claim.get("unit", "")))
    period = normalize_query(str(claim.get("period") or claim.get("as_of") or ""))
    if not entity or not metric or not unit:
        return None
    try:
        float(claim["value"])
    except (KeyError, TypeError, ValueError):
        return None
    return entity, metric, unit, period


def _comparison(evidence: list[dict]) -> tuple[str, list[str]]:
    groups: dict[tuple[str, str, str, str], list[tuple[str, float]]] = {}
    for item in evidence:
        key = _claim_key(item["raw"])
        if key is not None:
            groups.setdefault(key, []).append((item["id"], float(item["raw"]["claim"]["value"])))
    comparable = [values for values in groups.values() if len(values) >= 2]
    if not comparable:
        return "not_comparable", []
    conflicts: list[str] = []
    for values in comparable:
        baseline = values[0][1]
        tolerance = max(abs(baseline) * 0.005, 1e-9)
        if any(abs(value - baseline) > tolerance for _, value in values[1:]):
            conflicts.extend(item_id for item_id, _ in values)
    return ("conflict", list(dict.fromkeys(conflicts))) if conflicts else ("consistent", [])


def verify_search(
    query: str, *,
    search_fn: Callable = web_search,
    force_refresh: bool = False,
    judge: Callable[[list[dict]], dict] | None = None,
) -> dict:
    """搜索并输出有界证据报告；不保存查询或用户资料。"""
    normalized = normalize_query(query)
    if not normalized:
        return {"status": "failed", "query": "", "reason": "empty_query", "evidence": []}
    try:
        try:
            hits = search_fn(query, max_results=10, force_refresh=force_refresh)
        except TypeError:
            hits = search_fn(query, max_results=10)
    except Exception as exc:
        return {
            "status": "failed", "query": normalized,
            "reason": f"search_error:{type(exc).__name__}", "evidence": [],
        }
    if not hits:
        from .search import last_error

        return {
            "status": "failed" if last_error() else "insufficient",
            "query": normalized,
            "reason": last_error() or "no_results",
            "evidence": [],
        }

    candidates: list[dict] = []
    seen_urls: set[str] = set()
    seen_domains: set[str] = set()
    seen_text: set[str] = set()
    for hit in hits:
        url = canonical_url(str(hit.get("url", "")))
        domain = site_domain(url)
        title = str(hit.get("title", "")).strip()
        snippet = str(hit.get("snippet", "")).strip()
        fingerprint = _text_fingerprint(title, snippet)
        if not url or not domain or url in seen_urls or domain in seen_domains or fingerprint in seen_text:
            continue
        seen_urls.add(url)
        seen_domains.add(domain)
        seen_text.add(fingerprint)
        candidates.append({
            "id": f"E{len(candidates) + 1}",
            "title": title[:300],
            "snippet": snippet[:1000],
            "url": url,
            "domain": domain,
            "published_at": str(hit.get("published_at") or hit.get("date") or ""),
            "provider": str(hit.get("provider") or "search"),
            "cache_hit": bool(hit.get("cache_hit", False)),
            "raw": hit,
        })

    selected = candidates[:2]
    if len(selected) < 2:
        public = [{k: v for k, v in item.items() if k != "raw"} for item in selected]
        return {
            "status": "insufficient", "query": normalized,
            "reason": "fewer_than_two_independent_domains", "evidence": public,
            "agreement": "not_comparable", "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    agreement, conflict_ids = _comparison(selected)
    if judge is not None:
        try:
            judged = judge([{k: v for k, v in item.items() if k != "raw"} for item in selected])
            cited = set(judged.get("evidence_ids") or []) if isinstance(judged, dict) else set()
            valid_ids = {item["id"] for item in selected}
            if cited and cited <= valid_ids and judged.get("status") in {"consistent", "conflict", "not_comparable"}:
                agreement = judged["status"]
                conflict_ids = sorted(cited) if agreement == "conflict" else []
        except Exception:
            pass

    if agreement == "conflict" and len(candidates) >= 3:
        selected.append(candidates[2])
        agreement, conflict_ids = _comparison(selected)

    public = [{k: v for k, v in item.items() if k != "raw"} for item in selected]
    return {
        "status": "conflict" if agreement == "conflict" else "supported",
        "query": normalized,
        "reason": "conflicting_comparable_claims" if agreement == "conflict" else "",
        "agreement": agreement,
        "conflict_evidence_ids": conflict_ids,
        "evidence": public,
        "used_third_source": len(public) == 3,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def format_verification_context(report: dict) -> str:
    """给表达层的有来源材料；原始结果始终标为不可信引用。"""
    from .external_content import EXTERNAL_DATA_POLICY, wrap_untrusted

    status = str(report.get("status", "failed"))
    lines = [f"联网求证状态：{status}。"]
    if report.get("agreement") == "not_comparable":
        lines.append("来源彼此独立，但内容不是可直接对齐的同一指标，不要宣称它们数值一致。")
    if status == "conflict":
        lines.append("来源之间仍有冲突，回答时并列差异，不自行裁定。")
    elif status in {"failed", "insufficient"}:
        lines.append("证据不足，必须明确说尚未核实充分。")
    if any(item.get("cache_hit") for item in report.get("evidence", [])):
        lines.append("部分结果来自缓存，不要说成刚刚实时查到。")
    for item in report.get("evidence", []):
        source = f"{item.get('id', '')}@{item.get('domain', '')}"
        content = f"{item.get('title', '')}：{item.get('snippet', '')}（{item.get('url', '')}）"
        lines.append(wrap_untrusted("web", content, source=source))
    lines.append("只转述来源支持的内容，保留可点击链接。" + EXTERNAL_DATA_POLICY)
    return "\n".join(lines)
