@echo off
rem Champ Studio — start the engine and open the web UI.
cd /d "%~dp0..\engine"
if not exist ".venv\Scripts\python.exe" (
    echo Run windows\setup-windows.ps1 first.
    pause
    exit /b 1
)
start "" http://127.0.0.1:8787
.venv\Scripts\python.exe -m champstudio.main
