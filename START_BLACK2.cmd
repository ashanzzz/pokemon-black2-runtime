@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "tools\black2_launcher.py" start
) else (
  python "tools\black2_launcher.py" start
)
if errorlevel 1 pause
