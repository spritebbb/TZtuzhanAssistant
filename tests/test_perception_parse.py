# -*- coding: utf-8 -*-
"""感知层 JSON 解析健壮性回归（用户实跑日志实证：部分模型偶发输出
Python 字面量风格 {'emotion_deltas': 2, ...}（单引号键），json.loads 必败
导致整轮感知降级关键词规则）。覆盖 _parse_json 的四条路径。

运行：python -m tests.test_perception_parse（或经 pytest tests/ 由套件运行器执行）
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MEMORY_EMBED_FORCE", "1")
os.environ.setdefault("MEMORY_MEM0", "0")
os.environ.setdefault("MOOD_CITY", "")
os.environ.setdefault("SEARCH_ENABLED", "0")

from backend.core.perception import _parse_json  # noqa: E402


def test_standard_json() -> None:
    assert _parse_json('{"emotion_deltas": 2}') == {"emotion_deltas": 2}
    print("[OK] 标准 JSON 正常解析")


def test_markdown_fence() -> None:
    raw = '好的，结果如下：\n```json\n{"emotion_deltas": 1}\n```\n以上。'
    assert _parse_json(raw) == {"emotion_deltas": 1}
    print("[OK] markdown 围栏与前后杂字容忍")


def test_python_literal_fallback() -> None:
    # 用户实跑实证的形态：单引号键（Python dict repr 风格），json.loads 必败
    raw = "{'emotion_deltas': 2, 'affection_delta': -1, 'abuse': False}"
    got = _parse_json(raw)
    assert got == {"emotion_deltas": 2, "affection_delta": -1, "abuse": False}, got
    # 围栏里包字面量同样兜住
    fenced = "```python\n{'emotional_hit': '委屈', 'hit_weight': 0.5}\n```"
    assert _parse_json(fenced) == {"emotional_hit": "委屈", "hit_weight": 0.5}
    print("[OK] Python 字面量（单引号键）经 literal_eval 安全兜底，不再降级")


def test_garbage_returns_none() -> None:
    assert _parse_json("") is None
    assert _parse_json("完全没有结构化内容") is None
    assert _parse_json("[1, 2, 3]") is None  # 非 dict 字面量不接受
    assert _parse_json("__import__('os')  # 恶意串不是字面量") is None
    print("[OK] 垃圾/非 dict/非字面量一律 None（无代码执行面）")


def main() -> None:
    test_standard_json()
    test_markdown_fence()
    test_python_literal_fallback()
    test_garbage_returns_none()
    print("\n=== 感知层解析健壮性: 4 项全部通过 ===")


if __name__ == "__main__":
    main()
