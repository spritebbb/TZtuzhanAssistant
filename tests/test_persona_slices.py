# -*- coding: utf-8 -*-
"""P1-01 人格切片编译回归：资源校验、门控、预算、缓存、指纹与 build_system_prompt 接线。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_slices_"))


def test_resource_valid_and_core_resident() -> int:
    from backend.core.persona_slices import compile_slices, build_state_view, load_resource

    res = load_resource("default")
    assert res is not None, "default 资源应加载成功"
    assert len(res["slices"]) == 12, f"切片数应为 12，当前 {len(res['slices'])}"
    view = build_state_view(stage="初识", affection=0, energy=80, now=datetime(2026, 9, 7, 10, 0))
    out = compile_slices(view, profile_id="default")
    assert out is not None
    # core 三条常驻，初识+无情绪时没有任何 deep 切片
    assert out.core_ids == ["PS-CORE-TRUTH", "PS-CORE-CAST", "PS-CORE-TOPIC"], out.core_ids
    assert not [i for i in out.dynamic_ids if i.startswith("PS-DEEP")], out.dynamic_ids
    assert len(out.dynamic_ids) <= 3
    print("[OK] 资源校验通过 + core 常驻 + 深水区默认关闭")
    return 0


def test_deep_gates() -> int:
    from backend.core.persona_slices import build_state_view, compile_slices

    # 恋人 + trust 95 + tenderness 0.6 → 白头专属按最高优先级入选
    view = build_state_view(
        stage="恋人", affection=95, energy=80, now=datetime(2026, 9, 7, 21, 0),
        emotions={"tenderness": 0.6},
    )
    out = compile_slices(view, profile_id="default")
    assert out is not None
    assert "PS-DEEP-03" in out.dynamic_ids, out.dynamic_ids
    assert out.dynamic_ids[0] == "PS-DEEP-03", "深水区应按优先级排最前"

    # 恋人但 trust 不足 95 → 白头台词绝不出现（防提前泄露）
    view2 = build_state_view(stage="恋人", affection=90, now=datetime(2026, 9, 7, 21, 0),
                             emotions={"tenderness": 0.9})
    out2 = compile_slices(view2, profile_id="default")
    assert "PS-DEEP-03" not in out2.dynamic_ids, out2.dynamic_ids

    # 非恋人 → 身世影子与全部深水区不可见
    view3 = build_state_view(stage="熟悉", affection=30, now=datetime(2026, 9, 7, 21, 0),
                             emotions={"hurt": 0.9})
    out3 = compile_slices(view3, profile_id="default")
    assert "PS-REV-03" not in out3.dynamic_ids
    assert not [i for i in out3.dynamic_ids if i.startswith("PS-DEEP")], out3.dynamic_ids
    print("[OK] 深水区硬门控（trust 阈值 / 恋人 / 情绪存在性）")
    return 0


def test_time_gated_canon_and_budget() -> int:
    from backend.core.persona_slices import (DYNAMIC_TOKEN_BUDGET, build_state_view,
                                             compile_slices)

    view = build_state_view(stage="熟悉", affection=30, energy=80,
                            now=datetime(2026, 9, 7, 15, 0))
    out = compile_slices(view, profile_id="default")
    assert "PS-CANON-AFTERNOON" in out.dynamic_ids, out.dynamic_ids
    assert "PS-CANON-GAMING" not in out.dynamic_ids

    view_late = build_state_view(stage="熟悉", affection=30, now=datetime(2026, 9, 8, 0, 30))
    out_late = compile_slices(view_late, profile_id="default")
    assert "PS-CANON-GAMING" in out_late.dynamic_ids, out_late.dynamic_ids

    # 预算：动态渲染文本保守估算总量 ≤ 800
    full = build_state_view(stage="恋人", affection=99, now=datetime(2026, 9, 7, 23, 30),
                            emotions={"tenderness": 0.8, "hurt": 0.5})
    out_full = compile_slices(full, profile_id="default")
    assert out_full is not None
    dynamic_lines = out_full.lines[len(out_full.core_ids):]
    assert sum(len(line) for line in dynamic_lines) <= DYNAMIC_TOKEN_BUDGET
    print("[OK] 时段正典门控 + 动态预算 ≤ 800")
    return 0


def test_cache_and_fingerprint() -> int:
    from backend.core import persona_slices as ps
    from backend.core.persona_slices import build_state_view, compile_slices

    ps._compile_cache.clear()  # 前序用例可能已填充同指纹缓存
    view = build_state_view(stage="熟悉", affection=30, now=datetime(2026, 9, 7, 15, 0))
    first = compile_slices(view, profile_id="default")
    second = compile_slices(view, profile_id="default")
    assert first is not None and second is not None
    assert not first.cache_hit and second.cache_hit, "同指纹应命中编译缓存"

    # 未被引用的键（quiet/energy_band/substage 不在任何谓词里）变化 → 指纹不变
    view_q = dict(view)
    view_q["quiet"] = True
    view_q["energy_band"] = "low"
    third = compile_slices(view_q, profile_id="default")
    assert third is not None and third.cache_hit, "未引用键不应改变指纹"

    # 引用键变化（trust）→ 指纹变化、缓存不命中
    view_t = dict(view)
    view_t["trust"] = 60
    fourth = compile_slices(view_t, profile_id="default")
    assert fourth is not None and not fourth.cache_hit
    print("[OK] 指纹只含引用键 + 编译缓存命中/失效")
    return 0


def test_invalid_resource_falls_back() -> int:
    from backend.core import persona_slices as ps

    assert ps.load_resource("no-such-persona") is None
    bad = {"format_version": 2}
    try:
        ps._validate_resource(bad, "default")
        raise AssertionError("非法资源应抛 PersonaSliceError")
    except ps.PersonaSliceError:
        pass
    # 非法谓词（未登记键 / 非法 op）整体拒绝
    bad_slice = {
        "format_version": 1, "state_keys_version": 1, "persona_id": "default", "version": 1,
        "slices": [{"id": "x", "kind": "tactic", "namespace": "n", "trigger_ids":
                    [{"field": "mood_score", "op": "gte", "value": 1}],
                    "priority": 1, "examples": [], "instruction": "i",
                    "source_doc": "d", "version": 1}],
    }
    try:
        ps._validate_resource(bad_slice, "default")
        raise AssertionError("未登记状态键应被拒绝")
    except ps.PersonaSliceError:
        pass
    # 编译入口对无人格资源返回空（回退旧 prompt）
    lines = ps.compile_prompt_lines(profile_id="no-such-persona", stage="初识", affection=0)
    assert lines == []
    print("[OK] 非法资源不激活，回退旧人格提示")
    return 0


def test_substage_boundaries() -> int:
    from backend.core.affection import substage_of

    assert substage_of(0, 0) == ("初识", "早")
    assert substage_of(8, 100) == ("初识", "中"), "主维=低维：8/100 仍是初识中档"
    assert substage_of(17, 17) == ("初识", "晚")
    assert substage_of(33, 40) == ("熟悉", "中")
    assert substage_of(42, 42) == ("熟悉", "晚")
    assert substage_of(58, 90) == ("亲密", "中")
    assert substage_of(67, 67) == ("亲密", "晚")
    assert substage_of(85, 100) == ("恋人", "中")
    assert substage_of(95, 100) == ("恋人", "晚")
    assert substage_of(90, 20) == ("初识", "晚"), "90/20 双门槛下主维 20=初识晚"
    print("[OK] 小档切点边界（含双门槛取低维）")
    return 0


def test_system_prompt_integration() -> int:
    from backend.core.persona import build_system_prompt
    from backend.core.userdb import db

    uid = "slices-integration-test"
    db.ensure_user(uid)
    prompt = build_system_prompt(
        stage="熟悉", address="你", lover_confirm=False, first_chat=False,
        affection=30, user_id=uid,
    )
    assert "## 行为参考" in prompt, "状态注入后应包含行为参考段"
    assert "PS-CORE-TRUTH" not in prompt, "条目 id 不得出现在运行时产物里"
    assert "菟丝子研究所" in prompt, "core 内容应进入 prompt"

    # user_id 为空（无状态场景）不注入，保持旧契约
    prompt_anon = build_system_prompt(
        stage="熟悉", address="你", lover_confirm=False, first_chat=False, affection=30,
    )
    assert "## 行为参考" not in prompt_anon
    print("[OK] build_system_prompt 接线（注入/匿名回退/无条目 id 泄露）")
    return 0


def main() -> int:
    failed = (
        test_resource_valid_and_core_resident()
        + test_deep_gates()
        + test_time_gated_canon_and_budget()
        + test_cache_and_fingerprint()
        + test_invalid_resource_falls_back()
        + test_substage_boundaries()
        + test_system_prompt_integration()
    )
    if failed:
        print(f"\n=== P1-01 人格切片：{failed} 项失败 ===")
        return 1
    print("\n=== P1-01 人格切片：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
