# -*- coding: utf-8 -*-
"""边界回归单测：把历史审查中锁定的边界 bug 沉淀成 pytest 可收集的用例。

pytest.ini 配置 python_functions = suite_*，本文件所有测试函数用 suite_ 前缀
（而非 test_），由 `pytest tests/` 直接收集执行，不依赖 test_suite_runner.py
的子进程编排。

覆盖（对应 CODE-REVIEW-V15.md 各轮修复）：
1. 降级路径辱骂「双扣」：perception._fallback_rule 辱骂时 affection_delta 必须为 0，
   好感度统一交关键词兜底 apply_abuse_penalty，避免「apply_impulse -5 + 兜底 -5」。
2. 归档搜索返回面收窄：preview 摘要生成 + 查询词长度上限 + 命中不含完整 messages。
3. 主动消息幂等：最后一条 bot content 相同则跳过落库（防重启/重连重复气泡）。
4. 归档搜索路由顺序：/archives/search 必须在 /archives/{id} 动态路由之前声明。
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import get_type_hints

import pytest


# ---- 1. 降级路径辱骂双扣 ----

def suite_fallback_abuse_has_zero_affection_delta():
    """降级规则判定为辱骂时，affection_delta 必须为 0（好感度交关键词兜底）。

    若这里返回 -5，pipeline 会「apply_impulse(-5) + apply_abuse_penalty(-5)」双扣。
    """
    from backend.core.perception import _fallback_rule
    from backend.core import affection

    # 选一个明确命中辱骂词库的输入（避免「骂别人」被 _ABUSE_OTHER_TARGET 过滤）
    abuse_text = "你这个傻逼"
    assert affection.check_abuse(abuse_text), "测试输入应命中辱骂词库"

    r = _fallback_rule(abuse_text)
    assert r["degraded"] is True, "降级结果必须标记 degraded"
    assert r["abuse"] is True
    assert r["affection_delta"] == 0, (
        "降级辱骂的 affection_delta 必须为 0，否则 pipeline 会双扣 "
        f"（实际={r['affection_delta']}）"
    )
    # 情绪冲击仍要保留（这是 emotion 维度，非好感度维度，不该被清掉）
    assert r["emotional_hit"] == "被冒犯了"


def suite_fallback_non_abuse_keeps_affection_delta():
    """降级规则对正向信号（关心）仍保留 affection_delta（语义覆盖不到的部分）。"""
    from backend.core.perception import _fallback_rule
    from backend.core import affection

    care_text = "你辛苦了，早点休息"
    if not affection.check_care(care_text):
        pytest.skip("关心词库未命中测试输入，跳过")

    r = _fallback_rule(care_text)
    assert r["degraded"] is True
    assert r["affection_delta"] == 1, "降级关心仍应保留 affection_delta=1"


# ---- 2. 归档搜索返回面收窄 ----

def suite_archive_preview_extracts_hit_context():
    """preview 应抽取出含命中词的摘要片段，而非返回完整消息或空串。"""
    from backend.session.store import _preview_for

    messages = [
        {"role": "user", "content": "今天天气不错"},
        {"role": "bot", "content": "我查了襄阳的天气预报，明天会下雨"},
    ]
    payload = importlib.import_module("json").dumps(messages, ensure_ascii=False)

    preview = _preview_for(payload, "天气")
    assert "天气" in preview, f"preview 应包含命中词（实际={preview!r}）"


def suite_archive_preview_returns_empty_for_bad_json():
    """messages_json 损坏时 preview 返回空串，不抛异常。"""
    from backend.session.store import _preview_for
    assert _preview_for("{not valid json", "任意词") == ""


def suite_archive_search_caps_query_length():
    """超长查询词应被截断到 _SEARCH_QUERY_MAX，避免超长 LIKE 输入。"""
    from backend.session import store

    assert store._SEARCH_QUERY_MAX == 200
    assert store._SEARCH_LIMIT == 50


def suite_archive_search_treats_wildcards_literally(tmp_path, monkeypatch):
    """搜索框里的 SQL LIKE 通配符应当按普通字符匹配。"""
    from backend.core.persona_profiles import active_id
    from backend.session import store

    monkeypatch.setattr(store, "_DB", tmp_path / "wildcards.db")
    store._init()
    conn = store._connect()
    try:
        conn.executemany(
            "INSERT INTO archives (id, title, created_at, message_count, messages_json, persona_id) "
            "VALUES (?, ?, ?, 1, ?, ?)",
            [
                ("literal", "100%_done", 2.0, json.dumps([{"content": "literal"}]), active_id()),
                ("plain", "ordinary", 1.0, json.dumps([{"content": "plain"}]), active_id()),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    assert [row["id"] for row in store._search_archives_sync("%")] == ["literal"]
    assert [row["id"] for row in store._search_archives_sync("_")] == ["literal"]


def suite_pipeline_user_lock_type_hints_resolve():
    """运行时解析 pipeline 注解不应因 asyncio 只在函数内导入而失败。"""
    import asyncio

    from backend.core import pipeline

    assert get_type_hints(pipeline._user_lock)["return"] is asyncio.Lock


def suite_meta_search_status_matches_real_fallback(monkeypatch):
    """没有博查 key 时仍可走 Bing/DDG，能力面板不能错误显示为关闭。"""
    from backend.api import meta

    monkeypatch.setattr(meta.config, "search_enabled", True)
    monkeypatch.setattr(meta.config, "search_api_key", "")
    assert meta._tool_status()["search"] is True
    monkeypatch.setattr(meta.config, "search_enabled", False)
    assert meta._tool_status()["search"] is False


def suite_persona_state_assets_have_real_alpha():
    """五档立绘必须是真透明 PNG，防止把棋盘格 RGB 误当成透明资产上线。"""
    from PIL import Image

    state_dir = Path(__file__).resolve().parents[1] / "assets" / "persona_states"
    for state in ("low", "plain", "lazy", "happy", "excited"):
        path = state_dir / f"{state}.png"
        assert path.is_file(), f"缺少 {state} 立绘"
        with Image.open(path) as image:
            assert image.mode == "RGBA", f"{state} 不是 RGBA"
            alpha_min, alpha_max = image.getchannel("A").getextrema()
            assert alpha_min == 0 and alpha_max == 255, f"{state} alpha 范围异常"


# ---- 3. 主动消息幂等 ----

def suite_proactive_append_is_idempotent(tmp_path, monkeypatch):
    """最后一条 bot 消息 content+image 相同才跳过，避免吞掉不同配图。"""
    from backend.session import store

    # 把会话 DB 指到临时目录，避免污染真实数据
    monkeypatch.setattr(store, "_DB", tmp_path / "sessions.db")
    store._init()

    sid = store.CURRENT_SESSION_ID
    # 先确保会话存在
    conn = store._connect()
    conn.execute("INSERT OR REPLACE INTO sessions (id, title, created_at, updated_at) VALUES (?,?,?,?)",
                 (sid, "新会话", 0.0, 0.0))
    conn.commit()
    conn.close()

    text = "菟菚主动说的同一句话"
    image = "/api/images/selfie.png"
    assert store._append_proactive_sync(sid, text, image) is True, "首次应落库"
    assert store._append_proactive_sync(sid, text, image) is False, "同内容同图片应被幂等跳过"
    assert store._append_proactive_sync(sid, text, "/api/images/doodle.png") is True, (
        "相同文字但图片不同不能误判为重复"
    )


def suite_message_explanation_round_trips(tmp_path, monkeypatch):
    """解释快照应随当前会话和归档持久化，旧消息则安全返回 null。"""
    from backend.session import store

    monkeypatch.setattr(store, "_DB", tmp_path / "explanation.db")
    store._init()
    snapshot = {
        "version": 1,
        "state": {"affection": 50, "stage": "亲密", "mood": 72, "mood_label": "开心", "energy": 64},
        "behavior": [{"label": "关系分寸", "text": "可以主动一些"}],
        "memories": [{"kind": "长期事实", "text": "用户喜欢雨天"}],
        "tools": {"search": False, "media": "none"},
    }
    assert store._append_sync(store.CURRENT_SESSION_ID, [
        {"role": "user", "content": "你好", "ts": 1},
        {"role": "bot", "content": "行", "explanation": snapshot, "ts": 2},
    ])
    messages = store._get_messages_sync(store.CURRENT_SESSION_ID)
    assert messages[0]["explanation"] is None
    assert messages[1]["explanation"] == snapshot
    archived = store._archive_current_sync()
    detail = store._get_archive_sync(archived["id"])
    assert detail["messages"][1]["explanation"] == snapshot


# ---- 4. 归档搜索路由顺序 ----

def suite_archive_search_route_declared_before_dynamic():
    """/archives/search 必须声明在 /archives/{archive_id} 之前，否则被动态路由吞掉。"""
    from backend.api.sessions import router

    paths = [getattr(r, "path", "") for r in router.routes]
    search_idx = paths.index("/api/sessions/archives/search")
    dynamic_idx = paths.index("/api/sessions/archives/{archive_id}")
    assert search_idx < dynamic_idx, (
        "/archives/search 必须在 /archives/{archive_id} 之前声明，"
        "否则搜索请求会被动态路由吞掉返回 404"
    )


# ---- 5. rebuild_all 自锁（P1）：重建期间 migrate 重灌必须放行 ----

def suite_rebuild_migrate_write_is_not_self_locked(monkeypatch):
    """rebuild_all 置 _rebuilding=True 后调 migration.migrate() 全量重灌。

    P1 自锁回归：rebuild_all 清空 _collections 缓存后置 _rebuilding=True，
    若 migration 写入走 add() 默认闸门、或 _collection() 第二道闸门不放行，
    清库后每一条重灌都会被拒 → 重灌 0 条 → 语义检索静默失效（仅 TF-IDF 兜底）。
    修复：vec.add 加 _allow_during_rebuild 参数，migration 重灌时显式放行，
    且 add() 必须把该参数透传给 _collection()（两道闸门同步放行）。

    本用例走真实 _collection() 闸门链，不触碰 chromadb / embedding 真实写入：
    - _embedding_ready_for_write 恒真（越过就绪检查）
    - 清空 _collections 缓存 + 打桩 _client_instance（模拟 rebuild_all 删除后的空缓存
      现场；get_or_create 返回记录 upsert 的 fake collection）
    注意：不得整体替换 vec._collection——那会绕过本缺陷所在的第二道闸门，
    使测试在 P1 未闭环时虚假通过（历史教训）。
    """
    import pytest

    from backend.core.memory import vector_store as vec

    # 让 add 越过 embedding-ready 检查，直达 _rebuilding 闸门决策。
    monkeypatch.setattr(vec, "_embedding_ready_for_write", lambda: True)

    upserts = []

    class _FakeCol:
        def upsert(self, **kw):
            upserts.append(kw)

    class _FakeClient:
        def get_or_create_collection(self, name, embedding_function=None, metadata=None):
            return _FakeCol()

    # 模拟 rebuild_all 现场：缓存已清空、client 层已删库（get_or_create 重建即可写）
    monkeypatch.setattr(vec, "_collections", {})
    monkeypatch.setattr(vec, "_client_instance", lambda: _FakeClient())

    # 场景 A：重建期间，业务写入（未传放行参数）→ 被 add() 内层闸门拦截，不触达 upsert
    monkeypatch.setattr(vec, "_rebuilding", True)
    assert vec.add("u", "lm", 1, "text") is False
    assert upserts == [], "重建期间业务写入不应触达 upsert（被闸门拦截）"

    # 场景 B：重建期间，migrate 重灌（_allow_during_rebuild=True）→ 真实 _collection()
    # 缓存 miss 后必须同步放行（第二道闸门），完成 get_or_create + upsert
    assert vec.add("u", "lm", 2, "text", _allow_during_rebuild=True) is True
    assert len(upserts) == 1, "重建期间 migrate 重灌必须穿透两道闸门完成 upsert，否则 P1 自锁"

    # 场景 C：非重建期，业务写正常触达 upsert
    monkeypatch.setattr(vec, "_rebuilding", False)
    assert vec.add("u", "lm", 3, "text") is True
    assert len(upserts) == 2


def suite_migrate_table_passes_rebuild_exemption(monkeypatch):
    """migration._migrate_table 对 vec.add 的每次调用必须传 _allow_during_rebuild=True。

    防止未来重构把该参数漏掉，重新引入「重建时 migrate 自锁、重灌 0 条」的 P1。
    包裹 vec.add 捕获实际 kwargs 断言。不触碰真实 SQLite / chroma：
    - monkeypatch _sqlite_rows 返回内存行（跳过真实读库）
    - monkeypatch vec.add 捕获 kwargs
    """
    import backend.core.memory.vector_store as vec
    import backend.core.memory.migration as mig

    captured = {}

    def _fake_add(*args, **kwargs):
        captured["kwargs"] = kwargs
        return True

    monkeypatch.setattr(vec, "add", _fake_add)

    class _Row(dict):
        """dict 子类让 r['content'] 可用，替代 sqlite3.Row。"""

    def _rows(table):
        if table == "long_memory":
            return [_Row(id=1, user_id="u1", content="一条长期记忆")]
        return []

    monkeypatch.setattr(mig, "_sqlite_rows", _rows)

    stats = {}
    done = mig._migrate_table("long_memory", "lm", lambda r: r["content"], stats)
    assert done == 1
    assert captured["kwargs"].get("_allow_during_rebuild") is True, (
        "migration 写向量必须传 _allow_during_rebuild=True，否则重建期 migrate 自锁"
    )
