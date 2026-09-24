# -*- coding: utf-8 -*-
"""L10 桌宠全屏避让探测：前台窗口是否全屏（Windows ctypes，离线件）。

契约（docs/Zcode技术指导.md §16 L10）：不抓屏、不读窗口内容、不装键鼠钩子；
只返回「前台是否全屏 + 所在显示器」；任何失效一律返回 not fullscreen（保守
不隐藏由 Electron 侧的 probeError 路径负责——helper 拿不准时上层保守隐藏，
本 helper 失效时返回 False 让上层按「未全屏」继续显示，避免误伤正常使用）。

全屏判定：前台窗口矩形完整覆盖其所在显示器工作外矩形（即真全屏，非最大化——
最大化的窗口仍留任务栏高度，不会命中），且前台不是桌面/壳层窗口。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

# 桌面/壳层窗口类名（全屏判定排除）
_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", ctypes.c_ulong),
    ]


def _user32():
    return ctypes.windll.user32


def probe_foreground() -> dict:
    """探测前台窗口。永不满屏断言系统可用性——内部异常一律降级为未全屏。

    返回 {"fullscreen": bool, "display_id": str, "window_class": str}；
    window_class 仅用于诊断与用户排除进程配置（不含窗口标题/内容）。
    """
    result = {"fullscreen": False, "display_id": "", "window_class": ""}
    try:
        u32 = _user32()
        hwnd = u32.GetForegroundWindow()
        if not hwnd:
            return result
        # 类名：排除桌面/壳层
        buf = ctypes.create_unicode_buffer(256)
        if u32.GetClassNameW(hwnd, buf, 256):
            result["window_class"] = buf.value or ""
        if result["window_class"] in _SHELL_CLASSES:
            return result
        rect = _RECT()
        if not u32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return result
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        hmon = u32.MonitorFromWindow(hwnd, 1)  # MONITOR_DEFAULTTONEAREST
        if not hmon or not u32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return result
        mon = mi.rcMonitor
        w = max(1, mon.right - mon.left)
        h = max(1, mon.bottom - mon.top)
        # 完整覆盖显示器矩形（1px 容差）= 真全屏；最大化窗口留任务栏不会命中
        covered = (
            rect.left <= mon.left + 1 and rect.top <= mon.top + 1
            and rect.right >= mon.right - 1 and rect.bottom >= mon.bottom - 1
        )
        if covered and (rect.right - rect.left) >= w and (rect.bottom - rect.top) >= h:
            result["fullscreen"] = True
        # 显示器标识：设备名需 EnumDisplayDevices 链，这里用矩形指纹（够Electron侧日志用）
        result["display_id"] = f"{mon.left},{mon.top},{w}x{h}"
        return result
    except Exception:
        return {"fullscreen": False, "display_id": "", "window_class": ""}


__all__ = ["probe_foreground"]
