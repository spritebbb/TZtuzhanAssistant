# -*- coding: utf-8 -*-
"""测试套件运行器：把现有 test_*.py 脚本接入 pytest，并并发执行。

背景：仓库现有测试是「可独立运行的脚本」风格（`asyncio.run(main())`），
不是 pytest 收集的 test 函数。本运行器把它们接入 pytest，
使 `pytest tests/` 或 `python -m pytest tests/` 即可一键验证整套回归。

并发：199 个脚本各起一个子进程、彼此隔离，串行跑约 14 分钟。这里用一个
线程池同时跑多个子进程（真正的活在子进程里，GIL 无影响），实测 ~4 倍加速。
并发度默认 min(8, CPU 核数)，可用环境变量 TZ_SUITE_WORKERS 覆盖。

保持按用例粒度：脚本仍逐个参数化成 suite_script[...]，`-k`/节点选择照常；
只有被选中的脚本才会进池（从 session 的已选用例反推），所以 `-k xxx` 仍是
只跑那一个、不做无用功。

已知取舍：`-x`（首错即停）不会中断已在池中的其余脚本——池在首个断言前就跑完
选中的脚本，`-x` 只影响断言阶段的报告。

排除项：
- test_live_chat.py / test_recall_real.py：需要真实 LLM/网络，CI 不跑。
- test_suite_runner.py 自身。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# 单脚本超时（秒）；并发下仍在各自子进程内独立计时
_TIMEOUT = 600


def _workers() -> int:
    raw = os.environ.get("TZ_SUITE_WORKERS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return max(1, min(8, os.cpu_count() or 4))


def _scripts() -> list[Path]:
    return sorted(
        p for p in TESTS_DIR.glob("test_*.py")
        if p.name not in _SKIP and p.name != "test_suite_runner.py"
    )


def _run_script(script: Path, data_dir: Path) -> tuple[int, str, float]:
    """在独立子进程中跑一个脚本，返回 (退出码, 输出尾巴, 耗时秒)。

    P3-6：先剔除「会改变产品行为」的测试专用变量——它们可能被别的 conftest 在
    导入期泄漏进 pytest 进程（实测 tests/security/conftest.py 设 MEMORY_V2=0），
    再经 os.environ.copy() 污染每一个子进程，造成「单跑绿、全量红」的顺序耦合。
    每个脚本各自 setdefault 所需值，剔除后行为等同单独运行。
    """
    env = {k: v for k, v in os.environ.items() if k not in _LEAKY_ENV_VARS}
    # 每个脚本独占数据目录：脚本多用 setdefault() 自建临时目录；若调用方设置了
    # TZTUZHAN_DATA_DIR 而各子进程共享同一 bot.db，会产生顺序污染。
    env["TZTUZHAN_DATA_DIR"] = str(data_dir)
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", str(script)],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        tail = ((exc.stdout or "") if isinstance(exc.stdout, str) else "")[-2000:]
        return 124, f"（超时 >{_TIMEOUT}s）\n{tail}", time.perf_counter() - t0
    tail = (proc.stdout or "")[-2000:] + (proc.stderr or "")[-2000:]
    return proc.returncode, tail, time.perf_counter() - t0


def _selected_scripts(request) -> list[Path]:
    """本次会话实际选中的脚本（尊重 -k / 节点选择；未选中则不进池）。"""
    picked: list[Path] = []
    for item in request.session.items:
        spec = getattr(item, "callspec", None)
        if spec is not None and "script" in spec.params:
            picked.append(spec.params["script"])
    return picked


@pytest.fixture(scope="session")
def suite_results(request, tmp_path_factory) -> dict[str, tuple[int, str, float]]:
    """并发跑完本次选中的全部脚本，结果按脚本名缓存（会话级，只跑一次）。"""
    scripts = _selected_scripts(request)
    if not scripts:
        return {}
    base = tmp_path_factory.mktemp("suite")
    workers = _workers()
    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(
            f"[suite] 并发执行 {len(scripts)} 个脚本（{workers} 并发）..."
        )
    results: dict[str, tuple[int, str, float]] = {}
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_run_script, s, base / s.stem): s for s in scripts
        }
        for fut in as_completed(futures):
            script = futures[fut]
            results[script.name] = fut.result()
    if reporter is not None:
        reporter.write_line(
            f"[suite] 完成：{len(results)} 个脚本，用时 {time.perf_counter() - t0:.1f}s"
        )
    return results


@pytest.mark.parametrize("script", _scripts(), ids=lambda p: p.name)
def suite_script(script: Path, suite_results: dict[str, tuple[int, str, float]]) -> None:
    """断言单个脚本的退出码为 0。

    函数名不带 test_ 前缀：pytest.ini 里 python_functions=suite_*，
    让 pytest 只收集本运行器，避免与脚本内的 async test_* 函数重复收集
    （脚本内函数由各自的 main() 编排执行）。
    """
    rc, tail, elapsed = suite_results[script.name]
    if rc != 0:
        pytest.fail(
            f"{script.name} 退出码 {rc}（{elapsed:.1f}s）\n{tail}"
        )
