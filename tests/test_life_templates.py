# -*- coding: utf-8 -*-
"""L06 低频生活模板池：确定性选择/精力门控/冷却/用户取消/提交幂等/主动候选。

契约（§16 L06 + 调度文档批次1 + 拍板 #9/#10）：
- 资源校验：非法字段拒绝、无效条目跳过、全无效 fallback rest；
- choose_life_event：概率 0.25 固定种子、当日 veto 不抽、精力 <30 只允许
  rest/quiet_reading、冷却不抽、无候选沉默；
- commit_life_event：唯一键幂等（同日同模板一条）、扣能量一次、冷却登记；
- 主动候选：今日有外出且未表达过才出文案，每日至多一次。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_l06_"))

from backend.core import life_templates as lt
from backend.core.life_templates import LifeTemplate, LifeTemplateError
from backend.core.userdb import db, kv_get, kv_set

# “今日外出”验收必须锚定运行日，避免跨日后把真实正确行为误判为失败。
AT = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)


def _tpl(**kw) -> LifeTemplate:
    base = dict(id="lt-test", location_id="P-00", energy_cost=10, weight=3,
                output_kind="outing", cooldown_days=7, min_stage="初识",
                activity_hint="weekend_stay", description="出门走走",
                return_note="回来了", prerequisites=())
    base.update(kw)
    return LifeTemplate(**base)


def _clear_kv(uid: str) -> None:
    kv_set(uid, lt.KV_LIFE_TEMPLATES, json.dumps(
        {"format_version": 1, "last_used": {}, "vetoed_date": ""}, ensure_ascii=False))
    kv_set(uid, "life_templates:outing_expressed:" + AT.date().isoformat(), "")


def test_validation() -> int:
    for bad in ({"id": "", "location_id": "P-00", "output_kind": "outing"},
                {"id": "x", "location_id": "P-99", "output_kind": "outing"},
                {"id": "x", "location_id": "P-00", "output_kind": "party"},
                {"id": "x", "location_id": "P-00", "output_kind": "outing", "min_stage": "挚友"}):
        try:
            lt._validate_template(bad, 0)
            raise AssertionError(f"应拒绝: {bad}")
        except LifeTemplateError:
            pass
    # 边界 clamp
    ok = lt._validate_template({"id": "x", "location_id": "P-00",
                                "output_kind": "outing", "energy_cost": 999,
                                "weight": 99, "cooldown_days": 999,
                                "description": "d", "return_note": "r"}, 0)
    assert (ok.energy_cost, ok.weight, ok.cooldown_days) == (100, 10, 60)
    print("[OK] 资源校验：非法字段拒绝 + 数值 clamp")
    return 0


def test_fallback_rest() -> int:
    fb = lt._fallback_rest_template()
    assert fb.low_energy_ok and fb.output_kind == "rest"
    # 全无效资源 → fallback（概率门内才抽候选）
    with patch.object(lt, "load_templates", return_value=(fb,)), \
         patch.object(lt, "_roll", side_effect=lambda u, d, extra="": 10 if extra == "" else 0):
        got = lt.choose_life_event("lt-fb-user", AT, energy=5, stage="初识")
        assert got is not None and got.output_kind == "rest"
    print("[OK] 全无效资源 fallback rest")
    return 0


def test_probability_deterministic() -> int:
    uid = "lt-prob"
    db.ensure_user(uid)
    _clear_kv(uid)
    # 同一天反复调用：roll 确定性 → 结果恒定（选中则恒选中，未选中则恒 None）
    results = {lt.choose_life_event(uid, AT, energy=80, stage="熟悉") for _ in range(5)}
    assert len(results) == 1, f"同日同种子结果应唯一: {results}"
    # 概率边界：构造 roll 结果验证阈值行为（0.25 = roll < 25 才候选）
    with patch.object(lt, "_roll", side_effect=lambda u, d, extra="": 24 if extra == "" else 0):
        assert lt.choose_life_event(uid, AT, energy=80, stage="熟悉") is not None
    with patch.object(lt, "_roll", return_value=25):
        assert lt.choose_life_event(uid, AT, energy=80, stage="熟悉") is None
    print("[OK] 概率 0.25 确定性 + 首次未选中当日不反复抽")
    return 0


def test_energy_gate() -> int:
    uid = "lt-energy"
    db.ensure_user(uid)
    _clear_kv(uid)
    # 精力 <30：outing 被排除，rest/quiet_reading 可用
    with patch.object(lt, "_roll", side_effect=lambda u, d, extra="": 10 if extra == "" else 0), \
         patch.object(lt, "load_templates", return_value=(
             _tpl(id="lt-out", output_kind="outing"),
             _tpl(id="lt-rest", output_kind="rest", energy_cost=0),
             _tpl(id="lt-read", output_kind="quiet_reading", energy_cost=5))):
        got = lt.choose_life_event(uid, AT, energy=25, stage="初识")
        assert got is not None and got.output_kind != "outing", f"低精力不得外出: {got}"
        got2 = lt.choose_life_event(uid, AT, energy=31, stage="初识")
        assert got2 is not None  # >=30 允许 outing
    print("[OK] 精力门控：<30 只允许 rest/quiet_reading")
    return 0


def test_cooldown_and_veto() -> int:
    uid = "lt-cool"
    db.ensure_user(uid)
    _clear_kv(uid)
    tpl = _tpl(id="lt-only", cooldown_days=7)
    with patch.object(lt, "load_templates", return_value=(tpl,)):
        # 冷却中不抽
        got = lt.choose_life_event(uid, AT, energy=80, stage="初识")
        assert got is not None  # 无冷却时可用
        data = lt._load_kv(uid)
        data["last_used"] = {"lt-only": (AT.date() - timedelta(days=3)).isoformat()}
        kv_set(uid, lt.KV_LIFE_TEMPLATES, json.dumps(data))
        assert lt.choose_life_event(uid, AT, energy=80, stage="初识") is None, "冷却中不得抽中"
    # 用户取消：当日不再抽 + 全模板当日冷却
    assert lt.mark_vetoed(uid, now=AT) is True
    assert lt.mark_vetoed(uid, now=AT) is False, "同日重复取消幂等"
    assert lt.choose_life_event(uid, AT, energy=80, stage="初识") is None, "取消当日不抽"
    print("[OK] 模板冷却 + 用户取消（当日作废且幂等）")
    return 0


def test_commit_idempotent_and_energy() -> int:
    uid = "lt-commit"
    db.ensure_user(uid)
    _clear_kv(uid)
    tpl = _tpl(id="lt-go", energy_cost=12)
    from backend.core import schedule

    before = json.loads(kv_get(uid, "state:schedule") or "{}").get("energy_delta_today", 0.0)
    payload = lt.commit_life_event(uid, tpl, AT)
    assert payload is not None and payload["template_id"] == "lt-go"
    # 幂等：同日同模板第二次提交不新增事件
    assert lt.commit_life_event(uid, tpl, AT) is None
    rows = db.conn.execute(
        "SELECT COUNT(*) n FROM character_life_events WHERE user_id=? AND kind='outing'",
        (uid,)).fetchone()["n"]
    assert rows == 1, f"outing 事件应恰好 1 条: {rows}"
    # 能量记账扣了一次
    after = json.loads(kv_get(uid, "state:schedule") or "{}").get("energy_delta_today", 0.0)
    assert after <= before - 12, f"能量记账应扣 12: {before} -> {after}"
    # 冷却已登记
    data = lt._load_kv(uid)
    assert data["last_used"].get("lt-go") == AT.date().isoformat()
    print("[OK] 提交幂等 + 一次性扣能量 + 冷却登记")
    return 0


def test_stage_gate() -> int:
    uid = "lt-stage"
    db.ensure_user(uid)
    _clear_kv(uid)
    romantic = _tpl(id="lt-late", min_stage="亲密")
    with patch.object(lt, "load_templates", return_value=(romantic,)), \
         patch.object(lt, "_roll", side_effect=lambda u, d, extra="": 10 if extra == "" else 0):
        assert lt.choose_life_event(uid, AT, energy=80, stage="初识") is None
        assert lt.choose_life_event(uid, AT, energy=80, stage="恋人") is not None
    print("[OK] 阶段门控：低阶段不触发高阶段模板")
    return 0


def test_outing_express_and_presence() -> int:
    # /api/presence 走 active_user_id()（默认 assistant-main）， seeding 需落在同一身份
    from backend.core.persona_profiles import active_user_id

    uid = active_user_id()
    db.ensure_user(uid)
    _clear_kv(uid)
    tpl = _tpl(id="lt-note", return_note="从城里回来了")
    lt.commit_life_event(uid, tpl, AT)
    # 主动候选：今日有外出未表达 → 出文案；第二次 → None（每日一次）
    first = lt.maybe_express_outing(uid)
    assert first is not None and "城里" in first, first
    assert lt.maybe_express_outing(uid) is None
    # 状态行端点消费 active_outing
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.meta import router as meta_router

    app = FastAPI()
    app.include_router(meta_router)
    with TestClient(app) as client:
        r = client.get("/api/presence")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ok"] is True
        assert d["active_outing"] is not None, "今日外出应在 active_outing 可见"
        assert d["active_outing"]["description"]
        assert any(e.get("description") for e in d["recent_events"]), "外出流并入生活流"
    print("[OK] 外出主动候选（每日一次）+ /api/presence active_outing 可见提醒")
    return 0


def test_veto_keeps_history() -> int:
    """已发生的外出事件不被取消操作抹掉（拍板 #10：数据保留，可导出/删除）。"""
    uid3 = "lt-veto-presence"
    db.ensure_user(uid3)
    _clear_kv(uid3)
    lt.commit_life_event(uid3, _tpl(id="lt-v", return_note="出门了"), AT)
    lt.mark_vetoed(uid3, now=AT)
    outings = lt.latest_outing(uid3, limit=1)
    assert outings and outings[0]["date"] == AT.date().isoformat(), "历史外出不应被抹"
    return 0


def test_switch_off() -> int:
    from backend.core.features import set_flag

    uid = "lt-switch"
    db.ensure_user(uid)
    _clear_kv(uid)
    set_flag("life_templates_enabled", False)
    try:
        assert lt.choose_life_event(uid, AT, energy=80, stage="恋人") is None
        assert lt.maybe_express_outing(uid) is None
    finally:
        set_flag("life_templates_enabled", True)
    print("[OK] 开关关闭：模板与主动候选全部停用")
    return 0


async def main() -> int:
    failed = (
        test_validation()
        + test_fallback_rest()
        + test_probability_deterministic()
        + test_energy_gate()
        + test_cooldown_and_veto()
        + test_commit_idempotent_and_energy()
        + test_stage_gate()
        + test_outing_express_and_presence()
        + test_veto_keeps_history()
        + test_switch_off()
    )
    if failed:
        print(f"\n=== L06 生活模板池：{failed} 项失败 ===")
        return 1
    print("\n=== L06 生活模板池：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
