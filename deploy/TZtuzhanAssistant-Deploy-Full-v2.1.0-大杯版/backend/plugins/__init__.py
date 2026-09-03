# -*- coding: utf-8 -*-
"""插件系统包。"""
from .loader import (
    PLUGINS_DIR,
    load_all_plugins,
    load_plugin,
    plugin_states,
    set_disabled,
    start_watch,
    unload_plugin,
    watch_plugins,
)

__all__ = ["PLUGINS_DIR", "load_all_plugins", "load_plugin",
           "unload_plugin", "watch_plugins", "start_watch",
           "plugin_states", "set_disabled"]
