# -*- coding: utf-8 -*-
"""B1 梦境/离线叙事：模式选择、记忆约束与退化行为。"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core.offline_narrative import collect_offline_context


class FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def recent_messages_with_ids(self, user_id, limit):
        assert user_id == "u1"
        return self.rows[-limit:]


def _row(row_id, role, content, ts):
    return {"id": row_id, "role": role, "content": content, "ts": ts.isoformat()}


def test_deep_night_becomes_dream() -> None:
    now = datetime(2026, 9, 4, 9, 0)
    late = now - timedelta(hours=10)
    rows = [
        _row(1, "user", "昨天那个记忆系统终于跑通了", late),
        _row(2, "assistant", "总算没继续和数据库打架", late + timedelta(minutes=1)),
        _row(3, "user", "修完以后整个人都空了", late + timedelta(minutes=2)),
        _row(4, "assistant", "空就先歇，别再给自己塞活", late + timedelta(minutes=3)),
    ]
    context = collect_offline_context(
        "u1",
        10,
        db_obj=FakeDB(rows),
        triple_query=lambda *_args, **_kwargs: [("对方", "person", "完成", "记忆系统", "project")],
        now=now,
    )
    assert context.mode == "dream"
    hint = context.prompt_hint("亲密")
    assert "梦到上次聊的事" in hint
    assert "记忆系统" in hint
    assert "禁止逐条复述" in hint and "不要捏造" in hint
    print("[OK] 深夜长聊后可进入有记忆锚点的梦境模式")


def test_long_gap_becomes_research() -> None:
    now = datetime(2026, 9, 4, 20, 0)
    rows = [_row(1, "user", "我最近在学做饭", now - timedelta(hours=28))]
    context = collect_offline_context(
        "u1", 28, db_obj=FakeDB(rows), triple_query=lambda *_args, **_kwargs: [], now=now
    )
    assert context.mode == "research"
    hint = context.prompt_hint("熟悉")
    assert "研究所" in hint and "不要虚构具体论文" in hint
    assert "不要用梦或回忆制造暧昧" in hint
    print("[OK] 长间隔使用研究碎片，低关系阶段禁止借梦越级")


def test_shorter_gap_and_memory_failure() -> None:
    class BrokenDB:
        def recent_messages_with_ids(self, *_args):
            raise RuntimeError("db unavailable")

    context = collect_offline_context(
        "u1", 9, db_obj=BrokenDB(), triple_query=lambda *_args, **_kwargs: []
    )
    assert context.mode == "small_life"
    assert not context.recent_lines and not context.triple_lines
    assert "没有可靠的共同记忆素材" in context.prompt_hint("亲密")
    print("[OK] 记忆层故障时退化为无外部后果的生活碎片")


def test_noise_is_sanitized_and_bounded() -> None:
    now = datetime(2026, 9, 4, 16, 0)
    rows = [
        _row(1, "user", "[图片：x.png]" + "很长的内容" * 40, now - timedelta(hours=10)),
        _row(2, "assistant", "\x00IMAGESTART\x00收到图片了", now - timedelta(hours=10)),
    ]
    context = collect_offline_context(
        "u1", 10, db_obj=FakeDB(rows), triple_query=lambda *_args, **_kwargs: [], now=now
    )
    assert context.recent_lines
    assert all(len(line) <= 106 for line in context.recent_lines)
    assert all("[图片" not in line and "IMAGESTART" not in line for line in context.recent_lines)
    print("[OK] 图片标记被清理，记忆素材有长度上限")


def main() -> None:
    test_deep_night_becomes_dream()
    test_long_gap_becomes_research()
    test_shorter_gap_and_memory_failure()
    test_noise_is_sanitized_and_bounded()
    print("\n=== B1 梦境/离线叙事：4 项全部通过 ===")


if __name__ == "__main__":
    main()

