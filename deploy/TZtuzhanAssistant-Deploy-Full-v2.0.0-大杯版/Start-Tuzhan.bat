@echo off
setlocal EnableExtensions
title Tuzhan Assistant - One-click Launcher
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"
set "URL=http://127.0.0.1:8801"
set "VENV=%ROOT%\.venv"
set "PY="

echo.
echo  ============================================================
echo   Tuzhan Assistant - one-click web launcher (port 8801)
echo  ============================================================
echo.

rem ---- 0) find python ----
if exist "%VENV%\Scripts\python.exe" (
    set "PY=%VENV%\Scripts\python.exe"
) else (
    where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo  [ERROR] Python not found.
    echo          Install Python 3.11-3.13 from https://www.python.org/downloads/
    echo          and tick "Add python.exe to PATH" during installation.
    pause
    exit /b 1
)
echo  [1/5] Python: %PY%

rem ---- 1) create venv if missing ----
if not exist "%VENV%\Scripts\python.exe" (
    echo  [2/5] Creating virtual environment...
    "%PY%" -m venv "%VENV%"
    if errorlevel 1 goto :venvfail
    set "PY=%VENV%\Scripts\python.exe"
)

rem ---- 2) install dependencies once ----
if not exist "%VENV%\Lib\site-packages\fastapi" (
    echo  [3/5] Installing Python dependencies, this may take a while...
    "%PY%" -m pip install --upgrade pip
    if errorlevel 1 goto :pipfail
    "%PY%" -m pip install -r "%ROOT%\requirements.txt"
    if errorlevel 1 goto :pipfail
) else (
    echo  [3/5] Dependencies already installed, skip.
)
set "PY=%VENV%\Scripts\python.exe"

rem ---- 3) first-run .env ----
if not exist "%ROOT%\.env" (
    echo  [4/5] Creating .env from template...
    copy /Y "%ROOT%\.env.example" "%ROOT%\.env" >nul
)
set "ENV_OK=0"
for /f %%i in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$t=Get-Content -Raw -Encoding UTF8 ''%ROOT%\.env''; if($t -match ''(?m)^\s*LLM_API_KEY\s*=\s*(\S)''){''1''}else{''0''}"') do set "ENV_OK=%%i"
if not "%ENV_OK%"=="1" (
    echo  [WARN] LLM_API_KEY is empty.
    echo         A text editor will open .env - fill in LLM_API_KEY, save, close it,
    echo         then come back here and press any key.
    start "" notepad "%ROOT%\.env"
    pause
)

rem ---- 4) start backend if not running ----
echo  [5/5] Checking backend on port 8801...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8801/api/health' -TimeoutSec 2; if($r.StatusCode -eq 200){exit 0} } catch {}; exit 1" >nul 2>&1
if errorlevel 1 (
    echo        Starting backend...
    start "Tuzhan-backend" /min "%ComSpec%" /c ""%PY%" -X utf8 "%ROOT%\backend\main.py" --host 127.0.0.1 --port 8801"
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok=$false; for($i=0;$i -lt 90;$i++){ try { $r=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8801/api/health' -TimeoutSec 2; if($r.StatusCode -eq 200){$ok=$true; break} } catch {}; Start-Sleep -Milliseconds 1000 }; if(-not $ok){exit 1}" >nul 2>&1
    if errorlevel 1 goto :backendfail
)

echo  Backend ready: %URL%
start "" "%URL%"
echo.
echo  Tuzhan Assistant is running in your browser.
echo  To stop it later, double-click Stop-Tuzhan.bat.
echo.
pause
exit /b 0

:venvfail
echo  [ERROR] Failed to create virtual environment.
echo          Check Python installation (3.11-3.13 recommended).
pause
exit /b 1

:pipfail
echo  [ERROR] pip install failed.
echo          If this is a network problem, run manually with a mirror:
echo          "%PY%" -m pip install -r "%ROOT%\requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple
pause
exit /b 1

:backendfail
echo  [ERROR] Backend did not become ready within 90 seconds.
echo          Check the "Tuzhan-backend" minimized window for errors.
pause
exit /b 1
