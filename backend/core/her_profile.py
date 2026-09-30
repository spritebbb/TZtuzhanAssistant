# -*- coding: utf-8 -*-
"""M4 双向了解：菟菚可以被了解的一面。

内容来自人格资源 ``backend/resources/personas/<人格id>/her_profile.json``
（schema=1，与 slices.json 同目录、随人格走）：她可以有自己的侧写，
其他人格也可以带自己的侧写文件。结构化展示、运行时不改写——
她是稳定的人：用户可以逐渐了解她的偏好、雷区和立场；这些是「她的」，
不会冒充用户的事实。增删条目改资源 JSON 即可（内容仍应与人格卡保持一致）。
"""
from __future__ import annotations

import json
from pathlib import Path

from .log import logger

_ROOT = Path(__file__).resolve().parents[1] / "resources" / "personas"

# 缓存（人格id → (mtime, sections)）；手改资源文件无需重启进程
_cache: dict[str, tuple[float, list[dict]]] = {}


def _load_sections(profile_id: str) -> list[dict] | None:
    path = _ROOT / profile_id / "her_profile.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    hit = _cache.get(profile_id)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("format_version") != 1:
            raise ValueError("her_profile 资源必须是 format_version=1 的 JSON 对象")
        raw = data.get("sections")
        if not isinstance(raw, list):
            raise ValueError("sections 必须是数组")
        sections: list[dict] = []
        for sec in raw:
            if (
                not isinstance(sec, dict)
                or not isinstance(sec.get("key"), str)
                or not isinstance(sec.get("label"), str)
                or not isinstance(sec.get("items"), list)
                or not all(isinstance(i, str) for i in sec["items"])
            ):
                raise ValueError("section 结构不合法（key/label/items 字符串数组）")
            sections.append({"key": sec["key"], "label": sec["label"],
                             "items": list(sec["items"])})
    except (ValueError, OSError, json.JSONDecodeError) as e:
        # fail-soft：资源坏了管理页显示空侧写，不拖垮接口
        logger.warning("[侧写] her_profile 资源加载失败（{}）：{}", profile_id, str(e)[:120])
        return None
    _cache[profile_id] = (mtime, sections)
    return sections


def her_profile() -> list[dict]:
    """返回当前人格的结构化「她的侧面」，供管理页展示；顺序稳定。

    资源不存在（多数非默认人格）返回空列表——不把菟菚的性格安到别人头上。
    """
    from .persona_profiles import active_id

    sections = _load_sections(active_id())
    return sections if sections else []
