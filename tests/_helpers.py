# -*- coding: utf-8 -*-
"""测试共享 helper：按后端 startup 的真实行为装载全部工具。

v2 起工具分两层：
- 内置（builtin）：仅 memory（记忆系统，用户要求保留内置）；
- 插件（plugins/*.py）：web_search/web_fetch/file_ops/file_search/file_edit/
  todo/subagent/skill/code_exec/system/external 等，由插件系统加载。

`load_all_tools()` = register_all() + load_all_plugins()，与 app startup 等价。
下划线开头：不被 pytest / suite_runner 当作测试收集。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load_all_tools() -> list[str]:
    """装载内置工具 + 全部插件，返回成功加载的插件名列表。"""
    from backend.plugins import loader
    from backend.tools.builtin.register_all import register_all

    register_all()
    return loader.load_all_plugins()
