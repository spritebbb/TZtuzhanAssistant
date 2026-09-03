# -*- coding: utf-8 -*-
"""真实 codex / dsh 冒烟验证：验证外部桥加固后能真实调通本机 CLI。

直接调用 plugin_external 的 _codex_run / _dsh_run（不写审计、不弹确认），
观察真实返回与耗时，用于发布前手工冒烟。

用法：
    ./.venv/Scripts/python.exe scripts/smoke_external_bridge.py
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.plugins import loader
from backend.tools.builtin.register_all import register_all

import importlib


async def _smoke_one(name: str, coro) -> None:
    t0 = time.monotonic()
    try:
        result = await coro
        elapsed = time.monotonic() - t0
        print(f"[{name}] 耗时 {elapsed:.1f}s，返回 {len(result)} 字符：")
        print(result)
    except Exception as e:
        elapsed = time.monotonic() - t0
        print(f"[{name}] 异常（{elapsed:.1f}s）：{type(e).__name__}: {e}")
    print("-" * 60)


async def main() -> None:
    # 插件加载必须在事件循环内（currency 插件的 ctx.schedule 依赖 running loop）
    register_all()
    loader.load_all_plugins()
    external = importlib.import_module("plugin_external")

    print("=== Codex CLI 路径 ===")
    print(external._codex_path())
    print("=== DSH CLI 路径 ===")
    print(external._dsh_path())
    print()
    await _smoke_one("codex_run", external._codex_run("请只回复两个字：冒烟"))
    await _smoke_one("dsh_run", external._dsh_run("请只回复两个字：冒烟"))


asyncio.run(main())
