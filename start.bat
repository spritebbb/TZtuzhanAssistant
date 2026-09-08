@echo off
rem ============================================================
rem  TZtuzhan Assistant one-click launcher (backend + Electron)
rem
rem  NOTE: keep this file pure ASCII. Non-ASCII text in .bat breaks
rem        parsing depending on the console codepage.
rem
rem  Usage:
rem    start.bat            full launch: build-if-needed + backend + Electron
rem    start.bat no-window  backend only (no Electron window)
rem
rem  Steps:
rem   1. npm run build if frontend/dist is missing or stale (auto npm install)
rem   2. start MCP stdio bridge (only when a local MCP server is registered)
rem   3. reuse backend on :8801 if already running, else start it (.venv first)
rem   4. wait for /api/health
rem   5. launch Electron (production: loads http://127.0.0.1:8801)
rem   6. on exit, stop only the processes started by this script
rem ============================================================
setlocal EnableExtensions
rem Electron main process prints UTF-8 Chinese via console.log; switch codepage to UTF-8
rem so those logs display correctly instead of mojibake.
chcp 65001 >nul
title TZT Assistant Launcher

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "FRONTEND=%ROOT%\frontend"
set "BACKEND_URL=http://127.0.0.1:8801"
set "PY=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "MODE=%~1"

echo.
echo  [TZT] project root: %ROOT%
if /i "%MODE%"=="no-window" echo  [TZT] mode: backend only (no Electron)
echo.

rem ---------- 0) sanity checks ----------
if not exist "%ROOT%\backend\main.py" (
    echo  [ERROR] backend\main.py not found. Put this bat in the project root.
    goto :fail
)
where curl >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] curl.exe not found. Windows 10 1803+ ships it; update Windows or add curl to PATH.
    goto :fail
)

rem ---------- 1) frontend dist: build if missing or stale ----------
set "DIST_STATE=MISSING"
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command ^
  "$d='%FRONTEND%\dist\index.html';" ^
  "if (Test-Path $d) {" ^
  "  $dist = (Get-Item $d).LastWriteTime;" ^
  "  $src = Get-ChildItem '%FRONTEND%\src','%FRONTEND%\electron','%FRONTEND%\public' -Recurse -File -ErrorAction SilentlyContinue |" ^
  "        Sort-Object LastWriteTime -Descending | Select-Object -First 1;" ^
  "  if ($src -and $src.LastWriteTime -gt $dist) { 'STALE' } else { 'FRESH' }" ^
  "} else { 'MISSING' }"`) do set "DIST_STATE=%%i"

if "%DIST_STATE%"=="FRESH" (
    echo  [1/5] frontend dist is up to date, skip build.
    goto :check_node
)
if "%DIST_STATE%"=="MISSING" (
    echo  [1/5] frontend dist missing, building...
) else (
    echo  [1/5] frontend sources newer than dist, rebuilding...
)
if not exist "%FRONTEND%\node_modules" (
    echo        node_modules missing, running npm install...
    pushd "%FRONTEND%"
    call npm install
    if errorlevel 1 (
        echo  [ERROR] npm install failed. Check Node/npm environment.
        popd
        goto :fail
    )
    popd
)
pushd "%FRONTEND%"
call npm run build
if errorlevel 1 (
    echo  [ERROR] npm run build failed. See errors above.
    popd
    goto :fail
)
popd

:check_node
if exist "%FRONTEND%\node_modules\electron" goto :mcp_bridge
echo        Electron dependency missing, running npm install...
pushd "%FRONTEND%"
call npm install
if errorlevel 1 (
    echo  [ERROR] npm install failed. Check Node/npm environment.
    popd
    goto :fail
)
popd

rem ---------- 2) MCP stdio bridge (only if a local MCP server is registered) ----------
rem  The backend connects to servers listed in data\mcp_servers.json and registers
rem  their tools at startup, so the bridge must be ready first. The bridge's
rem  /health returns 200 only after the child process handshake succeeds.
:mcp_bridge
set "MCP_BRIDGE_STARTED=0"
findstr /i "127.0.0.1:8932" "%ROOT%\data\mcp_servers.json" >nul 2>&1
if errorlevel 1 (
    echo  [2/5] no local MCP server registered, skipping bridge.
    goto :backend_step
)
curl --noproxy "*" -s -o NUL -f --max-time 2 "http://127.0.0.1:8932/health" >nul 2>&1
if not errorlevel 1 (
    echo  [2/5] MCP bridge already running, reusing it.
    goto :backend_step
)
where npx >nul 2>&1
if errorlevel 1 (
    echo  [2/5] WARNING: npx not found, MCP bridge skipped ^(MCP tools unavailable^).
    goto :backend_step
)
echo  [2/5] starting MCP stdio bridge (Playwright), waiting until ready...
rem  cd /d ROOT is required: the bridge runs as a module (-m backend.tools...)
start "TZT-mcp-bridge" /min "%ComSpec%" /c "cd /d "%ROOT%" && "%PY%" -X utf8 -m backend.tools.mcp_stdio_bridge --port 8932 -- npx --yes @playwright/mcp@latest --browser chromium"
set "MCP_BRIDGE_STARTED=1"
set /a MCPTRIES=0
:wait_bridge
curl --noproxy "*" -s -o NUL -f --max-time 2 "http://127.0.0.1:8932/health" >nul 2>&1
if not errorlevel 1 goto :bridge_ready
set /a MCPTRIES+=1
if %MCPTRIES% geq 30 (
    echo        WARNING: MCP bridge not ready within 90s, continuing without it.
    goto :backend_step
)
ping -n 3 127.0.0.1 >nul
goto :wait_bridge

:bridge_ready
echo  [2/5] MCP bridge ready: http://127.0.0.1:8932/mcp

rem ---------- 3) backend: reuse or start ----------
:backend_step
set "REUSED=0"
curl --noproxy "*" -s -o NUL -f --max-time 2 "%BACKEND_URL%/api/health" >nul 2>&1
if not errorlevel 1 (
    echo  [3/5] backend already running, reusing it.
    set "REUSED=1"
    goto :backend_ready
)

echo  [3/5] starting backend in a minimized window...
start "TZT-backend" /min "%ComSpec%" /c ""%PY%" -X utf8 "%ROOT%\backend\main.py" --host 127.0.0.1 --port 8801"

set /a TRIES=0
:wait_backend
curl --noproxy "*" -s -o NUL -f --max-time 2 "%BACKEND_URL%/api/health" >nul 2>&1
if not errorlevel 1 goto :backend_ready
set /a TRIES+=1
if %TRIES% geq 60 (
    echo  [ERROR] backend not ready within 60s. Check the TZT-backend window for errors.
    goto :fail
)
ping -n 2 127.0.0.1 >nul
goto :wait_backend

:backend_ready
echo  [3/5] backend ready: %BACKEND_URL%

if /i "%MODE%"=="no-window" (
    echo  [4/5] no-window mode: keeping backend running, exiting launcher.
    echo        Backend stays up. Close it manually or run: taskkill /f /im python.exe
    exit /b 0
)

rem ---------- 4) Electron (blocks until window closed) ----------
echo  [4/5] launching Electron window (close it to exit)...
rem Electron rejects --use-env-proxy in NODE_OPTIONS (exit 9). This machine sets
rem NODE_OPTIONS=--use-env-proxy for proxy sync; strip proxy flags for Electron only.
set "NODE_OPTIONS_SAVED=%NODE_OPTIONS%"
set "NODE_OPTIONS="
pushd "%FRONTEND%"
call npx electron .
set "EC=%errorlevel%"
popd
set "NODE_OPTIONS=%NODE_OPTIONS_SAVED%"
set "NODE_OPTIONS_SAVED="
echo  [4/5] Electron exited (code %EC%).

rem ---------- 5) cleanup: only what this script started ----------
if "%REUSED%"=="0" (
    echo  [5/5] stopping the backend started by this script...
    powershell -NoProfile -Command ^
      "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" |" ^
      " Where-Object { $_.CommandLine -like '*backend*main.py*--port 8801*' } |" ^
      " ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
) else (
    echo  [5/5] backend was reused, leaving it running.
)
if "%MCP_BRIDGE_STARTED%"=="1" (
    echo  [5/5] stopping the MCP bridge started by this script...
    rem Kill both the python bridge and its npx/node child, or the browser stays
    powershell -NoProfile -Command ^
      "Get-CimInstance Win32_Process |" ^
      " Where-Object { $_.CommandLine -like '*mcp_stdio_bridge*--port 8932*' -or $_.CommandLine -like '*@playwright/mcp*' } |" ^
      " ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
) else (
    echo  [5/5] MCP bridge not started by this script, leaving it as is.
)
echo  Bye.
timeout /t 2 >nul
exit /b 0

:fail
echo.
echo  Launch failed. Common causes:
echo   - Node/npm not in PATH (frontend build)
echo   - Python deps not installed (create .venv, then pip install -r requirements.txt)
echo   - Port 8801 occupied (netstat -ano ^| findstr 8801)
pause
exit /b 1
