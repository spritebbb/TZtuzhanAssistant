# -*- coding: utf-8 -*-
"""相对时间推理（批6e-#27，2026-09-30）：把「上周/前天/上个月」解析成绝对日期范围。

动机：用户说「上周我们聊的那个电影叫什么」，检索层拿到的 query 里「上周」只是
两个汉字——SQLite 的 ts 过滤和向量相似度都不认识它。解析成
2026-09-21 ~ 2026-09-27 之后，「按时间找对话/记忆」才有落点。

契约：
- 纯函数、零 IO、失败即空列表（宁可不解析，绝不猜错日期）；
- 只认白名单表达（见 _WEEKDAYS / 主词表），多义表达（「之前」「那天」）不解析；
- 同一文本命中多个表达时全部返回，由调用方决定取舍；
- 周以周一为一周之始（ISO 语义），「本周」含今天，「上周」永远不含今天。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

# 上周X / 周X 的 X 表（ISO：0=周一 … 6=周日）
_WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


@dataclass(frozen=True)
class TimeRange:
    label: str
    start: str  # ISO 日期
    end: str    # ISO 日期（含端点）


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def resolve_ranges(text: str, today: date | None = None) -> list[TimeRange]:
    """解析文本中的相对时间表达，返回命中的绝对日期范围（可能多个）。"""
    t = (text or "").strip()
    if not t:
        return []
    now = today or date.today()
    out: list[TimeRange] = []

    def add(label: str, start: date, end: date) -> None:
        out.append(TimeRange(label, start.isoformat(), end.isoformat()))

    mon = _monday(now)
    last_mon = mon - timedelta(days=7)

    # 单日表达（今天/昨天/前天/大前天）；「大前天」包含「前天」字串，命中前者跳过后者
    has_datian = "大前天" in t
    if has_datian:
        add("大前天", now - timedelta(days=3), now - timedelta(days=3))
    if "前天" in t and not has_datian:
        add("前天", now - timedelta(days=2), now - timedelta(days=2))
    if "昨天" in t:
        add("昨天", now - timedelta(days=1), now - timedelta(days=1))
    if "今天" in t:
        add("今天", now, now)

    # 上周X：上周一 ~ 上周日（单日）；命中时跳过整段「上周」（上周X ⊂ 上周）
    m = re.search(r"上[周礼拜]([一二三四五六日天])", t)
    if m:
        d = last_mon + timedelta(days=_WEEKDAYS[m.group(1)])
        add(f"上周{m.group(1)}", d, d)
    elif "上周" in t or "上礼拜" in t:
        add("上周", last_mon, last_mon + timedelta(days=6))
    if "本周" in t or "这周" in t or "这礼拜" in t:
        add("本周", mon, mon + timedelta(days=6))
    if "本周" in t or "这周" in t or "这礼拜" in t:
        add("本周", mon, mon + timedelta(days=6))

    # 月粒度（自然月：上月 1 日 ~ 月末）
    if "上个月" in t or "上月" in t:
        first_this = now.replace(day=1)
        last_month_end = first_this - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        add("上个月", last_month_start, last_month_end)
    if "这个月" in t or "本月" in t:
        add("本月", now.replace(day=1), now)

    # N 天前 / N 周前（半角数字，1~30 天 / 1~8 周防误解析大数）
    m = re.search(r"([0-9]{1,2})\s*天前", t)
    if m and 1 <= int(m.group(1)) <= 30:
        d = now - timedelta(days=int(m.group(1)))
        add(f"{m.group(1)}天前", d, d)
    m = re.search(r"([0-9]{1,2})\s*周前", t)
    if m and 1 <= int(m.group(1)) <= 8:
        end = now - timedelta(days=int(m.group(1)) * 7)
        add(f"{m.group(1)}周前", end, end)

    return out


__all__ = ["TimeRange", "resolve_ranges"]
