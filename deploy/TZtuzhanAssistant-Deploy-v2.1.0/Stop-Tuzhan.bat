@echo off
setlocal
echo Stopping Tuzhan backend (port 8801) if running...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*backend*main.py*--port 8801*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo Done.
pause
