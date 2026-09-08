# -*- coding: utf-8 -*-
"""F01 问候素材与变体池：素材授权/阶段门控/冷却/隐私/回退/开关/双接入。

验收锚点（docs/Zcode技术指导.md §15 F01 + 调度文档批次 4）：
- 素材只取授权真实来源，进行中活动优先，最多 3 条，无素材不编；
- 同 variant 7 天冷却；阶段不足不选；全部冷却时返回 None；
- 敏感事件（privacy != normal）不进素材；虚构生活素材明确标注；
- 模型失败用「无素材」类诚实短回退；生成期会话活跃则丢弃且不占冷却；
- 开关关闭只退素材/变体增强，不破坏原 gap 门控与去重。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_f01_"))

from backend.core import greeting_material as gm
from backend.core.userdb import db

NOW = datetime(2026, 9, 8, 20, 0)
UID = "f01-user"


def _ensure(uid: str) -> None:
    db.ensure_user(uid)


def test_resource_load_and_validation() -> int:
    variants = gm.load_variants()
    assert len(variants) == 12, len(variants)
    by_cat: dict[str, list] = {}
    for v in variants:
        by_cat.setdefault(v.category, []).append(v)
    for category in gm._CATEGORIES:
        assert len(by_cat.get(category, [])) >= 3, (category, by_cat.get(category))
    # 非法资源回退内置最小池，不抛异常
    with tempfile.TemporaryDirectory(prefix="f01-bad-") as raw:
        bad = Path(raw) / "greeting_variants.json"
        bad.write_text("{ not json", encoding="utf-8")
        with patch.object(gm, "_variant_path", return_value=bad):
            gm._VARIANT_CACHE.clear()
            fallback = gm.load_variants("bad-persona")
            assert len(fallback) == 1 and fallback[0].category == gm.CATEGORY_NO_MATERIAL
        gm._VARIANT_CACHE.clear()
    print("[OK] 资源加载：四类各 ≥3 个变体；坏资源回退内置最小池")
    return 0


def test_collect_material_authorized_only() -> int:
    uid = "f01-material"
    _ensure(uid)
    # 进行中的活动优先
    with db._lock:
        db.conn.execute(
            "INSERT INTO activities (user_id, kind, document_id, title, status, created_at, updated_at) "
            "VALUES (?, 'reading', 0, '人类简史', 'active', ?, ?)",
            (uid, NOW.isoformat(), NOW.isoformat()),
        )
        db.conn.commit()
    # 真实完成事件（正常隐私）+ 一条敏感事件
    from backend.core.relationship_events import record

    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO activities (user_id, kind, document_id, title, status, created_at, updated_at) "
            "VALUES (?, 'goal', 0, '晨跑计划', 'completed', ?, ?)",
            (uid, NOW.isoformat(), NOW.isoformat()),
        )
        goal_id = cur.lastrowid
        db.conn.commit()
    record(uid, "goal_completed", "activity", goal_id,
           subject=uid, obj="晨跑计划", occurred_at=NOW.isoformat())
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO activities (user_id, kind, document_id, title, status, created_at, updated_at) "
            "VALUES (?, 'goal', 0, '私密目标', 'completed', ?, ?)",
            (uid, NOW.isoformat(), NOW.isoformat()),
        )
        secret_id = cur.lastrowid
        db.conn.commit()
    record(uid, "goal_completed", "activity", secret_id,
           subject=uid, obj="私密目标", privacy="private", occurred_at=NOW.isoformat())
    # 她自己的虚构生活
    with db._lock:
        db.conn.execute(
            "INSERT INTO character_life_events "
            "(user_id, kind, block_id, occurrence, payload_json, occurred_at, computed_at) "
            "VALUES (?, 'daily_life', 'evening', ?, ?, ?, ?)",
            (uid, NOW.date().isoformat(),
             json.dumps({"description": "在阳台晒了一下午太阳"}, ensure_ascii=False),
             NOW.isoformat(), NOW.isoformat()),
        )
        db.conn.commit()

    material = gm.collect_greeting_material(uid, NOW)
    lines = [m.line for m in material]
    assert len(material) == 3, lines
    assert material[0].kind == "activity" and "人类简史" in material[0].line, lines
    assert any("晨跑计划" in line for line in lines), lines
    assert all("私密目标" not in line for line in lines), "敏感事件不得进素材"
    life = [m for m in material if m.kind == "life"]
    assert life and life[0].fiction is True, "虚构生活必须标注"

    # 无任何来源 → 空列表（调用方走无素材类，不编造）
    empty_uid = "f01-empty"
    _ensure(empty_uid)
    assert gm.collect_greeting_material(empty_uid, NOW) == []
    # 开关关闭 → 不取素材
    with patch("backend.core.features.flag", return_value=False):
        assert gm.collect_greeting_material(uid, NOW) == []
    print("[OK] 素材：活动优先 / 事件与虚构生活 / 敏感排除 / 无源为空 / 开关关闭")
    return 0


def test_variant_choice_stage_and_cooldown() -> int:
    uid = "f01-variant"
    _ensure(uid)
    # 无素材 → 无素材类
    variant = gm.choose_greeting_variant(uid, {"material": [], "stage": "熟悉"}, now=NOW)
    assert variant is not None and variant.category == gm.CATEGORY_NO_MATERIAL
    # 有活动素材 → 完成活动类
    material = [gm.SourceRef(kind="activity", source_id=1, line="共读《人类简史》",
                             occurred_at=NOW.isoformat())]
    variant = gm.choose_greeting_variant(uid, {"material": material, "stage": "熟悉"}, now=NOW)
    assert variant.category == gm.CATEGORY_ACTIVITY_DONE
    # 忙碌后 → 忙碌后类；且初识阶段不会选到「熟悉+」变体
    variant = gm.choose_greeting_variant(
        uid, {"material": material, "busy_return": True, "stage": "初识"}, now=NOW)
    assert variant.category == gm.CATEGORY_NO_MATERIAL, "初识不得用忙碌后类"
    # 冷却：用过的变体 7 天内不再选；7 天后恢复
    chosen = gm.choose_greeting_variant(uid, {"material": material, "stage": "亲密"}, now=NOW)
    gm.mark_variant_used(uid, chosen.id, source_id=1, now=NOW)
    assert gm.variant_in_cooldown(uid, chosen.id, now=NOW) is True
    assert gm.variant_in_cooldown(uid, chosen.id, now=NOW + timedelta(days=8)) is False
    again = gm.choose_greeting_variant(uid, {"material": material, "stage": "亲密"}, now=NOW)
    assert again is not None and again.id != chosen.id, "冷却中的变体不应被再次选中"
    # 全部冷却 → None（调用方保持沉默/走回退）
    for v in gm.load_variants():
        gm.mark_variant_used(uid, v.id, now=NOW)
    assert gm.choose_greeting_variant(uid, {"material": material, "stage": "恋人"}, now=NOW) is None
    print("[OK] 变体选择：分类匹配 / 阶段门控 / 7 天冷却 / 全冷却返回 None")
    return 0


def test_greeting_integration() -> int:
    from backend.core import greeting

    uid = "f01-greeting"
    _ensure(uid)
    session = "current"  # 会话存储只为 current 前缀建行
    with db._lock:
        db.conn.execute(
            "INSERT INTO activities (user_id, kind, document_id, title, status, created_at, updated_at) "
            "VALUES (?, 'goal', 0, '读完一章', 'active', ?, ?)",
            (uid, NOW.isoformat(), NOW.isoformat()),
        )
        db.conn.commit()
    # 生成成功 → 落会话并登记冷却
    with patch("backend.core.greeting.chat", new=AsyncMock(return_value="哟\n今天想聊点什么")), \
         patch("backend.core.greeting.config") as cfg:
        cfg.proactive_greeting_idle_hours = 8
        text = asyncio.run(greeting.greeting_for(uid, session))
    assert text, "应生成问候"
    used = db.conn.execute(
        "SELECT COUNT(*) n FROM greeting_variant_usage WHERE user_id=?", (uid,)).fetchone()["n"]
    assert used == 1, "成功问候应登记一次变体冷却"

    # 模型失败 → 诚实短回退（无素材类基调），且不新增冷却
    uid2 = "f01-greeting-fail"
    _ensure(uid2)
    with patch("backend.core.greeting.chat", new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch("backend.core.greeting.config") as cfg:
        cfg.proactive_greeting_idle_hours = 8
        text = asyncio.run(greeting.greeting_for(uid2, "current"))
    assert text, "失败也要有诚实短回退"
    assert db.conn.execute(
        "SELECT COUNT(*) n FROM greeting_variant_usage WHERE user_id=?", (uid2,)).fetchone()["n"] == 0
    # 开关关闭 → 走旧逻辑（不选变体、不记冷却）
    uid3 = "f01-greeting-off"
    _ensure(uid3)
    real_flag = __import__("backend.core.features", fromlist=["flag"]).flag
    with patch("backend.core.features.flag",
               side_effect=lambda name: False if name == "greeting_material_enabled" else real_flag(name)), \
         patch("backend.core.greeting.chat", new=AsyncMock(return_value="回来了")), \
         patch("backend.core.greeting.config") as cfg:
        cfg.proactive_greeting_idle_hours = 8
        text = asyncio.run(greeting.greeting_for(uid3, "current"))
    assert text
    assert db.conn.execute(
        "SELECT COUNT(*) n FROM greeting_variant_usage WHERE user_id=?", (uid3,)).fetchone()["n"] == 0

    # 迟到候选：生成期间用户已发言 → 丢弃问候，不落会话也不占冷却
    uid4 = "f01-greeting-late"
    _ensure(uid4)
    with patch("backend.core.greeting.chat", new=AsyncMock(return_value="哟")), \
         patch("backend.core.greeting.config") as cfg, \
         patch("backend.session.store.message_count", new=AsyncMock(side_effect=[0, 1])):
        cfg.proactive_greeting_idle_hours = 8
        text = asyncio.run(greeting.greeting_for(uid4, "current"))
    assert text is None, "生成期会话已活跃应丢弃问候"
    assert db.conn.execute(
        "SELECT COUNT(*) n FROM greeting_variant_usage WHERE user_id=?", (uid4,)).fetchone()["n"] == 0
    print("[OK] greeting 接入：成功记冷却 / 失败短回退不记 / 开关关闭退旧逻辑 / 迟到候选丢弃")
    return 0


def test_initiative_integration() -> int:
    from backend.core import initiative

    uid = "f01-proactive"
    _ensure(uid)
    with db._lock:
        db.conn.execute(
            "INSERT INTO activities (user_id, kind, document_id, title, status, created_at, updated_at) "
            "VALUES (?, 'reading', 0, '深夜食堂', 'active', ?, ?)",
            (uid, NOW.isoformat(), NOW.isoformat()),
        )
        db.conn.commit()
    msgs = initiative._build_proactive_prompt(uid)
    assert msgs, "应能组装主动提示"
    payload = msgs[-1]["content"]
    assert "深夜食堂" in payload, "主动消息应复用问候素材"
    variant_id = initiative._PROACTIVE_VARIANT_KEY.get(uid)
    assert variant_id, "应记录选中的变体 id 供冷却登记"
    with patch("backend.core.initiative.chat", new=AsyncMock(return_value="刚翻到一条有意思的")), \
         patch("backend.core.llm.chat", new=AsyncMock(return_value="刚翻到一条有意思的")):
        text = asyncio.run(initiative.generate_proactive_message(uid))
    assert text
    assert db.conn.execute(
        "SELECT COUNT(*) n FROM greeting_variant_usage WHERE user_id=? AND variant_id=?",
        (uid, variant_id)).fetchone()["n"] == 1, "主动消息生成成功应登记冷却"
    print("[OK] initiative 接入：主动提示含真实素材 + 生成成功记冷却")
    return 0


def main() -> int:
    failed = (
        test_resource_load_and_validation()
        + test_collect_material_authorized_only()
        + test_variant_choice_stage_and_cooldown()
        + test_greeting_integration()
        + test_initiative_integration()
    )
    if failed:
        print(f"\n=== F01 问候素材与变体池：{failed} 项失败 ===")
        return 1
    print("\n=== F01 问候素材与变体池：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
