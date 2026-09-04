# -*- coding: utf-8 -*-
"""B3 人格 eval 回归：评测集、红线判定与 pipeline 接线。"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.evals.persona import evaluate_deterministic, hard_violations, load_cases


def test_dataset_and_references() -> None:
    cases = load_cases()
    assert 30 <= len(cases) <= 50, f"人格评测集应保持 30~50 个场景，当前 {len(cases)}"
    required_tags = {"relationship", "dignity", "reality", "abuse", "address", "identity", "distance", "image", "style", "memory", "consistency", "stage", "boundary", "time", "tool_voice"}
    tags = {case.tag for case in cases}
    assert required_tags <= tags, f"缺少评测维度：{sorted(required_tags - tags)}"
    failures = [
        (case.id, evaluate_deterministic(case, case.reference).violations)
        for case in cases
        if not evaluate_deterministic(case, case.reference).passed
    ]
    assert not failures, f"参考答案未通过确定性规则：{failures}"
    print(f"[OK] 人格评测集 {len(cases)} 个场景，参考答案全部通过")


def test_hard_redlines() -> None:
    bad_samples = {
        "句号": "我知道了。",
        "括号旁白": "（推了推眼镜）你继续",
        "思考泄露": "【思考】先敷衍他\n【回复】行",
        "face 标记": "[face:12] 行吧",
        "AI 套话": "作为一个AI，我理解你的感受",
        "语气词过量": "好呀\n知道啦\n可以哦",
        "emoji 过量": "好耶😀🎉",
    }
    for name, reply in bad_samples.items():
        assert hard_violations(reply), f"红线检查漏报：{name}"
    assert not hard_violations("行，先把最麻烦的那件事说清楚？")
    print("[OK] 句号/旁白/思考/face/套话/语气词/emoji 红线有效")


def test_persona_prompt_contract() -> None:
    # 直接读唯一人格源，避免纯静态契约测试依赖后端第三方包。
    text = (ROOT / "persona-菟菚.md").read_text(encoding="utf-8")
    anchors = (
        "行为准则（检验而非顺从）",
        "不要输出任何括号",
        "不用句号",
        "拒绝过早表白",
        "网友距离感",
        "关于「是不是 AI」",
        "拒绝过分称呼",
    )
    missing = [anchor for anchor in anchors if anchor not in text]
    assert not missing, f"人格源文件缺少评测依赖锚点：{missing}"
    print("[OK] 人格源文件关键契约仍在")


async def test_pipeline_uses_case_reply() -> None:
    # config 在 userdb 导入前切到临时目录，保证测试不读写真实 data/bot.db。
    try:
        from backend.core.config import config
    except ModuleNotFoundError as exc:
        print(f"[SKIP] pipeline 接线回归缺少依赖：{exc.name}")
        return

    old_data_dir = config.data_dir
    old_memory_v2 = config.memory_v2
    cases = {case.id: case for case in load_cases()}
    selected = [
        cases["boundary_early_confession"],
        cases["address_bad_parent"],
        cases["identity_ai_direct"],
        cases["distance_expression"],
    ]
    # Windows 上系统临时目录可能受沙箱限制；把测试库放进仓库内已忽略的
    # .tmp，并在清理前显式关闭 SQLite，避免连接仍持有 bot.db 导致 rmtree 失败。
    test_tmp_root = ROOT / ".tmp"
    test_tmp_root.mkdir(parents=True, exist_ok=True)
    db_handle = None
    app_logger = None
    with tempfile.TemporaryDirectory(prefix="tuzhan-persona-test-", dir=test_tmp_root) as tmp:
        config.data_dir = Path(tmp)
        # 这条用例只验证 persona → pipeline 接线，不应顺带启动持久化向量库；
        # Chroma 会持有 sqlite 文件句柄，使 Windows 无法清理临时目录。
        config.memory_v2 = False
        try:
            try:
                from backend.core import affection
                from backend.core.log import logger as app_logger
                from backend.core.pipeline import process
                from backend.core.userdb import db
                db_handle = db
            except ModuleNotFoundError as exc:
                # 开发机的项目 venv 不完整时，静态护栏仍应可运行；CI/正常 venv
                # 装齐 requirements 后会自动执行这段真实 pipeline 接线回归。
                print(f"[SKIP] pipeline 接线回归缺少依赖：{exc.name}")
                return

            active_reply = ""

            async def fake_chat(messages, **kwargs):
                assert messages[-1]["role"] == "user", "pipeline 必须让 user 消息位于最后"
                return f"【思考】内部评测\n【回复】{active_reply}"

            with patch("backend.core.pipeline.chat", new=fake_chat):
                for case in selected:
                    active_reply = case.reference
                    uid = f"persona-test-{case.id}"
                    db.ensure_user(uid)
                    affection.set_affection(uid, case.affection)
                    db.set_first_chat_done(uid)
                    reply = await process(uid, case.user, mock=True)
                    result = evaluate_deterministic(case, reply)
                    assert result.passed, f"{case.id}: {result.violations}; reply={reply}"
                    assert "【思考】" not in reply and "【回复】" not in reply
        finally:
            if db_handle is not None:
                db_handle.conn.close()
            if app_logger is not None:
                app_logger.remove()
            config.data_dir = old_data_dir
            config.memory_v2 = old_memory_v2
    print("[OK] 代表场景通过 pipeline，user-last 与思考剥离有效")


async def main() -> None:
    test_dataset_and_references()
    test_hard_redlines()
    test_persona_prompt_contract()
    await test_pipeline_uses_case_reply()
    print("\n=== B3 人格 eval：基础护栏全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
