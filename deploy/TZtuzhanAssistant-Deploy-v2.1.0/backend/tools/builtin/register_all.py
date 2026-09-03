# -*- coding: utf-8 -*-
"""注册内置工具（v2：仅记忆系统）。

其余工具已全部插件化（plugins/*.py，由插件系统加载管理）：
web_search / web_fetch / file_ops / file_search / file_edit / todo /
subagent / skill / code_exec / system / external。
人格、记忆、好感度三大核心保留内置（用户要求）。
"""
from __future__ import annotations

from . import memory


def register_all() -> None:
    memory.register()
