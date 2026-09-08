@echo off
rem ============================================================
rem  一键启动 Playwright MCP（stdio） + 菟菚 stdio 桥
rem
rem  用法：双击本文件，或 start-mcp-playwright.bat
rem  作用：把官方 Playwright MCP 的 stdio 协议桥接成菟菚能连的
rem        Streamable HTTP 端点 http://127.0.0.1:8932/mcp
rem
rem  前置：
rem   1. .env 里有 AGENT_MCP_ALLOW_LOOPBACK=1（已配好）
rem   2. data/mcp_servers.json 已登记 playwright → 127.0.0.1:8932/mcp（已配好）
rem  演示顺序：先跑本脚本 → 再 start.bat 启动菟菚 → 设置页确认工具已注册
rem  停止：关掉本窗口（或 Ctrl+C）
rem ============================================================
setlocal EnableExtensions
chcp 65001 >nul
title TZT MCP Bridge (Playwright)

set "ROOT=%~dp0.."
cd /d "%ROOT%"

set "PY=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo  [ERROR] 找不到 %PY%
    echo          请先在项目根创建 .venv 并安装依赖。
    pause
    exit /b 1
)

echo.
echo  [TZT] 启动 Playwright MCP 桥
echo        子进程：npx --yes @playwright/mcp@latest
echo        端点  ：http://127.0.0.1:8932/mcp
echo        提示  ：首次运行会下载 Playwright MCP 包，请耐心等待；
echo                需要无头模式可把下面的 --headless 加上。
echo.

"%PY%" -X utf8 -m backend.tools.mcp_stdio_bridge --port 8932 -- npx --yes @playwright/mcp@latest

echo.
echo  [TZT] 桥已退出。若这里出现错误，请把上面的输出发给管理员排查。
pause
