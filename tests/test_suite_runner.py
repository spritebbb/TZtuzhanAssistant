# -*- coding: utf-8 -*-
"""测试套件运行器：逐个在子进程中执行现有 test_*.py 脚本并断言退出码为 0。

背景：仓库现有测试是「可独立运行的脚本」风格（`asyncio.run(main())`），
不是 pytest 收集的 test 函数。本运行器把它们接入 pytest，
使 `pytest tests/` 或 `python -m pytest tests/` 即可一键验证整套回归。

排除项：
- test_live_chat.py / test_recall_real.py：需要真实 LLM/网络，CI 不跑。
- test_suite_runner.py 自身。
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent

# 需要真实 LLM/外部服务的测试（默认跳过）
_SKIP = {"test_live_chat.py", "test_recall_real.py"}


def _scripts() -> list[Path]:
    return sorted(
        p for p in TESTS_DIR.glob("test_*.py")
        if p.name not in _SKIP and p.name != "test_suite_runner.py"
    )


@pytest.mark.parametrize("script", _scripts(), ids=lambda p: p.name)
def suite_script(script: Path) -> None:
    """运行单个测试脚本，任何非零退出码都视为失败。

    函数名不带 test_ 前缀：pytest.ini 里 python_functions=suite_*，
    让 pytest 只收集本运行器，避免与脚本内的 async test_* 函数重复收集
    （脚本内函数由各自的 main() 编排执行）。
    """
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    if proc.returncode != 0:
        tail = (proc.stdout or "")[-2000:] + (proc.stderr or "")[-2000:]
        pytest.fail(f"{script.name} 退出码 {proc.returncode}\n{tail}")
