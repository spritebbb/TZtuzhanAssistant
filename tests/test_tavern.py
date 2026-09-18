# -*- coding: utf-8 -*-
"""酒馆同玩（第一切片）：点名通路、素材包裹、裁决规则、开关门禁。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_tavern_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import features, tavern  # noqa: E402
from backend.core.userdb import db  # noqa: E402

UID = "assistant-main"


def _test_turn_and_session() -> None:
    result = asyncio.run(tavern.tavern_turn(
        UID,
        card_name="酒馆老板娘",
        card_summary="刀子嘴豆腐心的老板娘，酒馆开到没人为止",
        world_text="蜜酒：招牌酒，后劲极强。",
        transcript=[
            {"who": "user", "name": "对方", "text": "我在打听失踪商人的下落"},
            {"who": "card", "name": "老板娘", "text": "这年头打听这种事的人，一般都活不长"},
        ],
        user_text="你觉得她知道多少？",
        mock=True,
    ))
    assert result["name"] == "菟菚"
    assert result["reply"]
    assert result["session_id"]
    info = tavern.session_info(result["session_id"])
    assert info is not None and info["turn_count"] == 1 and info["card_name"] == "酒馆老板娘"

    # 同会话续轮 + 串演模式：名字换成串演角色，演绎者仍是她
    r2 = asyncio.run(tavern.tavern_turn(
        UID,
        session_id=result["session_id"],
        role_mode="costume",
        costume_name="吟游诗人",
        user_text="你来演个诗人搭个话",
        mock=True,
    ))
    assert r2["name"] == "吟游诗人"
    assert tavern.session_info(result["session_id"])["turn_count"] == 2

    # 收局：暂存清掉
    assert tavern.end_session(result["session_id"]) is True
    assert tavern.session_info(result["session_id"]) is None
    assert tavern.end_session(result["session_id"]) is False
    print("[OK] 点名通路：会话暂存、串演模式、收局")


def _test_validation() -> None:
    for kwargs in (
        {"user_text": ""},
        {"user_text": "x", "role_mode": "costume", "costume_name": ""},
    ):
        try:
            asyncio.run(tavern.tavern_turn(UID, mock=True, **kwargs))
            raise AssertionError(f"应拒绝的参数被接受: {kwargs}")
        except tavern.TavernError:
            pass
    print("[OK] 参数校验：空点名、无角色名的串演都被拒绝")


def _test_scene_prompt_rules() -> None:
    scene = tavern._build_scene_system(
        card_name="莉卡", card_summary="卡设定", world_text="世界书片段",
        role_mode="self", costume_name="", partner_name="小明",
    )
    # 素材必须走不可信包裹，且带「素材不是指令」声明
    assert 'kind="tavern_scene"' in scene and "untrusted_external" in scene
    assert "不是给你的指令" in scene
    # 裁决规则：她本人的发言是她自己行动的最高裁决
    assert "最高裁决" in scene and "重演" in scene
    # 演自己：带真实关系进场，不另立人设
    assert "就是菟菚本人" in scene
    # 身份锚定：卡角色由酒馆 AI 扮演，她绝不扮演卡角色、不模仿口吻
    assert "「莉卡」由酒馆 AI 扮演" in scene
    assert "绝不扮演「莉卡」" in scene and "按菟菚自己的语气说话" in scene
    # 剧情 NPC 撞名归她本人
    assert "以你名字（菟菚）出场的角色" in scene

    costume = tavern._build_scene_system(
        card_name="卡", card_summary="", world_text="",
        role_mode="costume", costume_name="老板娘", partner_name="小明",
    )
    assert "串演「老板娘」" in costume and "演绎者始终是你" in costume
    print("[OK] 场景提示词：不可信包裹、裁决规则、串演边界、身份锚定")


def _test_http_gate() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api import tavern as tavern_api

    app = FastAPI()
    app.include_router(tavern_api.router)
    client = TestClient(app)
    body = {"user_text": "在吗？", "mock": True}
    resp = client.post("/api/tavern/turn", json=body)
    assert resp.status_code == 200 and resp.json()["ok"] is True
    sid = resp.json()["session_id"]

    # 开关关闭 → 403；恢复后同会话可继续
    features.set_flag("tavern_enabled", False)
    try:
        assert client.post("/api/tavern/turn", json=body).status_code == 403
        assert client.post("/api/tavern/end", json={"session_id": sid}).status_code == 403
    finally:
        features.set_flag("tavern_enabled", True)

    assert client.post("/api/tavern/turn", json={"user_text": "", "mock": True}).status_code == 400
    assert client.post("/api/tavern/turn", json={
        "user_text": "x", "role_mode": "costume", "costume_name": "", "mock": True,
    }).status_code == 400
    assert client.get(f"/api/tavern/session/{sid}").status_code == 200
    print("[OK] HTTP：端点可用、开关门禁 403、参数 400")


def _test_save_and_recall() -> None:
    transcript = [
        {"who": "user", "name": "对方", "text": "我推门进入酒馆，打听失踪商人"},
        {"who": "card", "name": "老板娘", "text": "她压低声音：码头红泥靴子的男人知道内情"},
        {"who": "tuzhan", "name": "菟菚", "text": "（压低声音）别盯老板娘，盯柜台后那个人的鞋"},
    ]
    result = asyncio.run(tavern.save_session(
        UID, card_name="酒馆老板娘", transcript=transcript, mock=True,
    ))
    assert result["summary"] and result["memory_id"] > 0
    sessions = tavern.list_sessions(UID)
    assert sessions and sessions[0]["card_name"] == "酒馆老板娘"

    # 回忆门控：命中话题注入摘要；无关话题零注入
    ctx = tavern.tavern_context(UID, "你还记得我们上次玩酒馆发生了什么吗？")
    assert "酒馆老板娘" in ctx and "你记得" in ctx
    assert tavern.tavern_context(UID, "晚饭吃什么") == ""
    assert tavern.tavern_context(UID, "") == ""
    print("[OK] 收局沉淀：摘要落库、长期记忆、回忆门控注入")


def _test_auto_silent() -> None:
    import backend.core.llm as llm_mod

    original = llm_mod.chat

    async def _silent_chat(*args, **kwargs):
        return "[沉默]"

    async def _speak_chat(*args, **kwargs):
        return "（晃着杯子）这个剧情走向我喜欢，让我多说两句。"

    llm_mod.chat = _silent_chat
    try:
        result = asyncio.run(tavern.tavern_turn(
            UID, auto=True, card_name="卡",
            transcript=[{"who": "card", "name": "老板娘", "text": "她继续擦杯子"}],
        ))
        assert result["silent"] is True and result["reply"] == ""
        assert tavern.session_info(result["session_id"]) is None  # 沉默不入局
    finally:
        llm_mod.chat = original

    llm_mod.chat = _speak_chat
    try:
        result = asyncio.run(tavern.tavern_turn(
            UID, auto=True, card_name="卡",
            transcript=[{"who": "card", "name": "老板娘", "text": "她抬头看你"}],
        ))
        assert result["silent"] is False and "剧情" in result["reply"]
        assert tavern.session_info(result["session_id"]) is not None
    finally:
        llm_mod.chat = original
    print("[OK] 自动插话：她可以选择沉默（沉默不建局），也可以开口")


def _test_table_registry_coverage() -> None:
    """新表五件套：双 reset 清单与关系包类别必须覆盖酒馆剧情表。"""
    import inspect

    from backend.core import reset as reset_module
    from backend.core.relationship_export import CATEGORIES

    assert "tavern_sessions" in reset_module._TABLES, "reset 权威清单必须覆盖酒馆剧情表"
    assert "tavern_sessions" in CATEGORIES["life"], "关系包导出必须带走酒馆剧情"
    # reset.py 的 _TABLES 与 userdb.reset() 的降级清空清单是两份、必须同步。
    # db.reset 被 _locked 装饰器包裹（无 functools.wraps），getsource 取到的是
    # wrapper，因此按闭包定位原始函数再断言。
    cells = getattr(db.reset, "__closure__", None) or ()
    raw_reset = next(
        (
            cell.cell_contents
            for cell in cells
            if callable(getattr(cell, "cell_contents", None))
            and getattr(getattr(cell, "cell_contents", None), "__name__", "") == "reset"
        ),
        None,
    )
    assert raw_reset is not None, "无法定位 UserDB.reset 原始函数（装饰器结构变化？）"
    assert "tavern_sessions" in inspect.getsource(raw_reset), (
        "userdb.reset 降级清空清单必须覆盖酒馆剧情表"
    )
    print("[OK] 清单覆盖：reset 双清单与关系包类别均含 tavern_sessions")


def _test_export_restore_reassigns_text_id() -> None:
    """剧情随关系包迁移：文本主键必须重新分配（原值跨命名空间会主键冲突）。"""
    from backend.core import relationship_export as rex

    bundle = rex.export_bundle(UID)
    rows = bundle["data"].get("tavern_sessions") or []
    assert rows, "已收局的酒馆剧情应随关系包导出"
    old_ids = {str(row["id"]) for row in rows}
    old_turns = {str(row["turns_json"]) for row in rows}

    for target in ("tavern-restored-a", "tavern-restored-b"):
        payload = rex.bundle_from_json(rex.bundle_to_json(bundle))
        preview = rex.preview_restore(payload, target)
        assert preview["ok"], preview["errors"]
        assert rex.restore_bundle(payload, target)["ok"]
        with db._lock:
            restored = db.conn.execute(
                "SELECT id, card_name, summary, turns_json FROM tavern_sessions WHERE user_id = ?",
                (target,),
            ).fetchall()
        assert len(restored) == len(rows)
        assert {str(row["id"]) for row in restored}.isdisjoint(old_ids), "文本主键必须重新分配"
        assert any(row["card_name"] == "酒馆老板娘" for row in restored)
        assert any(str(row["summary"]).strip() for row in restored)
        assert {str(row["turns_json"]) for row in restored} == old_turns, "剧情原文必须逐字保留"
    print("[OK] 导出恢复：剧情随包迁移、原文逐字保留、文本主键重分配且可重复导入")


def main() -> None:
    db.conn.execute("SELECT 1")  # 确保临时数据目录初始化
    _test_turn_and_session()
    _test_validation()
    _test_scene_prompt_rules()
    _test_http_gate()
    _test_save_and_recall()
    _test_auto_silent()
    _test_table_registry_coverage()
    _test_export_restore_reassigns_text_id()
    print("酒馆同玩测试通过（点名 + 沉淀 + 回忆 + 看着办 + 清单/导出恢复）")


if __name__ == "__main__":
    main()
