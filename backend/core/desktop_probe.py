# -*- coding: utf-8 -*-
"""L10 桌宠全屏避让探测：前台窗口是否全屏（Windows ctypes，离线件）。

契约（docs/Zcode技术指导.md §16 L10）：不抓屏、不读窗口内容、不装键鼠钩子；
返回「前台是否全屏 + 所在显示器」；NP-14 桌面感知扩展（默认关闭，DESKTOP_AWARENESS=1
才启用）：+ 前台进程名→类别 + 输入空闲秒数。#24 标题脱敏级（2026-09-30 拍板，
同样默认关）：窗口标题只经 `title_context` 提取脱敏语境标签（敏感域/未知语境
一律空串），永不透传标题原文；仍不抓屏、不读窗口内容、不装键鼠钩子。任何失效一律
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
        # #24 标题脱敏级：读标题只为提取脱敏语境标签（敏感域/未知语境都返回空，
        # 绝不透传标题原文）——「读窗口标题」红线从「不读」升为「读了只出标签」，
        # 用户拍板于 2026-09-30。
        tbuf = ctypes.create_unicode_buffer(512)
        title = tbuf.value if u32.GetWindowTextW(hwnd, tbuf, 512) else ""
        result["title_context"] = title_context(title, str(result.get("process_name") or ""))
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

# ---- #24 标题脱敏级（2026-09-30 拍板「升到标题脱敏级」）----
# 读窗口标题只为提取「语境标签」，绝不透传标题原文：
# 1) 敏感域命中（网银/密码/私聊/医疗/邮箱…）→ 一律返回空串，她「看不见」；
# 2) 已知语境关键词命中 → 返回 ≤20 字的脱敏标签（"在看 B 站视频"）；
# 3) 无命中 → 返回空串（宁缺毋滥，退回 category 五类）。
_TITLE_SENSITIVE = (
    "银行", "网银", "支付", "支付宝", "微信支付", "转账", "余额", "征信", "贷款",
    "公积金", "社保", "医保", "病历", "挂号", "体检", "密码", "password", "passwd",
    "验证", "verify", "otp", "登录", "login", "signin", "注册", "logout", "账号",
    "邮箱", "mailbox", "inbox", "bitwarden", "1password", "keepass", "lastpass",
    "微信", "wechat", "qq", "tim", "telegram", "discord", "whatsapp", "飞书",
    "钉钉", "snapchat", "signal", "聊天", "消息", "私信", "brief", "无痕",
    "incognito", "inprivate", "简历", "resume", "offer", "工资", "薪资", "报税",
)
_TITLE_CONTEXTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("哔哩哔哩", "bilibili", "b站", "番剧"), "在看 B 站视频"),
    (("youtube", "油管"), "在看 YouTube 视频"),
    (("腾讯视频", "爱奇艺", "优酷", "芒果tv", "netflix", "迪士尼"), "在看视频"),
    (("抖音", "tiktok"), "在刷短视频"),
    (("github", "gitee", "gitlab", "stack overflow", "stackoverflow", "csdn"), "在看技术社区"),
    (("steam", "epic games", "育碧", "riot"), "在逛游戏商店"),
    (("淘宝", "京东", "拼多多", "天猫", "苏宁"), "在网购"),
    (("知乎",), "在刷知乎"),
    (("微博",), "在刷微博"),
    (("小红书",), "在逛小红书"),
    (("贴吧", "豆瓣"), "在逛社区"),
    (("word", "wps", "文档", "论文", "毕业设计", "开题", "tex", "latex"), "在看文档"),
    (("excel", "表格", "spreadsheet", "数据透视"), "在处理表格"),
    (("pdf", "caj"), "在读 PDF"),
    (("figma", "即时设计", "master go"), "在做设计"),
    (("photoshop", "ps ", "修图", "lightroom"), "在修图"),
    (("premiere", "剪映", "达芬奇", "resolve", "剪辑"), "在剪视频"),
    (("地图", "高德", "百度地图"), "在看地图"),
    (("天气",), "在看天气"),
    (("新闻", "头条", "澎湃", "参考消息"), "在看新闻"),
)


def title_context(title: str, process_name: str = "") -> str:
    """窗口标题 → 脱敏语境标签（纯函数，可离线测试）。

    返回空串的三种情况：无标题 / 敏感域命中 / 无已知语境命中。
    永不返回标题原文——这是本函数的契约底线。
    """
    t = (title or "").strip()
    if not t:
        return ""
    probe = (t + " " + (process_name or "").lower()).lower()
    if any(k in probe for k in _TITLE_SENSITIVE):
        return ""
    for keys, label in _TITLE_CONTEXTS:
        if any(k in probe for k in keys):
            return label
    return ""


def categorize(process_name: str, *, fullscreen: bool, idle_seconds: float) -> str:
    """前台应用类别（v1 五类）：fullscreen / idle / code / browse / other。

    保守取向：全屏最优先、宁归 other 不猜、不设 game 类。全屏判定必须先于
    code/browse（DF-6）：否则 F11 全屏浏览器看视频（最普遍的「看视频」形态）
    会归 browse，主动性静默被架空——docstring 里「全屏信号已覆盖她该闭嘴」
    的设计意图即全屏优先。process_name 取可执行文件名（小写，不含路径）。
    """
    if fullscreen:
        return "fullscreen"
    if idle_seconds >= _IDLE_AFTER_SECONDS:
        return "idle"
    name = (process_name or "").strip().lower()
    if name and any(h in name for h in _CODE_HINTS):
        return "code"
    if name and any(h in name for h in _BROWSE_HINTS):
        return "browse"
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
            # DF-6：GetTickCount 默认按 signed 32 位解释，开机 24.8~49.7 天区间
            # 读出负值、与 dwTime（c_ulong 无符号）相减得大负数被 max(0) 钳成 0
            # ——长期不关机的台机 idle 永远失效。显式声明无符号 32 位返回。
            k32.GetTickCount.restype = wintypes.ULONG
            return max(0.0, float(k32.GetTickCount() - info.dwTime) / 1000.0)
        return 0.0
    except Exception:
        return 0.0


__all__ = ["probe_foreground", "categorize", "title_context"]
