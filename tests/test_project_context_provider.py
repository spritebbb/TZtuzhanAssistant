# -*- coding: utf-8 -*-
"""38项#33 工作流累积：ProjectContextProvider（topic 向量分区从死数据变语境）。

验收锚点：
- provider 默认注册（priority 55、namespace work/project）；
- 工作相关 query：向量命中 + bigram 相关性（交集 ≥1）→ 产出候选，
  render 文本含「这是他的项目/工作上下文…别汇报」声明；
- 无关 query：向量层哪怕返回命中，bigram 校验不过 → 零注入；
- topic 无表行：refresh 恒 None → sticky 不续（命中轮之后普通轮静默），
  fresh 命中不受冷却限制。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_38_33_"))

WORK_QUERY = "菟菚助手的记忆模块重构得怎么样了"
IDLE_QUERY = "今晚吃什么好呢"
TOPIC_TEXT = "他在做菟菚助手的开发，最近在重构记忆模块"


def _only_project_provider() -> dict:
    """临时只留 project provider（避免其它 provider 数据干扰断言），返回被摘除项。"""
    from backend.core import context_registry as cr

    removed = {k: cr._PROVIDERS.pop(k) for k in list(cr._PROVIDERS) if k != "project_context"}
    return removed


def _restore_providers(removed: dict) -> None:
    from backend.core import context_registry as cr

    cr._PROVIDERS.update(removed)


def _install_fake_topic_hits():
    """把向量层替换为恒返回一条假 topic 命中（模拟 Chroma 检索结果）。"""
    from backend.core.memory import vector_store as vec
    from backend.core.memory.vector_store import SearchHit

    original = vec.search

    def _fake_search(user_id, query, top_k=5, kind=None):
        assert kind == "topic", "project provider 必须只检索 topic 分区"
        return [SearchHit(record_id=1_000_000_042, distance=0.3, text=TOPIC_TEXT)]

    vec.search = _fake_search
    return vec, original


def test_provider_registered_and_collects() -> int:
    from backend.core import context_registry as cr

    cr._ensure_default_providers()
    assert "project_context" in cr._PROVIDERS, "project provider 必须默认注册"
    provider = cr._PROVIDERS["project_context"]
    assert provider.entry.priority == 55.0 and provider.entry.namespace == "work/project"

    removed = _only_project_provider()
    vec, original = _install_fake_topic_hits()
    try:
        uid = "3833-work"
        sel = cr.collect_context(uid, WORK_QUERY, turn_id=1)
        items = [i for i in sel.items if i.entry_id == "project_context"]
        assert items and items[0].fresh, "工作相关 query 应命中 topic 上下文"
        assert items[0].text == TOPIC_TEXT
        assert items[0].token_cost == len(TOPIC_TEXT)

        rendered = sel.assemble()
        assert TOPIC_TEXT in rendered
        assert "这是他的项目/工作上下文" in rendered and "别汇报" in rendered, rendered
        entry = next(e for e in sel.explain() if e["id"].startswith("project_context:"))
        assert entry["namespace"] == "work/project" and entry["reasons"] == ["topic_match"]

        # 无关 query：向量层照样返回命中，但 bigram 校验不过 → 零注入
        sel_idle = cr.collect_context(uid, IDLE_QUERY, turn_id=2)
        assert not [i for i in sel_idle.items if i.entry_id == "project_context"], \
            "无关闲聊不得注入工作上下文"
        assert not sel_idle.items, "隔离后无其它 provider，整体应零注入"
    finally:
        vec.search = original
        _restore_providers(removed)
    print("[OK] 默认注册 / 工作命中产出+声明 / 无关 query 零注入")
    return 0


def test_refresh_none_no_sticky() -> int:
    from backend.core import context_registry as cr

    removed = _only_project_provider()
    vec, original = _install_fake_topic_hits()
    try:
        uid = "3833-lifecycle"
        # refresh 恒 None：sticky 不可能复活
        assert cr._PROVIDERS["project_context"].refresh(uid, "1000000042") is None

        # turn 1 命中并提交 → turn 2 普通轮静默（无 sticky 续命）
        sel1 = cr.collect_context(uid, WORK_QUERY, turn_id=1)
        cr.commit_context_turn(uid, 1, sel1.fresh_entry_keys)
        sel2 = cr.collect_context(uid, IDLE_QUERY, turn_id=2)
        assert not [i for i in sel2.items if i.entry_id == "project_context"], \
            "topic 无表行：sticky 不得延续"

        # fresh 命中不受冷却限制（用户明确聊到就给）
        sel3 = cr.collect_context(uid, WORK_QUERY, turn_id=2)
        assert [i for i in sel3.items if i.entry_id == "project_context"]

        # turn 3 仍处名义 sticky 窗口，但 refresh=None → 普通轮依旧静默；
        # 唯一注入路径就是每轮 collect 的相关性命中
        sel4 = cr.collect_context(uid, IDLE_QUERY, turn_id=3)
        assert not [i for i in sel4.items if i.entry_id == "project_context"]
    finally:
        vec.search = original
        _restore_providers(removed)
    print("[OK] refresh=None / sticky 不续 / fresh 不受限")
    return 0


def main() -> int:
    failed = (
        test_provider_registered_and_collects()
        + test_refresh_none_no_sticky()
    )
    if failed:
        print(f"\n=== 38项#33 工作上下文：{failed} 项失败 ===")
        return 1
    print("\n=== 38项#33 工作上下文：全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
