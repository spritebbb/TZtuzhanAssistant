# -*- coding: utf-8 -*-
"""M8 阶段封存与告别：选择性导出纪念包、LLM 告别信（失败回退）、标准包可恢复、
只导出不删除。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZTUZHAN_DATA_DIR", tempfile.mkdtemp(prefix="tztuzhan_test_sealing_"))
os.environ.setdefault("MEMORY_V2", "0")

from backend.core import relationship_export as rex, sealing  # noqa: E402
from backend.core.relationship_events import record as record_event  # noqa: E402
from backend.core.userdb import db, save_promise  # noqa: E402

UID = "assistant-main"
TARGET = "sealing-restored"


def _rows(sql: str, args: tuple) -> list:
    with db._lock:
        return db.conn.execute(sql, args).fetchall()


async def _test_seal_llm_letter_and_structure() -> None:
    db.ensure_user(UID)
    promise_id = save_promise(UID, "一起看完那本书")
    assert promise_id is not None
    event_id = record_event(
        UID, "promise_completed", source_type="promise", source_id=promise_id,
        obj="一起看完那本书", commit=True,
    )
    assert event_id is not None

    captured = {}

    async def fake_chat(messages, **kwargs):
        captured["prompt"] = messages[1]["content"]
        return "这些一起走完的日子，我轻轻收进信封了。不道别，我们只是慢慢往前走。"

    with patch("backend.core.sealing.chat", new=fake_chat):
        bundle = await sealing.seal(UID, ["memory", "tasks", "events"])

    s = bundle["sealing"]
    assert bundle["kind"] == rex.BUNDLE_KIND, "纪念包必须是标准关系包"
    assert s["letter_generated_by"] == "llm"
    assert s["letter"].startswith("这些一起走完的日子")
    # prompt 只含真实统计：必须有约定计数，不得含未选类别（如 conversations）
    prompt = captured["prompt"]
    assert "约定与任务" in prompt and "promises 1" in prompt
    assert "聊天记录" not in prompt
    assert "identity" in s["categories"], "身份档案应自动携带"
    # 计数与真实行一致
    assert s["counts"]["tasks"]["promises"] == 1
    assert s["counts"]["events"]["relationship_events"] == 1
    # 只导出不删除
    assert _rows("SELECT id FROM promises WHERE user_id = ?", (UID,))
    print("[OK] 封存：LLM 告别信 + prompt 只含真实统计 + 标准包结构 + 原数据未动")


async def _test_letter_fallback_on_llm_failure() -> None:
    async def failing_chat(messages, **kwargs):
        raise RuntimeError("model down")

    with patch("backend.core.sealing.chat", new=failing_chat):
        bundle = await sealing.seal(UID, ["events"])
    s = bundle["sealing"]
    assert s["letter_generated_by"] == "fallback"
    assert "收进箱子" in s["letter"], "回退告别文应确定性地引用统计"
    print("[OK] LLM 失败：回退确定性告别文，封存流程不失败")


async def _test_bundle_restorable_and_letter_flag_off() -> None:
    bundle = await sealing.seal(UID, ["tasks", "events"], letter=False)
    s = bundle["sealing"]
    assert s["letter_generated_by"] == "fallback" and "收进箱子" in s["letter"]

    preview = rex.preview_restore(bundle, TARGET)
    assert preview["ok"], preview["errors"]
    result = rex.restore_bundle(bundle, TARGET)
    assert result["ok"], "纪念包应可被标准恢复通道直接恢复"
    restored = _rows("SELECT content FROM promises WHERE user_id = ?", (TARGET,))
    assert any(row["content"] == "一起看完那本书" for row in restored)
    print("[OK] 纪念包走标准恢复通道可还原；letter=False 仍给确定性告别文")


async def _test_invalid_category() -> None:
    try:
        await sealing.seal(UID, ["magic"])
        raise AssertionError("未知类别应拒绝")
    except rex.BundleError as exc:
        assert "未知的数据类别" in str(exc)
    print("[OK] 非法类别拒绝")


async def main() -> None:
    await _test_seal_llm_letter_and_structure()
    await _test_letter_fallback_on_llm_failure()
    await _test_bundle_restorable_and_letter_flag_off()
    await _test_invalid_category()
    print("\n=== M8 阶段封存与告别：全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
