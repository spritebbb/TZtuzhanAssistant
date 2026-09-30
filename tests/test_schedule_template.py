# -*- coding: utf-8 -*-
"""38 项清单 #17 回归：周模板人格资源化——作者私货（周三蛲蛲联机夜）进
backend/resources/personas/<id>/schedule_template.json，代码只留通用兜底。

- 加载态：default 人格下 weekly_template() 返回 11 块且含 weekday-wed-night；
- 兜底态：资源缺失/损坏（坏 JSON、字段缺失、weekdays 越界、时间非 HH:MM）
  整包回退 _FALLBACK_TEMPLATE（10 块、无周三特化、weekday-night 覆盖周三）；
- 集成：block_at() 周三 21:30 在加载态命中联机夜块。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tz_schedtpl_"))

from backend.core.schedule import (  # noqa: E402
    _FALLBACK_TEMPLATE,
    _PERSONAS_ROOT,
    block_at,
    weekly_template,
)


def test_default_persona_loads_resource() -> None:
    from backend.core.persona_profiles import active_id

    assert active_id() == "default", "测试环境应解析到 default 人格"
    blocks = weekly_template()
    assert len(blocks) == 11, f"default 人格资源应加载 11 块，实际 {len(blocks)}"
    ids = [b.id for b in blocks]
    assert "weekday-wed-night" in ids, "周三联机夜私货必须在人格数据里"
    wed = next(b for b in blocks if b.id == "weekday-wed-night")
    assert wed.weekdays == (2,)
    assert (wed.start_local, wed.end_local) == ("21:00", "23:00")
    assert wed.location_id == "P-02" and wed.activity == "late_night"
    assert wed.energy_delta_per_hour == -0.8 and wed.mood_delta_per_hour == 0.1
    night = next(b for b in blocks if b.id == "weekday-night")
    assert night.weekdays == (0, 1, 3, 4), "加载态 weekday-night 不含周三（被联机夜顶掉）"
    # mtime 缓存命中：同路径重复调用返回同一对象
    assert weekly_template() is blocks


def test_missing_resource_falls_back() -> None:
    target = _PERSONAS_ROOT / "no-such-persona" / "schedule_template.json"
    assert weekly_template(path=target) == _FALLBACK_TEMPLATE


def test_corrupt_resource_falls_back() -> None:
    valid_block = {
        "id": "x-night", "weekdays": [0], "start_local": "21:00",
        "end_local": "23:00", "location_id": "P-01", "activity": "evening_stay",
        "presence": "home", "energy_delta_per_hour": -0.5, "mood_delta_per_hour": 0.0,
    }

    def payload(**overrides: object) -> dict:
        return {"format_version": 1, "blocks": [{**valid_block, **overrides}]}

    bad_cases: list[object] = [
        "{not json",
        {"format_version": 2, "blocks": [valid_block]},        # 版本不符
        {"format_version": 1, "blocks": []},                   # 空块数组
        {"format_version": 1},                                  # 缺 blocks
        payload(weekdays=[7]),                                  # 越界星期
        payload(weekdays=[]),                                   # 空星期
        payload(weekdays=[True]),                               # bool 冒充 int
        payload(start_local="9:00"),                            # 非 HH:MM
        payload(end_local="24:00"),                             # 时越界
        payload(start_local=800),                               # 非字符串时间
        {"format_version": 1, "blocks": [
            {k: v for k, v in valid_block.items() if k != "presence"}
        ]},                                                        # 块内字段缺失
        payload(energy_delta_per_hour="low"),                   # delta 非数值
        payload(id=""),                                         # 空 id
    ]
    with tempfile.TemporaryDirectory(prefix="tz_schedtpl_bad_") as tmp:
        for index, case in enumerate(bad_cases):
            path = Path(tmp) / f"bad-{index}.json"
            text = case if isinstance(case, str) else json.dumps(case, ensure_ascii=False)
            path.write_text(text, encoding="utf-8")
            assert weekly_template(path=path) == _FALLBACK_TEMPLATE, \
                f"损坏用例 {index} 必须整包回退兜底: {case!r}"


def test_fallback_shape_is_generic() -> None:
    ids = [b.id for b in _FALLBACK_TEMPLATE]
    assert len(_FALLBACK_TEMPLATE) == 10, "兜底是通用 10 块"
    assert "weekday-wed-night" not in ids, "兜底不含作者私货（周三联机夜）"
    night = next(b for b in _FALLBACK_TEMPLATE if b.id == "weekday-night")
    assert night.weekdays == (0, 1, 2, 3, 4), "兜底 weekday-night 覆盖全部工作日（含周三）"


def test_block_at_wednesday_night_loaded() -> None:
    block = block_at(datetime(2026, 10, 14, 21, 30))  # 周三 21:30
    assert block is not None and block.id == "weekday-wed-night", \
        "加载态下周三 21:30 应命中人格数据里的联机夜块"


def main() -> None:
    test_default_persona_loads_resource()
    test_missing_resource_falls_back()
    test_corrupt_resource_falls_back()
    test_fallback_shape_is_generic()
    test_block_at_wednesday_night_loaded()
    print("\n=== 周模板人格资源化: 5 组全部通过 ===")


if __name__ == "__main__":
    main()
