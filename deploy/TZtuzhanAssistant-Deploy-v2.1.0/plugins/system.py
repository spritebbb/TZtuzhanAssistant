# -*- coding: utf-8 -*-
"""工具插件：本机操控（系统信息、进程、窗口、截图、剪贴板、打开应用）。

全部 tools 使用 Windows 原生能力（tasklist/taskkill/PowerShell/Win32 API/PIL），
不引入额外依赖。
"""
from __future__ import annotations

PLUGIN_META = {
    "name": "本机操控",
    "version": "1.0.0",
    "description": "system_info / 进程 / 窗口 / 截图 / 剪贴板 / 打开应用（每步确认）",
    "author": "tuzhan",
}

import asyncio
import os
import platform
import subprocess
import tempfile
import time
from pathlib import Path

from backend.tools.base import ToolRegistry, tool_failure
from backend.tools.safety import check_command

# ---- 工具实现 ----

async def _system_info() -> str:
    """获取系统信息：OS/CPU/内存/磁盘/开机时间。"""
    lines = [f"系统: {platform.system()} {platform.version()}",
             f"主机: {platform.node()}",
             f"CPU: {platform.processor()}",
             f"Python: {platform.python_version()}",
             f"工作目录: {os.getcwd()}"]
    # 磁盘（C: 盘）
    try:
        import shutil
        usage = shutil.disk_usage("C:\\")
        lines.append(f"C: 盘: 总 {usage.total // (1024**3)}GB, 剩余 {usage.free // (1024**3)}GB")
    except Exception:
        pass
    # 系统资源（内存）：wmic 自 Win11 24H2 起默认移除，改用 PowerShell
    # Get-CimInstance；失败再退回 ctypes GlobalMemoryStatusEx；都不行时
    # 显式标注"获取失败"，不再静默缺字段。
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell", "-NoProfile", "-Command",
            "$os = Get-CimInstance Win32_OperatingSystem;"
            "Write-Output (('{0}|{1}' -f $os.TotalVisibleMemorySize, $os.FreePhysicalMemory).Replace(',', ''))",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        txt = (out.decode("utf-8", errors="replace") if out else "").strip()
        if txt:
            parts = txt.strip().split("|")
            if len(parts) >= 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
                total_kb = int(parts[0].strip())
                free_kb = int(parts[1].strip())
                lines.append(f"内存: 总 {total_kb // 1024}MB, 剩余 {free_kb // 1024}MB")
            else:
                raise ValueError(f"内存输出无法解析: {txt[:80]}")
        else:
            raise ValueError("内存输出为空")
    except Exception:
        try:
            import ctypes

            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                total_mb = stat.ullTotalPhys // (1024 * 1024)
                free_mb = stat.ullAvailPhys // (1024 * 1024)
                lines.append(f"内存: 总 {total_mb}MB, 剩余 {free_mb}MB")
            else:
                lines.append("内存: （获取失败：PowerShell 与 Win32 API 均不可用）")
        except Exception:
            lines.append("内存: （获取失败：PowerShell 与 Win32 API 均不可用）")
    return "\n".join(lines)


async def _list_process(filter: str = "") -> str:
    """列出进程。可选 filter 按名称过滤（如 notepad、chrome）。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "tasklist", "/FO", "CSV", "/NH",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        txt = out.decode("utf-8", errors="replace") if out else ""
        if not txt:
            return tool_failure("（无法获取进程列表）")
        lines = []
        for line in txt.strip().splitlines():
            if not line.strip():
                continue
            parts = line.strip().strip('"').split('","')
            if len(parts) >= 2:
                name = parts[0].strip('"')
                pid = parts[1].strip('"')
                mem = parts[-1].strip('"') if len(parts) >= 5 else ""
                if filter and filter.lower() not in name.lower():
                    continue
                lines.append(f"{name} (PID:{pid}) {mem}")
        if not lines:
            return f"（未找到匹配\"{filter}\"的进程）"
        return "\n".join(lines[:50]) + (f"\n…（共 {len(lines)} 个，仅显示前 50）" if len(lines) > 50 else "")
    except Exception as e:
        return tool_failure(f"（获取进程列表失败：{e}）")


async def _kill_process(pid: int = 0, name: str = "") -> str:
    """结束进程（按 PID 或名称）。有安全限制，需确认。"""
    if pid:
        try:
            proc = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(pid), "/T",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            if proc.returncode == 0:
                return f"已结束进程 PID {pid}"
            return tool_failure(f"（结束进程失败，PID {pid} 可能不存在或权限不足）")
        except Exception as e:
            return tool_failure(f"（结束进程失败：{e}）")
    if name:
        # 校验名称本身（不再拼 taskkill 前缀——那会命中自身黑名单导致永远被拦）。
        # 名称作为 taskkill 的独立参数传入（无 shell 注入面），这里只拒绝
        # 危险模式与 shell 元字符，避免误杀关键进程/夹带命令。
        ok, err = check_command(name)
        if not ok:
            return err
        if any(ch in name for ch in "&|<>^;\""):
            return tool_failure("（进程名包含非法字符，已拒绝）")
        try:
            proc = await asyncio.create_subprocess_exec(
                "taskkill", "/IM", name, "/F",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            if proc.returncode == 0:
                return f"已结束进程：{name}"
            return tool_failure(f"（未找到进程：{name}）")
        except Exception as e:
            return tool_failure(f"（结束进程失败：{e}）")
    return tool_failure("（请指定 pid 或 name）")


async def _list_window() -> str:
    """列出当前可见窗口。"""
    try:
        import win32gui
        windows = []
        def _enum(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            text = win32gui.GetWindowText(hwnd)
            if text and len(text) > 1:
                rect = win32gui.GetWindowRect(hwnd)
                w, h = rect[2] - rect[0], rect[3] - rect[1]
                if w > 50 and h > 50:  # 忽略极小窗口
                    windows.append(f"{text} (hwnd:{hwnd}, {w}x{h})")
        win32gui.EnumWindows(_enum, None)
        if not windows:
            return "（未找到可见窗口）"
        return "\n".join(windows[:30]) + (f"\n…（共 {len(windows)} 个，仅显示前 30）" if len(windows) > 30 else "")
    except Exception as e:
        return tool_failure(f"（获取窗口列表失败：{e}）")


async def _activate_window(hwnd: int = 0, title: str = "") -> str:
    """激活指定窗口（按 hwnd 或标题关键词）。"""
    try:
        import win32gui
        import win32con
        hwnd_target = hwnd
        if not hwnd_target and title:
            # 按标题关键词搜索
            def _find(h, _):
                nonlocal hwnd_target
                if win32gui.IsWindowVisible(h) and title.lower() in win32gui.GetWindowText(h).lower():
                    hwnd_target = h
            win32gui.EnumWindows(_find, None)
        if not hwnd_target:
            return tool_failure("（未找到匹配的窗口）")
        win32gui.ShowWindow(hwnd_target, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd_target)
        text = win32gui.GetWindowText(hwnd_target)
        return f"已激活窗口：{text}"
    except Exception as e:
        return tool_failure(f"（激活窗口失败：{e}）")


async def _open_app(command: str = "") -> str:
    """打开应用/文件（通过 shell 启动，如 notepad, calc, explorer, 或路径）。"""
    if not command:
        return tool_failure("（缺少要打开的应用）")

    # 与 run_command 同一套危险命令黑名单（路径分支与应用名分支都要过）
    ok, err = check_command(command)
    if not ok:
        return err

    # 路径分支：命令含盘符/反斜杠/首尾引号时视为"程序或文件路径"。
    # 带空格的路径（如 C:\Program Files\...）此前被 cmd 引号过滤挡死；
    # 这里改经 PowerShell Start-Process -FilePath（单引号字面量传参），
    # 不经 cmd /c 解释，既支持空格路径也无 shell 元字符注入面。
    stripped = command.strip()
    path_target = stripped
    if len(stripped) >= 2 and stripped[0] == '"' and stripped[-1] == '"':
        path_target = stripped[1:-1]
    looks_like_path = (
        (len(path_target) >= 2 and path_target[1] == ":")
        or "\\" in path_target
        or path_target.startswith(("\\\\", "/"))
    )
    if looks_like_path:
        try:
            safe = path_target.replace("'", "''")
            proc = await asyncio.create_subprocess_exec(
                "powershell", "-NoProfile", "-Command",
                f"Start-Process -FilePath '{safe}'",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
            if proc.returncode == 0:
                return f"✓ 已启动：{command}"
            return tool_failure(f"（启动失败，返回码 {proc.returncode}）")
        except Exception as e:
            return tool_failure(f"（启动失败：{e}）")

    # 应用名分支（notepad/calc 等）：cmd /c start 会把参数当命令行解释，
    # 拒绝 shell 元字符，防止命令夹带（含 % 环境变量展开与换行续写）
    if any(ch in command for ch in "&|<>^;\"%\r\n"):
        return tool_failure("（要打开的内容包含非法字符，已拒绝）")
    try:
        # 用 subprocess 启动（不等待，不捕获输出）
        proc = await asyncio.create_subprocess_exec(
            "cmd", "/c", "start", "", command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=5)
        if proc.returncode == 0:
            return f"✓ 已启动：{command}"
        return tool_failure(f"（启动失败，返回码 {proc.returncode}）")
    except Exception as e:
        return tool_failure(f"（启动失败：{e}）")


async def _screenshot() -> str:
    """截取屏幕并保存到 data/screenshots/，返回文件名。"""
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        screenshots_dir = Path(__file__).resolve().parents[2] / "data" / "screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        name = f"screen_{int(time.time())}.png"
        path = screenshots_dir / name
        img.save(path, "PNG")
        # 返回相对路径（前端可拼接 /api/images/screenshots/{name}）
        return f"screenshots/{name}"
    except Exception as e:
        return tool_failure(f"（截图失败：{e}）")


async def _clipboard_get() -> str:
    """读取剪贴板文本内容。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell", "-Command", "Get-Clipboard",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return tool_failure("（读取剪贴板超时）")
        txt = out.decode("utf-8", errors="replace") if out else ""
        txt = txt.strip()
        return txt or "（剪贴板为空或非文本内容）"
    except Exception as e:
        return tool_failure(f"（读取剪贴板失败：{e}）")


async def _clipboard_set(text: str = "") -> str:
    """写入剪贴板文本内容。"""
    if not text:
        return tool_failure("（缺少要写入的内容）")
    try:
        # 避免编码问题：用临时文件+PowerShell。文件名用随机 uuid（不再用可预测的
        # 秒级时间戳，防 TOCTOU）；路径里的单引号转义，防 PowerShell 插值出错
        import uuid

        tmp = Path(tempfile.gettempdir()) / f"_clip_{uuid.uuid4().hex}.txt"
        tmp.write_text(text, encoding="utf-8")
        try:
            safe_path = str(tmp).replace("'", "''")
            proc = await asyncio.create_subprocess_exec(
                "powershell", "-Command", f"Get-Content '{safe_path}' -Encoding UTF8 | Set-Clipboard",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
            return f"✓ 已写入剪贴板（{len(text)} 字符）"
        finally:
            tmp.unlink(missing_ok=True)
    except Exception as e:
        return tool_failure(f"（写入剪贴板失败：{e}）")


async def _browser_open(url: str = "") -> str:
    """在默认浏览器中打开 URL。"""
    if not url:
        return tool_failure("（缺少 URL）")
    if not url.startswith(("http://", "https://", "file://")):
        url = "https://" + url
    try:
        import webbrowser
        webbrowser.open(url)
        return f"✓ 已在浏览器中打开：{url}"
    except Exception as e:
        return tool_failure(f"（打开浏览器失败：{e}）")


# ---- 注册 ----

def register(ctx=None) -> None:
    specs = [
        ("system_info", "获取系统基本信息（OS、CPU、内存、磁盘、开机时间）",
         _system_info, {}, "read", "info", False),
        ("list_process", "列出当前运行进程（可选按名称过滤）",
         _list_process, {
             "filter": {"type": "string", "description": "进程名称过滤（如 notepad、chrome，留空显示全部）"}
         }, "read", "info", False),
        ("kill_process", "结束进程（按 PID 或进程名，高权限操作需确认）",
         _kill_process, {
             "pid": {"type": "integer", "description": "进程 PID"},
             "name": {"type": "string", "description": "进程名（如 notepad.exe）"}
         }, "run", "high", True),
        ("list_window", "列出当前可见窗口（标题、句柄、尺寸）",
         _list_window, {}, "read", "info", False),
        ("activate_window", "激活指定窗口（按 hwnd 或标题关键词）",
         _activate_window, {
             "hwnd": {"type": "integer", "description": "窗口句柄（hwnd）"},
             "title": {"type": "string", "description": "窗口标题关键词"}
         }, "run", "normal", True),
        ("open_app", "打开应用/文件（如 notepad, calc, 或路径）",
         _open_app, {
             "command": {"type": "string", "description": "要打开的应用或文件路径"}
         }, "run", "normal", True),
        ("screenshot", "截取屏幕并保存到本地，返回路径",
         _screenshot, {}, "read", "info", False),
        ("clipboard_get", "读取剪贴板文本内容",
         _clipboard_get, {}, "read", "info", False),
        ("clipboard_set", "写入剪贴板文本内容",
         _clipboard_set, {
             "text": {"type": "string", "description": "要写入的文本"}
         }, "write", "normal", True),
        ("browser_open", "在默认浏览器中打开 URL",
         _browser_open, {
             "url": {"type": "string", "description": "要打开的 URL（如 https://example.com）"}
         }, "run", "normal", True),
    ]
    for name, desc, func, props, cat, danger, confirm in specs:
        ToolRegistry.register_func(
            name=name, description=desc, func=func,
            owner="system",
            input_schema={"type": "object", "properties": props, "required": list(props.keys())},
            category=cat, danger_level=danger, needs_confirm=confirm,
        )
