@echo off
setlocal
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"
set "PY=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo  [ERROR] .venv not found. Run Start-Tuzhan.bat once first to install dependencies.
    pause
    exit /b 1
)
echo Initializing Tuzhan Assistant databases (idempotent, keeps existing data)...
"%PY%" "%ROOT%\init_database.py"
if errorlevel 1 (
    echo  [ERROR] Database initialization failed.
    pause
    exit /b 1
)
echo.
echo Done. Databases are ready under .\data
pause
