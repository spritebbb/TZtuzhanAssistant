# -*- coding: utf-8 -*-
"""38项#23 事件感：每日外部世界摘要——生成幂等/开关/成本闸/失败不写库，
进问候素材池的末位补充与标注。LLM 与搜索全 mock。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztzhan_news_"))

UID = "u_news"
DAY = datetime.now().date().isoformat()


def _run(coro):
    return asyncio.run(coro)


def _real_flag():
    return __import__("backend.core.features", fromlist=["flag"]).flag


def _results(n: int = 2) -> list[dict]:
    return [
        {"title": f"天文台预告今夜有流星雨 {i}", "snippet": "傍晚肉眼可见", "url": "https://example.com"}
        for i in range(n)
    ]


def test_ensure_success_and_idempotent() -> int:
    from backend.core import news_digest as nd
    from backend.core.userdb import db, kv_get

    db.ensure_user(UID)
    ws = MagicMock(return_value=_results())
    chat = AsyncMock(return_value="“听说今夜有流星雨，傍晚抬头就能看到。”\n")
    with patch("backend.core.search.web_search", new=ws), \
         patch("backend.core.llm.chat", new=chat):
        _run(nd.ensure_daily_news(UID, DAY))
    assert ws.call_count == 1 and chat.await_count == 1
    # 引号/换行被清洗，只留一句话
    summary = nd.news_line(UID, DAY)
    assert summary == "听说今夜有流星雨，傍晚抬头就能看到。", summary
    data = json.loads(kv_get(UID, f"greeting:news:{DAY}") or "{}")
    assert data.get("generated_at"), "kv 应存 ISO 生成时间"

    # 幂等：当日 kv 已有 → 不再搜索/压缩
    ws2 = MagicMock(return_value=_results())
    chat2 = AsyncMock(return_value="另一条无关内容")
    with patch("backend.core.search.web_search", new=ws2), \
         patch("backend.core.llm.chat", new=chat2):
        _run(nd.ensure_daily_news(UID, DAY))
    assert ws2.call_count == 0 and chat2.await_count == 0, "已有 kv 不得重跑"
    assert nd.news_line(UID, DAY) == summary
    print("[OK] 成功写 kv（清洗引号换行）+ 当日幂等不重跑")
    return 0


def test_ensure_flag_off_and_cost_blocked() -> int:
    from backend.core import news_digest as nd
    from backend.core.userdb import db, kv_get

    # 开关关闭 → 不跑
    uid = UID + "_off"
    db.ensure_user(uid)
    ws = MagicMock(return_value=_results())
    with patch("backend.core.features.flag",
               side_effect=lambda name: False if name == "news_digest_enabled" else _real_flag()(name)), \
         patch("backend.core.search.web_search", new=ws):
        _run(nd.ensure_daily_news(uid, DAY))
    assert ws.call_count == 0, "开关关闭不得搜索"
    assert kv_get(uid, f"greeting:news:{DAY}") is None

    # 成本闸 hard/extreme 档 → 拦下（模块名 "news"）
    uid2 = UID + "_cost"
    db.ensure_user(uid2)
    ws2 = MagicMock(return_value=_results())
    with patch("backend.core.cost_guard.check", new=MagicMock(return_value=False)), \
         patch("backend.core.search.web_search", new=ws2):
        _run(nd.ensure_daily_news(uid2, DAY))
    assert ws2.call_count == 0, "成本闸拦截后不得搜索"
    assert kv_get(uid2, f"greeting:news:{DAY}") is None
    print("[OK] 开关关闭不跑 / 成本闸 hard 档不跑")
    return 0


def test_ensure_failure_no_kv() -> int:
    from backend.core import news_digest as nd
    from backend.core.userdb import db, kv_get

    # 压缩失败（LLM 异常）→ 不写 kv，fail-soft 不抛
    uid = UID + "_fail"
    db.ensure_user(uid)
    with patch("backend.core.search.web_search", new=MagicMock(return_value=_results())), \
         patch("backend.core.llm.chat", new=AsyncMock(side_effect=RuntimeError("boom"))):
        _run(nd.ensure_daily_news(uid, DAY))
    assert kv_get(uid, f"greeting:news:{DAY}") is None, "压缩失败不得写 kv"

    # 搜索空结果 → 不喂 LLM、不写 kv
    uid2 = UID + "_empty"
    db.ensure_user(uid2)
    chat2 = AsyncMock(return_value="x")
    with patch("backend.core.search.web_search", new=MagicMock(return_value=[])), \
         patch("backend.core.llm.chat", new=chat2):
        _run(nd.ensure_daily_news(uid2, DAY))
    assert chat2.await_count == 0 and kv_get(uid2, f"greeting:news:{DAY}") is None
    print("[OK] 压缩失败/搜索为空 → 不写 kv，全程 fail-soft")
    return 0


def test_collect_news_into_material() -> int:
    from backend.core import greeting_material as gm
    from backend.core.userdb import db, kv_set

    uid = UID + "_mat"
    db.ensure_user(uid)
    kv_set(uid, f"greeting:news:{DAY}", json.dumps(
        {"summary": "听说今夜有流星雨", "generated_at": datetime.now().isoformat(timespec="seconds")},
        ensure_ascii=False))

    # 纯新闻素材 → 1 条 news SourceRef，归 plain_return
    now = datetime.now()
    material = gm.collect_greeting_material(uid, now)
    kinds = [m.kind for m in material]
    assert kinds == ["news"], kinds
    news = material[0]
    assert news.fiction is False and news.source_id == 0, news
    assert "流星雨" in news.line and news.occurred_at, news
    hint = gm.build_material_hint(material)
    assert "流星雨" in hint, hint
    assert "（外部世界今天的事，不是你们之间的事，最多顺带一提）" in hint, hint
    assert gm._pick_category(material, busy_return=False) == gm.CATEGORY_PLAIN_RETURN, \
        "news 只归 plain_return，不参与 activity_done"

    # 关系素材优先（news 永远末位）+ 敏感事件排除回归 + 总开关关闭回归
    with db._lock:
        db.conn.execute(
            "INSERT INTO activities (user_id, kind, document_id, title, status, created_at, updated_at) "
            "VALUES (?, 'reading', 0, '人类简史', 'active', ?, ?)",
            (uid, now.isoformat(), now.isoformat()))
        db.conn.commit()
    from backend.core.relationship_events import record

    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO activities (user_id, kind, document_id, title, status, created_at, updated_at) "
            "VALUES (?, 'goal', 0, '私密目标', 'completed', ?, ?)",
            (uid, now.isoformat(), now.isoformat()))
        secret_id = cur.lastrowid
        db.conn.commit()
    record(uid, "goal_completed", "activity", secret_id,
           subject=uid, obj="私密目标", privacy="private", occurred_at=now.isoformat())

    material2 = gm.collect_greeting_material(uid, now)
    kinds2 = [m.kind for m in material2]
    assert kinds2[0] == "activity" and kinds2[-1] == "news", kinds2
    assert all("私密目标" not in m.line for m in material2), "敏感事件不得进素材"

    with patch("backend.core.features.flag",
               side_effect=lambda name: False if name == "greeting_material_enabled" else _real_flag()(name)):
        assert gm.collect_greeting_material(uid, now) == [], "开关关闭素材应为空（既有语义）"
    print("[OK] news 进素材末位 + 标注 + plain_return + 关系素材优先 + 敏感排除回归")
    return 0


def main() -> int:
    failed = (
        test_ensure_success_and_idempotent()
        + test_ensure_flag_off_and_cost_blocked()
        + test_ensure_failure_no_kv()
        + test_collect_news_into_material()
    )
    if failed:
        print(f"\n=== 事件感（每日新闻摘要）：{failed} 项失败 ===")
        return 1
    print("\n=== 事件感（每日新闻摘要）：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
