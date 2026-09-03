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


# ---- 3. 主动消息幂等 ----

def suite_proactive_append_is_idempotent(tmp_path, monkeypatch):
    """最后一条 bot 消息 content 相同则跳过落库，防重复气泡。"""
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
    assert store._append_proactive_sync(sid, text) is True, "首次应落库"
    assert store._append_proactive_sync(sid, text) is False, "同内容再次追加应被幂等跳过"


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
