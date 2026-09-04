@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (".venv\Scripts\python.exe" "tools\black2_launcher.py" stop) else (python "tools\black2_launcher.py" stop)
if errorlevel 1 pause
