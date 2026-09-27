# -*- coding: utf-8 -*-
"""L10 桌宠全屏避让探测：前台窗口是否全屏（Windows ctypes，离线件）。

契约（docs/Zcode技术指导.md §16 L10）：不抓屏、不读窗口内容、不装键鼠钩子；
返回「前台是否全屏 + 所在显示器」；NP-14 桌面感知扩展（默认关闭，DESKTOP_AWARENESS=1
才启用）：+ 前台进程名→类别 + 输入空闲秒数，仍不读窗口标题/内容。任何失效一律
降级（全屏=False、类别=other），不隐藏由 Electron 侧的 probeError 路径负责——
helper 拿不准时上层保守隐藏，本 helper 失效时返回 False 让上层按「未全屏」继续
显示，避免误伤正常使用。

全屏判定：前台窗口矩形完整覆盖其所在显示器工作外矩形（即真全屏，非最大化——
最大化的窗口仍留任务栏高度，不会命中），且前台不是桌面/壳层窗口。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path

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
        # NP-14 桌面感知 v1（契约扩展，见技术指导 §16）：+ 前台进程名→类别 + 空闲秒数。
        # 仍遵守三不红线：不抓屏、不读窗口内容、不装键鼠钩子（空闲用 GetLastInputInfo）。
        result["process_name"] = _foreground_process_name(hwnd)
        result["idle_seconds"] = _input_idle_seconds()
        result["category"] = categorize(
            str(result.get("process_name") or ""),
            fullscreen=bool(result.get("fullscreen")),
            idle_seconds=float(result.get("idle_seconds") or 0.0),
        )
        return result
    except Exception:
        return {"fullscreen": False, "display_id": "", "window_class": ""}


# ---- NP-14 桌面感知：类别归类（纯函数，可离线测试）----

# v1 保守映射：只认进程名关键词，不做游戏识别（全屏且非 code/browse 归 fullscreen）
_CODE_HINTS = ("code", "cursor", "devenv", "idea", "pycharm", "clion", "goland",
               "webstorm", "rider", "windows terminal", "windowsterminal",
               "powershell", "pwsh", "cmd", "conhost", "wezterm", "alacritty")
_BROWSE_HINTS = ("chrome", "msedge", "edge", "firefox", "brave", "arc", "opera")
_IDLE_AFTER_SECONDS = 300.0


def categorize(process_name: str, *, fullscreen: bool, idle_seconds: float) -> str:
    """前台应用类别（v1 五类）：idle / code / browse / fullscreen / other。

    保守取向：输入空闲优先、宁归 other 不猜、不设 game 类（全屏信号已覆盖
    「她该闭嘴」的诉求）。process_name 取可执行文件名（小写，不含路径）。
    """
    if idle_seconds >= _IDLE_AFTER_SECONDS:
        return "idle"
    name = (process_name or "").strip().lower()
    if name and any(h in name for h in _CODE_HINTS):
        return "code"
    if name and any(h in name for h in _BROWSE_HINTS):
        return "browse"
    if fullscreen:
        return "fullscreen"
    return "other"


def _foreground_process_name(hwnd: int) -> str:
    """前台窗口的可执行文件名（不含路径）；失败返回空串（归 other）。"""
    try:
        k32 = ctypes.windll.kernel32
        u32 = ctypes.windll.user32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        pid = ctypes.c_ulong()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(512)
            size = ctypes.c_ulong(512)
            if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return Path(buf.value).name
            return ""
        finally:
            k32.CloseHandle(handle)
    except Exception:
        return ""


def _input_idle_seconds() -> float:
    """距上次键鼠输入的秒数（GetLastInputInfo，非钩子）；失败返回 0。"""
    try:
        u32 = _user32()

        class _LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("dwTime", ctypes.c_ulong)]

        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if u32.GetLastInputInfo(ctypes.byref(info)):
            return max(0.0, float(ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0)
        return 0.0
    except Exception:
        return 0.0


__all__ = ["probe_foreground", "categorize"]
