# -*- coding: utf-8 -*-
"""测试套件运行器：逐个在子进程中执行现有 test_*.py 脚本并断言退出码为 0。

背景：仓库现有测试是「可独立运行的脚本」风格（`asyncio.run(main())`），
不是 pytest 收集的 test 函数。本运行器把它们接入 pytest，
使 `pytest tests/` 或 `python -m pytest tests/` 即可一键验证整套回归。

排除项：
- test_live_chat.py / test_recall_real.py：需要真实 LLM/网络，CI 不跑。
- test_suite_runner.py 自身。
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent

# 需要真实 LLM/外部服务的测试（默认跳过）
_SKIP = {"test_live_chat.py", "test_recall_real.py"}

# 会改变被测产品行为、且可能被 conftest 在导入期泄漏进 pytest 进程的环境变量：
# 复制给子进程前先剔除，避免「全量跑」与「单跑」行为不一致（审计 P3-6）。
_LEAKY_ENV_VARS = (
    "MEMORY_V2", "MEMORY_MEM0", "MEMORY_EMBED_FORCE", "MEMORY_VECTOR",
    "MOOD_CITY", "AGENT_DEMO_MODE", "TZTUZHAN_DEMO_MODE",
)


def _scripts() -> list[Path]:
    return sorted(
        p for p in TESTS_DIR.glob("test_*.py")
        if p.name not in _SKIP and p.name != "test_suite_runner.py"
    )


@pytest.mark.parametrize("script", _scripts(), ids=lambda p: p.name)
def suite_script(script: Path, tmp_path: Path) -> None:
    """运行单个测试脚本，任何非零退出码都视为失败。

    函数名不带 test_ 前缀：pytest.ini 里 python_functions=suite_*，
    让 pytest 只收集本运行器，避免与脚本内的 async test_* 函数重复收集
    （脚本内函数由各自的 main() 编排执行）。
    """
    # P3-6：先剔除「会改变产品行为」的测试专用变量——它们可能被别的 conftest 在
    # 导入期泄漏进 pytest 进程（实测 tests/security/conftest.py 设 MEMORY_V2=0），
    # 再经 os.environ.copy() 污染每一个子进程，造成「单跑绿、全量红」的顺序耦合。
    # 每个脚本各自 setdefault 所需值，剔除后行为等同单独运行。
    env = {k: v for k, v in os.environ.items() if k not in _LEAKY_ENV_VARS}
    # 每个脚本独占数据目录。多数脚本用 setdefault() 自建临时目录；调用方若设置了
    # TZTUZHAN_DATA_DIR，则旧运行器会让全部子进程共享同一个 bot.db，产生顺序污染。
    env["TZTUZHAN_DATA_DIR"] = str(tmp_path / "data")
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    if proc.returncode != 0:
        tail = (proc.stdout or "")[-2000:] + (proc.stderr or "")[-2000:]
        pytest.fail(f"{script.name} 退出码 {proc.returncode}\n{tail}")
