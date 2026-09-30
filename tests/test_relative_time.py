# -*- coding: utf-8 -*-
"""批6e-#27 相对时间推理回归：白名单表达 → 绝对日期范围。

固定 today=2026-09-30（周三）断言，纯函数零 IO。
ISO 语义：周一为一周之始——2026-09-30 所在周：09-28(一) ~ 10-04(日)；
上周：09-21(一) ~ 09-27(日)；上个月：09-01 ~ 09-30。
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tz_reltime_"))

from backend.core.relative_time import resolve_ranges  # noqa: E402

TODAY = date(2026, 9, 30)  # 周三


def _labels(text: str) -> list[str]:
    return [r.label for r in resolve_ranges(text, TODAY)]


def test_single_days() -> None:
    assert _labels("今天天气不错") == ["今天"]
    assert _labels("昨天说的那个") == ["昨天"]
    assert _labels("前天聊过的") == ["前天"]
    assert _labels("大前天就到了") == ["大前天"], "「大前天」含「前天」子串但只应出一条"
    r = resolve_ranges("前天聊过的", TODAY)[0]
    assert (r.start, r.end) == ("2026-09-28", "2026-09-28")


def test_week_granularity() -> None:
    r = resolve_ranges("上周我们聊的那个电影", TODAY)[0]
    assert r.label == "上周"
    assert (r.start, r.end) == ("2026-09-21", "2026-09-27"), "ISO 周：周一到周日"
    assert resolve_ranges("上礼拜去的店", TODAY)[0].label == "上周"
    r = resolve_ranges("这周忙不忙", TODAY)[0]
    assert (r.start, r.end) == ("2026-09-28", "2026-10-04"), "本周含今天"
    # 上周X 单日：上周五 = 09-25
    r = resolve_ranges("上周五的约定", TODAY)
    assert [x.label for x in r] == ["上周五"], "上周X ⊂ 上周，只出单日"
    assert (r[0].start, r[0].end) == ("2026-09-25", "2026-09-25")


def test_month_granularity() -> None:
    r = resolve_ranges("上个月买的东西", TODAY)[0]
    assert (r.start, r.end) == ("2026-08-01", "2026-08-31")
    r = resolve_ranges("这个月过得真快", TODAY)[0]
    assert (r.start, r.end) == ("2026-09-01", "2026-09-30"), "本月到今天为止"


def test_numeric_and_negative() -> None:
    r = resolve_ranges("3天前说过的话", TODAY)
    assert [x.label for x in r] == ["3天前"]
    assert (r[0].start, r[0].end) == ("2026-09-27", "2026-09-27")
    r = resolve_ranges("2周前的事", TODAY)
    assert (r[0].start, r[0].end) == ("2026-09-16", "2026-09-16")
    # 宁缺毋滥：多义/未知表达不解析
    assert resolve_ranges("之前说过的", TODAY) == []
    assert resolve_ranges("那天的事", TODAY) == []
    assert resolve_ranges("365天前", TODAY) == [], "超 30 天上限不解析（防误配）"
    assert resolve_ranges("", TODAY) == []


def main() -> None:
    test_single_days()
    test_week_granularity()
    test_month_granularity()
    test_numeric_and_negative()
    print("\n=== 相对时间推理: 4 组全部通过 ===")


if __name__ == "__main__":
    main()
